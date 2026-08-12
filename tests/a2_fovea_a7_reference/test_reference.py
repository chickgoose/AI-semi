#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path

from fovea_a7_reference import (
    A7R1Endpoint,
    FoveaWeight5,
    OracleViolation,
    check_exact,
    run_full_contention,
)


HERE = Path(__file__).resolve().parent


class FoveaTests(unittest.TestCase):
    def test_full_contention_is_exact_1_5_5_1(self):
        fovea = FoveaWeight5()
        fovea.reset()
        rows = []
        for _ in range(12):
            fovea.posedge(0xFFFF)
            self.assertTrue(fovea.valid)
            rows.append(fovea.addr >> 2)
        self.assertEqual(rows, [1, 2, 1, 2, 1, 0, 2, 1, 2, 1, 2, 3])
        self.assertEqual([rows.count(row) for row in range(4)], [1, 5, 5, 1])

    def test_scalar_output_is_continuous(self):
        fovea = FoveaWeight5()
        for _ in range(32):
            fovea.posedge(0xFFFF)
            self.assertTrue(fovea.valid)


class EndpointTests(unittest.TestCase):
    def test_first_release_edge_only_arms(self):
        endpoint = A7R1Endpoint()
        endpoint.posedge(0, False, 0, reset=True)
        self.assertFalse(endpoint.posedge(1, True, 4))
        self.assertTrue(endpoint.ready)
        self.assertTrue(endpoint.posedge(2, True, 5))

    def test_continuous_one_per_cycle_and_two_cycle_consume(self):
        endpoint = A7R1Endpoint()
        endpoint.posedge(0, False, 0, reset=True)
        endpoint.posedge(1, False, 0)
        accepted_cycles = []
        for cycle in range(2, 18):
            if endpoint.posedge(cycle, True, cycle & 0xF):
                accepted_cycles.append(cycle)
        for cycle in range(18, 21):
            endpoint.posedge(cycle, False, 0)
        self.assertEqual(accepted_cycles, list(range(2, 18)))
        check_exact(endpoint.accepted, endpoint.delivered)

    def test_reset_requires_drain_and_post_reset_has_no_phantom(self):
        endpoint = A7R1Endpoint()
        endpoint.posedge(0, False, 0, reset=True)
        endpoint.posedge(1, False, 0)
        endpoint.posedge(2, True, 0xD)
        with self.assertRaisesRegex(OracleViolation, "RESET_WHILE_NOT_DRAINED"):
            endpoint.posedge(3, False, 0, reset=True)
        endpoint.posedge(3, False, 0)
        endpoint.posedge(4, False, 0)
        self.assertTrue(endpoint.drain_idle)
        endpoint.posedge(5, False, 0, reset=True)
        delivered_before = len(endpoint.delivered)
        endpoint.posedge(6, False, 0)
        endpoint.posedge(7, True, 0x2)
        endpoint.posedge(8, False, 0)
        endpoint.posedge(9, False, 0)
        self.assertEqual(len(endpoint.delivered), delivered_before + 1)
        self.assertEqual(endpoint.delivered[-1].address, 0x2)

    def test_composed_run_drains_and_rearms(self):
        result = run_full_contention(24)
        self.assertEqual(len(result.accepted), 25)
        self.assertEqual(len(result.delivered), 25)
        self.assertEqual(result.delivered[-1].address, 0)
        self.assertEqual(len(result.reset_cycles), 2)


class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run_full_contention(24)
        cls.accepted = result.accepted
        cls.delivered = result.delivered

    def test_drop_is_rejected(self):
        mutated = self.delivered[:7] + self.delivered[8:]
        with self.assertRaisesRegex(OracleViolation, "DROP_DETECTED"):
            check_exact(self.accepted, mutated)

    def test_duplicate_is_rejected(self):
        mutated = self.delivered[:8] + [self.delivered[7]] + self.delivered[8:]
        with self.assertRaisesRegex(OracleViolation, "DUPLICATE_DETECTED"):
            check_exact(self.accepted, mutated)

    def test_reorder_is_rejected(self):
        mutated = list(self.delivered)
        mutated[7], mutated[8] = mutated[8], mutated[7]
        with self.assertRaisesRegex(OracleViolation, "REORDER_DETECTED"):
            check_exact(self.accepted, mutated)


class CliTests(unittest.TestCase):
    def test_executable_oracle_reports_pass(self):
        completed = subprocess.run(
            [sys.executable, str(HERE / "fovea_a7_reference.py"),
             "--active-cycles", "24"],
            check=True, capture_output=True, text=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(report["first_twelve_row_counts"], [1, 5, 5, 1])
        self.assertEqual(report["accepted"], report["delivered"])


if __name__ == "__main__":
    unittest.main()
