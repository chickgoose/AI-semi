#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from run_actual_owner_mutations import AuditFailure, replace_once, validate_outcome


class FailClosedHelpersTest(unittest.TestCase):
    def test_replace_once_rejects_missing_or_ambiguous_anchor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "source.sv"
            path.write_text("alpha alpha\n", encoding="utf-8")
            with self.assertRaises(AuditFailure):
                replace_once(path, "alpha", "beta")
            with self.assertRaises(AuditFailure):
                replace_once(path, "missing", "beta")

    def test_replace_once_changes_exactly_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "source.sv"
            path.write_text("alpha\n", encoding="utf-8")
            replace_once(path, "alpha", "beta")
            self.assertEqual(path.read_text(encoding="utf-8"), "beta\n")

    def test_baseline_requires_zero_and_exact_pass_sentinel(self) -> None:
        validate_outcome("baseline", 0, True, True)
        for rc, sentinel, markers in (
            (1, True, True), (0, False, True), (1, False, True),
            (0, True, False),
        ):
            with self.subTest(rc=rc, sentinel=sentinel, markers=markers):
                with self.assertRaises(AuditFailure):
                    validate_outcome("baseline", rc, sentinel, markers)

    def test_mutant_rejects_rc_zero_or_pass_sentinel(self) -> None:
        validate_outcome("premature_drain", 134, False, True)
        for rc, sentinel, diagnostic in (
            (0, False, True), (134, True, True), (0, True, True),
            (134, False, False),
        ):
            with self.subTest(rc=rc, sentinel=sentinel, diagnostic=diagnostic):
                with self.assertRaises(AuditFailure):
                    validate_outcome("mutant", rc, sentinel, diagnostic)


if __name__ == "__main__":
    unittest.main(verbosity=2)
