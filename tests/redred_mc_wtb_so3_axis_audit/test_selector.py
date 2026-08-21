from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_so3_axis_audit import selector as selector_module
from benchmarks.redred_mc_wtb_so3_axis_audit.selector import (
    DEFAULT_EXCLUSIONS,
    DEFAULT_HISTORICAL_POSE_HALO,
    EXPECTED_HISTORICAL_POSE_IDS_SHA256,
    EXPECTED_WINDOW_COUNT,
    SelectorError,
    audit_score_free_imports,
    select_full_source,
    verify_registry,
)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def quaternion_step(axis, sign, angle):
    vector = [0.0, 0.0, 0.0]
    vector[axis] = sign * math.sin(angle / 2.0)
    return tuple(vector) + (math.cos(angle / 2.0),)


def multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw*rx + lx*rw + ly*rz - lz*ry,
        lw*ry - lx*rz + ly*rw + lz*rx,
        lw*rz + lx*ry - ly*rx + lz*rw,
        lw*rw - lx*rx - ly*ry - lz*rz,
    )


def timestamp_text(timestamp_ns):
    return "%d.%09d" % divmod(timestamp_ns, 1_000_000_000)


class SyntheticSource:
    def __init__(self, root, per_cell=7):
        self.root = Path(root)
        cells = [(axis, sign, angle)
                 for angle in (0.2, 0.7, 1.2)
                 for axis in range(3) for sign in (-1, 1)]
        motions = [cell for _ in range(per_cell) for cell in cells]
        targets = [20_000_000 + index * 30_000_000 for index in range(len(motions))]
        changes = dict(zip(targets, motions))

        q = (0.0, 0.0, 0.0, 1.0)
        pose_rows = []
        final_time = targets[-1] + 10_000_000 if targets else 20_000_000
        for timestamp in range(0, final_time + 1, 5_000_000):
            pose_rows.append((timestamp, q))
            if timestamp in changes:
                axis, sign, angle = changes[timestamp]
                q = multiply(q, quaternion_step(axis, sign, angle))
                pose_rows.append((timestamp + 1_000_000, q))
        pose_rows.sort(key=lambda row: row[0])
        poses = b"".join(
            ("%s 0.0 0.0 0.0 %.17f %.17f %.17f %.17f\n"
             % ((timestamp_text(timestamp),) + tuple(qvalue))).encode("ascii")
            for timestamp, qvalue in pose_rows
        )

        events = b"".join(
            ("%s 1 1 %d\n" % (timestamp_text(timestamp), event_id & 1)).encode("ascii")
            for event_id, timestamp in enumerate(
                value for target in targets
                for value in (target - 500_000, target + 500_000)
            )
        )
        calibration = b"1.0 1.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0\n"
        payloads = {"events": ("events.txt", events),
                    "poses": ("groundtruth.txt", poses),
                    "calibration": ("calib.txt", calibration)}
        members = {}
        for role, (name, payload) in payloads.items():
            (self.root / name).write_bytes(payload)
            members[role] = {"filename": name, "size_bytes": len(payload),
                             "line_count": payload.count(b"\n"), "sha256": sha(payload)}
        self.source_lock = self.root / "source-lock.json"
        source_lock_payload = canonical({
            "schema": "redred.mc_wtb_so3_axis_audit.source_lock/v1",
            "sequence": "synthetic-axis-selector-test", "members": members,
        })
        self.source_lock.write_bytes(source_lock_payload)
        self.expected_source_lock_sha256 = sha(source_lock_payload)
        self.expected_members = members
        self.exclusions = self.root / "exclusions.json"
        exclusions_payload = canonical({
            "schema": "redred.mc_wtb_so3_axis_audit.historical_exclusions/v1",
            "documents": [], "intervals": [],
        })
        self.exclusions.write_bytes(exclusions_payload)
        self.expected_exclusions_sha256 = sha(exclusions_payload)
        halo = json.loads(DEFAULT_HISTORICAL_POSE_HALO.read_text())
        halo["source_poses_sha256"] = members["poses"]["sha256"]
        self.halo = self.root / "halo.json"
        self.halo.write_bytes(canonical(halo))

    @contextmanager
    def frozen_fixture_authority(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_SOURCE_LOCK_SHA256",
                self.expected_source_lock_sha256))
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_SOURCE_MEMBERS", self.expected_members))
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_EXCLUSIONS_SHA256",
                self.expected_exclusions_sha256))
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_HISTORICAL_INTERVAL_COUNT", 0))
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_HISTORICAL_INTERVALS_SHA256", sha(b"")))
            yield

    def select(self):
        with self.frozen_fixture_authority():
            return selector_module._select_full_source_unlocked(
                self.root, source_lock_path=self.source_lock,
                exclusions_path=self.exclusions,
                historical_pose_halo_path=self.halo,
            )

    def verify(self, registry):
        with self.frozen_fixture_authority():
            selector_module._verify_registry_unlocked(
                registry, dataset_directory=self.root,
                source_lock_path=self.source_lock,
                exclusions_path=self.exclusions,
                historical_pose_halo_path=self.halo,
            )


