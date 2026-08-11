#!/usr/bin/env python3

from __future__ import annotations

import itertools
import unittest

from lane_price_model import (
    Event,
    ExactKGrant,
    FlatRoundRobin,
    LanePriceMatcher,
    adjacency,
    legal_lanes,
)


class LanePriceModelTest(unittest.TestCase):
    def test_legal_graph_is_balanced_and_degree_two(self) -> None:
        for sources, lanes in ((16, 4), (64, 8)):
            graph = adjacency(sources, lanes)
            self.assertEqual(sum(map(len, graph)), 2 * sources)
            self.assertLessEqual(max(map(len, graph)) - min(map(len, graph)), 2)
            for source in range(sources):
                self.assertEqual(len(set(legal_lanes(source, sources, lanes))), 2)

    def test_simultaneous_matching_is_exact(self) -> None:
        fabric = LanePriceMatcher(16, 4)
        fabric.step([Event(0, source, source) for source in range(16)], [True] * 4)
        self.assertLessEqual(sum(map(len, fabric.queues)), 4)
        queued = [event.source for queue in fabric.queues for event in queue]
        self.assertEqual(len(queued), len(set(queued)))
        for lane, queue in enumerate(fabric.queues):
            for event in queue:
                self.assertIn(lane, legal_lanes(event.source, 16, 4))

    def test_route_lock_preserves_source_order(self) -> None:
        fabric = LanePriceMatcher(4, 2)
        rows = {
            cycle: [Event(cycle, cycle, 0)] for cycle in range(8)
        }
        fabric.run(rows, 8)
        self.assertEqual(fabric.metrics.delivered, fabric.metrics.accepted)
        self.assertEqual(fabric.metrics.overrun, 0)

    def test_price_only_observes_occupancy_and_stall(self) -> None:
        fabric = LanePriceMatcher(4, 2, price_bits=2)
        fabric.queues[0].append(Event(0, 0, 0))
        fabric.outstanding[0] = 1
        fabric.route_lock[0] = 0
        fabric.metrics.generated = 1
        fabric.metrics.accepted = 1
        fabric.metrics.accepted_by_source[0] = 1
        fabric.generated_by_source[0] = 1
        fabric.step([], [False, True])
        self.assertEqual(fabric.prices[0], 1)
        self.assertEqual(fabric.prices[1], 0)
        fabric.step([], [True, True])
        fabric.step([], [True, True])
        self.assertEqual(fabric.prices, [0, 0])

    def test_bounded_escape_under_always_ready_contention(self) -> None:
        fabric = LanePriceMatcher(16, 4, reject_bits=2)
        events = {0: [Event(0, source, source) for source in range(16)]}
        metrics = fabric.run(events, 1)
        self.assertEqual(metrics.delivered, 16)
        local_degree = max(map(len, fabric.incoming))
        # reject_max normal attempts, one escape-entry edge, then at most one
        # local adjacency rotation per ready service opportunity.
        self.assertLessEqual(metrics.max_escape_wait, local_degree)

    def test_alternating_lane_stalls_drain(self) -> None:
        fabric = LanePriceMatcher(16, 4)
        events = {
            cycle: [Event(cycle, cycle * 4 + source, source) for source in range(4)]
            for cycle in range(24)
        }
        ready = lambda cycle: [((cycle + lane) & 1) == 0 for lane in range(4)]
        metrics = fabric.run(events, 24, ready_fn=ready)
        self.assertEqual(metrics.accepted, metrics.delivered)
        self.assertGreater(metrics.price_updates, 0)

    def test_all_same_cheapest_uses_escape(self) -> None:
        fabric = LanePriceMatcher(16, 4, reject_bits=1)
        fabric.prices = [0, 7, 7, 7]
        sources = [source for source in range(16) if 0 in legal_lanes(source, 16, 4)]
        events = {0: [Event(0, index, source) for index, source in enumerate(sources)]}
        metrics = fabric.run(events, 1)
        self.assertEqual(metrics.delivered, len(sources))
        self.assertGreater(metrics.escape_entries, 0)
        self.assertLessEqual(metrics.max_escape_wait, max(map(len, fabric.incoming)))

    def test_price_oscillation_has_no_alternating_stall_gain(self) -> None:
        events = {
            cycle: [Event(cycle, cycle * 4 + source, source) for source in range(4)]
            for cycle in range(128)
        }
        ready = lambda cycle: [((cycle + lane) & 1) == 0 for lane in range(4)]
        enabled = LanePriceMatcher(16, 4, price_enabled=True)
        disabled = LanePriceMatcher(16, 4, price_enabled=False)
        enabled.run(events, 128, ready_fn=ready)
        disabled.run(events, 128, ready_fn=ready)
        self.assertGreater(enabled.metrics.price_bit_toggles, 0)
        self.assertEqual(enabled.metrics.delivered, disabled.metrics.delivered)
        self.assertEqual(
            enabled.metrics.percentile(enabled.metrics.occurrence_latencies, 99),
            disabled.metrics.percentile(disabled.metrics.occurrence_latencies, 99),
        )

    def test_locked_route_has_adaptive_stall_starvation_counterexample(self) -> None:
        fabric = LanePriceMatcher(4, 2, price_bits=2)
        # Source zero initially selects lane zero.  Keeping that selected lane
        # stalled while its other fixed legal lane is ready strands the accepted
        # head: price saturation cannot override the source-order route lock.
        fabric.step([Event(0, 0, 0)], [False, True])
        self.assertEqual(fabric.route_lock[0], 0)
        for _ in range(16):
            fabric.step([], [False, True])
        self.assertEqual(fabric.metrics.delivered, 0)
        self.assertEqual(fabric.prices[0], fabric.price_max)
        self.assertTrue(fabric.queues[0])
        fabric.step([], [True, True])
        self.assertEqual(fabric.metrics.delivered, 1)

    def test_exhaustive_n4_conservation_and_drain(self) -> None:
        for pending_mask, q0, q1, prices, cursor0, cursor1 in itertools.product(
            range(16), range(3), range(3), range(16), range(4), range(4)
        ):
            fabric = LanePriceMatcher(4, 2, lane_depth=2, price_bits=2, reject_bits=1)
            event_id = 0
            for source in range(4):
                if pending_mask & (1 << source):
                    fabric.pending[source] = Event(0, event_id, source)
                    fabric.metrics.generated += 1
                    fabric.generated_by_source[source] += 1
                    event_id += 1
            for lane, occupancy in enumerate((q0, q1)):
                for _ in range(occupancy):
                    source = lane
                    fabric.queues[lane].append(Event(0, event_id, source))
                    fabric.metrics.generated += 1
                    fabric.metrics.accepted += 1
                    fabric.metrics.accepted_by_source[source] += 1
                    fabric.generated_by_source[source] += 1
                    fabric.outstanding[source] += 1
                    fabric.route_lock[source] = lane
                    event_id += 1
            fabric.prices = [prices & 3, (prices >> 2) & 3]
            fabric.tie_cursor = [cursor0, cursor1]
            fabric._check_conservation()
            for _ in range(32):
                if fabric.drained():
                    break
                fabric.step([], [True, True], measured=False)
            self.assertTrue(fabric.drained())

    def test_reference_models_conserve(self) -> None:
        events = {
            cycle: [Event(cycle, cycle * 4 + source, source) for source in range(4)]
            for cycle in range(16)
        }
        for model in (ExactKGrant, FlatRoundRobin):
            fabric = model(16, 4)
            metrics = fabric.run(events, 16)
            self.assertEqual(metrics.accepted, metrics.delivered)


if __name__ == "__main__":
    unittest.main()
