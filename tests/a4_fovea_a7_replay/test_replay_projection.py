from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("replay_projection", HERE / "replay_projection.py")
assert spec and spec.loader
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class ProjectionUnitTest(unittest.TestCase):
    def write_trace(self, root: Path) -> tuple[dict, Path]:
        events = [
            {"tb_only_event_id": 0, "logical_source": 3, "occurrence_cycle": 4},
            {"tb_only_event_id": 1, "logical_source": 7, "occurrence_cycle": 5},
            {"tb_only_event_id": 2, "logical_source": 3, "occurrence_cycle": 6},
            {"tb_only_event_id": 3, "logical_source": 9, "occurrence_cycle": 7},
        ]
        path = root / "case.events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
        metadata = {"run": {"name": "case", "workload": "uniform", "seed": 11},
                    "trace_sha256": replay.sha256(path), "trace_file": path.name}
        return metadata, path

    def write_results(self, root: Path, *, duplicate_cycle: bool = False,
                      bad_source: bool = False) -> Path:
        path = root / "trace.events.csv"
        rows = [
            [replay.EXPECTED_CANDIDATE, "case", 11, 100, 0, 3, 16, 4, 5, 10, 36, 20, "delivered"],
            [replay.EXPECTED_CANDIDATE, "case", 11, 100, 1, 8 if bad_source else 7, 16, 5, 6,
             10 if duplicate_cycle else 11, 37, 20, "delivered"],
            [replay.EXPECTED_CANDIDATE, "case", 11, 100, 2, 3, 16, 6, "", "", 38, 20, "source_overrun"],
            [replay.EXPECTED_CANDIDATE, "case", 11, 100, 3, 9, 16, 7, 8, 14, 39, 20, "delivered"],
        ]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["candidate", "test", "seed", "load_pct", "tb_only_event_id",
                             "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
                             "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state"])
            writer.writerows(rows)
        return path

    def test_exact_stream_plus_two_and_overrun_not_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, trace = self.write_trace(root)
            result = replay.project_run("case", metadata, trace, self.write_results(root))
        self.assertEqual([10, 11, 14], [row["a7_admission_cycle"] for row in result["events"]])
        self.assertEqual([12, 13, 16], [row["a7_consumer_cycle"] for row in result["events"]])
        self.assertEqual([0, 1, 3], [row["tb_only_event_id"] for row in result["events"]])
        self.assertEqual([3, 7, 9], [row["logical_source"] for row in result["events"]])
        self.assertEqual(1, result["state_counts"]["source_overrun"])
        self.assertEqual(0, result["state_counts"]["pending"])
        self.assertEqual(result["fovea_output_stream_sha256"],
                         result["a7_admission_stream_sha256"])
        self.assertEqual(0, result["fovea_delivery_to_a7_admission_delta_cycles"])
        self.assertTrue(result["no_free_queue"])

    def test_rejects_more_than_one_scalar_event_per_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); metadata, trace = self.write_trace(root)
            with self.assertRaisesRegex(replay.ProjectionError, "exceeds one event/cycle"):
                replay.project_run("case", metadata, trace,
                                   self.write_results(root, duplicate_cycle=True))

    def test_rejects_trace_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); metadata, trace = self.write_trace(root)
            with self.assertRaisesRegex(replay.ProjectionError, "trace identity mismatch"):
                replay.project_run("case", metadata, trace,
                                   self.write_results(root, bad_source=True))

    def test_output_is_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            replay.write_new(output, {"a": 1})
            with self.assertRaises(FileExistsError):
                replay.write_new(output, {"a": 2})


class OfficialGeneratorV4ProvenanceTest(unittest.TestCase):
    A1 = Path("/home/chickgoose/projects/a1")

    def test_exact_full50_and_capacity22_generation(self) -> None:
        generator = self.A1 / "benchmarks/clean_slate_aer/generate_trace.py"
        official = self.A1 / "scripts/common_suite_official.py"
        manifests = {
            "full50": self.A1 / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json",
            "capacity22": self.A1 / "benchmarks/clean_slate_aer/manifest.multilane-n16.json",
        }
        if not all(path.is_file() for path in [generator, official, *manifests.values()]):
            self.fail("exact A1 generator-v4 provenance inputs are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            counts = {}
            for suite, manifest in manifests.items():
                trace_root = root / suite
                subprocess.run([sys.executable, str(generator), "--manifest", str(manifest),
                                "--output-dir", str(trace_root)], check=True)
                _, rows = replay.validate_generation(suite, trace_root, manifest,
                                                     generator, official)
                counts[suite] = len(rows)
        self.assertEqual({"full50": 50, "capacity22": 22}, counts)


if __name__ == "__main__":
    unittest.main()
