import csv
import io
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import bracket_fmax


HEADER = (
    "candidate,period_ns,synthesis_mode,corner,setup_wns_ns,hold_wns_ns,"
    "route_ok,unconstrained_paths\n"
)


class PhysicalBracketTest(unittest.TestCase):
    def setUp(self):
        self.fixture = ROOT / "fixtures" / "ganghee_fixed_netlist_example.csv"

    def _read_text(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.csv"
            path.write_text(text, encoding="utf-8")
            return bracket_fmax.read_points([path])

    def test_observed_example_is_bracket_not_exact_fmax(self):
        result = bracket_fmax.summarize(
            bracket_fmax.read_points([self.fixture])
        )[0]
        self.assertEqual(result["status"], "BRACKETED")
        self.assertEqual(result["synthesis_mode"], "fixed_netlist")
        self.assertAlmostEqual(result["demonstrated_fmax_mhz"], 500.0)
        self.assertAlmostEqual(result["first_fail_fmax_mhz"], 1000.0 / 1.5)
        self.assertEqual(result["fmax_bracket_mhz"], "[500, 666.666667)")
        self.assertEqual(result["demonstrated_drc"], "NOT_REPORTED")
        self.assertEqual(result["demonstrated_antenna"], "NOT_REPORTED")

    def test_all_pass_requirements_are_enforced(self):
        points = self._read_text(
            HEADER
            + "dut,4,resynth,c,0.1,0.1,true,0\n"
            + "dut,3,resynth,c,-0.1,0.1,true,0\n"
            + "dut,2.5,resynth,c,0.1,-0.1,true,0\n"
            + "dut,2,resynth,c,0.1,0.1,false,0\n"
            + "dut,1.5,resynth,c,0.1,0.1,true,1\n"
        )
        self.assertEqual([point.qualified_pass for point in points], [True] + [False] * 4)
        result = bracket_fmax.summarize(points)[0]
        self.assertEqual(result["fmax_bracket_mhz"], "[250, 333.333333)")

    def test_modes_and_corners_are_never_combined(self):
        points = self._read_text(
            HEADER
            + "dut,2,fixed,c1,0,0,true,0\n"
            + "dut,2,resynth,c1,0,0,true,0\n"
            + "dut,2,fixed,c2,0,0,true,0\n"
        )
        results = bracket_fmax.summarize(points)
        self.assertEqual(len(results), 3)
        self.assertEqual(
            {(row["synthesis_mode"], row["corner"]) for row in results},
            {("fixed", "c1"), ("resynth", "c1"), ("fixed", "c2")},
        )

    def test_drc_and_antenna_are_disclosed_but_do_not_change_timing_pass(self):
        points = self._read_text(
            HEADER.rstrip("\n")
            + ",drc_violations,antenna_violations\n"
            + "dut,2,resynth,c,0.01,0.02,true,0,5,2\n"
        )
        result = bracket_fmax.summarize(points)[0]
        self.assertEqual(result["status"], "LOWER_BOUND_ONLY")
        self.assertEqual(result["demonstrated_drc"], "VIOLATIONS:5")
        self.assertEqual(result["demonstrated_antenna"], "VIOLATIONS:2")

    def test_nonmonotonic_sweep_is_flagged(self):
        points = self._read_text(
            HEADER
            + "dut,3,resynth,c,-0.1,0,true,0\n"
            + "dut,2,resynth,c,0.1,0,true,0\n"
        )
        result = bracket_fmax.summarize(points)[0]
        self.assertEqual(result["status"], "NON_MONOTONIC")
        self.assertFalse(result["monotonic"])

    def test_schema_and_duplicate_period_are_rejected(self):
        with self.assertRaisesRegex(bracket_fmax.InputError, "missing columns"):
            self._read_text("candidate,period_ns\ndut,2\n")
        points = self._read_text(
            HEADER
            + "dut,2,resynth,c,0,0,true,0\n"
            + "dut,2,resynth,c,-0.1,0,true,0\n"
        )
        with self.assertRaisesRegex(bracket_fmax.InputError, "duplicate period"):
            bracket_fmax.summarize(points)

    def test_csv_writer(self):
        results = bracket_fmax.summarize(
            bracket_fmax.read_points([self.fixture])
        )
        stream = io.StringIO()
        bracket_fmax.write_csv(results, stream)
        row = next(csv.DictReader(io.StringIO(stream.getvalue())))
        self.assertEqual(row["status"], "BRACKETED")
        self.assertEqual(row["demonstrated_fmax_mhz"], "500")


if __name__ == "__main__":
    unittest.main()
