from __future__ import annotations

import tempfile
import unittest
import json
import hashlib
from pathlib import Path

from tests.mc_wtb_occurrence_baseline.inspect_run import (
    InspectionFailure,
    inspect,
    reference_endpoint_schedule,
)
from tests.mc_wtb_occurrence_baseline.prepare import START_NS, pack


class InspectorMutationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        root = Path(self.fixture.name)
        self.source = root / "source_records.jsonl"
        self.stimulus = root / "stimulus.txt"
        self.manifest = root / "stimulus_manifest.json"
        self.raw = root / "raw.log"
        self.status = root / "status.txt"
        self.simulator_log = root / "xrun.log"
        self.commit = "0" * 40
        source_rows = []
        for ordinal in range(1100):
            occurrence_ns = START_NS + ordinal * 10
            occurrence_cycle = (ordinal * 20 + 12) // 13
            logical_source = ordinal % 16
            event = {
                "dataset_event_index": 13_856_250 + ordinal,
                "join_sequence_index": ordinal,
                "timestamp_ns": occurrence_ns,
                "x": ordinal % 240,
                "y": ordinal % 180,
                "polarity_01": ordinal % 2,
                "causal_pose": {"source_pose_index": 8241},
            }
            payload = f"{pack(event, logical_source):026x}"
            source_rows.append({
                "dataset_event_index": event["dataset_event_index"],
                "join_sequence_index": ordinal,
                "occurrence_timestamp_ns": occurrence_ns,
                "occurrence_cycle": occurrence_cycle,
                "projection_floor_cycle": (ordinal * 20) // 13,
                "logical_source": logical_source,
                "x": event["x"],
                "y": event["y"],
                "polarity_01": event["polarity_01"],
                "causal_pose_source_index": 8241,
                "payload_hex": payload,
            })
        canonical_rows = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in source_rows
        )
        self.source.write_bytes(canonical_rows)
        self.stimulus.write_text("synthetic inspector fixture\n")
        manifest = {
            "record_count": 1100,
            "source_records_sha256": hashlib.sha256(canonical_rows).hexdigest(),
            "clock_period_ps": 6500,
            "source_epoch_start_ns": START_NS,
            "admission_cycle_mapping": "ceil((occurrence_timestamp_ns-source_epoch_start_ns)*2/13)",
            "never_admit_before_occurrence": True,
            "stimulus_sha256": hashlib.sha256(self.stimulus.read_bytes()).hexdigest(),
        }
        self.manifest.write_text(json.dumps(manifest))
        ingress, accepts, retires = reference_endpoint_schedule(source_rows)
        raw_records = []
        for kind_order, kind, records in (
            (0, "INGRESS", ingress), (1, "ACCEPT", accepts), (2, "RETIRE", retires)
        ):
            for cycle, lane, logical_source, payload in records:
                raw_records.append((
                    cycle, kind_order, lane,
                    f"{kind},{cycle},{lane},{logical_source},{payload}",
                ))
        raw_records.sort(key=lambda row: row[:3])
        self.raw.write_text("\n".join(row[3] for row in raw_records) + "\n")
        last_retire_cycle = max(row[0] for row in retires)
        self.status.write_text(
            f"PASS ingress=1100 accepted=1100 retired=1100 last_cycle={last_retire_cycle + 1} "
            "overflow=0 protocol_error=0\n"
        )
        self.simulator_log.write_text(
            "MC_WTB_OCCURRENCE_BASELINE_RTL_PASS\n"
            "Simulation complete via $finish(1) at time 1 NS\n"
        )

    def call(self, raw: Path | None = None, status: Path | None = None):
        return inspect(
            self.source,
            self.stimulus,
            self.manifest,
            raw or self.raw,
            status or self.status,
            self.simulator_log,
            self.commit,
            "mutation-test",
            False,
        )

    def mutate_raw(self, transform):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "raw.log"
        lines = self.raw.read_text().splitlines()
        path.write_text("\n".join(transform(lines)) + "\n")
        return path

    def test_pristine_passes(self):
        receipt, mapping, summary = self.call()
        self.assertIn(b'"record_count":1100', receipt)
        self.assertIn(b'"validated":false', mapping)
        self.assertEqual(summary["retired"], 1100)

    def test_missing_retire_rejected(self):
        def change(rows):
            rows = list(rows)
            rows.pop(next(i for i, row in enumerate(rows) if row.startswith("RETIRE,")))
            return rows
        path = self.mutate_raw(change)
        with self.assertRaises(InspectionFailure):
            self.call(path)

    def test_duplicate_rejected(self):
        path = self.mutate_raw(lambda rows: rows + [next(row for row in rows if row.startswith("RETIRE,"))])
        with self.assertRaises(InspectionFailure):
            self.call(path)

    def test_payload_corruption_rejected(self):
        def change(rows):
            rows = list(rows)
            index = next(i for i, row in enumerate(rows) if row.startswith("RETIRE,"))
            rows[index] = rows[index][:-1] + ("0" if rows[index][-1] != "0" else "1")
            return rows
        with self.assertRaises(InspectionFailure):
            self.call(self.mutate_raw(change))

    def test_retire_order_rejected(self):
        def change(rows):
            rows = list(rows)
            indices = [i for i, row in enumerate(rows) if row.startswith("RETIRE,")][:2]
            rows[indices[0]], rows[indices[1]] = rows[indices[1]], rows[indices[0]]
            return rows
        with self.assertRaises(InspectionFailure):
            self.call(self.mutate_raw(change))

    def test_ingress_cycle_or_lane_rewrite_rejected(self):
        def change(rows):
            rows = list(rows)
            index = next(i for i, row in enumerate(rows) if row.startswith("INGRESS,"))
            columns = rows[index].split(",")
            columns[2] = "5"
            rows[index] = ",".join(columns)
            return rows
        with self.assertRaises(InspectionFailure):
            self.call(self.mutate_raw(change))

    def test_coherent_accept_retire_clock_shift_rejected(self):
        def change(rows):
            output = []
            for row in rows:
                columns = row.split(",")
                if columns[0] in ("ACCEPT", "RETIRE"):
                    columns[1] = str(int(columns[1]) + 1000)
                output.append(",".join(columns))
            return output
        raw = self.mutate_raw(change)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        status = Path(directory.name) / "status.txt"
        original_last = int(self.status.read_text().split("last_cycle=")[1].split()[0])
        status.write_text(
            f"PASS ingress=1100 accepted=1100 retired=1100 last_cycle={original_last + 1000} "
            "overflow=0 protocol_error=0\n"
        )
        with self.assertRaises(InspectionFailure):
            self.call(raw=raw, status=status)

    def test_bad_status_rejected(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "status.txt"
        path.write_text("PASS ingress=1100 accepted=1019 retired=1019 last_cycle=0 overflow=0 protocol_error=0\n")
        with self.assertRaises(InspectionFailure):
            self.call(status=path)


if __name__ == "__main__":
    unittest.main()
