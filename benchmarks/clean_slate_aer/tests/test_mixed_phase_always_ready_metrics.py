import csv
import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mixed_phase_always_ready_metrics as mixed_metrics


MASK64 = (1 << 64) - 1


class SplitMix64:
    """The deterministic PRNG used by generator-v4."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
        return (value ^ (value >> 31)) & MASK64

    def probability(self, probability: Decimal) -> bool:
        return self.next_u64() < int(probability * Decimal(1 << 64))

    def randbelow(self, bound: int) -> int:
        limit = (1 << 64) - ((1 << 64) % bound)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % bound


class A3CompatibleGeneratedFixture:
    """Generate the full generator-v4 4x4/4096 mixed trace, not a toy trace."""

    event_fields = (
        "candidate", "test", "seed", "load_pct", "tb_only_event_id",
        "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
        "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
    )
    summary_fields = (
        "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
        "source_overrun", "accepted", "delivered", "errors", "total_cycles",
        "avg_e2e_latency", "max_e2e_latency", "avg_internal_latency",
        "max_internal_latency", "throughput", "fairness", "max_request_wait",
        "avg_timing_error", "max_timing_error",
    )

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.trace_path = directory / "mixed_phase_always_ready_identity.events.jsonl"
        self.manifest_path = directory / "mixed_phase_always_ready_identity.manifest.json"
        self.event_path = directory / "mixed.events.csv"
        self.summary_path = directory / "mixed.summary.csv"
        self.trace = self._generate_trace()
        self.write_generated_artifacts()
        self.write_events()
        self.write_summary()

    @staticmethod
    def _generate_trace() -> list[dict[str, object]]:
        rng = SplitMix64(4001)
        raw_events: list[tuple[int, int]] = []

        uniform_counts = [0] * 16
        for cycle in range(640):
            for source in range(16):
                if rng.probability(Decimal("0.125")):
                    raw_events.append((cycle, source))
                    uniform_counts[source] += 1
        for source, count in enumerate(uniform_counts):
            for occurrence in range(count):
                raw_events.append((640 + (occurrence * 640) // count, source))

        for relative_cycle in range(256):
            persistent_column = relative_cycle // 64
            rotating_column = relative_cycle % 4
            for row in range(4):
                raw_events.append((1280 + relative_cycle, row * 4 + persistent_column))
                raw_events.append((1536 + relative_cycle, row * 4 + rotating_column))

        map_a = [5, 6, 9, 10] + [source for source in range(16) if source not in {5, 6, 9, 10}]
        map_b = [0, 5, 10, 15] + [source for source in range(16) if source not in {0, 5, 10, 15}]
        for relative_cycle in range(768):
            hot = list(range(4))
            cold = list(range(4, 16))
            selected = []
            for _ in range(2):
                choose_hot = rng.probability(Decimal("0.8"))
                pool = hot if choose_hot else cold
                if not pool:
                    pool = cold if choose_hot else hot
                selected.append(pool.pop(rng.randbelow(len(pool))))
            for rank in selected:
                raw_events.append((1792 + relative_cycle, map_a[rank]))
                raw_events.append((2560 + relative_cycle, map_b[rank]))
                raw_events.append((3328 + relative_cycle, map_a[rank]))

        raw_events.sort(key=lambda item: item[0])
        return [
            {
                "occurrence_cycle": cycle,
                "tb_only_event_id": event_id,
                "logical_source": source,
                "x": source % 4,
                "y": source // 4,
                "polarity": 1,
                "event_type": "spike",
                "relation_id": None,
                "relation_role": None,
                "deadline": cycle + 32,
            }
            for event_id, (cycle, source) in enumerate(raw_events)
        ]

    def write_generated_artifacts(self) -> None:
        payload = "".join(
            json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
            for row in self.trace
        )
        self.trace_path.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(self.trace_path.read_bytes()).hexdigest()
        metadata = {
            "schema_version": 1,
            "generator_version": "4.0",
            "run": {
                "name": "mixed_phase_always_ready_identity",
                "workload": "mixed_phase_always_ready",
                "seed": 4001,
                "geometry": {"width": 4, "height": 4},
                "load": "2.25",
                "stim_cycles": 4096,
                "parameters": {
                    "uniform_source_probability": 0.125,
                    "hot_probability": 0.8,
                    "fixed_polarity": 1,
                    "fixed_event_type": "spike",
                },
                "sink": {"mode": "always"},
            },
            "report_group": "mixed_phase_always_ready",
            "declared_mean_load": "2.25",
            "actual_mean_load": str(Decimal(len(self.trace)) / Decimal(4096)),
            "peak_events_per_cycle": max(Counter(
                int(row["occurrence_cycle"]) for row in self.trace
            ).values()),
            "trace_file": self.trace_path.name,
            "trace_sha256": digest,
            "event_count": len(self.trace),
            "event_schema": [
                "occurrence_cycle", "tb_only_event_id", "logical_source", "x", "y",
                "polarity", "event_type", "relation_id", "relation_role", "deadline",
            ],
            "event_identity_mode": "address_only",
            "dut_address_fields": ["logical_source"],
            "dut_payload_fields": [],
            "dut_sideband_fields": ["logical_source"],
            "trace_metadata_fields": ["x", "y", "polarity", "event_type"],
            "logical_source_permutation": list(range(16)),
            "tb_only_fields": [
                "occurrence_cycle", "tb_only_event_id", "relation_id",
                "relation_role", "deadline",
            ],
            "generation_contract": "trace_is_fully_generated_before_any_DUT_ready_is_observed",
        }
        self.manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    def write_events(self, *, censored_id: int | None = None, overrun_id: int | None = None) -> None:
        with self.event_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.event_fields)
            writer.writeheader()
            for row in self.trace:
                event_id = int(row["tb_only_event_id"])
                occurrence = int(row["occurrence_cycle"]) + 5
                state = "delivered"
                accept: int | str = occurrence + 1
                delivery: int | str = occurrence + 2
                if event_id == overrun_id:
                    state, accept, delivery = "source_overrun", "", ""
                if event_id == censored_id:
                    state, accept, delivery = "accepted", occurrence + 1, ""
                writer.writerow({
                    "candidate": "fixture",
                    "test": "mixed_phase_always_ready",
                    "seed": "4001",
                    "load_pct": "225",
                    "tb_only_event_id": event_id,
                    "logical_source": row["logical_source"],
                    "source_count": 16,
                    "occurrence_cycle": occurrence,
                    "accept_cycle": accept,
                    "delivery_cycle": delivery,
                    "deadline_cycle": int(row["deadline"]) + 5,
                    "observation_end_cycle": 5000,
                    "event_state": state,
                })

    def write_summary(self, *, errors: int = 0, generated_delta: int = 0,
                      overrun: int = 0) -> None:
        generated = len(self.trace) + generated_delta
        accepted = len(self.trace) - overrun
        with self.summary_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.summary_fields)
            writer.writeheader()
            writer.writerow({
                "candidate": "fixture", "test": "mixed_phase_always_ready",
                "seed": "4001", "load_pct": "225", "stim_cycles": 4096,
                "generated": generated, "source_overrun": overrun,
                "accepted": accepted, "delivered": accepted, "errors": errors,
                "total_cycles": 5000, "avg_e2e_latency": 2,
                "max_e2e_latency": 2, "avg_internal_latency": 1,
                "max_internal_latency": 1, "throughput": accepted / 4096,
                "fairness": 1, "max_request_wait": 1,
                "avg_timing_error": 0, "max_timing_error": 0,
            })


class MixedPhaseMetricTest(unittest.TestCase):
    def test_full_a3_compatible_generated_trace_and_summary_qualify(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            self.assertEqual(
                hashlib.sha256(fixture.trace_path.read_bytes()).hexdigest(),
                "9fde0ee816a80975d219b57e9799e73c198efc85d6e9aec4cb2a2e4816974705",
            )
            result = mixed_metrics.analyze(
                fixture.manifest_path, fixture.event_path, fixture.summary_path
            )
            self.assertEqual(result["classification"]["correctness_status"], "qualified_pass")
            self.assertEqual(result["classification"]["capacity_status"], "lossless")
            self.assertEqual(result["summary_evidence"]["conservation_validated"], True)
            self.assertEqual([row["phase"] for row in result["phases"]], [
                "u_bernoulli", "u_smooth", "s_persistent", "s_rotating",
                "h_a", "h_b", "h_a_replay",
            ])
            self.assertEqual(result["phases"][2]["generated"], 1024)
            self.assertEqual(result["phases"][4]["generated"], 1536)
            self.assertTrue(result["matched_trace_validation"]["sustained_frozen_dwell_and_rotation"])
            self.assertTrue(result["matched_trace_validation"]["hotspot_a_replay_exact_physical_replay"])
            self.assertIn("backlog_recovery_to_zero_cycles", result["phases"][0])

    def test_events_without_common_summary_do_not_claim_correctness(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            result = mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)
            self.assertEqual(result["classification"]["correctness_status"], "not_qualified")
            self.assertEqual(result["classification"]["analysis_status"], "correctness_not_qualified")
            self.assertIn("not qualified", result["classification"]["correctness_scope"])

    def test_common_summary_errors_are_correctness_failure_not_capacity(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            fixture.write_summary(errors=1)
            result = mixed_metrics.analyze(
                fixture.manifest_path, fixture.event_path, fixture.summary_path
            )
            self.assertEqual(result["classification"]["analysis_status"], "correctness_failure")
            self.assertEqual(result["classification"]["correctness_status"], "qualified_fail")

    def test_source_overrun_is_qualified_capacity_loss_not_correctness(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            fixture.write_events(overrun_id=0)
            fixture.write_summary(overrun=1)
            result = mixed_metrics.analyze(
                fixture.manifest_path, fixture.event_path, fixture.summary_path
            )
            self.assertEqual(result["classification"]["analysis_status"], "capacity_loss")
            self.assertEqual(result["classification"]["correctness_status"], "qualified_pass")
            self.assertEqual(result["classification"]["capacity_loss_events"], 1)

    def test_rejects_censored_or_summary_counter_mismatch(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            fixture.write_events(censored_id=0)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "right-censored"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)
            fixture.write_events()
            fixture.write_summary(generated_delta=1)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "counters"):
                mixed_metrics.analyze(
                    fixture.manifest_path, fixture.event_path, fixture.summary_path
                )

    def test_rejects_sustained_dwell_or_hotspot_replay_mutation(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            first = next(
                row for row in fixture.trace
                if row["occurrence_cycle"] == 1280 and row["logical_source"] == 0
            )
            second = next(
                row for row in fixture.trace
                if row["occurrence_cycle"] == 1344 and row["logical_source"] == 1
            )
            first["logical_source"], second["logical_source"] = 1, 0
            first["x"], first["y"] = 1, 0
            second["x"], second["y"] = 0, 0
            fixture.write_generated_artifacts()
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "dwell/rotation"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

        with tempfile.TemporaryDirectory() as directory_name:
            fixture = A3CompatibleGeneratedFixture(Path(directory_name))
            replay = [row for row in fixture.trace if row["occurrence_cycle"] == 3328]
            replay[0]["logical_source"], replay[1]["logical_source"] = (
                replay[1]["logical_source"], replay[0]["logical_source"]
            )
            for row in replay:
                row["x"], row["y"] = int(row["logical_source"]) % 4, int(row["logical_source"]) // 4
            fixture.write_generated_artifacts()
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "replay"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)


if __name__ == "__main__":
    unittest.main()
