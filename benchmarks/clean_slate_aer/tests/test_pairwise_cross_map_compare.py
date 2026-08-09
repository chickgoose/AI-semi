import copy
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pairwise_cross_map_compare as cross_map


def manifest(name, permutation, source_permutation):
    return {
        "generator_version": "3.1",
        "event_identity_mode": "address_only",
        "trace_sha256": ("1" if "identity" in name else "2") * 64,
        "logical_source_permutation": permutation,
        "report_group": name,
        "run": {
            "name": name,
            "workload": "pairwise_contention",
            "seed": 7,
            "geometry": {"width": 2, "height": 2},
            "load": "0.5",
            "stim_cycles": 64,
            "sink": {"mode": "always"},
            "parameters": {
                "pair_spacing": 2,
                "pair_repeats": 2,
                "source_permutation": source_permutation,
            },
        },
    }


def report(metadata, latencies, states=None, orders=None):
    permutation = metadata["logical_source_permutation"]
    pairs = list(itertools.combinations(range(4), 2))
    states = states or {}
    orders = orders or {}
    trials = []
    for relation_id in range(12):
        canonical = pairs[relation_id % 6]
        state_a, state_b = states.get(relation_id, ("delivered", "delivered"))
        physical_a = permutation[canonical[0]]
        physical_b = permutation[canonical[1]]
        trial = {
            "relation_id": relation_id,
            "repeat_index": relation_id // 6,
            "canonical_source_a": canonical[0],
            "canonical_source_b": canonical[1],
            "physical_source_a": physical_a,
            "physical_source_b": physical_b,
            "event_state_a": state_a,
            "event_state_b": state_b,
        }
        has_drop = "source_overrun" in (state_a, state_b)
        has_censor = state_a in {"pending", "accepted"} or state_b in {"pending", "accepted"}
        if has_drop:
            trial["result"] = "dropped"
        elif has_censor:
            trial["result"] = "censored"
        else:
            completion, skew = latencies[relation_id]
            order = orders.get(relation_id, "A_FIRST")
            if order == "A_FIRST":
                delivery_a, delivery_b = 100, 100 + skew
            elif order == "B_FIRST":
                delivery_a, delivery_b = 100 + skew, 100
            else:
                delivery_a = delivery_b = 100
                skew = 0
            trial.update({
                "result": "evaluable",
                "delivery_a": delivery_a,
                "delivery_b": delivery_b,
                "completion_latency_cycles": completion,
                "service_skew_cycles": skew,
            })
        trials.append(trial)
    return {
        "candidate": "fixture",
        "test": metadata["report_group"],
        "seed": "7",
        "trace_sha256": metadata["trace_sha256"],
        "generator_version": metadata["generator_version"],
        "logical_source_permutation": permutation,
        "pair_count": 12,
        "trials": trials,
    }


