#!/usr/bin/env python3
from __future__ import annotations

import unittest

from live_trace_monitor import LiveTraceMonitor, Observation, legal_frame


class LiveTraceMonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = LiveTraceMonitor()

    def codes(self, trace: list[Observation]) -> set[str]:
        return {fault.code for fault in self.monitor.check(trace).faults}

    def test_legal_single_event(self) -> None:
        result = self.monitor.check(legal_frame(0, 1, 0x9))
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.accepted, ((1, 0x9),))
        self.assertEqual(result.delivered, ((1, 0x9),))

    def test_continuous_valid_changing_address_is_legal_back_to_back(self) -> None:
        trace = [
            Observation(0, "source", 1, 0x1, True, True, True),
            Observation(1, "source", 2, 0xE, True, True, True),
            Observation(2, "launch", 1), Observation(3, "rise", data=1),
            Observation(4, "fall", data=0), Observation(5, "observer_publish", 1, 0x1),
            Observation(6, "sink_sample", 1, 0x1),
            Observation(6, "launch", 2), Observation(7, "rise", data=2),
            Observation(8, "fall", data=3), Observation(9, "observer_publish", 2, 0xE),
            Observation(10, "sink_sample", 2, 0xE),
        ]
        result = self.monitor.check(trace)
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.accepted, ((1, 0x1), (2, 0xE)))

    def test_stalled_valid_must_hold_then_accept_once(self) -> None:
        trace = [
            Observation(0, "source", 4, 0xA, True, False, False),
            Observation(1, "source", 4, 0xA, True, False, False),
            Observation(2, "source", 4, 0xA, True, True, True),
            Observation(3, "launch", 4), Observation(4, "rise", data=2),
            Observation(5, "fall", data=2), Observation(6, "observer_publish", 4, 0xA),
            Observation(7, "sink_sample", 4, 0xA),
        ]
        result = self.monitor.check(trace)
        self.assertTrue(result.passed, result.faults)
        self.assertEqual(result.accepted, ((4, 0xA),))

    def test_stalled_valid_data_change_is_rejected(self) -> None:
        trace = [
            Observation(0, "source", 4, 0xA, True, False, False),
            Observation(1, "source", 5, 0xB, True, False, False),
        ]
        self.assertIn("STALL_DATA_CHANGED", self.codes(trace))

    def test_duplicate_launch_under_one_handshake_is_rejected(self) -> None:
        trace = legal_frame(0, 1, 0x9)
        trace.insert(2, Observation(1, "launch", 1))
        self.assertIn("DUPLICATE_OR_PHANTOM_LAUNCH", self.codes(trace))

    def test_missing_and_extra_edges_are_rejected(self) -> None:
        missing = legal_frame(0, 1, 0x9)
        del missing[3]
        self.assertIn("MISSING_FALL", self.codes(missing))
        extra = legal_frame(0, 1, 0x9)
        extra.append(Observation(6, "fall", data=0))
        self.assertIn("EXTRA_FALL", self.codes(extra))

    def test_rise_over_open_frame_is_rejected(self) -> None:
        trace = legal_frame(0, 1, 0x9)
        trace.insert(3, Observation(2, "rise", data=1))
        self.assertIn("RISE_OVER_OPEN_FRAME", self.codes(trace))

    def test_wrong_half_ordering_is_rejected(self) -> None:
        trace = legal_frame(0, 1, 0x9)
        trace[2] = Observation(2, "rise", data=2)
        trace[3] = Observation(3, "fall", data=1)
        codes = self.codes(trace)
        self.assertIn("WRONG_LOW_HALF", codes)
        self.assertIn("WRONG_HIGH_HALF", codes)

    def test_unstable_data_is_rejected(self) -> None:
        trace = legal_frame(0, 1, 0x9)
        trace[2] = Observation(2, "rise", data=None, stable=False)
        self.assertIn("UNSTABLE_RISE_DATA", self.codes(trace))

    def test_reset_in_flight_and_stale_post_reset_event_are_rejected(self) -> None:
        trace = [
            Observation(0, "source", 1, 0x9, True, True, True),
            Observation(1, "launch", 1), Observation(2, "rise", data=1),
            Observation(3, "reset_assert"), Observation(4, "reset_release"),
            Observation(5, "sink_sample", 1, 0x9),
        ]
        codes = self.codes(trace)
        self.assertIn("RESET_IN_FLIGHT", codes)
        self.assertIn("STALE_POST_RESET_EVENT", codes)

    def test_cdc_style_duplicate_at_sync_observer_is_rejected(self) -> None:
        # This mutation is injected at the charged phase-related ref observer;
        # it is not a claim that the primary endpoint contains a 2FF CDC.
        trace = legal_frame(0, 1, 0x9)
        trace.append(Observation(7, "sink_sample", 1, 0x9))
        self.assertIn("SINK_DUPLICATE_OR_PHANTOM", self.codes(trace))

    def test_cdc_style_drop_at_sync_observer_is_rejected(self) -> None:
        trace = legal_frame(0, 1, 0x9)[:-1]
        self.assertIn("SINK_DROP", self.codes(trace))

    def test_drain_idle_must_cover_launch_and_registered_output(self) -> None:
        launch = Observation(0, "drain", drain_idle=True,
                             retire_valid=False, launch_fire=True)
        pending_output = Observation(1, "drain", drain_idle=True,
                                     retire_valid=True, launch_fire=False)
        self.assertIn("FALSE_DRAIN_IDLE", self.codes([launch]))
        self.assertIn("FALSE_DRAIN_IDLE", self.codes([pending_output]))

    def test_false_ready_is_rejected(self) -> None:
        trace = [Observation(0, "source", 1, 0x9, True, True, False)]
        self.assertIn("FALSE_READY", self.codes(trace))


if __name__ == "__main__":
    unittest.main(verbosity=2)
