from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("a4_k2_export", HERE / "export_frozen_v4.py")
assert spec and spec.loader
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class VectorFormatTest(unittest.TestCase):
    def test_cycle_explicit_round_trip_preserves_identity_and_window(self) -> None:
        cycles = [
            {"cycle": 0, "reset_n": True, "bundle_ready": True,
             "occurrences": [{"source": 3, "event_id": 0}]},
            {"cycle": 1, "reset_n": True, "bundle_ready": False,
             "occurrences": [{"source": 7, "event_id": 1}]},
            {"cycle": 2, "reset_n": True, "bundle_ready": True, "occurrences": []},
        ]
        with tempfile.TemporaryDirectory() as directory:
            vector = Path(directory) / "case.a4k2v"
            exporter.encode_vector(vector, cycles, 2, (0, 2), 2)
            parsed = exporter.parse_vector(vector)
        self.assertEqual([(0, 3, 0), (1, 7, 1)], parsed["occurrences"])
        self.assertEqual([0, 2], parsed["measurement_window"])
        self.assertEqual(0, parsed["max_accept_retire_latency"])

    def test_cycle_index_time_shift_fails_closed(self) -> None:
        cycles = [{"cycle": 0, "reset_n": True, "bundle_ready": True,
                   "occurrences": [{"source": 0, "event_id": 0}]}]
        with tempfile.TemporaryDirectory() as directory:
            vector = Path(directory) / "shift.a4k2v"
            exporter.encode_vector(vector, cycles, 1, (0, 1), 1)
            lines = vector.read_text(encoding="ascii").splitlines()
            fields = lines[1].split()
            fields[0] = "1"
            lines[1] = " ".join(fields)
            vector.write_text("\n".join(lines) + "\n", encoding="ascii")
            with self.assertRaisesRegex(exporter.ExportError, "cycle/index/control mismatch"):
                exporter.parse_vector(vector)

    def test_reset_drain_vector_has_live_abort_and_post_reset_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = exporter.make_reset_drain_run(root)
            parsed = exporter.parse_vector(root / record["vector_file"])
        self.assertEqual(7, parsed["expected_generated"])
        self.assertEqual([6, 16], parsed["measurement_window"])
        self.assertEqual([3, 4], parsed["reset_cycles"])
        self.assertEqual(4, record["expected_reset_aborted_events"])
        self.assertEqual(1, record["expected_source_overrun_events"])

    def test_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle.json"
            exporter.write_new(output, {"first": True})
            with self.assertRaises(FileExistsError):
                exporter.write_new(output, {"second": True})


if __name__ == "__main__":
    unittest.main()
