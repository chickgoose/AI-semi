#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent
CANDIDATE = HERE.parent
ROOT = CANDIDATE.parents[2]
REPLAY = CANDIDATE / "replay_generator_v4.py"
COMMON_ROOT = pathlib.Path(
    os.environ.get("AER_COMMON_ROOT", "/home/chickgoose/projects/a1")
)


class GeneratorV4ReplayTest(unittest.TestCase):
    def test_exact_full50_capacity22_and_representative_rtl(self) -> None:
        resolved = os.environ.get("AER_VERILATOR_RESOLVED")
        self.assertTrue(resolved, "run.sh must resolve Verilator fail-closed")
        self.assertTrue(
            (COMMON_ROOT / "benchmarks/clean_slate_aer/generate_trace.py").is_file(),
            f"missing read-only common generator under {COMMON_ROOT}",
        )

        with tempfile.TemporaryDirectory(prefix="a4-generator-v4-") as tmp_name:
            tmp = pathlib.Path(tmp_name)
            generated = tmp / "generated"
            report_path = tmp / "report.json"
            vector_dir = tmp / "vectors"
            replay = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(REPLAY),
                    "--common-root",
                    str(COMMON_ROOT),
                    "--suite",
                    "all",
                    "--generated-root",
                    str(generated),
                    "--output",
                    str(report_path),
                    "--rtl-vectors-dir",
                    str(vector_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            self.assertIn(
                "A4_GENERATOR_V4_REPLAY_PASS full50=50 capacity22=22",
                replay.stdout,
            )

            report = json.loads(report_path.read_text())
            self.assertEqual(report["qualification"], "LOCAL_MODEL_REPLAY_ONLY")
            self.assertEqual(report["common_qualification"], "HOLD")
            self.assertEqual(report["ppa_qualification"], "HOLD")
            self.assertEqual(report["provenance"]["generator_version"], "4.0")
            self.assertEqual(report["provenance"]["common_tracked_status"], "")
            self.assertEqual(
                report["provenance"]["generator_sha256"],
                "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
            )
            self.assertEqual(report["suites"]["full50"]["run_count"], 50)
            self.assertEqual(report["suites"]["capacity22"]["run_count"], 22)
            for suite in report["suites"].values():
                self.assertEqual(suite["fixed"]["accepted"], suite["fixed"]["retired"])
                self.assertEqual(
                    suite["moving"]["accepted"], suite["moving"]["retired"]
                )

            vector_records = report["suites"]["full50"]["rtl_vectors"]
            self.assertEqual(
                {item["name"] for item in vector_records},
                {
                    "core_simultaneous_identity",
                    "shape_b16",
                    "global_fanin_identity",
                    "mixed_phase_always_ready_identity",
                },
            )

            object_dir = tmp / "obj-representative"
            build = subprocess.run(
                [
                    resolved,
                    "--binary",
                    "--timing",
                    "--assert",
                    "-Wall",
                    "-Wno-fatal",
                    "--top-module",
                    "a4_moving_block_lockstep_tb",
                    "-GDUT_MAX_ADVANCE=2",
                    "--Mdir",
                    str(object_dir),
                    "-o",
                    "sim",
                    str(CANDIDATE / "a4_moving_block_tree.sv"),
                    str(HERE / "a4_moving_block_lockstep_tb.sv"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            for record in vector_records:
                vector = vector_dir / f"{record['name']}.vectors.txt"
                for line in vector.read_text().splitlines():
                    fields = line.split()
                    valid_mask = int(fields[1], 16)
                    payloads = [int(value, 16) for value in fields[3:19]]
                    for source in range(16):
                        if valid_mask & (1 << source):
                            self.assertEqual(
                                payloads[source],
                                source,
                                "representative RTL pins must remain address-only",
                            )
                run = subprocess.run(
                    [str(object_dir / "sim"), f"+VECTORS={vector}"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn(
                    f"A4_MOVING_BLOCK_LOCKSTEP_PASS cycles={record['vector_cycles']}",
                    run.stdout,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
