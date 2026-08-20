from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any, Iterable

from benchmarks.redred_uzh_shapes_pose_join.import_join import import_join, inspect


ROOT = Path(os.environ.get("REDRED_POSE_JOIN_ROOT", Path(__file__).resolve().parents[2]))
PRODUCTION_SPEC = ROOT / "benchmarks" / "redred_uzh_shapes_pose_join" / "join_spec.json"
EXTERNAL_ARCHIVE = Path("/tmp/uzh-shapes_rotation.zip")
EXTERNAL_LICENSE = Path("/tmp/CC-BY-NC-SA-3.0.txt")
EXTERNAL_ARCHIVE_SHA256 = "56aade6bf53dcf73e8fe40905ccac8385cd7606bc9a85103bf2c9f9045117551"
EXTERNAL_LICENSE_SHA256 = "8812f83442fd0eca14eb0208988e190fdcbfebec58fa5459d3218edfdfdc5a32"

START_NS = 41_321_000_000
END_NS = 41_322_000_000
LICENSE = (
    b"Creative Commons Legal Code\n\n"
    b"Attribution-NonCommercial-ShareAlike 3.0 Unported\n"
    b"deterministic pose-join fixture\n"
)
CALIB = (
    b"199.092366542 198.82882047 132.192071378 110.712660011 "
    b"-0.368436311798 0.150947243557 -0.000296130534385 "
    b"-0.000759431726241 0.0\n"
)
EVENTS = (
    b"41.320999999 1 1 0\n"              # start - 1 ns: excluded
    b"41.321000000 10 20 1\n"            # start: included
    b"41.321000000 10 20 1\n"            # exact duplicate occurrence
    b"41.321000000 11 20 1\n"            # timestamp collision
    b"41.321500000 12 21 0\n"
    b"41.321999999 239 179 1\n"          # end - 1 ns: included
    b"41.322000000 2 2 0\n"              # end: excluded
)
POSES = (
    b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
    b"41.322500000 0.1 0.2 0.3 0.0 0.0 0.7071067811865475 0.7071067811865476\n"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="ascii"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def selected_event_lines(events: bytes) -> list[tuple[int, bytes]]:
    selected: list[tuple[int, bytes]] = []
    for index, line in enumerate(events.splitlines(keepends=True)):
        timestamp = line.split(b" ", 1)[0]
        whole, fraction = timestamp.split(b".")
        timestamp_ns = int(whole) * 1_000_000_000 + int(fraction)
        if START_NS <= timestamp_ns < END_NS:
            selected.append((index, line))
    return selected


def zip_info(name: str, *, symlink: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 2, 3, 4, 6))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o120777 if symlink else 0o100644) << 16)
    return info


def build_zip(path: Path, members: Iterable[tuple[str, bytes, bool]]) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload, symlink in members:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(zip_info(name, symlink=symlink), payload)
    return path.read_bytes()


def set_existing(mapping: dict[str, Any], names: tuple[str, ...], value: Any) -> str:
    for name in names:
        if name in mapping:
            mapping[name] = value
            return name
    raise AssertionError(f"production spec lacks every expected key {names!r}; present={sorted(mapping)}")


def member_spec(spec: dict[str, Any], role: str) -> dict[str, Any]:
    members = spec["required_members"]
    if isinstance(members, dict):
        for key in (role, f"{role}.txt", "poses" if role == "groundtruth" else role):
            if key in members and isinstance(members[key], dict):
                return members[key]
        for value in members.values():
            if isinstance(value, dict) and value.get("name", value.get("member")) == f"{role}.txt":
                return value
    elif isinstance(members, list):
        for value in members:
            if isinstance(value, dict) and value.get("name", value.get("member")) == f"{role}.txt":
                return value
    raise AssertionError(f"cannot locate required member {role}.txt in production spec")


