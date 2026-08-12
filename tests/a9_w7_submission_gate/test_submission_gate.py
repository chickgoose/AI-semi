#!/usr/bin/env python3
"""Negative bypass and round-trip tests for the A9 W7 submission gate."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts/a9_w7_submission_gate.py"
SPEC = importlib.util.spec_from_file_location("a9_w7_submission_gate", TOOL_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class SubmissionGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = gate.load_json(ROOT / gate.POLICY_PATH)
        cls.git = gate.BoundGit(ROOT, cls.policy)
        cls.commit = cls.git.text("rev-parse", "HEAD")
        try:
            cls.bound_policy, _ = gate.tracked_policy(cls.git, cls.commit)
        except gate.GateError as exc:
            raise unittest.SkipTest(f"W7 files are not committed yet: {exc}") from exc

    def document(self, profile: str = "static_n64_timing") -> dict:
        return gate.build_manifest(self.git, self.commit, profile, self.bound_policy)

    @staticmethod
    def resign(document: dict) -> None:
        payload = {key: value for key, value in document.items() if key != "receipt"}
        document["receipt"]["payload_sha256"] = gate.sha_bytes(gate.canonical(payload))

    def test_round_trip_is_fail_closed_hold(self) -> None:
        for profile in self.bound_policy["profiles"]:
            with self.subTest(profile=profile):
                document = self.document(profile)
                gate.validate_manifest(document, self.git, self.bound_policy)
                self.assertEqual(document["status"], "HOLD")
                self.assertEqual(document["physical_evidence"]["status"], "ABSENT")

    def test_fake_path_git_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = pathlib.Path(directory) / "git"
            fake.write_text("#!/bin/sh\nprintf 'git version 999.fake\\n'\n")
            fake.chmod(0o755)
            previous = os.environ.get("PATH")
            os.environ["PATH"] = directory
            try:
                trusted = gate.BoundGit(ROOT, self.policy)
                self.assertEqual(trusted.path, pathlib.Path("/usr/bin/git"))
                self.assertEqual(trusted.text("rev-parse", "HEAD"), self.commit)
            finally:
                if previous is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = previous

    def test_self_declared_physical_pass_is_rejected_even_when_resigned(self) -> None:
        document = self.document()
        document["status"] = "GO"
        document["physical_evidence"]["status"] = "PASS"
        document["physical_evidence"]["trusted_parser"] = "claimed-parser"
        self.resign(document)
        with self.assertRaisesRegex(gate.GateError, "physical release is disabled"):
            gate.validate_manifest(document, self.git, self.bound_policy)

    def test_source_closure_tamper_is_rejected_even_when_resigned(self) -> None:
        document = self.document()
        document["profile"]["ordered_source_closure"].pop()
        self.resign(document)
        with self.assertRaisesRegex(gate.GateError, "profile differs"):
            gate.validate_manifest(document, self.git, self.bound_policy)

    def test_result_hash_and_hardlink_bypasses_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result = root / "result.rpt"
            result.write_bytes(b"measured\n")
            row = {"path": result.name, "sha256": "0" * 64,
                   "size": result.stat().st_size}
            with self.assertRaisesRegex(gate.GateError, "hash-mismatched"):
                gate.validate_artifact_rows([row], root, "result")
            row["sha256"] = gate.sha_file(result)
            alias = root / "alias.rpt"
            os.link(result, alias)
            with self.assertRaisesRegex(gate.GateError, "shared or size-mismatched"):
                gate.validate_artifact_rows([row], root, "result")

    def test_windows_inventory_excludes_outputs_and_user_state(self) -> None:
        document = self.document()
        paths = [row["path"] for row in document["windows_handoff"]["files"]]
        self.assertIn(str(gate.POLICY_PATH), paths)
        self.assertIn(str(gate.SCHEMA_PATH), paths)
        self.assertIn(str(gate.VALIDATOR_PATH), paths)
        payload = gate.windows_inventory_payload(document["windows_handoff"]["files"])
        self.assertEqual(gate.sha_bytes(payload),
                         document["windows_handoff"]["inventory_sha256"])
        for path in paths:
            self.assertFalse(path.startswith(("build/", "results/", "reports/", "vivado/")))
            self.assertNotIn("__pycache__", path)
            self.assertFalse(path.endswith((".log", ".vcd", ".fst")))

    def test_output_reuse_is_rejected(self) -> None:
        if self.git.run("status", "--porcelain=v1", "--untracked-files=all"):
            self.skipTest("generation requires the post-commit clean worktree")
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "receipt"
            gate.generate(ROOT, "static_n64_timing", output)
            self.assertTrue((output / gate.MANIFEST_NAME).is_file())
            self.assertTrue((output / gate.WINDOWS_NAME).is_file())
            with self.assertRaisesRegex(gate.GateError, "refusing to reuse"):
                gate.generate(ROOT, "static_n64_timing", output)


if __name__ == "__main__":
    unittest.main()
