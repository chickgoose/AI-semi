#!/usr/bin/env python3
"""Unit tests for the A6 W4 fixed-pin replay model."""

from __future__ import annotations

import importlib.util
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


MODEL = Path(__file__).parents[1] / "a6_w4_fixed_pin_replay.py"
SPEC = importlib.util.spec_from_file_location("a6_w4_fixed_pin_replay", MODEL)
assert SPEC and SPEC.loader
w4 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = w4
SPEC.loader.exec_module(w4)


def event(cycle: int, sequence: int, address: int) -> object:
    return w4.Event(cycle, sequence, address)


class FixedPinReplayTest(unittest.TestCase):
    def test_frozen_link_contracts(self) -> None:
        self.assertEqual((w4.LINKS["parallel4"].pins,
                          w4.LINKS["parallel4"].fixed_rtl_state_bits), (5, 10))
        self.assertEqual((w4.LINKS["ddr2"].pins,
                          w4.LINKS["ddr2"].fixed_rtl_state_bits), (3, 12))
        self.assertEqual((w4.LINKS["serial1"].pins,
                          w4.LINKS["serial1"].fixed_rtl_state_bits), (2, 16))
        self.assertEqual(
            {name: 4 / spec.periods_per_event for name, spec in w4.LINKS.items()},
            {"parallel4": 4.0, "ddr2": 4.0, "serial1": 2.0},
        )

    def test_exact_order_and_latency_at_equal_clock(self) -> None:
        events = [event(0, 0, 1), event(1, 1, 14)]
        for name in ("parallel4", "ddr2"):
            result = w4.replay(events, suite="unit", run="dense", stim_cycles=2,
                               spec=w4.LINKS[name], link_ratio=1)
            self.assertTrue(result.sequence_exact)
            self.assertEqual(result.delivered, 2)
            self.assertEqual(result.max_latency_core_cycles, 1.0)
            self.assertEqual(result.forwarded_framing_edges, 4)

    def test_ddr_idle_data_mux_cost_is_not_hidden(self) -> None:
        events = [event(0, 0, 6)]
        short = w4.replay(events, suite="unit", run="idle", stim_cycles=1,
                          spec=w4.LINKS["ddr2"], link_ratio=1)
        long = w4.replay(events, suite="unit", run="idle", stim_cycles=4,
                         spec=w4.LINKS["ddr2"], link_ratio=1)
        parallel = w4.replay(events, suite="unit", run="idle", stim_cycles=4,
                             spec=w4.LINKS["parallel4"], link_ratio=1)
        self.assertGreater(long.physical_data_toggles, short.physical_data_toggles)
        self.assertEqual(long.forwarded_framing_edges, 2)
        self.assertEqual(parallel.physical_data_toggles, 2)

    def test_serial1_has_half_service_and_builds_backlog(self) -> None:
        events = [event(cycle, cycle, cycle) for cycle in range(4)]
        ddr = w4.replay(events, suite="unit", run="dense", stim_cycles=4,
                        spec=w4.LINKS["ddr2"], link_ratio=1)
        serial = w4.replay(events, suite="unit", run="dense", stim_cycles=4,
                           spec=w4.LINKS["serial1"], link_ratio=1)
        self.assertEqual(ddr.elapsed_core_cycles, 4.0)
        self.assertEqual(serial.elapsed_core_cycles, 8.0)
        self.assertGreater(serial.required_fifo_depth, ddr.required_fifo_depth)
        self.assertFalse(serial.no_cross_core_backlog_schedule_compatible)

    def test_multihot_requires_endpoint_queue_at_ratio_one(self) -> None:
        events = [event(0, sequence, sequence) for sequence in range(4)]
        result = w4.replay(events, suite="unit", run="multihot", stim_cycles=1,
                           spec=w4.LINKS["ddr2"], link_ratio=1)
        self.assertEqual(result.delivered, 4)
        self.assertEqual(result.required_fifo_depth, 3)
        self.assertFalse(result.no_cross_core_backlog_schedule_compatible)
        self.assertEqual(result.modeled_storage_control_state_bits_lower_bound,
                         12 + 12 + 6)

    def test_fifo_storage_accounting(self) -> None:
        self.assertEqual(w4.fifo_state_bits(0), (0, 0))
        self.assertEqual(w4.fifo_state_bits(1), (4, 3))
        self.assertEqual(w4.fifo_state_bits(15), (60, 12))
        self.assertEqual(w4.fifo_state_bits(5132), (20528, 39))

    def test_trace_mutation_fails_closed(self) -> None:
        original = (b'{"occurrence_cycle":0,"logical_source":3,'
                    b'"tb_only_event_id":"e0"}\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.events.jsonl"
            path.write_bytes(original)
            expected = hashlib.sha256(original).hexdigest()
            self.assertEqual(w4.load_events(path, expected)[0].address, 3)
            path.write_bytes(original.replace(b'"logical_source":3',
                                              b'"logical_source":4'))
            with self.assertRaisesRegex(w4.ReplayError, "trace SHA mismatch"):
                w4.load_events(path, expected)

    def test_duplicate_identity_fails_closed(self) -> None:
        contents = (
            b'{"occurrence_cycle":0,"logical_source":3,"tb_only_event_id":"x"}\n'
            b'{"occurrence_cycle":1,"logical_source":4,"tb_only_event_id":"x"}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.events.jsonl"
            path.write_bytes(contents)
            with self.assertRaisesRegex(w4.ReplayError, "duplicate event identity"):
                w4.load_events(path, hashlib.sha256(contents).hexdigest())


if __name__ == "__main__":
    unittest.main()
