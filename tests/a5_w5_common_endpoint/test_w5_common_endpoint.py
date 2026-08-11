#!/usr/bin/env python3

import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "w5_runner", HERE / "w5_common_endpoint_runner.py")
W5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W5)


class W5ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="a5-w5-test.")
        cls.root = Path(cls.temporary.name)
        cls.boundary = cls.root / "boundary"
        W5.prepare_boundary(Path("/home/chickgoose/projects/a1"), cls.boundary)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_v4_boundary_exact_cardinality_and_address_only(self):
        index = W5.validate_boundary(self.boundary)
        self.assertEqual(index["suites"]["full50"]["run_count"], 50)
        self.assertEqual(index["suites"]["capacity22"]["run_count"], 22)
        self.assertEqual(index["serializer_audit"], {
            "unbounded_tb_serializer": True,
            "max_occurrence_to_launch_wait_cycles": 5132,
            "worst_backlog_events": 5133,
            "full50_launches_after_stimulus_window": 21306,
            "capacity22_launches_after_stimulus_window": 21064,
            "capacity22_is_name_subset_of_full50": True,
        })
        for suite in ("full50", "capacity22"):
            for run in index["suites"][suite]["runs"]:
                rows = W5.read_jsonl(self.boundary / run["boundary_file"])
                self.assertTrue(all(set(row) == {"presentation_index", "launch_cycle",
                    "occurrence_cycle", "tb_only_event_id", "address"} for row in rows))
                self.assertTrue(all(0 <= row["address"] < 16 for row in rows))

    def test_pinned_production_endpoint_full_e2e(self):
        output = self.root / "production-evaluation.json"
        report = W5.evaluate_endpoint(
            self.boundary, Path("/home/chickgoose/projects/a7"),
            W5.A7_W5_ENDPOINT_COMMIT, "A5_BUILTIN_PINNED_A7_W5", output)
        self.assertEqual(report["status"], "EXACT_SERIALIZED_LINK_REPLAY_PASS")
        self.assertEqual(len(report["runs"]), 144)
        for suite in ("full50", "capacity22"):
            left = report["aggregates"][f"parallel_r1_full:{suite}"]
            right = report["aggregates"][f"ddr_r1_full:{suite}"]
            self.assertEqual(left["accepted"], right["accepted"])
            self.assertEqual(left["delivered"], left["accepted"])
            self.assertEqual(right["delivered"], right["accepted"])
            self.assertEqual(left["latency_ticks"]["launch_to_retire"]["max"], 8)
            self.assertEqual(right["latency_ticks"]["launch_to_retire"]["max"], 8)
        self.assertEqual(report["provenance"]["driver_sha256"],
                         W5.A5_PRODUCTION_SHA256["driver"])
        self.assertEqual(report["provenance"]["harness_sha256"],
                         W5.A5_PRODUCTION_SHA256["harness"])
        second_root = self.root / "independent-clean-root"
        second_boundary = second_root / "boundary"
        W5.prepare_boundary(Path("/home/chickgoose/projects/a1"), second_boundary)
        second_report = W5.evaluate_endpoint(
            second_boundary, Path("/home/chickgoose/projects/a7"),
            W5.A7_W5_ENDPOINT_COMMIT, "A5_BUILTIN_PINNED_A7_W5",
            second_root / "evaluation.json")
        first_bytes = W5.canonical_bytes(report)
        second_bytes = W5.canonical_bytes(second_report)
        tracked = (HERE.parents[1] /
            "docs/research/results/a5_w5_common_endpoint_summary.json").read_bytes()
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_bytes, tracked)
        canonical = json.loads(first_bytes)
        self.assertNotIn("compile_log_sha256", canonical["provenance"])
        self.assertNotIn("binary_sha256", canonical["provenance"])
        self.assertEqual(canonical["provenance"]["common"]["common_commit"],
                         W5.COMMON_COMMIT)
        self.assertEqual(canonical["provenance"]["a7_rtl_blob_sha256"],
                         W5.A7_W5_SOURCE_SHA256)
        self.assertEqual(canonical["provenance"]["a5_source_sha256"]["runner"],
            hashlib.sha256((HERE / "w5_common_endpoint_runner.py").read_bytes()).hexdigest())
        self.assertEqual(len(canonical["runs"]), 144)
        self.assertNotIn("/tmp/", json.dumps(canonical, sort_keys=True))

    def test_canonicalize_rejects_missing_attempt_identity(self):
        malformed = {"status": "EXACT_SERIALIZED_LINK_REPLAY_PASS",
                     "runs": [{}] * 144, "provenance": {
                         "compile_log_sha256": "1" * 64}}
        with self.assertRaises(W5.ContractError):
            W5.canonicalize(malformed)

    def test_missing_or_pre_fix_endpoint_fails_closed(self):
        for commit in ("0" * 40, "ca1a20971ee7bc32520aef47a3a97c89747c7fa5"):
            if commit == W5.A7_W5_ENDPOINT_COMMIT:
                continue
            with self.assertRaises(W5.ContractError):
                W5.evaluate_endpoint(self.boundary, Path("/home/chickgoose/projects/a7"),
                    commit, "A5_BUILTIN_PINNED_A7_W5", self.root / f"bad-{commit[:7]}.json")

    def test_a5_driver_and_harness_bundle_sha_mutations_rejected(self):
        for key in ("driver", "harness"):
            original = W5.A5_PRODUCTION_SHA256[key]
            W5.A5_PRODUCTION_SHA256[key] = "0" * 64
            try:
                with self.assertRaises(W5.ContractError):
                    W5.load_endpoint_bundle(
                        Path("/home/chickgoose/projects/a7"), W5.A7_W5_ENDPOINT_COMMIT,
                        "A5_BUILTIN_PINNED_A7_W5",
                        self.root / f"mutated-{key}-source-load")
            finally:
                W5.A5_PRODUCTION_SHA256[key] = original

    def test_ca1a209_negative_control_hits_real_drain_assertion(self):
        commit = "ca1a20971ee7bc32520aef47a3a97c89747c7fa5"
        root = self.root / "ca1-negative-bundle"
        root.mkdir()
        for relative in W5.A7_W5_SOURCE_SHA256:
            W5.exclusive_write(root / relative,
                W5.git_blob(Path("/home/chickgoose/projects/a7"), commit, relative))
        driver = HERE / "production_endpoint_driver.py"
        tb = HERE / "a5_w5_production_tb.sv"
        W5.exclusive_write(root / "a5/production_endpoint_driver.py", driver.read_bytes())
        W5.exclusive_write(root / "a5/a5_w5_production_tb.sv", tb.read_bytes())
        output = self.root / "ca1-negative-output"
        boundary_sha = hashlib.sha256(
            (self.boundary / "boundary-index.json").read_bytes()).hexdigest()
        result = subprocess.run([
            sys.executable, "-B", str(root / "a5/production_endpoint_driver.py"),
            "--bundle-root", str(root), "--boundary-root", str(self.boundary),
            "--boundary-index-sha256", boundary_sha, "--endpoint-commit", commit,
            "--endpoint-manifest-sha256", "0" * 64, "--output-dir", str(output),
            "--runner-sha256", "1" * 64,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("drain_idle high during launch_fire", result.stdout)
        self.assertFalse((output / "endpoint-result-index.json").exists())

    def test_edge_suppression_swapped_result_and_postedge_boundary_rejected(self):
        index = W5.validate_boundary(self.boundary)
        run = index["suites"]["full50"]["runs"][0]
        rows = W5.read_jsonl(self.boundary / run["boundary_file"])
        base = {
            "schema_version": 1, "endpoint": "parallel_r1_full", "suite": "full50",
            "name": run["name"], "trace_sha256": run["trace_sha256"],
            "boundary_sha256": run["boundary_sha256"],
            "timebase_ticks_per_core_cycle": 4, "dut_visible_fields": ["address"],
            "tb_only_observer_fields": ["presentation_index"],
            "handshake_contract": W5.HANDSHAKE_CONTRACT,
            "clock_contract": W5.PRIMARY_CLOCK_CONTRACT,
            "retire_contract": W5.RETIRE_CONTRACT, "sink_ready_policy": "always_ready",
            "accepted": [{"presentation_index": r["presentation_index"],
                          "address": r["address"], "accept_tick": r["launch_cycle"] * 4}
                         for r in rows],
            "retired": [{"presentation_index": r["presentation_index"],
                         "address": r["address"], "retire_tick": r["launch_cycle"] * 4 + 8}
                        for r in rows],
            "handshake": {"accepted_on_valid_and_ready_posedge": True,
                          "continuous_valid_back_to_back_supported": True,
                          "held_address_check_applicable": False,
                          "held_address_reason": "always_ready_primary_has_no_stall_sample",
                          "edge_suppression_used": False},
            "observation": {"consumer_boundary": "next_ref_rise",
                            "phase_related_synchronous": True,
                            "unrelated_cdc_claimed": False,
                            "fair_boundary": "next_ref_rise_after_transmit_commit"},
            "reset_probe": {"second_reset_after_complete_drain": True,
                "second_reset_cycles": 2, "post_reset_quiet_cycles": 3,
                "retired_during_second_reset": 0,
                "stale_or_phantom_during_quiet": 0,
                "post_reset_sentinel_delivered": 1,
                "post_reset_sentinel_exact_once": True,
                "ready_retire_normalized_during_reset": True,
                "ready_retire_normalized_during_quiet": True},
            "value_transition_proxy": {
                "shared": {"input_data": 1, "input_control": 1, "base_clocks": 1},
                "endpoint": {"internal_data": 1, "internal_control": 1,
                             "link_clock": 1}},
        }
        W5.validate_run_result(rows, base, run, "parallel_r1_full", "full50")
        bad_edge = copy.deepcopy(base)
        bad_edge["handshake"]["edge_suppression_used"] = True
        with self.assertRaises(W5.ContractError):
            W5.validate_run_result(rows, bad_edge, run, "parallel_r1_full", "full50")
        swapped = copy.deepcopy(base)
        swapped["name"] = "another-run"
        with self.assertRaises(W5.ContractError):
            W5.validate_run_result(rows, swapped, run, "parallel_r1_full", "full50")
        post_nba = copy.deepcopy(base)
        post_nba["retired"][0]["retire_tick"] = post_nba["accepted"][0]["accept_tick"] + 4
        # A one-cycle value would indicate producer post-NBA availability, not
        # the registered consumer retirement required by the production TB.
        with self.assertRaises(W5.ContractError):
            W5.validate_run_result(rows, post_nba, run, "parallel_r1_full", "full50")
        bad_reset = copy.deepcopy(base)
        bad_reset["reset_probe"]["stale_or_phantom_during_quiet"] = 1
        with self.assertRaises(W5.ContractError):
            W5.validate_run_result(rows, bad_reset, run, "parallel_r1_full", "full50")
        bad_proxy = copy.deepcopy(base)
        del bad_proxy["value_transition_proxy"]["endpoint"]["link_clock"]
        with self.assertRaises(W5.ContractError):
            W5.validate_run_result(rows, bad_proxy, run, "parallel_r1_full", "full50")

    def test_boundary_backlog_provenance_mutation_rejected(self):
        path = self.boundary / "boundary-index.json"
        document = W5.read_json(path)
        document["serializer_audit"]["max_occurrence_to_launch_wait_cycles"] -= 1
        mutated = self.root / "mutated-boundary"
        mutated.mkdir()
        # Reuse immutable run artifacts; only the machine audit is mutated.
        for suite in ("full50", "capacity22"):
            (mutated / suite).symlink_to(self.boundary / suite, target_is_directory=True)
        (mutated / "boundary-index.json").write_text(
            json.dumps(document, sort_keys=True) + "\n")
        with self.assertRaises(W5.ContractError):
            W5.validate_boundary(mutated)


if __name__ == "__main__":
    unittest.main()