def reseal(registry):
    registry.pop("registry_sha256", None)
    registry["registry_sha256"] = sha(canonical(registry))


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = SyntheticSource(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_deterministic_full_cohort_and_frozen_round_ranks(self):
        first = self.fixture.select()
        second = self.fixture.select()
        self.assertEqual(first, second)
        self.assertEqual(first["window_count"], EXPECTED_WINDOW_COUNT)
        counts = {}
        for row in first["windows"]:
            key = row["motion_bin"], row["axis"], row["sign"]
            counts[key] = counts.get(key, 0) + 1
            self.assertTrue(row["warmup_event_ids"])
            self.assertTrue(row["query_event_ids"])
            self.assertRegex(row["selected_raw_event_lines_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(set(row["axis_pose_support_indices"]).issubset(
                row["pose_support_indices"]))
        self.assertEqual(set(counts.values()), {6})
        self.fixture.verify(first)

    def test_round_schedule_is_bin_major_before_signed_axis(self):
        candidates = []
        serial = 0
        for motion_bin in ("LOW", "MID", "HIGH"):
            for axis in ("X", "Y", "Z"):
                for sign in ("NEGATIVE", "POSITIVE"):
                    count = 7 if (motion_bin, axis, sign) in {
                        ("LOW", "X", "POSITIVE"),
                        ("MID", "X", "NEGATIVE"),
                    } else 6
                    for rank_index in range(count):
                        support = (serial + 10_000,)
                        if rank_index == 0 and (motion_bin, axis, sign) in {
                            ("LOW", "X", "POSITIVE"),
                            ("MID", "X", "NEGATIVE"),
                        }:
                            support = (999,)
                        start = (serial + 1) * 100_000_000
                        candidates.append(selector_module._Candidate(
                            "fixture/%d" % serial, start, (0.1, 0.0, 0.0),
                            1.0, 0.1, axis, sign, motion_bin,
                            support, support, support, support,
                            "%064x" % rank_index,
                        ))
                        serial += 1
        selected = selector_module._select_candidates(candidates)
        selected_ids = {row.candidate_id for row in selected}
        low_positive_first = next(
            row for row in candidates
            if row.motion_bin == "LOW" and row.axis == "X"
            and row.sign == "POSITIVE" and row.rank_sha256 == "0" * 64
        )
        mid_negative_first = next(
            row for row in candidates
            if row.motion_bin == "MID" and row.axis == "X"
            and row.sign == "NEGATIVE" and row.rank_sha256 == "0" * 64
        )
        self.assertIn(low_positive_first.candidate_id, selected_ids)
        self.assertNotIn(mid_negative_first.candidate_id, selected_ids)

    def test_rank_binds_all_sources_and_canonical_candidate(self):
        registry = self.fixture.select()
        changed = copy.deepcopy(registry)
        changed["windows"][0]["rank_sha256"] = "0" * 64
        reseal(changed)
        with self.assertRaisesRegex(SelectorError, "rank|selection"):
            self.fixture.verify(changed)

    def test_raw_line_hash_mutation_fails(self):
        changed = copy.deepcopy(self.fixture.select())
        changed["windows"][0]["selected_raw_event_lines_sha256"] = "f" * 64
        reseal(changed)
        with self.assertRaisesRegex(SelectorError, "raw event"):
            self.fixture.verify(changed)

    def test_empty_warmup_or_query_mutations_fail(self):
        for field in ("warmup_event_ids", "query_event_ids"):
            changed = copy.deepcopy(self.fixture.select())
            changed["windows"][0][field] = []
            hash_field = field + "_sha256"
            changed["windows"][0][hash_field] = sha(b"")
            reseal(changed)
            with self.subTest(field=field), self.assertRaisesRegex(SelectorError, "each contain"):
                self.fixture.verify(changed)

    def test_shared_nonboundary_common_pose_support_fails(self):
        changed = copy.deepcopy(self.fixture.select())
        shared = changed["windows"][0]["pose_support_indices"][0]
        self.assertNotIn(shared, changed["windows"][1]["axis_pose_support_indices"])
        changed["windows"][1]["pose_support_indices"][0] = shared
        reseal(changed)
        with self.assertRaisesRegex(SelectorError, "pose support"):
            self.fixture.verify(changed)

    def test_oracle_pose_support_mutation_fails(self):
        changed = copy.deepcopy(self.fixture.select())
        changed["windows"][0]["oracle_pose_support_indices"].pop()
        reseal(changed)
        with self.assertRaisesRegex(SelectorError, "pose support"):
            self.fixture.verify(changed)

    def test_oracle_support_adds_source_bracket_beyond_dataset_deadline(self):
        timestamps = (0, 5, 10, 15, 20, 21, 29)
        poses = []
        for index, milliseconds in enumerate(timestamps):
            quaternion = ((0.0, 0.0, 0.0, 1.0) if milliseconds <= 20 else
                          quaternion_step(0, 1, 0.2))
            sample = selector_module.PoseSample(
                milliseconds * 1_000_000, quaternion)
            poses.append(selector_module._Pose(index, sample))
        candidate = selector_module._candidate(
            20_000_000, tuple(poses),
            tuple(row.sample.timestamp_ns for row in poses), 1.0, (),
            frozenset(), ("a" * 64, "b" * 64, "c" * 64))
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn(6, candidate.oracle_pose_support_indices)
        self.assertNotIn(6, candidate.dataset_pose_support_indices)
        self.assertIn(6, candidate.pose_support_indices)

    def test_empty_complete_halo_cannot_weaken_frozen_digest(self):
        halo = json.loads(self.fixture.halo.read_text())
        halo["pose_support_indices"] = []
        halo["pose_support_indices_sha256"] = sha(b"")
        self.fixture.halo.write_bytes(canonical(halo))
        with self.assertRaisesRegex(SelectorError, "historical pose-support hash"):
            self.fixture.select()

    def test_insufficient_quota_fails_after_event_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            short = SyntheticSource(temporary, per_cell=5)
            with self.assertRaisesRegex(SelectorError, "insufficient"):
                short.select()

    def test_source_hash_or_count_and_score_like_inputs_fail(self):
        events = self.fixture.root / "events.txt"
        events.write_bytes(events.read_bytes() + b"9.999999999 1 1 0\n")
        with self.assertRaisesRegex(SelectorError, "hash/count"):
            self.fixture.select()

        source = json.loads(self.fixture.source_lock.read_text())
        source["outcome_score"] = 0
        self.fixture.source_lock.write_bytes(canonical(source))
        with self.assertRaisesRegex(SelectorError, "score-like"):
            self.fixture.select()

    def test_internally_consistent_alternate_source_lock_is_rejected(self):
        source = json.loads(self.fixture.source_lock.read_text())
        source["sequence"] = "alternate-but-internally-consistent"
        payload = canonical(source)
        self.fixture.source_lock.write_bytes(payload)
        with self.assertRaisesRegex(SelectorError, "official source lock"):
            self.fixture.select()

    def test_removed_official_exclusion_is_rejected(self):
        exclusions = json.loads(DEFAULT_EXCLUSIONS.read_text())
        exclusions["intervals"].pop()
        altered = self.fixture.root / "removed-exclusion.json"
        altered.write_bytes(canonical(exclusions))
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_SOURCE_LOCK_SHA256",
                self.fixture.expected_source_lock_sha256))
            stack.enter_context(mock.patch.object(
                selector_module, "OFFICIAL_SOURCE_MEMBERS",
                self.fixture.expected_members))
            with self.assertRaisesRegex(SelectorError, "official historical exclusion"):
                select_full_source(
                    self.fixture.root, source_lock_path=self.fixture.source_lock,
                    exclusions_path=altered,
                    historical_pose_halo_path=self.fixture.halo,
                )

    def test_nonallowlisted_import_fails(self):
        audit_score_free_imports("import math\n")
        with self.assertRaisesRegex(SelectorError, "import"):
            audit_score_free_imports("from benchmark.scoring import result\n")
        for call in ("__import__('scoring')", "eval('1')", "exec('pass')",
                     "compile('1', '<test>', 'eval')"):
            with self.subTest(call=call), self.assertRaisesRegex(
                    SelectorError, "dynamic import or code-loading"):
                audit_score_free_imports(call + "\n")

    def test_indirect_dynamic_loading_aliases_fail(self):
        mutations = (
            ("alias assignment",
             "loader = __import__\nloader('scoring')\n",
             "dynamic import or code-loading"),
            ("lambda forwarding",
             "forward = lambda loader, name: loader(name)\n"
             "forward(__import__, 'scoring')\n",
             "lambda"),
            ("getattr builtins import",
             "loader = getattr(__builtins__, '__import__')\n"
             "loader('scoring')\n",
             "dynamic import or code-loading"),
        )
        for label, source, message in mutations:
            with self.subTest(label=label), self.assertRaisesRegex(
                    SelectorError, message):
                audit_score_free_imports(source)

    def test_reversed_resealed_registry_fails_order_binding(self):
        changed = copy.deepcopy(self.fixture.select())
        changed["windows"].reverse()
        reseal(changed)
        with self.assertRaisesRegex(SelectorError, "ordered candidate IDs"):
            self.fixture.verify(changed)

    def test_monkeypatched_self_audit_cannot_reauthorize_registry(self):
        changed = copy.deepcopy(self.fixture.select())
        fake_selector_sha = "0" * 64
        changed["bindings"]["selector_py_sha256"] = fake_selector_sha
        reseal(changed)
        with self.fixture.frozen_fixture_authority(), mock.patch.object(
                selector_module, "_audit_self", return_value=fake_selector_sha):
            with self.assertRaisesRegex(SelectorError,
                                        "production selector authority"):
                verify_registry(
                    changed, dataset_directory=self.fixture.root,
                    source_lock_path=self.fixture.source_lock,
                    exclusions_path=self.fixture.exclusions,
                    historical_pose_halo_path=self.fixture.halo,
                )

    def test_committed_historical_locks_are_exact(self):
        exclusions = json.loads(DEFAULT_EXCLUSIONS.read_text())
        self.assertEqual(len(exclusions["intervals"]), 26)
        intervals = {(row["start_ns_inclusive"], row["end_ns_exclusive"])
                     for row in exclusions["intervals"]}
        self.assertIn((41_320_750_000, 41_322_000_000), intervals)
        self.assertIn((43_320_750_000, 43_322_000_000), intervals)
        halo = json.loads(DEFAULT_HISTORICAL_POSE_HALO.read_text())
        ids = halo["pose_support_indices"]
        self.assertEqual(len(ids), 126)
        self.assertEqual(sha(b"".join((str(value) + "\n").encode("ascii")
                                     for value in ids)),
                         EXPECTED_HISTORICAL_POSE_IDS_SHA256)
        package = DEFAULT_EXCLUSIONS.parent
        lock = json.loads((package / "registry_lock.json").read_text())
        for field, filename in (
            ("source_lock_sha256", "source_lock.json"),
            ("selector_py_sha256", "selector.py"),
            ("historical_exclusions_sha256", "historical_exclusions.json"),
            ("historical_pose_halo_sha256", "historical_pose_halo.json"),
        ):
            self.assertEqual(lock[field], sha((package / filename).read_bytes()))
        self.assertEqual(lock["window_count"], EXPECTED_WINDOW_COUNT)
        self.assertEqual(lock["historical_pose_support_indices_sha256"],
                         EXPECTED_HISTORICAL_POSE_IDS_SHA256)


if __name__ == "__main__":
    unittest.main()