def update_member(row: dict[str, Any], info: zipfile.ZipInfo, payload: bytes) -> None:
    set_existing(row, ("name", "member", "member_name"), info.filename)
    set_existing(row, ("size_bytes", "uncompressed_size_bytes"), len(payload))
    set_existing(row, ("compressed_size_bytes",), info.compress_size)
    set_existing(row, ("crc32",), f"{info.CRC:08x}")
    set_existing(row, ("sha256",), sha256(payload))
    set_existing(row, ("line_count", "record_count"), len(payload.splitlines()))


class Fixture:
    def __init__(self, root: Path, *, events: bytes = EVENTS, poses: bytes = POSES,
                 calib: bytes = CALIB, license_bytes: bytes = LICENSE) -> None:
        self.root = root
        self.events = events
        self.poses = poses
        self.calib = calib
        self.license_bytes = license_bytes
        base = json.loads(PRODUCTION_SPEC.read_text(encoding="ascii"))
        self.spec_value: dict[str, Any] = copy.deepcopy(base)
        archive_basename = self.spec_value["source_archive"]["basename"]
        license_basename = self.spec_value["license"]["basename"]
        self.archive = root / archive_basename
        self.license = root / license_basename
        self.spec = root / "fixture_join_spec.json"
        self.license.write_bytes(license_bytes)
        self.rebuild()

    def _members(self) -> list[tuple[str, bytes, bool]]:
        return [
            ("events.txt", self.events, False),
            ("groundtruth.txt", self.poses, False),
            ("calib.txt", self.calib, False),
        ]

    def rebuild(self, *, members: list[tuple[str, bytes, bool]] | None = None,
                update_members: bool = True) -> None:
        archive_bytes = build_zip(self.archive, self._members() if members is None else members)
        archive_row = self.spec_value["source_archive"]
        set_existing(archive_row, ("size_bytes",), len(archive_bytes))
        set_existing(archive_row, ("sha256",), sha256(archive_bytes))
        set_existing(archive_row, ("expected_entry_count", "entry_count"),
                     len(self._members() if members is None else members))
        with zipfile.ZipFile(self.archive) as archive:
            if update_members:
                update_member(member_spec(self.spec_value, "events"), archive.getinfo("events.txt"), self.events)
                update_member(member_spec(self.spec_value, "groundtruth"), archive.getinfo("groundtruth.txt"), self.poses)
                update_member(member_spec(self.spec_value, "calib"), archive.getinfo("calib.txt"), self.calib)
        license_row = self.spec_value["license"]
        set_existing(license_row, ("size_bytes",), len(self.license_bytes))
        set_existing(license_row, ("sha256",), sha256(self.license_bytes))
        self.update_selection()
        self.write_spec()

    def update_archive_identity_only(self) -> None:
        payload = self.archive.read_bytes()
        row = self.spec_value["source_archive"]
        set_existing(row, ("size_bytes",), len(payload))
        set_existing(row, ("sha256",), sha256(payload))
        with zipfile.ZipFile(self.archive) as archive:
            set_existing(row, ("expected_entry_count", "entry_count"), len(archive.infolist()))
        self.write_spec()

    def update_selection(self) -> None:
        selected = selected_event_lines(self.events)
        selection = self.spec_value["selection"]
        set_existing(selection, ("start_ns_inclusive", "start_timestamp_ns_inclusive"), START_NS)
        set_existing(selection, ("end_ns_exclusive", "end_timestamp_ns_exclusive"), END_NS)
        set_existing(selection, ("expected_event_count", "expected_count"), len(selected))
        set_existing(selection, ("expected_first_dataset_event_index", "first_dataset_event_index"),
                     selected[0][0] if selected else 0)
        set_existing(selection, ("expected_last_dataset_event_index", "last_dataset_event_index"),
                     selected[-1][0] if selected else 0)
        raw = b"".join(line for _, line in selected)
        set_existing(selection, (
            "selected_raw_lines_sha256", "selected_raw_sha256", "expected_selected_raw_sha256",
        ), sha256(raw))
        first_timestamp = int(selected[0][1].split(b" ", 1)[0].replace(b".", b"")) if selected else 0
        last_timestamp = int(selected[-1][1].split(b" ", 1)[0].replace(b".", b"")) if selected else 0
        set_existing(selection, ("expected_first_timestamp_ns",), first_timestamp)
        set_existing(selection, ("expected_last_timestamp_ns",), last_timestamp)

    def write_spec(self) -> None:
        self.spec.write_bytes(canonical_json(self.spec_value))

    def replace_sources(self, *, events: bytes | None = None, poses: bytes | None = None,
                        calib: bytes | None = None) -> None:
        if events is not None:
            self.events = events
        if poses is not None:
            self.poses = poses
        if calib is not None:
            self.calib = calib
        self.rebuild()

    def run(self, result: Path) -> dict[str, Any]:
        return import_join(self.archive, self.license, self.spec, result)


