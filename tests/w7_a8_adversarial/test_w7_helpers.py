#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from run_w7_audit import AuditError, mutate_csv


class MutationHelperTest(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        path = root / "events.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["logical_source", "delivery_cycle"])
            writer.writeheader()
            writer.writerow({"logical_source": "3", "delivery_cycle": "9"})
        return path

    def test_duplicate_is_exact(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = self.fixture(Path(directory))
            mutate_csv(path, "duplicate")
            with path.open(newline="", encoding="utf-8") as stream:
                self.assertEqual(2, len(list(csv.DictReader(stream))))

    def test_timing_and_address_mutations_are_distinct(self) -> None:
        for mutation, field, expected in (
            ("timing", "delivery_cycle", "10"),
            ("swapped_address", "logical_source", "4"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(dir="/tmp") as directory:
                path = self.fixture(Path(directory))
                mutate_csv(path, mutation)
                with path.open(newline="", encoding="utf-8") as stream:
                    self.assertEqual(expected, next(csv.DictReader(stream))[field])

    def test_unknown_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            with self.assertRaises(AuditError):
                mutate_csv(self.fixture(Path(directory)), "unknown")

    def test_hold_is_not_go_sentinel(self) -> None:
        self.assertNotEqual("W7_A8_ADVERSARIAL_HOLD",
                            "W7_A8_ADVERSARIAL_GO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
