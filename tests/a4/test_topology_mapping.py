#!/usr/bin/env python3
"""Unit and equivalence tests for the candidate-only mapping study model."""

from __future__ import annotations

import unittest

from quadtree_reference import A4Model, run_trace as run_fixed_trace
from topology_mapping_model import (
    Event,
    Radix4Tree,
    ceil_power_of_four,
    clone_trace,
    named_mapping,
    run_trace,
)


class TopologyMappingTest(unittest.TestCase):
    def test_identity_reproduces_frozen_n16_model(self) -> None:
        generic_events = [
            Event(event_id, (3 * cycle + event_id) % 16, cycle)
            for event_id, cycle in enumerate(range(0, 96, 3))
        ]
        fixed_events = [
            {"tb_only_event_id": event.event_id, "logical_source": event.source,
             "occurrence_cycle": event.occurrence, "deadline": event.occurrence + 64}
            for event in generic_events
        ]
        metadata = {"run": {"name": "equivalence", "workload": "unit", "seed": 0,
                             "load": 1 / 3, "stim_cycles": 100},
                    "report_group": "unit", "trace_sha256": "candidate-only"}
        fixed = run_fixed_trace(A4Model, fixed_events, metadata)
        generic = run_trace(16, named_mapping("identity", 16),
                            clone_trace(generic_events), 100)
        self.assertEqual(fixed["accepted"], generic["accepted"])
        self.assertEqual(fixed["source_overrun"], generic["overrun"])
        self.assertEqual(fixed["p99_e2e_latency"], generic["event_p99_latency"])
        self.assertEqual(fixed["max_request_wait"], generic["max_request_wait"])

    def test_all_simultaneous_events_are_preserved(self) -> None:
        trace = [Event(source, source, 0, 0) for source in range(16)]
        result = run_trace(16, named_mapping("identity", 16), trace, 1)
        self.assertEqual(16, result["accepted"])
        self.assertEqual(0, result["overrun"])
        self.assertLessEqual(result["max_request_wait"], 15)

    def test_padding_ports_never_create_events(self) -> None:
        n = 18
        mapping = named_mapping("bit_reversed", n)
        self.assertEqual(n, len(set(mapping)))
        self.assertLess(max(mapping), ceil_power_of_four(n))
        trace = [Event(source, source, source % 3) for source in range(n)]
        result = run_trace(n, mapping, trace, 4)
        self.assertEqual(n, result["accepted"])
        self.assertEqual(0, result["overrun"])

    def test_mapping_and_link_invariants(self) -> None:
        weights = list(range(16))
        for name in ("identity", "interleaved", "bit_reversed",
                     "placement_best", "placement_worst"):
            mapping = named_mapping(name, 16, weights)
            self.assertEqual(list(range(16)), sorted(mapping))
        model = Radix4Tree(64, named_mapping("identity", 64))
        pending = [None] * 64
        for cycle in range(128):
            source = cycle % 64
            pending[source] = Event(cycle, source, cycle)
            accepted, _ = model.step(pending)
            for source in accepted:
                pending[source] = None
        for value in model.utilization_metrics(model.cycles).values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()