class PairwiseCrossMapCompareTest(unittest.TestCase):
    def setUp(self):
        self.identity_manifest = manifest("pairwise_identity", [0, 1, 2, 3], "identity")
        self.affine_manifest = manifest(
            "pairwise_affine",
            [1, 0, 3, 2],
            {"mode": "affine", "multiplier": 3, "offset": 1},
        )
        self.identity_latencies = [(10 + index, 1 + index % 3) for index in range(12)]
        self.affine_latencies = [(12 + index, 2 + index % 3) for index in range(12)]

    def compare(self, **overrides):
        identity_report = overrides.get(
            "identity_report", report(self.identity_manifest, self.identity_latencies)
        )
        affine_report = overrides.get(
            "affine_report", report(self.affine_manifest, self.affine_latencies)
        )
        return cross_map.compare(
            overrides.get("identity_manifest", self.identity_manifest),
            identity_report,
            overrides.get("affine_manifest", self.affine_manifest),
            affine_report,
        )

    def test_joins_by_relation_repeat_and_canonical_pair(self):
        identity_report = report(
            self.identity_manifest,
            self.identity_latencies,
            orders={1: "B_FIRST"},
        )
        affine_report = report(
            self.affine_manifest,
            self.affine_latencies,
            orders={1: "A_FIRST"},
        )
        result = self.compare(identity_report=identity_report, affine_report=affine_report)
        self.assertEqual(result["trial_count"], 12)
        self.assertEqual(result["both_evaluable_trials"], 12)
        self.assertEqual(result["mean_completion_delta_affine_minus_identity"], 2.0)
        self.assertEqual(result["mean_skew_delta_affine_minus_identity"], 1.0)
        self.assertEqual(result["order_changed_trials"], 1)
        relation7 = result["trials"][7]
        self.assertEqual(relation7["repeat_index"], 1)
        self.assertEqual(
            (relation7["canonical_source_a"], relation7["canonical_source_b"]),
            (0, 2),
        )
        self.assertEqual(
            (relation7["affine_physical_source_a"], relation7["affine_physical_source_b"]),
            (1, 3),
        )
        self.assertEqual(len(result["canonical_pair_aggregates"]), 6)
        self.assertEqual(
            {row["trial_count"] for row in result["canonical_pair_aggregates"]}, {2}
        )

    def test_drop_and_censor_are_independent_and_timing_delta_is_null(self):
        identity = report(
            self.identity_manifest,
            self.identity_latencies,
            states={2: ("source_overrun", "pending")},
        )
        affine = report(
            self.affine_manifest,
            self.affine_latencies,
            states={3: ("delivered", "accepted")},
        )
        result = self.compare(identity_report=identity, affine_report=affine)
        row2 = result["trials"][2]
        self.assertTrue(row2["identity_drop"])
        self.assertTrue(row2["identity_censor"])
        self.assertIsNone(row2["completion_delta_affine_minus_identity"])
        self.assertEqual(row2["drop_delta_affine_minus_identity"], -1)
        row3 = result["trials"][3]
        self.assertEqual(row3["comparison_state"], "AFFINE_INCOMPLETE")
        self.assertEqual(row3["censor_delta_affine_minus_identity"], 1)
        self.assertEqual(result["incomplete_comparisons"], 2)

    def test_rejects_missing_duplicate_and_mismatched_trials(self):
        base = report(self.identity_manifest, self.identity_latencies)
        cases = []
        missing = copy.deepcopy(base)
        missing["trials"].pop()
        cases.append((missing, "missing or has extra"))
        duplicate = copy.deepcopy(base)
        duplicate["trials"][11] = copy.deepcopy(duplicate["trials"][10])
        cases.append((duplicate, "duplicate relation_id"))
        mismatch = copy.deepcopy(base)
        mismatch["trials"][7]["repeat_index"] = 0
        cases.append((mismatch, "contract mismatch"))
        for bad_report, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(cross_map.CrossMapError, message):
                    self.compare(identity_report=bad_report)

    def test_rejects_manifest_permutation_and_provenance_mismatch(self):
        bad_affine = copy.deepcopy(self.affine_manifest)
        bad_affine["logical_source_permutation"] = [0, 1, 2, 3]
        with self.assertRaisesRegex(cross_map.CrossMapError, "affine frozen permutation"):
            self.compare(affine_manifest=bad_affine)

        bad_report = report(self.affine_manifest, self.affine_latencies)
        bad_report["trace_sha256"] = "wrong"
        with self.assertRaisesRegex(cross_map.CrossMapError, "trace SHA256"):
            self.compare(affine_report=bad_report)

        bad_candidate = report(self.affine_manifest, self.affine_latencies)
        bad_candidate["candidate"] = "another"
        with self.assertRaisesRegex(cross_map.CrossMapError, "different candidates"):
            self.compare(affine_report=bad_candidate)

    def test_rejects_order_and_state_inconsistency(self):
        bad_skew = report(self.affine_manifest, self.affine_latencies)
        bad_skew["trials"][0]["service_skew_cycles"] = 99
        with self.assertRaisesRegex(cross_map.CrossMapError, "skew disagrees"):
            self.compare(affine_report=bad_skew)

        bad_state = report(self.affine_manifest, self.affine_latencies)
        bad_state["trials"][0]["event_state_a"] = "source_overrun"
        with self.assertRaisesRegex(cross_map.CrossMapError, "result disagrees"):
            self.compare(affine_report=bad_state)

    def test_path_api_records_all_input_hashes(self):
        payloads = {
            "identity_manifest.json": self.identity_manifest,
            "identity_report.json": report(
                self.identity_manifest, self.identity_latencies
            ),
            "affine_manifest.json": self.affine_manifest,
            "affine_report.json": report(
                self.affine_manifest, self.affine_latencies
            ),
        }
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            for name, payload in payloads.items():
                (directory / name).write_text(
                    json.dumps(payload) + "\n", encoding="utf-8"
                )
            result = cross_map.analyze_paths(
                directory / "identity_manifest.json",
                directory / "identity_report.json",
                directory / "affine_manifest.json",
                directory / "affine_report.json",
            )
        self.assertEqual(
            set(result["input_sha256"]),
            {
                "identity_manifest", "identity_report",
                "affine_manifest", "affine_report",
            },
        )
        self.assertTrue(
            all(len(digest) == 64 for digest in result["input_sha256"].values())
        )


if __name__ == "__main__":
    unittest.main()
