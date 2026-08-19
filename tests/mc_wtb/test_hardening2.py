from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import demos.known_motion_coordinate.model as known_model
import demos.mc_wtb.model as mc_model
from demos.mc_wtb.model import (
    LOGICAL_BIT_FORMAT,
    MODEL_IMPLEMENTATION_ID,
    RESULT_CONTRACT_REVISION,
    InterfaceError,
    analyze_files,
)


ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "demos" / "mc_wtb" / "fixtures" / "events.jsonl"
KNOWN = ROOT / "demos" / "known_motion_coordinate" / "fixtures"
INTRINSICS = KNOWN / "intrinsics.json"
POSES = KNOWN / "poses.jsonl"


class DelegatingFileOps:
    def __init__(self) -> None:
        self.real = mc_model._PosixFileOps()

    def __getattr__(self, name):
        return getattr(self.real, name)


class ParentRedirectOps(DelegatingFileOps):
    def __init__(self, parent: Path, moved: Path, redirect: Path) -> None:
        super().__init__()
        self.parent = parent
        self.moved = moved
        self.redirect = redirect
        self.injected = False

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        descriptor = self.real.open(path, flags, mode, dir_fd=dir_fd)
        if not self.injected and flags & os.O_DIRECTORY:
            os.rename(self.parent, self.moved)
            os.symlink(self.redirect, self.parent, target_is_directory=True)
            self.injected = True
        return descriptor


class TargetInjectionOps(DelegatingFileOps):
    def __init__(self, output: Path, source: Path, kind: str) -> None:
        super().__init__()
        self.output = output
        self.source = source
        self.kind = kind
        self.injected = False

    def statat(self, path, *, dir_fd, follow_symlinks):
        if dir_fd is not None and path == self.output.name and not self.injected:
            if self.kind == "hardlink":
                os.link(self.source, self.output)
            else:
                os.symlink(self.source, self.output)
            self.injected = True
        return self.real.statat(
            path, dir_fd=dir_fd, follow_symlinks=follow_symlinks
        )


class PublicationFailureOps(DelegatingFileOps):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure
        self.failed = False

    def write(self, descriptor, data):
        if self.failure == "write" and not self.failed:
            self.failed = True
            raise OSError(errno.ENOSPC, "injected write failure")
        return self.real.write(descriptor, data)

    def renameat(self, source, target, *, src_dir_fd, dst_dir_fd):
        if self.failure == "rename" and not self.failed:
            self.failed = True
            raise OSError(errno.EPERM, "injected rename failure")
        return self.real.renameat(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )


