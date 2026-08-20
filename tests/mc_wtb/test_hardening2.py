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
    PublicationCleanupError,
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


class LateParentRedirectOps(DelegatingFileOps):
    def __init__(self, parent: Path, moved: Path, redirect: Path) -> None:
        super().__init__()
        self.parent = parent
        self.moved = moved
        self.redirect = redirect
        self.injected = False

    def renameat(self, source, target, *, src_dir_fd, dst_dir_fd):
        if not self.injected:
            os.rename(self.parent, self.moved)
            os.symlink(self.redirect, self.parent, target_is_directory=True)
            self.injected = True
        return self.real.renameat(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )


class LateTargetAliasOps(DelegatingFileOps):
    def __init__(self, output: Path, victim: Path, kind: str) -> None:
        super().__init__()
        self.output = output
        self.victim = victim
        self.kind = kind
        self.injected = False

    def renameat(self, source, target, *, src_dir_fd, dst_dir_fd):
        if not self.injected:
            if self.kind == "hardlink":
                os.link(self.victim, self.output)
            else:
                os.symlink(self.victim, self.output)
            self.injected = True
        return self.real.renameat(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )


class PostRenameTargetReplacementOps(DelegatingFileOps):
    def __init__(self, output: Path, displaced: Path, victim: Path) -> None:
        super().__init__()
        self.output = output
        self.displaced = displaced
        self.victim = victim

    def renameat(self, source, target, *, src_dir_fd, dst_dir_fd):
        self.real.renameat(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        os.rename(self.output, self.displaced)
        os.link(self.victim, self.output)


class CleanupFailureOps(DelegatingFileOps):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure
        self.parent_fd = None
        self.temporary_fd = None
        self.open_fds: set[int] = set()
        self.close_attempts: list[int] = []
        self.write_failed = False

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        descriptor = self.real.open(path, flags, mode, dir_fd=dir_fd)
        self.open_fds.add(descriptor)
        if flags & os.O_DIRECTORY:
            self.parent_fd = descriptor
        elif dir_fd is not None:
            self.temporary_fd = descriptor
        return descriptor

    def write(self, descriptor, data):
        if not self.write_failed:
            self.write_failed = True
            raise OSError(errno.ENOSPC, "injected primary write failure")
        return self.real.write(descriptor, data)

    def close(self, descriptor):
        self.close_attempts.append(descriptor)
        if self.failure in ("temporary_close", "all") and descriptor == self.temporary_fd:
            raise OSError(errno.EIO, "injected temporary cleanup close failure")
        if self.failure in ("parent_close", "all") and descriptor == self.parent_fd:
            raise OSError(errno.EIO, "injected parent cleanup close failure")
        self.real.close(descriptor)
        self.open_fds.discard(descriptor)

    def unlinkat(self, path, *, dir_fd):
        if self.failure in ("unlink", "all"):
            raise OSError(errno.EIO, "injected temporary unlink failure")
        self.real.unlinkat(path, dir_fd=dir_fd)

    def force_close_residual_fds(self) -> None:
        for descriptor in tuple(self.open_fds):
            self.real.close(descriptor)
            self.open_fds.discard(descriptor)


class ParentCloseAfterSuccessOps(CleanupFailureOps):
    def write(self, descriptor, data):
        return self.real.write(descriptor, data)


class ShortWriteEintrOps(DelegatingFileOps):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def write(self, descriptor, data):
        self.calls += 1
        if self.calls == 1:
            raise InterruptedError(errno.EINTR, "injected first-write interrupt")
        return self.real.write(descriptor, data[: min(7, len(data))])


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
            self.assertEqual(
                provenance["stability_scope"], provenance["snapshot_scope"]
            )

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

    def test_late_parent_redirect_is_reported_after_pinned_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_dir = base / "inputs"
            input_dir.mkdir()
            events, intrinsics, poses = self.copy_inputs(str(input_dir))
            parent = base / "outparent"
            parent.mkdir()
            moved = base / "outparent-pinned"
            output = parent / "result.json"
            before = {path: path.read_bytes() for path in (events, intrinsics, poses)}
            ops = LateParentRedirectOps(parent, moved, input_dir)
            try:
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    with self.assertRaisesRegex(
                        InterfaceError,
                        "post-rename publication verification failed.*not rolled back",
                    ):
                        self.analyze(events, intrinsics, poses, output)
                self.assertFalse((input_dir / output.name).exists())
                pinned_result = moved / output.name
                self.assertTrue(pinned_result.is_file())
                self.assertEqual(
                    json.loads(pinned_result.read_text(encoding="utf-8"))["schema"],
                    mc_model.RESULT_SCHEMA,
                )
                self.assertFalse(
                    any(path.name.endswith(".tmp") for path in moved.iterdir())
                )
            finally:
                if parent.is_symlink():
                    parent.unlink()
                if moved.exists():
                    os.rename(moved, parent)
            self.assertEqual(before, {path: path.read_bytes() for path in before})

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

    def test_target_alias_created_after_lstat_is_replaced_without_victim_write(self) -> None:
        for kind in ("hardlink", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                inputs = base / "inputs"
                output_dir = base / "output"
                inputs.mkdir()
                output_dir.mkdir()
                events, intrinsics, poses = self.copy_inputs(str(inputs))
                output = output_dir / "result.json"
                victim = base / "victim.json"
                victim_bytes = b"hostile-late-alias-victim\n"
                victim.write_bytes(victim_bytes)
                ops = LateTargetAliasOps(output, victim, kind)
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    result = self.analyze(events, intrinsics, poses, output)
                self.assertEqual(victim.read_bytes(), victim_bytes)
                self.assertFalse(output.is_symlink())
                self.assertNotEqual(output.stat().st_ino, victim.stat().st_ino)
                self.assertEqual(json.loads(output.read_text())["schema"], result["schema"])

    def test_target_replaced_after_rename_fails_post_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            inputs = base / "inputs"
            output_dir = base / "output"
            inputs.mkdir()
            output_dir.mkdir()
            events, intrinsics, poses = self.copy_inputs(str(inputs))
            output = output_dir / "result.json"
            displaced = output_dir / "displaced-result.json"
            victim = base / "victim.json"
            victim_bytes = b"post-rename-victim\n"
            victim.write_bytes(victim_bytes)
            ops = PostRenameTargetReplacementOps(output, displaced, victim)
            with mock.patch.object(mc_model, "_FILE_OPS", ops):
                with self.assertRaisesRegex(
                    InterfaceError,
                    "published target no longer names the written temporary inode",
                ):
                    self.analyze(events, intrinsics, poses, output)
            self.assertEqual(victim.read_bytes(), victim_bytes)
            self.assertEqual(output.read_bytes(), victim_bytes)
            self.assertEqual(output.stat().st_ino, victim.stat().st_ino)
            self.assertEqual(
                json.loads(displaced.read_text(encoding="utf-8"))["schema"],
                mc_model.RESULT_SCHEMA,
            )
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

    def test_cleanup_failures_are_composite_and_all_cleanup_is_attempted(self) -> None:
        for failure in ("unlink", "temporary_close", "all"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                events, intrinsics, poses = self.copy_inputs(directory)
                output = Path(directory) / "result.json"
                old_bytes = b"old-output-survives-cleanup-fault\n"
                output.write_bytes(old_bytes)
                ops = CleanupFailureOps(failure)
                try:
                    with mock.patch.object(mc_model, "_FILE_OPS", ops):
                        with self.assertRaises(PublicationCleanupError) as raised:
                            self.analyze(events, intrinsics, poses, output)
                    error = raised.exception
                    self.assertIsInstance(error.primary_error, InterfaceError)
                    self.assertIn("injected primary write failure", str(error.primary_error))
                    stages = [stage for stage, _ in error.cleanup_failures]
                    expected_stages = {
                        "unlink": ["temporary_unlink"],
                        "temporary_close": ["temporary_fd_close"],
                        "all": [
                            "temporary_fd_close",
                            "temporary_unlink",
                            "parent_fd_close",
                        ],
                    }
                    self.assertEqual(stages, expected_stages[failure])
                    self.assertEqual(output.read_bytes(), old_bytes)
                    self.assertIn(ops.parent_fd, ops.close_attempts)
                    temps = [
                        path
                        for path in Path(directory).iterdir()
                        if path.name.endswith(".tmp")
                    ]
                    if failure == "unlink":
                        self.assertEqual(len(temps), 1)
                        self.assertEqual(error.temporary_name, temps[0].name)
                        self.assertEqual(ops.open_fds, set())
                    elif failure == "temporary_close":
                        self.assertEqual(temps, [])
                        self.assertIsNone(error.temporary_name)
                        self.assertEqual(ops.open_fds, {ops.temporary_fd})
                        self.assertTrue(error.temporary_fd_close_uncertain)
                    else:
                        self.assertEqual(len(temps), 1)
                        self.assertEqual(error.temporary_name, temps[0].name)
                        self.assertEqual(
                            ops.open_fds, {ops.temporary_fd, ops.parent_fd}
                        )
                        self.assertTrue(error.temporary_fd_close_uncertain)
                        self.assertTrue(error.parent_fd_close_uncertain)
                finally:
                    ops.force_close_residual_fds()

    def test_parent_close_failure_reports_successful_publication_and_fd_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            output = Path(directory) / "result.json"
            ops = ParentCloseAfterSuccessOps("parent_close")
            try:
                with mock.patch.object(mc_model, "_FILE_OPS", ops):
                    with self.assertRaises(PublicationCleanupError) as raised:
                        self.analyze(events, intrinsics, poses, output)
                error = raised.exception
                self.assertIsNone(error.primary_error)
                self.assertEqual(
                    [stage for stage, _ in error.cleanup_failures],
                    ["parent_fd_close"],
                )
                self.assertTrue(error.parent_fd_close_uncertain)
                self.assertIsNone(error.temporary_name)
                self.assertEqual(ops.open_fds, {ops.parent_fd})
                self.assertEqual(
                    json.loads(output.read_text(encoding="utf-8"))["schema"],
                    mc_model.RESULT_SCHEMA,
                )
                self.assertFalse(
                    any(path.name.endswith(".tmp") for path in Path(directory).iterdir())
                )
            finally:
                ops.force_close_residual_fds()

    def test_first_write_eintr_and_repeated_short_writes_publish_exact_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, intrinsics, poses = self.copy_inputs(directory)
            output = Path(directory) / "result.json"
            ops = ShortWriteEintrOps()
            with mock.patch.object(mc_model, "_FILE_OPS", ops):
                result = self.analyze(events, intrinsics, poses, output)
            expected = (
                json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
            ).encode("utf-8")
            self.assertGreater(ops.calls, 2)
            self.assertEqual(output.read_bytes(), expected)
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

    def test_symlink_final_output_parent_is_intentionally_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            events, intrinsics, poses = self.copy_inputs(directory)
            real_parent = base / "real-output"
            parent_link = base / "output-link"
            real_parent.mkdir()
            os.symlink(real_parent, parent_link, target_is_directory=True)
            with self.assertRaisesRegex(
                InterfaceError, "hardened output publication failed"
            ):
                self.analyze(
                    events, intrinsics, poses, parent_link / "result.json"
                )
            self.assertFalse((real_parent / "result.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