class PoseJoinTest(unittest.TestCase):
    maxDiff = None

    def assert_import_rejected(self, fixture: Fixture, result: Path) -> BaseException:
        with self.assertRaises(Exception) as caught:
            fixture.run(result)
        self.assertNotIsInstance(caught.exception, (TypeError, AttributeError, AssertionError))
        self.assertFalse((result / "COMPLETE.json").exists(), "failed import exposed COMPLETE.json")
        if result.exists():
            with self.assertRaises(Exception):
                inspect(result)
        return caught.exception

    def assert_inspect_rejected(self, result: Path) -> None:
        with self.assertRaises(Exception) as caught:
            inspect(result)
        self.assertNotIsInstance(caught.exception, (TypeError, AttributeError, AssertionError))

    def test_half_open_zero_drop_order_collisions_and_causal_future_bracket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            result = root / "result"
            receipt = fixture.run(result)
            inspected = inspect(result)
            self.assertIsInstance(inspected, dict)
            self.assertEqual(receipt["status"], "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED")
            self.assertFalse(receipt["claim_scope"]["official_uzh_source"])
            self.assertFalse(inspected["official_uzh_source"])

            rows = read_jsonl(result / "events_pose_join.jsonl")
            records = [row for row in rows if "dataset_event_index" in row]
            self.assertEqual(len(records), 5)
            self.assertEqual([row["dataset_event_index"] for row in records], [1, 2, 3, 4, 5])
            self.assertEqual([row["join_sequence_index"] for row in records], list(range(5)))
            self.assertEqual([row["timestamp_ns"] for row in records],
                             [START_NS, START_NS, START_NS, 41_321_500_000, END_NS - 1])
            self.assertEqual([(row["x"], row["y"], row["polarity_01"]) for row in records],
                             [(10, 20, 1), (10, 20, 1), (11, 20, 1),
                              (12, 21, 0), (239, 179, 1)])
            self.assertEqual(records[0], records[1] | {"dataset_event_index": 1, "join_sequence_index": 0})

            for row in records:
                self.assertEqual(row["causal_pose"]["source_pose_index"], 0)
                self.assertEqual(row["bracket"]["left_source_pose_index"], 0)
                self.assertEqual(row["bracket"]["right_source_pose_index"], 1)
                self.assertLessEqual(row["bracket"]["left_timestamp_ns"], row["timestamp_ns"])
                self.assertLess(row["timestamp_ns"], row["bracket"]["right_timestamp_ns"])
                self.assertEqual(row["causal_pose"]["age_ns"],
                                 row["timestamp_ns"] - row["causal_pose"]["pose_timestamp_ns"])
                self.assertEqual(row["bracket"]["alpha_numerator_ns"], row["causal_pose"]["age_ns"])
                self.assertEqual(row["bracket"]["alpha_denominator_ns"], 2_500_000)

            conservation = receipt["conservation"]
            self.assertEqual(conservation["source_event_records"], 7)
            self.assertEqual(conservation["before_window_records"], 1)
            self.assertEqual(conservation["admitted_window_records"], 5)
            self.assertEqual(conservation["after_window_records"], 1)
            self.assertEqual(conservation["joined_event_records"], 5)
            self.assertEqual(conservation["join_rejected_or_dropped_records"], 0)
            self.assertEqual(
                conservation["source_event_records"],
                conservation["before_window_records"]
                + conservation["admitted_window_records"]
                + conservation["after_window_records"],
            )

    def test_exact_pose_timestamp_uses_exact_causal_sample_and_next_future(self) -> None:
        poses = (
                b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
                b"41.321000000 0.0 0.0 0.0 0.0 0.0 0.7071067811865475 0.7071067811865476\n"
                b"41.322500000 0.0 0.0 0.0 0.0 0.0 1.0 0.0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root, poses=poses)
            result = root / "result"
            fixture.run(result)
            records = [row for row in read_jsonl(result / "events_pose_join.jsonl")
                       if "dataset_event_index" in row]
            for row in records[:3]:
                self.assertEqual(row["causal_pose"]["source_pose_index"], 1)
                self.assertEqual(row["causal_pose"]["age_ns"], 0)
                self.assertEqual(row["bracket"]["left_source_pose_index"], 1)
                self.assertEqual(row["bracket"]["right_source_pose_index"], 2)
                self.assertEqual(row["bracket"]["alpha_numerator_ns"], 0)

    def test_missing_left_or_right_halo_and_stale_pose_fail_closed(self) -> None:
        cases = {
            "missing-left": b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n",
            "missing-right": b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n",
            "stale": (
                b"41.315000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
                b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.7071067811865475 0.7071067811865476\n"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, poses in cases.items():
                case = root / name
                case.mkdir()
                fixture = Fixture(case, poses=poses)
                self.assert_import_rejected(fixture, case / "result")

    def test_bad_archive_member_hash_calibration_and_pose_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            case = root / "bad-archive"
            case.mkdir()
            fixture = Fixture(case)
            fixture.archive.write_bytes(b"not a zip archive\n")
            row = fixture.spec_value["source_archive"]
            set_existing(row, ("size_bytes",), fixture.archive.stat().st_size)
            set_existing(row, ("sha256",), file_sha256(fixture.archive))
            fixture.write_spec()
            self.assert_import_rejected(fixture, case / "result")

            case = root / "archive-hash"
            case.mkdir()
            fixture = Fixture(case)
            fixture.spec_value["source_archive"]["sha256"] = "0" * 64
            fixture.write_spec()
            self.assert_import_rejected(fixture, case / "result")

            case = root / "missing-member"
            case.mkdir()
            fixture = Fixture(case)
            fixture.rebuild(members=[
                ("events.txt", fixture.events, False),
                ("groundtruth.txt", fixture.poses, False),
            ], update_members=False)
            self.assert_import_rejected(fixture, case / "result")

            case = root / "duplicate-member"
            case.mkdir()
            fixture = Fixture(case)
            fixture.rebuild(members=fixture._members() + [("groundtruth.txt", fixture.poses, False)],
                            update_members=False)
            self.assert_import_rejected(fixture, case / "result")

            case = root / "symlink-member"
            case.mkdir()
            fixture = Fixture(case)
            fixture.rebuild(members=[
                ("events.txt", fixture.events, False),
                ("groundtruth.txt", b"events.txt", True),
                ("calib.txt", fixture.calib, False),
            ], update_members=False)
            self.assert_import_rejected(fixture, case / "result")

            case = root / "member-hash"
            case.mkdir()
            fixture = Fixture(case)
            member_spec(fixture.spec_value, "events")["sha256"] = "f" * 64
            fixture.write_spec()
            self.assert_import_rejected(fixture, case / "result")

            case = root / "bad-calib"
            case.mkdir()
            fixture = Fixture(case, calib=b"1 1 1 1 0 0 0 0\n")
            self.assert_import_rejected(fixture, case / "result")

            case = root / "bad-pose"
            case.mkdir()
            fixture = Fixture(case, poses=(
                b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 0.0\n"
                b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
            ))
            self.assert_import_rejected(fixture, case / "result")

    def test_duplicate_or_nonmonotonic_pose_and_nonmonotonic_event_rejected(self) -> None:
        cases = {
            "pose-duplicate": (
                EVENTS,
                b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
                b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
                b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n",
            ),
            "pose-nonmonotonic": (
                EVENTS,
                b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
                b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n",
            ),
            "event-nonmonotonic": (
                b"41.321500000 1 1 0\n41.321000000 2 2 1\n",
                POSES,
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (events, poses) in cases.items():
                case = root / name
                case.mkdir()
                fixture = Fixture(case, events=events, poses=poses)
                self.assert_import_rejected(fixture, case / "result")

    def test_input_result_symlinks_and_preexisting_result_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)

            archive_link = root / "archive-link.zip"
            archive_link.symlink_to(fixture.archive)
            original_archive = fixture.archive
            fixture.archive = archive_link
            self.assert_import_rejected(fixture, root / "archive-link-result")
            fixture.archive = original_archive

            existing = root / "existing"
            existing.mkdir()
            sentinel = existing / "user-owned"
            sentinel.write_bytes(b"do not overwrite\n")
            self.assert_import_rejected(fixture, existing)
            self.assertEqual(sentinel.read_bytes(), b"do not overwrite\n")

            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            self.assert_import_rejected(fixture, linked_parent / "result")
            self.assertEqual(list(real_parent.iterdir()), [])

    def test_artifact_tamper_and_rehashed_promotion_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)

            tampered = root / "tampered"
            fixture.run(tampered)
            joined = tampered / "events_pose_join.jsonl"
            joined.write_bytes(joined.read_bytes() + b" \n")
            self.assert_inspect_rejected(tampered)

            promoted = root / "promoted"
            fixture.run(promoted)
            receipt_path = promoted / "receipt.json"
            complete_path = promoted / "COMPLETE.json"
            old_receipt_bytes = receipt_path.read_bytes()
            old_digest = sha256(old_receipt_bytes)
            receipt = json.loads(old_receipt_bytes)
            receipt["promotion_status"] = "GO"
            claim_scope = receipt.setdefault("claim_scope", {})
            claim_scope["official_redred_traffic"] = True
            claim_scope["canonical_redred_traffic"] = True
            claim_scope["pure_rotation_claimed"] = True
            claim_scope["warp_performed"] = True
            new_receipt_bytes = canonical_json(receipt)
            receipt_path.write_bytes(new_receipt_bytes)
            new_digest = sha256(new_receipt_bytes)

            completion = read_json(complete_path)

            def replace_digest(value: Any) -> Any:
                if isinstance(value, dict):
                    return {key: replace_digest(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [replace_digest(item) for item in value]
                return new_digest if value == old_digest else value

            completion = replace_digest(completion)
            # Update an explicitly keyed receipt size if the implementation records it.
            inventories = [completion.get(key) for key in ("artifacts", "artifact_inventory")]
            for inventory in inventories:
                if isinstance(inventory, dict):
                    row = inventory.get("receipt.json")
                    if isinstance(row, dict) and "size_bytes" in row:
                        row["size_bytes"] = len(new_receipt_bytes)
                elif isinstance(inventory, list):
                    for row in inventory:
                        if isinstance(row, dict) and row.get("name", row.get("basename")) == "receipt.json":
                            if "size_bytes" in row:
                                row["size_bytes"] = len(new_receipt_bytes)
            complete_path.write_bytes(canonical_json(completion))
            self.assert_inspect_rejected(promoted)

    def test_rehashed_selection_tamper_is_rejected_against_bound_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            result = root / "selection-tampered"
            fixture.run(result)

            events_path = result / "events_pose_join.jsonl"
            receipt_path = result / "receipt.json"
            complete_path = result / "COMPLETE.json"

            event_lines = events_path.read_bytes().splitlines(keepends=True)
            event_header = json.loads(event_lines[0])
            event_header["selection"]["start_timestamp_ns_inclusive"] -= 1
            event_bytes = canonical_json(event_header) + b"".join(event_lines[1:])
            events_path.write_bytes(event_bytes)

            receipt = read_json(receipt_path)
            receipt["selection"]["start_timestamp_ns_inclusive"] -= 1
            event_inventory = receipt["artifact_inventory"]["events_pose_join.jsonl"]
            event_inventory["size_bytes"] = len(event_bytes)
            event_inventory["sha256"] = sha256(event_bytes)
            receipt_bytes = canonical_json(receipt)
            receipt_path.write_bytes(receipt_bytes)

            completion = read_json(complete_path)
            completion["artifacts"]["events_pose_join.jsonl"] = {
                "size_bytes": len(event_bytes),
                "sha256": sha256(event_bytes),
            }
            completion["artifacts"]["receipt.json"] = {
                "size_bytes": len(receipt_bytes),
                "sha256": sha256(receipt_bytes),
            }
            complete_path.write_bytes(canonical_json(completion))

            with self.assertRaises(Exception) as caught:
                inspect(result, fixture.spec)
            self.assertNotIsInstance(
                caught.exception, (TypeError, AttributeError, AssertionError)
            )

    def test_fixture_zip_and_all_published_artifacts_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_fixture_root = root / "fixture-a"
            second_fixture_root = root / "fixture-b"
            first_fixture_root.mkdir()
            second_fixture_root.mkdir()
            first = Fixture(first_fixture_root)
            second = Fixture(second_fixture_root)
            self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
            self.assertEqual(first.spec.read_bytes(), second.spec.read_bytes())

            first_result = root / "result-a"
            second_result = root / "result-b"
            first.run(first_result)
            first.run(second_result)
            first_names = sorted(path.name for path in first_result.iterdir())
            self.assertEqual(first_names, sorted(path.name for path in second_result.iterdir()))
            for name in first_names:
                self.assertEqual((first_result / name).read_bytes(), (second_result / name).read_bytes(), name)

    @unittest.skipUnless(os.environ.get("REDRED_RUN_UZH_FULL_BYTES") == "1",
                         "set REDRED_RUN_UZH_FULL_BYTES=1 for the optional 157 MB/509 MB integration")
    def test_optional_full_pinned_external_archive(self) -> None:
        if not EXTERNAL_ARCHIVE.is_file() or not EXTERNAL_LICENSE.is_file():
            self.skipTest("pinned UZH archive/license bytes are absent")
        self.assertEqual(file_sha256(EXTERNAL_ARCHIVE), EXTERNAL_ARCHIVE_SHA256)
        self.assertEqual(file_sha256(EXTERNAL_LICENSE), EXTERNAL_LICENSE_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            receipt = import_join(
                EXTERNAL_ARCHIVE, EXTERNAL_LICENSE, PRODUCTION_SPEC, result,
            )
            self.assertEqual(receipt["conservation"]["admitted_window_records"], 1100)
            self.assertEqual(receipt["conservation"]["joined_event_records"], 1100)
            self.assertEqual(receipt["conservation"]["join_rejected_or_dropped_records"], 0)
            records = [row for row in read_jsonl(result / "events_pose_join.jsonl")
                       if "dataset_event_index" in row]
            self.assertEqual(records[0]["dataset_event_index"], 13_856_250)
            self.assertEqual(records[-1]["dataset_event_index"], 13_857_349)
            self.assertEqual({row["causal_pose"]["source_pose_index"] for row in records}, {8241})
            self.assertEqual({row["bracket"]["right_source_pose_index"] for row in records}, {8242})
            inspected = inspect(result, PRODUCTION_SPEC)
            self.assertEqual(receipt["status"], "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED")
            self.assertTrue(receipt["claim_scope"]["official_uzh_source"])
            self.assertTrue(inspected["official_uzh_source"])


if __name__ == "__main__":
    unittest.main()