class HardeningTwoTests(unittest.TestCase):
    def copy_inputs(self, directory: str) -> tuple[Path, Path, Path]:
        base = Path(directory)
        events = base / "events.jsonl"
        intrinsics = base / "intrinsics.json"
        poses = base / "poses.jsonl"
        for source, target in (
            (EVENTS, events),
            (INTRINSICS, intrinsics),
            (POSES, poses),
        ):
            shutil.copyfile(source, target)
        return events, intrinsics, poses

    def analyze(
        self,
        events: Path,
        intrinsics: Path,
        poses: Path,
        output: Path,
        *,
        max_pose_age_ns: int = 0,
        tile_width: int = 8,
        tile_height: int = 8,
        time_bin_ns: int = 1000,
    ) -> dict:
        return analyze_files(
            events,
            intrinsics,
            poses,
            output,
            tile_width=tile_width,
            tile_height=tile_height,
            time_bin_ns=time_bin_ns,
            max_pose_age_ns=max_pose_age_ns,
        )

    def test_each_parser_gets_one_read_blob_and_reported_hashes_match_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            primary = {path.resolve() for path in (events, intrinsics, poses)}
            counts = {path: 0 for path in primary}
            original_open = known_model.os.open

            def counted(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is None:
                    resolved = Path(path).resolve()
                    if resolved in counts:
                        counts[resolved] += 1
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.object(known_model.os, "open", counted):
                result = self.analyze(
                    events, intrinsics, poses, Path(directory) / "result.json"
                )
            self.assertEqual(set(counts.values()), {1})
            provenance = result["input_provenance"]
            for path, key in (
                (events, "events_sha256"),
                (intrinsics, "intrinsics_sha256"),
                (poses, "poses_sha256"),
            ):
                self.assertEqual(
                    provenance[key], hashlib.sha256(path.read_bytes()).hexdigest()
                )
            self.assertEqual(
                provenance["hash_scope"],
                "exact immutable bytes consumed by each parser",
            )
            self.assertIn("no atomic three-file snapshot", provenance["snapshot_scope"])

    def test_a_to_b_to_a_during_parse_cannot_mismatch_hash_and_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            baseline = self.analyze(
                events, intrinsics, poses, Path(directory) / "baseline.json"
            )
            original_bytes = intrinsics.read_bytes()
            altered = json.loads(original_bytes)
            altered["fx"] = altered["fx"] + 7
            variant = Path(directory) / "intrinsics-b.json"
            held = Path(directory) / "intrinsics-a-held.json"
            variant.write_text(json.dumps(altered) + "\n", encoding="utf-8")
            real_parser = mc_model.parse_intrinsics_blob

            def aba_parser(blob):
                os.replace(intrinsics, held)
                os.replace(variant, intrinsics)
                try:
                    return real_parser(blob)
                finally:
                    os.replace(intrinsics, variant)
                    os.replace(held, intrinsics)

            with mock.patch.object(
                mc_model, "parse_intrinsics_blob", side_effect=aba_parser
            ):
                result = self.analyze(
                    events, intrinsics, poses, Path(directory) / "aba.json"
                )
            self.assertEqual(result, baseline)
            self.assertEqual(
                result["input_provenance"]["intrinsics_sha256"],
                hashlib.sha256(original_bytes).hexdigest(),
            )
            self.assertEqual(intrinsics.read_bytes(), original_bytes)

    def test_logical_contract_is_immutable_and_results_are_isolated(self) -> None:
        with self.assertRaises(TypeError):
            LOGICAL_BIT_FORMAT["format_id"] = "mutated"
        with self.assertRaises(TypeError):
            LOGICAL_BIT_FORMAT["raw_sensor_payload"]["x_bits"] = 1

        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            first_path = Path(directory) / "first.json"
            second_path = Path(directory) / "second.json"
            first = self.analyze(events, intrinsics, poses, first_path)
            pristine_bytes = first_path.read_bytes()

            def mutate(value):
                if isinstance(value, dict):
                    for child in list(value.values()):
                        mutate(child)
                    value["__caller_mutation__"] = True
                elif isinstance(value, list):
                    for child in list(value):
                        mutate(child)
                    value.append("caller-mutation")

            mutate(first)
            second = self.analyze(events, intrinsics, poses, second_path)
            self.assertEqual(second_path.read_bytes(), pristine_bytes)
            self.assertNotIn("__caller_mutation__", second)
            self.assertEqual(
                second["logical_bit_accounting"]["format"]["raw_sensor_payload"]
                ["x_bits"],
                16,
            )
            self.assertEqual(
                second["logical_bit_accounting"]["sensor_fixed"]
                ["raw_sensor_payload_width_bits"],
                sum(
                    second["logical_bit_accounting"]["format"]
                    ["raw_sensor_payload"].values()
                ),
            )
            self.assertIsNot(
                first["logical_bit_accounting"], second["logical_bit_accounting"]
            )

    def test_analysis_contract_binds_parameters_and_semantic_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            zero_path = Path(directory) / "zero.json"
            wide_path = Path(directory) / "wide.json"
            zero = self.analyze(events, intrinsics, poses, zero_path)
            wide = self.analyze(
                events, intrinsics, poses, wide_path, max_pose_age_ns=999999
            )
            self.assertNotEqual(zero_path.read_bytes(), wide_path.read_bytes())
            zero_contract = zero["analysis_contract"]
            wide_contract = wide["analysis_contract"]
            self.assertEqual(zero_contract["parameters"]["max_pose_age_ns"], 0)
            self.assertEqual(wide_contract["parameters"]["max_pose_age_ns"], 999999)
            self.assertEqual(zero_contract["implementation_id"], MODEL_IMPLEMENTATION_ID)
            self.assertEqual(
                zero_contract["known_motion_blob_api_id"],
                "redred.known_motion.input-blob/v1",
            )
            self.assertEqual(
                zero_contract["result_contract_revision"], RESULT_CONTRACT_REVISION
            )
            self.assertEqual(
                zero_contract["logical_bit_format_id"],
                LOGICAL_BIT_FORMAT["format_id"],
            )
            self.assertEqual(zero_contract["parameters"]["tile_width"], 8)
            self.assertEqual(zero_contract["parameters"]["tile_height"], 8)
            self.assertEqual(zero_contract["parameters"]["time_bin_ns"], 1000)
            self.assertEqual(zero["tiling"]["tile_width"], 8)
            self.assertEqual(zero["tiling"]["logical_time_bin_ns"], 1000)

    def test_parent_redirect_after_dirfd_open_fails_without_touching_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_dir = base / "inputs"
            input_dir.mkdir()
            events, intrinsics, poses = self.copy_inputs(str(input_dir))
            parent = base / "outparent"
            parent.mkdir()
            moved = base / "outparent-pinned"
            output = parent / events.name
            before = {path: path.read_bytes() for path in (events, intrinsics, poses)}
            ops = ParentRedirectOps(parent, moved, input_dir)
            try:
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    with self.assertRaisesRegex(
                        InterfaceError, "no longer names the pinned directory"
                    ):
                        self.analyze(events, intrinsics, poses, output)
            finally:
                if parent.is_symlink():
                    parent.unlink()
                if moved.exists():
                    os.rename(moved, parent)
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertFalse((input_dir / "result.json").exists())
            self.assertFalse(any(path.name.endswith(".tmp") for path in parent.iterdir()))

    def test_target_hardlink_and_symlink_injection_are_rejected(self) -> None:
        for kind in ("hardlink", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                inputs = base / "inputs"
                output_dir = base / "output"
                inputs.mkdir()
                output_dir.mkdir()
                events, intrinsics, poses = self.copy_inputs(str(inputs))
                output = output_dir / "result.json"
                source = events if kind == "hardlink" else base / "victim.json"
                if kind == "symlink":
                    source.write_bytes(b"victim-bytes")
                source_before = source.read_bytes()
                ops = TargetInjectionOps(output, source, kind)
                pattern = "aliases an immutable input inode|symlinks are forbidden"
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    with self.assertRaisesRegex(InterfaceError, pattern):
                        self.analyze(events, intrinsics, poses, output)
                self.assertEqual(source.read_bytes(), source_before)
                self.assertTrue(output.exists() or output.is_symlink())
                self.assertFalse(
                    any(path.name.endswith(".tmp") for path in output_dir.iterdir())
                )

    def test_publication_failures_preserve_existing_output_and_remove_temp(self) -> None:
        for failure in ("write", "rename"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                events, intrinsics, poses = self.copy_inputs(directory)
                output = Path(directory) / "result.json"
                old_bytes = b"old-complete-output\n"
                output.write_bytes(old_bytes)
                ops = PublicationFailureOps(failure)
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    with self.assertRaisesRegex(
                        InterfaceError, "hardened output publication failed"
                    ):
                        self.analyze(events, intrinsics, poses, output)
                self.assertEqual(output.read_bytes(), old_bytes)
                self.assertFalse(
                    any(path.name.endswith(".tmp") for path in Path(directory).iterdir())
                )

    def test_missing_dirfd_features_fail_closed_without_weak_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            output = Path(directory) / "result.json"
            old_bytes = b"old-output-remains\n"
            output.write_bytes(old_bytes)
            with mock.patch.object(mc_model, "_HARDENED_DIRFD_SUPPORTED", False):
                with self.assertRaisesRegex(
                    InterfaceError, "hardened dirfd publication unsupported"
                ):
                    self.analyze(events, intrinsics, poses, output)
            self.assertEqual(output.read_bytes(), old_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
