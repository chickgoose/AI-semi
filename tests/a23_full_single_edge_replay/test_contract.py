#!/usr/bin/env python3
"""Static and fail-closed tests for the independent single-edge replay."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]


class ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pins = json.loads((PACKAGE / "pins.json").read_text(encoding="utf-8"))

    def test_independent_scope_and_exact_campaign(self) -> None:
        runner = (PACKAGE / "run_replay.py").read_text(encoding="utf-8")
        self.assertEqual(set(self.pins["owners"]), {"a2", "a3"})
        self.assertEqual(tuple(self.pins["mutations"]["a2"]),
                         ("drop", "duplicate", "reorder", "reset_escape"))
        self.assertEqual(tuple(self.pins["mutations"]["a3"]),
                         ("drop", "duplicate", "reorder", "reset_escape"))
        self.assertIn('"full50_actual_RTL_executions": 100', runner)
        self.assertIn('"receipt_only_executions": 0', runner)
        self.assertNotIn("EXPECTED_FULL50", runner)
        self.assertNotIn("capacity22", runner.lower())

    def test_no_p6_rtl_or_receipt_dependency(self) -> None:
        serialized = json.dumps(self.pins, sort_keys=True).lower()
        self.assertNotIn("p6", serialized)
        self.assertNotIn("a23_full_p6_replay", serialized)
        for config in self.pins["owners"].values():
            self.assertIn("single_edge", config["top"])
            self.assertIn("single_edge", config["filelist"])

    def test_missing_integration_never_passes(self) -> None:
        missing = [
            config["top"] for config in self.pins["owners"].values()
            if not (PROJECT / config["top"]).is_file()
        ]
        if not missing:
            self.skipTest("actual single-edge RTL is integrated")
        process = subprocess.run(
            [sys.executable, str(PACKAGE / "run_replay.py"), "--preflight",
             "--allow-dirty"],
            cwd=PROJECT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(process.returncode, 3, process.stdout)
        self.assertIn("A23_FULL_SINGLE_EDGE_HOLD_NOT_RUN", process.stdout)
        self.assertNotIn("REPLAY_PASS", process.stdout)
        self.assertFalse((PACKAGE / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
