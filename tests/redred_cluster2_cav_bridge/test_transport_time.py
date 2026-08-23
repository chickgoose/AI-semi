from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest

from benchmarks.redred_cluster2_cav_bridge.transport_time import (
    DualTimeEvent,
    MAX_NATIVE_CYCLE,
    MAX_SERIALIZED_TIMESTAMP_NS,
    TRANSPORT_TIME_SEMANTICS,
    TransportTimeValidationError,
    build_dual_time_event,
    validate_dual_time_event,
)


class DualTimeEventTests(unittest.TestCase):
    def test_preserves_source_time_and_cycles_while_injecting_only_latency(self):
        record = build_dual_time_event(
            event_timestamp_ns=43_321_000_000,
            occurrence_cycle=500_000,
            retire_cycle=500_007,
            clock_period_ps=2_000,
        )
        self.assertEqual(record.event_timestamp_ns, 43_321_000_000)
        self.assertEqual(record.occurrence_cycle, 500_000)
        self.assertEqual(record.retire_cycle, 500_007)
        self.assertEqual(record.latency_cycles, 7)
        self.assertEqual(record.clock_ns, 2)
        self.assertEqual(record.latency_ns, 14)
        self.assertEqual(record.latency_injected_timestamp_ns, 43_321_000_014)
        self.assertFalse(hasattr(record, "derived_retire_timestamp_ns"))
        self.assertEqual(record.semantics_label, TRANSPORT_TIME_SEMANTICS)
        self.assertIs(validate_dual_time_event(record), record)

    def test_zero_latency_keeps_both_times_distinct_and_equal_in_value(self):
        record = build_dual_time_event(1_000_000, 900_000, 900_000, 2_000)
        self.assertEqual(record.event_timestamp_ns, 1_000_000)
        self.assertEqual(record.occurrence_cycle, 900_000)
        self.assertEqual(record.retire_cycle, 900_000)
        self.assertEqual(record.latency_cycles, 0)
        self.assertEqual(record.latency_ns, 0)
        self.assertEqual(record.latency_injected_timestamp_ns, 1_000_000)

    def test_record_is_frozen(self):
        record = build_dual_time_event(10, 2, 3, 1_000)
        with self.assertRaises(FrozenInstanceError):
            record.event_timestamp_ns = 11

    def test_rejects_fractional_nanosecond_and_invalid_clock(self):
        for clock_period_ps in (0, 1, 999, 1_500, -1, True):
            with self.subTest(clock_period_ps=clock_period_ps):
                with self.assertRaises(TransportTimeValidationError):
                    build_dual_time_event(10, 2, 3, clock_period_ps)

    def test_rejects_retirement_before_occurrence(self):
        with self.assertRaisesRegex(
            TransportTimeValidationError, "must not precede"
        ):
            build_dual_time_event(10, 4, 3, 2_000)

    def test_rejects_non_integer_and_negative_source_or_cycle_values(self):
        mutations = (
            (True, 1, 2),
            (1.0, 1, 2),
            (-1, 1, 2),
            (1, True, 2),
            (1, -1, 2),
            (1, 1, True),
            (1, 1, -1),
        )
        for timestamp, occurrence, retire in mutations:
            with self.subTest(
                timestamp=timestamp, occurrence=occurrence, retire=retire
            ):
                with self.assertRaises(TransportTimeValidationError):
                    build_dual_time_event(timestamp, occurrence, retire, 2_000)

    def test_mutations_of_each_derived_contract_field_fail_closed(self):
        record = build_dual_time_event(1_000_000, 20, 25, 2_000)
        mutations = (
            {"latency_cycles": 4},
            {"latency_ns": 9},
            {"latency_injected_timestamp_ns": 1_000_009},
            {"semantics_label": "PHYSICAL_REPLAY"},
            {"retire_cycle": 24},
            {"occurrence_cycle": 21},
            {"clock_period_ps": 3_000},
            {"event_timestamp_ns": 999_999},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(TransportTimeValidationError):
                    replace(record, **mutation)

    def test_direct_constructor_cannot_smuggle_physical_cycle_replay(self):
        with self.assertRaisesRegex(
            TransportTimeValidationError, "inject only transport latency"
        ):
            DualTimeEvent(
                event_timestamp_ns=1_000_000,
                occurrence_cycle=500_000,
                retire_cycle=500_005,
                clock_period_ps=2_000,
                latency_cycles=5,
                latency_ns=10,
                latency_injected_timestamp_ns=2_000_010,
                semantics_label=TRANSPORT_TIME_SEMANTICS,
            )

    def test_old_physical_derived_name_is_not_part_of_the_api(self):
        record = build_dual_time_event(10, 2, 3, 1_000)
        self.assertNotIn("derived_retire_timestamp_ns", DualTimeEvent.__annotations__)
        self.assertFalse(hasattr(record, "derived_retire_timestamp_ns"))
        with self.assertRaises(TypeError):
            DualTimeEvent(
                event_timestamp_ns=10,
                occurrence_cycle=2,
                retire_cycle=3,
                clock_period_ps=1_000,
                latency_cycles=1,
                latency_ns=1,
                latency_injected_timestamp_ns=11,
                derived_retire_timestamp_ns=11,
            )

    def test_accepts_exact_native_cycle_and_serialized_timestamp_limits(self):
        record = build_dual_time_event(
            event_timestamp_ns=1,
            occurrence_cycle=0,
            retire_cycle=MAX_NATIVE_CYCLE,
            clock_period_ps=2_000,
        )
        self.assertEqual(record.latency_cycles, MAX_NATIVE_CYCLE)
        self.assertEqual(record.latency_ns, MAX_SERIALIZED_TIMESTAMP_NS - 1)
        self.assertEqual(
            record.latency_injected_timestamp_ns,
            MAX_SERIALIZED_TIMESTAMP_NS,
        )

    def test_rejects_native_cycle_and_serialized_timestamp_overflow(self):
        invalid_builds = (
            (MAX_SERIALIZED_TIMESTAMP_NS + 1, 0, 0, 1_000),
            (0, MAX_NATIVE_CYCLE + 1, MAX_NATIVE_CYCLE + 1, 1_000),
            (0, 0, MAX_NATIVE_CYCLE + 1, 1_000),
            (MAX_SERIALIZED_TIMESTAMP_NS, 0, 1, 1_000),
            (0, 0, MAX_NATIVE_CYCLE, 3_000),
        )
        for arguments in invalid_builds:
            with self.subTest(arguments=arguments):
                with self.assertRaises(TransportTimeValidationError):
                    build_dual_time_event(*arguments)

    def test_overflow_mutations_fail_closed(self):
        record = build_dual_time_event(1, 0, 1, 1_000)
        mutations = (
            {"event_timestamp_ns": MAX_SERIALIZED_TIMESTAMP_NS + 1},
            {"occurrence_cycle": MAX_NATIVE_CYCLE + 1},
            {"retire_cycle": MAX_NATIVE_CYCLE + 1},
            {"latency_cycles": MAX_NATIVE_CYCLE + 1},
            {"latency_ns": MAX_SERIALIZED_TIMESTAMP_NS + 1},
            {"latency_injected_timestamp_ns": MAX_SERIALIZED_TIMESTAMP_NS + 1},
            {"event_timestamp_ns": MAX_SERIALIZED_TIMESTAMP_NS},
            {"clock_period_ps": (MAX_SERIALIZED_TIMESTAMP_NS + 1) * 1_000},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(TransportTimeValidationError):
                    replace(record, **mutation)

    def test_validator_rejects_non_record(self):
        with self.assertRaisesRegex(TransportTimeValidationError, "exact"):
            validate_dual_time_event({"event_timestamp_ns": 10})

    def test_module_is_python38_syntax_and_imports_no_evaluator(self):
        module_path = (
            Path(__file__).resolve().parents[2]
            / "benchmarks"
            / "redred_cluster2_cav_bridge"
            / "transport_time.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path), feature_version=(3, 8))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.append(node.module)
        self.assertFalse(any("evaluat" in name.lower() for name in imports))


if __name__ == "__main__":
    unittest.main()
