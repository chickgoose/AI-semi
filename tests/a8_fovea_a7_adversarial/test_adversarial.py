#!/usr/bin/env python3
from __future__ import annotations

import unittest

from mutants import ALL
from oracle import Event, FoveaA7Oracle


class FoveaA7AdversarialTest(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = FoveaA7Oracle()

    def codes(self, events: list[Event]) -> set[str]:
        return {fault.code for fault in self.oracle.check(events).faults}

    def test_masked_held_request_and_continuous_valid_are_legal(self) -> None:
        events = [
            Event(0, "request", 10, 0x5),
            Event(1, "native", 10, 0x5),
            Event(2, "source", 20, 0x2, valid=True, ready=True),
            Event(2, "launch", 20, 0x2),
            Event(3, "source", 21, 0xB, valid=True, ready=True),
            Event(3, "launch", 21, 0xB),
            Event(3, "available", 20, 0x2),
            Event(4, "retire", 20, 0x2),
            Event(4, "available", 21, 0xB),
            Event(5, "retire", 21, 0xB),
            Event(5, "drain", drain_idle=True, launch_fire=False,
                  retire_valid=False),
        ]
        result = self.oracle.check(events)
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.native, ((10, 0x5),))
        self.assertEqual(result.accepted, ((20, 0x2), (21, 0xB)))
        self.assertEqual(result.retired, result.accepted)

    def test_all_named_mutants_are_killed_by_the_expected_guard(self) -> None:
        killed: set[str] = set()
        for name, (make_trace, expected_code) in ALL.items():
            with self.subTest(mutant=name):
                result = self.oracle.check(make_trace())
                codes = {fault.code for fault in result.faults}
                self.assertFalse(result.passed)
                self.assertIn(expected_code, codes)
                killed.add(name)
        self.assertEqual(killed, set(ALL))

    def test_stalled_valid_is_not_an_extra_occurrence(self) -> None:
        events = [
            Event(0, "source", 1, 0xA, valid=True, ready=False),
            Event(1, "source", 1, 0xA, valid=True, ready=False),
            Event(2, "source", 1, 0xA, valid=True, ready=True),
            Event(2, "launch", 1, 0xA),
            Event(3, "available", 1, 0xA),
            Event(4, "retire", 1, 0xA),
        ]
        result = self.oracle.check(events)
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.accepted, ((1, 0xA),))

    def test_reset_aborted_occurrence_cannot_reappear(self) -> None:
        events = [
            Event(0, "source", 1, 0xD, valid=True, ready=True),
            Event(0, "launch", 1, 0xD),
            Event(1, "reset_assert"),
            Event(2, "reset_release"),
            Event(3, "available", 1, 0xD),
        ]
        self.assertIn("STALE_POST_RESET_EVENT", self.codes(events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
