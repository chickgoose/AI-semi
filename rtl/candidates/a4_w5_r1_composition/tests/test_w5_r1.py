#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
CANDIDATE = ROOT / "rtl/candidates/a4_w5_r1_composition"


class W5R1CompositionTest(unittest.TestCase):
    def test_shell_uses_production_endpoints_without_queue_or_edge_detector(self):
        source = (CANDIDATE / "a4_w5_r1_composition.sv").read_text()
        self.assertNotIn("always_ff", source)
        self.assertIn("a7_r1_candidate_endpoint", source)
        self.assertIn("a7_r1_parallel_reference_top", source)
        self.assertIn("producer_valid_i & producer_ready_o", source)

    def test_pinned_rtl_e2e_and_fail_closed_overwrite(self):
        runner = CANDIDATE / "run_w5_r1.py"
        with tempfile.TemporaryDirectory(prefix="a4-w5-test-") as temp:
            result = Path(temp) / "result-first.json"
            second_result = Path(temp) / "result-second.json"
            command = [str(runner), "--output", str(result)]
            env = dict(os.environ)
            if Path("/tmp/a7-sim-bin/verilator").is_file():
                env["AER_VERILATOR"] = "/tmp/a7-sim-bin/verilator"
            completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            report = json.loads(result.read_text())
            self.assertEqual(report["schema"], "a4_w5_r1_composition_canonical_v2")
            self.assertEqual(report["status"], "LOCAL_R1_COMPOSITION_PASS")
            self.assertEqual(report["counts"]["continuous_valid_changing_address"], 32)
            self.assertEqual(report["counts"]["accepted"], 51)
            self.assertEqual(report["counts"]["retired"], 50)
            self.assertEqual(report["counts"]["reset_aborted"], 1)
            self.assertEqual(report["architecture_contract"]["qualifier_state_bits_per_endpoint"], 1)
            self.assertEqual(report["architecture_contract"]["cdc_claim"], "none")

            second = subprocess.run(
                [str(runner), "--output", str(second_result)],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(result.read_bytes(), second_result.read_bytes())
            tracked = CANDIDATE / "results/w5_r1_composition.json"
            self.assertEqual(
                result.read_bytes(), tracked.read_bytes(),
                "tracked evidence is not the canonical runner output",
            )

            mutation_result = Path(temp) / "phase-mutation.json"
            mutation = subprocess.run(
                [str(runner), "--phase-mutation", "--output", str(mutation_result)],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            self.assertEqual(mutation.returncode, 0, mutation.stdout + mutation.stderr)
            mutation_report = json.loads(mutation_result.read_text())
            self.assertEqual(mutation_report["status"], "EXPECTED_PHASE_MUTATION_REJECTED")
            self.assertEqual(mutation_report["mutated_release_sites"], 4)
            self.assertEqual(mutation_report["expected_marker"], "RESET_RELEASE_PHASE_FAIL")

            repeated = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("refusing to overwrite output", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
