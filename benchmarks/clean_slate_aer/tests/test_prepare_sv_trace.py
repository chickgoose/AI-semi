import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_sv_trace import TracePreparationError, prepare_trace


class PrepareSvTraceTest(unittest.TestCase):
    def make_fixture(self, directory: Path) -> tuple[Path, Path]:
        events = [
            {"occurrence_cycle": 1, "tb_only_event_id": 0, "logical_source": 0,
             "x": 0, "y": 0, "polarity": -1, "event_type": "spike", "deadline": 5},
            {"occurrence_cycle": 1, "tb_only_event_id": 1, "logical_source": 1,
             "x": 1, "y": 0, "polarity": 1, "event_type": "spike", "deadline": 6},
            {"occurrence_cycle": 7, "tb_only_event_id": 2, "logical_source": 3,
             "x": 1, "y": 1, "polarity": 1, "event_type": "timing_b", "deadline": 8},
        ]
        trace = directory / "tiny.events.jsonl"
        raw = "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
        trace.write_text(raw, encoding="ascii")
        manifest = directory / "tiny.manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "run": {"name": "tiny", "workload": "timing_pair", "seed": 9,
                    "geometry": {"width": 2, "height": 2}, "load": "0.5",
                    "stim_cycles": 8, "parameters": {}},
            "trace_file": trace.name,
            "trace_sha256": hashlib.sha256(raw.encode("ascii")).hexdigest(),
            "event_count": len(events),
        }) + "\n", encoding="utf-8")
        return trace, manifest

    def test_emits_numeric_trace_with_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace, manifest = self.make_fixture(directory)
            output = directory / "tiny.svtrace"
            result = prepare_trace(trace, manifest, output, 8)
            self.assertEqual(result["source_count"], 4)
            self.assertEqual(result["load_milli"], 500)
            lines = output.read_text(encoding="ascii").splitlines()
            self.assertEqual(lines[0], "3 3 8 4 500 0 0 0 9")
            self.assertEqual([line.split()[:3] for line in lines[1:]],
                             [["1", "0", "0"], ["1", "1", "1"], ["7", "2", "3"]])
            self.assertNotEqual(lines[1].split()[3], lines[3].split()[3])

    def test_rejects_tampered_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace, manifest = self.make_fixture(directory)
            trace.write_text(
                trace.read_text(encoding="ascii").replace('"deadline":5', '"deadline":4'),
                encoding="ascii",
            )
            with self.assertRaisesRegex(TracePreparationError, "SHA256"):
                prepare_trace(trace, manifest, directory / "out.svtrace", 8)

    def test_rejects_too_small_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace, manifest = self.make_fixture(directory)
            with self.assertRaisesRegex(TracePreparationError, "ADDR_WIDTH"):
                prepare_trace(trace, manifest, directory / "out.svtrace", 2)

    def test_encodes_sink_shock_from_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace, manifest = self.make_fixture(directory)
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            metadata["run"]["sink"] = {"mode": "shock", "start": 2, "cycles": 3}
            manifest.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
            output = directory / "out.svtrace"
            prepare_trace(trace, manifest, output, 8)
            self.assertEqual(
                output.read_text(encoding="ascii").splitlines()[0],
                "3 3 8 4 500 2 2 3 9",
            )


if __name__ == "__main__":
    unittest.main()
