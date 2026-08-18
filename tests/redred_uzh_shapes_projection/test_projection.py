from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchmarks.clean_slate_aer.prepare_sv_trace import prepare_trace
from benchmarks.redred_uzh_shapes_projection import project as projector


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SPEC = ROOT / "benchmarks" / "redred_uzh_shapes_projection" / "projection_spec.json"
EXTERNAL = {
    "archive": Path("/tmp/uzh-shapes_rotation.zip"),
    "events": Path("/tmp/uzh-shapes_rotation/events.txt"),
    "license": Path("/tmp/CC-BY-NC-SA-3.0.txt"),
}
LICENSE = (
    b"Creative Commons Legal Code\n\n"
    b"Attribution-NonCommercial-ShareAlike 3.0 Unported\n"
    b"fixture legal text\n"
)
EVENTS = (
    b"41.320999000 10 10 0\n"
    b"41.321000000 0 0 0\n"
    b"41.321000000 0 0 0\n"
    b"41.321006500 239 179 1\n"
    b"41.321999999 60 45 1\n"
    b"41.322000000 20 20 0\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="ascii"))


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


class Fixture:
    def __init__(self, root: Path, events: bytes = EVENTS, license_bytes: bytes = LICENSE) -> None:
        self.root = root
        self.events = root / "events.txt"
        self.events.write_bytes(events)
        self.license = root / "CC-BY-NC-SA-3.0.txt"
        self.license.write_bytes(license_bytes)
        self.archive = root / "uzh-shapes_rotation.zip"
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("events.txt", events)
        with zipfile.ZipFile(self.archive) as archive:
            info = archive.getinfo("events.txt")
        spec = json.loads(PRODUCTION_SPEC.read_text(encoding="ascii"))
        spec["artifacts"]["archive"].update({
            "size_bytes": self.archive.stat().st_size,
            "sha256": sha(self.archive.read_bytes()),
            "events_member_size_bytes": len(events),
            "events_member_crc32": f"{info.CRC:08x}",
        })
        spec["artifacts"]["events"].update({
            "size_bytes": len(events),
            "line_count": len(events.splitlines()),
            "sha256": sha(events),
        })
        spec["artifacts"]["license"].update({
            "size_bytes": len(license_bytes),
            "sha256": sha(license_bytes),
        })
        selected = [line for line in events.splitlines() if b"41.321" <= line.split()[0] < b"41.322"]
        indices = [index for index, line in enumerate(events.splitlines()) if b"41.321" <= line.split()[0] < b"41.322"]
        spec["window"].update({
            "expected_event_count": len(selected),
            "expected_first_dataset_event_index": indices[0] if indices else 0,
            "expected_last_dataset_event_index": indices[-1] if indices else 0,
        })
        self.spec_value = spec
        self.spec = root / "spec.json"
        self.write_spec()

    def write_spec(self) -> None:
        self.spec.write_text(json.dumps(self.spec_value, sort_keys=True) + "\n", encoding="ascii")

    def rebuild(self, events: bytes) -> None:
        self.events.write_bytes(events)
        self.archive.unlink()
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("events.txt", events)
        with zipfile.ZipFile(self.archive) as archive:
            info = archive.getinfo("events.txt")
        self.spec_value["artifacts"]["archive"].update({
            "size_bytes": self.archive.stat().st_size,
            "sha256": sha(self.archive.read_bytes()),
            "events_member_size_bytes": len(events),
            "events_member_crc32": f"{info.CRC:08x}",
        })
        self.spec_value["artifacts"]["events"].update({
            "size_bytes": len(events), "line_count": len(events.splitlines()), "sha256": sha(events),
        })
        self.write_spec()

    def project(self, result: Path, **kwargs: object) -> dict[str, object]:
        return projector.project(self.archive, self.events, self.license, self.spec, result, **kwargs)


class ProjectionTest(unittest.TestCase):
    def test_lossless_projection_exact_cycles_collisions_and_preparer_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            result = root / "result"
            receipt = fixture.project(result)
            projected = read_jsonl(result / projector.PROJECTION_NAME)
            self.assertEqual(receipt["status"], projector.STATUS)
            self.assertEqual(receipt["release_status"], projector.HOLD)
            self.assertFalse(receipt["canonical_redred_traffic"])
            self.assertFalse(receipt["official_redred_traffic"])
            self.assertEqual(len(projected), 4)
            # Dataset indices remain distinct even when all physical fields duplicate.
            self.assertEqual(
                {key: value for key, value in projected[0].items() if key != "dataset_event_index"},
                {key: value for key, value in projected[1].items() if key != "dataset_event_index"},
            )
            self.assertEqual([row["dataset_event_index"] for row in projected], [1, 2, 3, 4])
            self.assertEqual([row["logical_source"] for row in projected], [0, 0, 15, 5])
            self.assertEqual((projected[2]["bx"], projected[2]["by"]), (3, 3))
            self.assertEqual(receipt["conservation"]["events_dropped"], 0)
            self.assertEqual(receipt["conservation"]["exact_duplicate_input_extras"], 1)
            traces = {scenario: read_jsonl(result / f"trace_{scenario}.jsonl") for scenario in ("1x", "64x", "256x")}
            self.assertEqual([row["occurrence_cycle"] for row in traces["1x"]], [0, 0, 1000, 153846])
            self.assertEqual([row["occurrence_cycle"] for row in traces["64x"]], [0, 0, 15, 2403])
            self.assertEqual([row["occurrence_cycle"] for row in traces["256x"]], [0, 0, 3, 600])
            self.assertEqual([row["tb_only_event_id"] for row in traces["1x"]], list(range(4)))
            self.assertEqual([row["logical_source"] for row in traces["1x"]], [0, 0, 15, 5])
            self.assertEqual([row["polarity"] for row in traces["1x"]], [-1, -1, 1, 1])
            inspected = projector.inspect(result)
            self.assertEqual(inspected["status"], projector.QUALIFY_STATUS)
            self.assertFalse(inspected["actual_replay_bound"])

            trace = result / "trace_256x.jsonl"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "trace_file": trace.name,
                "trace_sha256": sha(trace.read_bytes()),
                "event_count": 4,
                "event_identity_mode": "address_only",
                "report_group": "uzh_fixture_256x",
                "run": {
                    "name": "uzh_fixture_256x", "geometry": {"width": 4, "height": 4},
                    "stim_cycles": 605, "load": 0.0, "seed": 0, "sink": {"mode": "always"},
                },
            }), encoding="ascii")
            prepared = root / "prepared.txt"
            summary = prepare_trace(trace, manifest, prepared, 4)
            self.assertEqual(summary["event_count"], 4)
            self.assertTrue(prepared.is_file())

    def test_outputs_are_deterministic_and_never_p6_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            first, second = root / "first", root / "second"
            fixture.project(first)
            fixture.project(second)
            for name in {path.name for path in first.iterdir()}:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
            receipt = read_json(first / projector.RECEIPT_NAME)
            self.assertEqual(receipt["lineage"]["interface"], "SINGLE_EDGE_REPLAY_PREPARER_INPUT_ONLY")
            self.assertFalse(receipt["lineage"]["p6_evidence_used"])
            self.assertIsNone(receipt["lineage"]["actual_replay_receipt"])
            self.assertEqual(receipt["lineage"]["replay_status"], "UNREPLAYED")

    def test_hash_size_line_count_archive_member_and_license_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutations = (
                ("archive size/SHA", lambda f: f.spec_value["artifacts"]["archive"].update({"sha256": "0" * 64})),
                ("archive events member|events size/SHA/line-count", lambda f: f.spec_value["artifacts"]["events"].update({"size_bytes": 1})),
                ("events size/SHA/line-count", lambda f: f.spec_value["artifacts"]["events"].update({"line_count": 1})),
                ("member metadata", lambda f: f.spec_value["artifacts"]["archive"].update({"events_member_crc32": "00000000"})),
                ("license size/SHA", lambda f: f.spec_value["artifacts"]["license"].update({"sha256": "0" * 64})),
            )
            for index, (pattern, mutate) in enumerate(mutations):
                case = root / str(index)
                case.mkdir()
                fixture = Fixture(case)
                mutate(fixture)
                fixture.write_spec()
                with self.assertRaisesRegex(projector.ProjectionFailure, pattern):
                    fixture.project(case / "result")
                self.assertFalse((case / "result").exists())

    def test_exact_decimal_format_bounds_order_and_resource_limits(self) -> None:
        cases = (
            (b"41.32100000 0 0 0\n", "9-digit"),
            (b"041.321000000 0 0 0\n", "9-digit"),
            (b"41.321000000 240 0 0\n", "outside"),
            (b"41.321000000 0 180 0\n", "outside"),
            (b"41.321000000 0 0 -1\n", "noncanonical"),
            (b"41.321000000 0 0 0\r\n", "single-space"),
            (b"41.321000000 0 0 0", "lacks LF"),
            (b"41.321000001 0 0 0\n41.321000000 0 0 0\n", "decrease"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (events, message) in enumerate(cases):
                case = root / str(index)
                case.mkdir()
                fixture = Fixture(case)
                fixture.rebuild(events)
                fixture.spec_value["window"].update({
                    "expected_event_count": 1, "expected_first_dataset_event_index": 0,
                    "expected_last_dataset_event_index": 0,
                })
                fixture.write_spec()
                with self.assertRaisesRegex(projector.ProjectionFailure, message):
                    fixture.project(case / "result")

            case = root / "line-resource"
            case.mkdir()
            long_events = b"41.321000000 " + b"0" * 100 + b" 0 0\n"
            fixture = Fixture(case)
            fixture.rebuild(long_events)
            fixture.spec_value["window"].update({
                "expected_event_count": 1, "expected_first_dataset_event_index": 0,
                "expected_last_dataset_event_index": 0,
            })
            fixture.write_spec()
            with self.assertRaisesRegex(projector.ProjectionFailure, "resource limit"):
                fixture.project(case / "result")

            case = root / "selected-resource"
            case.mkdir()
            fixture = Fixture(case)
            fixture.spec_value["resource_limits"]["max_selected_events"] = 3
            fixture.write_spec()
            with self.assertRaisesRegex(projector.ProjectionFailure, "selected event count"):
                fixture.project(case / "result")

            case = root / "license-resource"
            case.mkdir()
            fixture = Fixture(case)
            fixture.spec_value["resource_limits"]["max_license_bytes"] = 10
            fixture.write_spec()
            with self.assertRaisesRegex(projector.ProjectionFailure, "artifact exceeds resource"):
                fixture.project(case / "result")

            case = root / "zip-resource"
            case.mkdir()
            fixture = Fixture(case)
            fixture.archive.unlink()
            with zipfile.ZipFile(fixture.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("events.txt", EVENTS)
                archive.writestr("extra.txt", b"extra")
            fixture.spec_value["artifacts"]["archive"].update({
                "size_bytes": fixture.archive.stat().st_size,
                "sha256": sha(fixture.archive.read_bytes()),
            })
            fixture.spec_value["resource_limits"]["max_zip_entries"] = 1
            fixture.write_spec()
            with self.assertRaisesRegex(projector.ProjectionFailure, "zip entry count"):
                fixture.project(case / "result")

    def test_symlink_overwrite_input_mutation_and_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            for name, target in (("events-link", fixture.events), ("archive-link", fixture.archive),
                                 ("license-link", fixture.license), ("spec-link", fixture.spec)):
                link = root / name
                link.symlink_to(target)
                args = [fixture.archive, fixture.events, fixture.license, fixture.spec]
                args[("archive-link", "events-link", "license-link", "spec-link").index(name)] = link
                with self.assertRaisesRegex(projector.ProjectionFailure, "symlink"):
                    projector.project(*args, root / f"result-{name}")

            existing = root / "existing"
            existing.mkdir()
            marker = existing / "keep"
            marker.write_text("keep", encoding="ascii")
            with self.assertRaisesRegex(projector.ProjectionFailure, "already exists"):
                fixture.project(existing)
            self.assertEqual(marker.read_text(encoding="ascii"), "keep")

            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(projector.ProjectionFailure, "symlink"):
                fixture.project(linked_parent / "result")

            with self.assertRaisesRegex(projector.ProjectionFailure, "changed during read"):
                with projector._stable_open(fixture.events) as (stream, _):
                    stream.read(1)
                    fixture.events.write_bytes(EVENTS + b"41.400000000 0 0 0\n")
            fixture.events.write_bytes(EVENTS)

            orphan = root / "orphan"
            writes = 0

            def fail_write(path: Path, data: bytes) -> None:
                nonlocal writes
                writes += 1
                if writes == 3:
                    raise projector.ProjectionFailure("injected write failure")
                projector._write_exclusive(path, data)

            with self.assertRaisesRegex(projector.ProjectionFailure, "injected write failure"):
                fixture.project(orphan, _write=fail_write)
            self.assertTrue(orphan.is_dir())
            self.assertFalse((orphan / projector.COMPLETION_NAME).exists())
            with self.assertRaisesRegex(projector.ProjectionFailure, "artifact set mismatch"):
                projector.inspect(orphan)

    def test_inspector_rejects_rehashing_tamper_and_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            for field, value in (
                ("status", "REPLAYED"),
                ("release_status", "GO"),
                ("canonical_redred_traffic", True),
                ("official_redred_traffic", True),
            ):
                result = root / field
                fixture.project(result)
                receipt = read_json(result / projector.RECEIPT_NAME)
                receipt[field] = value
                receipt_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("ascii")
                (result / projector.RECEIPT_NAME).chmod(0o644)
                (result / projector.RECEIPT_NAME).write_bytes(receipt_bytes)
                completion = read_json(result / projector.COMPLETION_NAME)
                completion["receipt_sha256"] = sha(receipt_bytes)
                (result / projector.COMPLETION_NAME).chmod(0o644)
                (result / projector.COMPLETION_NAME).write_text(
                    json.dumps(completion, sort_keys=True, indent=2) + "\n", encoding="ascii"
                )
                with self.assertRaisesRegex(projector.ProjectionFailure, "classification|unreplayed"):
                    projector.inspect(result)

    def test_spec_pins_production_hashes_exactly(self) -> None:
        spec = projector.validate_spec(json.loads(PRODUCTION_SPEC.read_text(encoding="ascii")))
        self.assertEqual(spec["artifacts"]["archive"]["sha256"], "56aade6bf53dcf73e8fe40905ccac8385cd7606bc9a85103bf2c9f9045117551")
        self.assertEqual(spec["artifacts"]["events"]["sha256"], "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda")
        self.assertEqual(spec["artifacts"]["license"]["sha256"], "8812f83442fd0eca14eb0208988e190fdcbfebec58fa5459d3218edfdfdc5a32")
        self.assertEqual(spec["window"]["expected_event_count"], 1100)
        self.assertEqual([row["time_compression"] for row in spec["scenarios"]], [1, 64, 256])

    @unittest.skipUnless(all(path.is_file() for path in EXTERNAL.values()), "pinned external UZH bytes absent")
    def test_full_pinned_external_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            receipt = projector.project(
                EXTERNAL["archive"], EXTERNAL["events"], EXTERNAL["license"], PRODUCTION_SPEC, result
            )
            self.assertEqual(receipt["conservation"], {
                "input_window_events": 1100,
                "projected_events": 1100,
                "events_dropped": 0,
                "identity_rule": "tb_only_event_id order equals projected_events line order equals source dataset_event_index order",
                "timestamp_tie_collision_extras": 458,
                "exact_duplicate_input_extras": 0,
            })
            scenarios = {row["id"]: row for row in receipt["scenarios"]}
            self.assertEqual((scenarios["1x"]["last_cycle"], scenarios["1x"]["same_source_cycle_collision_extras"]), (153692, 81))
            self.assertEqual((scenarios["64x"]["last_cycle"], scenarios["64x"]["same_source_cycle_collision_extras"]), (2401, 81))
            self.assertEqual((scenarios["256x"]["last_cycle"], scenarios["256x"]["same_source_cycle_collision_extras"]), (600, 133))
            self.assertEqual(sha((result / projector.PROJECTION_NAME).read_bytes()), "b38d5946d2817905ef5471db7cf0df3d8cf92df4bb21678aed859e64a6e61d95")
            self.assertEqual(sha((result / "trace_1x.jsonl").read_bytes()), "c02aa20d8dc6cb2b85a500648e91f320d05f1f7e3b2d6e11d7189550b639ec94")
            self.assertEqual(sha((result / "trace_64x.jsonl").read_bytes()), "b005def64b130bc0e83b73cd9e6e4ab6d0a8f6e83f12b5b008b03642e17dcebb")
            self.assertEqual(sha((result / "trace_256x.jsonl").read_bytes()), "8428936e62b494747e9b75445e2a9b1f40677b92ede9f6569923729237c6f14a")
            self.assertEqual(projector.inspect(result)["status"], projector.QUALIFY_STATUS)
