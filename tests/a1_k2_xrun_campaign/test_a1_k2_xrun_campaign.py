#!/usr/bin/env python3
"""Fail-closed unit tests for the compile-once local Xcelium K2 campaign."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import a1_k2_xrun_campaign as campaign  # noqa: E402


FAKES = Path(__file__).resolve().parent
TRACE_SHA = hashlib.sha256(b'{"tb_only_event_id":0}\n').hexdigest()


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.candidate_source = self.project / "candidate.sv"
        self.candidate_source.write_text("module candidate; endmodule\n", encoding="utf-8")
        (self.project / "tb.sv").write_text("module aer_clean_tb; endmodule\n", encoding="utf-8")
        (self.project / "candidate.f").write_text("candidate.sv\n", encoding="utf-8")
        (self.project / "tb.f").write_text("tb.sv\n", encoding="utf-8")
        self.manifest_doc = {
            "schema_version": 1,
            "runs": [{
                "name": "trace_one", "workload": "uniform", "seed": 1,
                "geometry": {"width": 4, "height": 4}, "load": 1.0,
                "stim_cycles": 4, "parameters": {},
            }],
        }
        self.full_manifest = self.project / "manifest.full50.json"
        self.capacity_manifest = self.project / "manifest.capacity22.json"
        payload = json.dumps(self.manifest_doc, sort_keys=True) + "\n"
        self.full_manifest.write_text(payload, encoding="utf-8")
        self.capacity_manifest.write_text(payload, encoding="utf-8")
        manifest_sha = hashlib.sha256(payload.encode()).hexdigest()
        self.specs = {
            suite: {
                "manifest_name": manifest.name,
                "manifest_sha256": manifest_sha,
                "names": ("trace_one",),
                "trace_sha256": {"trace_one": TRACE_SHA},
            }
            for suite, manifest in (
                ("full50", self.full_manifest), ("capacity22", self.capacity_manifest))
        }
        self.output = self.root / "results"
        self.journal = self.root / "xrun.journal"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def argv(self, *, retire_lanes: str = "2") -> list[str]:
        return [
            "--candidate", "candidate-k2",
            "--top", "aer_clean_tb",
            "--candidate-filelist", "candidate.f",
            "--tb-filelist", "tb.f",
            "--define", "AER_K2_CANDIDATE",
            "--param", "aer_clean_tb.NUM_SOURCES=16",
            "--param", f"aer_clean_tb.RETIRE_LANES={retire_lanes}",
            "--suite", "full50", "--suite", "capacity22",
            "--xrun", str(FAKES / "fake_xrun.py"),
            "--generator", str(FAKES / "fake_generator.py"),
            "--preparer", str(FAKES / "fake_preparer.py"),
            "--full50-manifest", str(self.full_manifest),
            "--capacity22-manifest", str(self.capacity_manifest),
            "--project-root", str(self.project),
            "--output-root", str(self.output),
        ]

    def invoke(self, *, xrun: str = "success", generator: str = "success",
               preparer: str = "success", extra_env: dict[str, str] | None = None,
               argv: list[str] | None = None) -> tuple[int, list[Path], str]:
        environment = {
            "FAKE_XRUN_MODE": xrun,
            "FAKE_GENERATOR_MODE": generator,
            "FAKE_PREPARER_MODE": preparer,
            "FAKE_XRUN_JOURNAL": str(self.journal),
            "FAKE_XRUN_MUTATE_PATH": "",
        }
        environment.update(extra_env or {})
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(campaign.SUITE_SPECS, self.specs, clear=True), \
                mock.patch.dict(os.environ, environment, clear=False), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = campaign.main(argv or self.argv())
        receipts = list(self.output.glob("candidate-k2/*/campaign.receipt.json"))
        return result, receipts, stderr.getvalue()

    def assert_closed(self, **kwargs: object) -> Path | None:
        result, receipts, _ = self.invoke(**kwargs)
        self.assertEqual(2, result)
        self.assertEqual([], receipts)
        attempts = list(self.output.glob("candidate-k2/*"))
        if attempts:
            self.assertTrue((attempts[0] / "campaign.failure.json").is_file())
            return attempts[0]
        return None

    def test_success_compiles_once_runs_reset_then_both_suites_and_hashes_receipt(self) -> None:
        result, receipts, stderr = self.invoke()
        self.assertEqual("", stderr)
        self.assertEqual(0, result)
        self.assertEqual(1, len(receipts))
        receipt_path = receipts[0]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual("PASS", receipt["status"])
        self.assertEqual(1, receipt["compile"]["invocation_count"])
        self.assertEqual(1, receipt["reset_run_count"])
        self.assertEqual(2, receipt["trace_run_count"])
        self.assertEqual("reset", receipt["runs"][0]["kind"])
        self.assertEqual({"full50", "capacity22"}, set(receipt["generation"]))
        self.assertEqual({"AER_K2_CANDIDATE": None}, receipt["configuration"]["defines"])
        self.assertEqual(2, receipt["configuration"]["retire_lanes"])
        self.assertEqual(["version", "compile", "run", "run", "run"],
                         self.journal.read_text(encoding="utf-8").splitlines())
        paths = [row["results"]["metrics"]["path"] for row in receipt["runs"]]
        self.assertEqual(len(paths), len(set(paths)))
        expected = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        self.assertEqual(expected, receipt_path.with_suffix(".sha256").read_text().split()[0])

    def test_nonzero_compile_fails_closed(self) -> None:
        self.assert_closed(xrun="compile_fail")

    def test_zero_exit_compile_error_text_fails_closed(self) -> None:
        self.assert_closed(xrun="compile_error_zero")

    def test_nonzero_run_fails_closed(self) -> None:
        self.assert_closed(xrun="run_fail")

    def test_missing_pass_fails_closed(self) -> None:
        self.assert_closed(xrun="missing_pass")

    def test_zero_exit_run_error_text_fails_closed(self) -> None:
        self.assert_closed(xrun="error_zero")

    def test_sentinel_only_fake_fails_closed(self) -> None:
        self.assert_closed(xrun="sentinel_only")

    def test_partial_result_bundle_fails_closed(self) -> None:
        self.assert_closed(xrun="partial_output")

    def test_hardlinked_duplicate_results_fail_closed(self) -> None:
        self.assert_closed(xrun="duplicate_output")

    def test_symlinked_result_fails_closed(self) -> None:
        self.assert_closed(xrun="symlink_output")

    def test_stale_timestamp_results_fail_closed(self) -> None:
        self.assert_closed(xrun="stale_output")

    def test_reset_requires_dedicated_pass_marker(self) -> None:
        self.assert_closed(xrun="reset_missing")

    def test_source_change_during_compile_blocks_receipt(self) -> None:
        self.assert_closed(extra_env={"FAKE_XRUN_MUTATE_PATH": str(self.candidate_source)})

    def test_prepared_trace_sha_change_blocks_receipt(self) -> None:
        self.assert_closed(xrun="mutate_trace")

    def test_partial_generation_fails_before_compile(self) -> None:
        self.assert_closed(generator="partial")
        self.assertFalse(self.journal.exists())

    def test_preparer_error_with_zero_exit_fails_before_compile(self) -> None:
        self.assert_closed(preparer="error_zero")
        self.assertFalse(self.journal.exists())

    def test_missing_candidate_source_fails_before_attempt(self) -> None:
        self.candidate_source.unlink()
        result, receipts, _ = self.invoke()
        self.assertEqual(2, result)
        self.assertEqual([], receipts)
        self.assertFalse(self.output.exists())

    def test_hidden_filelist_compile_option_is_rejected(self) -> None:
        (self.project / "candidate.f").write_text(
            "-define HIDDEN_CONFIGURATION\ncandidate.sv\n", encoding="utf-8")
        result, receipts, _ = self.invoke()
        self.assertEqual(2, result)
        self.assertEqual([], receipts)
        self.assertFalse(self.output.exists())

    def test_retire_lanes_other_than_two_is_rejected(self) -> None:
        result, receipts, _ = self.invoke(argv=self.argv(retire_lanes="4"))
        self.assertEqual(2, result)
        self.assertEqual([], receipts)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
