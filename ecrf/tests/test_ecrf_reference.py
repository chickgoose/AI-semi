from __future__ import annotations

import unittest

from ecrf.reference.ecrf_reference import (
    Topology,
    TraceEvent,
    conservative_reaction,
    exhaustive_check,
    hall_counterexample,
    simulate_trace,
    smallest_peeling_stopping_set,
)


class EcrfReferenceTest(unittest.TestCase):
    def test_full_two_cell_topology_reaches_two_grants(self) -> None:
        topology = Topology(
            n=4, k=2, b=2, d=2, seed=0,
            neighbors=(0b11, 0b11, 0b11, 0b11),
        )
        result = conservative_reaction(topology, 0b1111, 2)
        self.assertEqual(2, len(result.matches))
        self.assertEqual(2, len({match.source for match in result.matches}))
        self.assertEqual(2, len({match.cell for match in result.matches}))
        self.assertIsNone(hall_counterexample(topology))

    def test_hall_violation_is_reported(self) -> None:
        topology = Topology(
            n=3, k=2, b=2, d=1, seed=0,
            neighbors=(0b01, 0b01, 0b10),
        )
        counterexample = hall_counterexample(topology)
        self.assertIsNotNone(counterexample)
        self.assertEqual(2, counterexample["source_count"])
        self.assertEqual(1, counterexample["neighbor_count"])

    def test_triangle_is_peeling_stopping_set(self) -> None:
        topology = Topology(
            n=3, k=3, b=3, d=2, seed=0,
            neighbors=(0b011, 0b110, 0b101),
        )
        stopping = smallest_peeling_stopping_set(topology)
        self.assertIsNotNone(stopping)
        self.assertEqual(3, stopping["size"])

    def test_small_exhaustive_preserves_p_invariant(self) -> None:
        topology = Topology(
            n=4, k=2, b=2, d=2, seed=0,
            neighbors=(0b11, 0b11, 0b11, 0b11),
        )
        result = exhaustive_check(topology)
        self.assertEqual(0, result["counters"]["p_invariant"])
        self.assertEqual(0, result["counters"]["capacity_failure"])
        self.assertEqual((1 << 4) * (1 << 2), result["counters"]["cases"])

    def test_trace_replay_is_exact(self) -> None:
        topology = Topology(
            n=16, k=2, b=2, d=2, seed=0,
            neighbors=tuple([0b11] * 16),
        )
        events = [
            TraceEvent(0, 0, 1), TraceEvent(1, 1, 1),
            TraceEvent(2, 0, 3), TraceEvent(3, 2, 3),
        ]
        flat = simulate_trace(events, 8, 2, None)
        ecrf = simulate_trace(events, 8, 2, topology)
        self.assertEqual(flat["accepted"], ecrf["accepted"])
        self.assertEqual(flat["delivered"], ecrf["delivered"])
        self.assertEqual(0, ecrf["source_overrun"])


if __name__ == "__main__":
    unittest.main()
