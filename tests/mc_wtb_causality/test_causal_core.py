from __future__ import annotations

import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from demos.mc_wtb.model import analyze_files
from tests.mc_wtb_causality.independent_fixture_generator import build_artifacts
from tests.mc_wtb_causality.independent_oracle import (
    FAIL_STATUS,
    PASS_STATUS,
    canonical_summary_bytes,
    decoded_coordinates,
    evaluate_coordinates,
    evaluate_result,
    load_oracle,
    mutant_candidate_coordinates,
    oracle_events_by_id,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "tests" / "mc_wtb_causality"
FIXTURES = PACKAGE / "fixtures"
INTRINSICS = FIXTURES / "intrinsics.json"
EVENTS_IDENTITY = FIXTURES / "events_identity.jsonl"
EVENTS_CAUSAL = FIXTURES / "events_causal.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class CausalCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.oracle = load_oracle(FIXTURES / "oracle.json")
        cls.event_ids = [entry["event_id"] for entry in cls.oracle["events"]]
        arm_inputs = {
            "C0_IDENTITY": (
                EVENTS_IDENTITY,
                FIXTURES / "poses_identity.jsonl",
                cls.event_ids[:8],
            ),
            "C1_CORRECT": (
                EVENTS_CAUSAL,
                FIXTURES / "poses_correct.jsonl",
                cls.event_ids,
            ),
            "C2_WRONG_VALID": (
                EVENTS_CAUSAL,
                FIXTURES / "poses_wrong_valid.jsonl",
                cls.event_ids,
            ),
            "C3_POSE_PERMUTED": (
                EVENTS_CAUSAL,
                FIXTURES / "poses_permuted.jsonl",
                cls.event_ids,
            ),
        }
        cls.results = {}
        cls.metrics = {}
        for arm, (events, poses, event_ids) in arm_inputs.items():
            result = analyze_files(
                events,
                INTRINSICS,
                poses,
                Path(cls.temporary.name) / f"{arm}.json",
                tile_width=8,
                tile_height=8,
                time_bin_ns=1000,
                max_pose_age_ns=0,
            )
            cls.results[arm] = result
            cls.metrics[arm] = evaluate_result(
                result, cls.oracle, list(event_ids)
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_generator_is_independent_deterministic_and_manifest_bound(self) -> None:
        first = build_artifacts()
        second = build_artifacts()
        self.assertEqual(first, second)
        self.assertEqual(set(first), {path.name for path in FIXTURES.iterdir()})
        for name, expected_bytes in first.items():
            self.assertEqual((FIXTURES / name).read_bytes(), expected_bytes)

        manifest = json.loads(first["manifest.json"])
        self.assertEqual(manifest["status"], PASS_STATUS)
        for name, digest in manifest["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256(first[name]).hexdigest(), digest)
        generator_source = PACKAGE / "independent_fixture_generator.py"
        self.assertEqual(
            hashlib.sha256(generator_source.read_bytes()).hexdigest(),
            manifest["generator_source_sha256"],
        )

        for source_path in (
            generator_source,
            PACKAGE / "independent_oracle.py",
        ):
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            self.assertFalse(
                [name for name in imported if name == "demos" or name.startswith("demos.")]
            )
            self.assertNotIn("warp_pixel", source)

        self.assertEqual(
            [entry["reference_xy"] for entry in self.oracle["landmarks"]],
            [
                [21, 24],
                [32, 41],
                [43, 31],
                [25, 21],
                [36, 38],
                [18, 28],
                [29, 45],
                [40, 20],
            ],
        )

    def test_c1_c2_c3_share_event_bytes_and_only_pose_matrices_vary(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        event_bytes = EVENTS_CAUSAL.read_bytes()
        self.assertEqual(
            hashlib.sha256(event_bytes).hexdigest(),
            manifest["common_c1_c2_c3_event_sha256"],
        )
        event_hashes = {
            self.results[arm]["input_provenance"]["events_sha256"]
            for arm in ("C1_CORRECT", "C2_WRONG_VALID", "C3_POSE_PERMUTED")
        }
        self.assertEqual(event_hashes, {hashlib.sha256(event_bytes).hexdigest()})

        pose_paths = [
            FIXTURES / "poses_correct.jsonl",
            FIXTURES / "poses_wrong_valid.jsonl",
            FIXTURES / "poses_permuted.jsonl",
        ]
        pose_records = [read_jsonl(path) for path in pose_paths]
        normalized = []
        for records in pose_records:
            copy_records = copy.deepcopy(records)
            for record in copy_records[1:]:
                del record["rotation_matrix"]
            normalized.append(copy_records)
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(normalized[0], normalized[2])
        self.assertEqual(len({path.read_bytes() for path in pose_paths}), 3)

    def test_corrected_timestamps_satisfy_latest_pose_with_zero_age(self) -> None:
        events = read_jsonl(EVENTS_CAUSAL)[1:]
        for pose_path in (
            FIXTURES / "poses_correct.jsonl",
            FIXTURES / "poses_wrong_valid.jsonl",
            FIXTURES / "poses_permuted.jsonl",
        ):
            poses = {record["pose_id"]: record for record in read_jsonl(pose_path)[1:]}
            for event in events:
                self.assertEqual(
                    poses[event["pose_version"]]["timestamp"]["value"],
                    event["timestamp_ns"],
                )
        for arm, metrics in self.metrics.items():
            with self.subTest(arm=arm):
                self.assertTrue(metrics["pose_age_zero"])
                self.assertEqual(metrics["maximum_pose_age_ns"], 0)
                self.assertTrue(metrics["ledger_closed"])
                self.assertTrue(metrics["metadata_preserved"])

    def test_c0_identity_is_geometry_and_packet_neutral(self) -> None:
        metrics = self.metrics["C0_IDENTITY"]
        expected = json.loads((FIXTURES / "manifest.json").read_text())[
            "expected_results"
        ]["C0_IDENTITY"]
        for key, value in expected.items():
            self.assertEqual(metrics[key], value)
        result = self.results["C0_IDENTITY"]
        self.assertEqual(
            result["representations"]["sensor_fixed"],
            result["representations"]["pose_compensated_reference"],
        )
        projection = result["bottleneck_metrics"][
            "1_packet_key_projection_not_wire_bandwidth"
        ]
        self.assertEqual(projection["projected_delta_bits"], 0)
        self.assertFalse(projection["actual_wire_bandwidth_measured"])

    def test_c1_correct_pose_exactly_reconstructs_absolute_pixels(self) -> None:
        metrics = self.metrics["C1_CORRECT"]
        expected = json.loads((FIXTURES / "manifest.json").read_text())[
            "expected_results"
        ]["C1_CORRECT"]
        for key, value in expected.items():
            self.assertEqual(metrics[key], value)
        self.assertEqual(set(metrics["per_landmark_unique_pixel_counts"].values()), {1})
        self.assertEqual(metrics["compensated_persistent_bins"], 7)
        self.assertEqual(metrics["compensated_same_tile_extra_events"], 25)
        self.assertEqual(metrics["compensated_max_same_tile_multiplicity"], 8)

    def test_c2_valid_wrong_pose_executes_but_fails_absolute_geometry(self) -> None:
        metrics = self.metrics["C2_WRONG_VALID"]
        expected = json.loads((FIXTURES / "manifest.json").read_text())[
            "expected_results"
        ]["C2_WRONG_VALID"]
        for key, value in expected.items():
            self.assertEqual(metrics[key], value)
        self.assertEqual(metrics["wrong_reference_events"], 24)
        self.assertEqual(metrics["pixel_rmse"], 17.378147196983)

    def test_c3_geometry_failure_overrides_misleading_packet_key_win(self) -> None:
        metrics = self.metrics["C3_POSE_PERMUTED"]
        expected = json.loads((FIXTURES / "manifest.json").read_text())[
            "expected_results"
        ]["C3_POSE_PERMUTED"]
        for key, value in expected.items():
            self.assertEqual(metrics[key], value)
        self.assertEqual(metrics["wrong_reference_events"], 32)
        self.assertEqual(metrics["pixel_rmse"], 17.378147196983)

        by_landmark_q3 = {
            entry["landmark_id"]: entry["sensor_xy"]
            for entry in self.oracle["events"]
            if entry["true_pose"] == "P3"
        }
        expected_by_id = oracle_events_by_id(self.oracle)
        for event_id, coordinate in decoded_coordinates(
            self.results["C3_POSE_PERMUTED"]
        ).items():
            landmark = expected_by_id[event_id]["landmark_id"]
            self.assertEqual(coordinate, by_landmark_q3[landmark])

        projection = self.results["C3_POSE_PERMUTED"]["bottleneck_metrics"][
            "1_packet_key_projection_not_wire_bandwidth"
        ]
        self.assertEqual(projection["projected_delta_bits"], 516)
        self.assertFalse(projection["actual_wire_bandwidth_measured"])
        self.assertFalse(metrics["geometry_accept"])

    def test_identity_polarity_and_same_tile_members_do_not_collapse(self) -> None:
        expected_ids = self.event_ids
        for arm in ("C1_CORRECT", "C2_WRONG_VALID", "C3_POSE_PERMUTED"):
            with self.subTest(arm=arm):
                result_ids = [
                    entry["event_id"] for entry in self.results[arm]["exact_event_ledger"]
                ]
                self.assertEqual(result_ids, expected_ids)
                self.assertEqual(self.metrics[arm]["positive_events"], 28)
                self.assertEqual(self.metrics[arm]["negative_events"], 4)

        correct_bins = self.results["C1_CORRECT"]["representations"]
        correct_bins = correct_bins["pose_compensated_reference"]["bins"]
        shared = next(
            entry
            for entry in correct_bins
            if entry["polarity"] == 1 and entry["tile"] == {"x": 2, "y": 3}
        )
        l0_l5_ids = {
            entry["event_id"]
            for entry in self.oracle["events"]
            if entry["landmark_id"] in ("L0", "L5")
        }
        self.assertEqual(shared["event_count"], 8)
        self.assertEqual(shared["unique_pixel_count"], 2)
        self.assertEqual(set(shared["member_event_ids"]), l0_l5_ids)

    def test_oracle_sensitivity_kills_four_minimal_mutations(self) -> None:
        expected_events = self.oracle["events"]

        def require_c1_geometry(metrics: dict) -> None:
            self.assertEqual(metrics["exact_reference_events"], 32)
            self.assertEqual(metrics["pixel_sse"], 0)
            self.assertTrue(metrics["geometry_accept"])

        for mutation in (
            "matrix_direction_transpose",
            "principal_point_sign",
            "pose_permutation",
        ):
            with self.subTest(mutation=mutation):
                mutant = evaluate_coordinates(
                    mutant_candidate_coordinates(expected_events, mutation),
                    expected_events,
                )
                with self.assertRaises(AssertionError):
                    require_c1_geometry(mutant)

        mutated_expected = copy.deepcopy(expected_events)
        mutated_expected[0]["reference_xy"][0] += 1
        mutant = evaluate_coordinates(
            decoded_coordinates(self.results["C1_CORRECT"]), mutated_expected
        )
        self.assertEqual(mutant["exact_reference_events"], 31)
        self.assertEqual(mutant["pixel_sse"], 1)
        with self.assertRaises(AssertionError):
            require_c1_geometry(mutant)

    def test_publication_is_byte_deterministic_and_status_is_narrow(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        first = canonical_summary_bytes(self.metrics, manifest["expected_results"])
        second = canonical_summary_bytes(self.metrics, manifest["expected_results"])
        self.assertEqual(first, second)
        self.assertEqual(first, (FIXTURES / "expected_summary.json").read_bytes())
        self.assertEqual(json.loads(first)["status"], PASS_STATUS)
        self.assertNotIn(b"PASS_SYNTHETIC_CAUSAL_DISCRIMINATION", first)

        mutated = copy.deepcopy(self.metrics)
        mutated["C3_POSE_PERMUTED"]["geometry_accept"] = True
        failed = canonical_summary_bytes(mutated, manifest["expected_results"])
        self.assertEqual(json.loads(failed)["status"], FAIL_STATUS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
