#!/usr/bin/env python3
"""Verify the noncircular native-pipeline publication against Git objects."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
PUBLICATION_PATH = (
    ROOT / "benchmarks" / "redred_single_edge_campaign"
    / "native_pipeline_publication.json"
)


def git_bytes(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True,
    ).stdout


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class NativePipelinePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = json.loads(PUBLICATION_PATH.read_text(encoding="utf-8"))

    def test_code_commit_tree_and_every_inventory_blob_are_exact(self) -> None:
        code = self.publication["code"]
        self.assertEqual(
            git_bytes("rev-parse", f'{code["commit"]}^{{tree}}').decode().strip(),
            code["tree"],
        )
        for entry in code["inventory"]:
            with self.subTest(path=entry["path"]):
                blob = git_bytes("show", f'{code["commit"]}:{entry["path"]}')
                self.assertEqual(len(blob), entry["size_bytes"])
                self.assertEqual(sha256(blob), entry["sha256"])
                self.assertEqual(
                    git_bytes("rev-parse", f'{code["commit"]}:{entry["path"]}')
                    .decode().strip(),
                    entry["git_blob_oid"],
                )

    def test_payload_commit_tree_result_blob_and_semantic_seal_are_exact(self) -> None:
        payload = self.publication["payload"]
        result_record = payload["result"]
        self.assertEqual(
            git_bytes("rev-parse", f'{payload["commit"]}^{{tree}}').decode().strip(),
            payload["tree"],
        )
        blob = git_bytes("show", f'{payload["commit"]}:{result_record["path"]}')
        self.assertEqual(len(blob), result_record["size_bytes"])
        self.assertEqual(sha256(blob), result_record["sha256"])
        self.assertEqual(
            git_bytes("rev-parse", f'{payload["commit"]}:{result_record["path"]}')
            .decode().strip(),
            result_record["git_blob_oid"],
        )
        document = json.loads(blob)
        unsigned = copy.deepcopy(document)
        seal = unsigned.pop("seal")
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(seal["semantic_sha256"], sha256(canonical))
        self.assertEqual(seal["semantic_sha256"], result_record["semantic_sha256"])

    def test_publication_is_noncircular_and_scope_is_fail_closed(self) -> None:
        document = self.publication
        self.assertTrue(document["noncircular_provenance"])
        publication_relpath = str(PUBLICATION_PATH.relative_to(ROOT))
        for commit in (document["code"]["commit"], document["payload"]["commit"]):
            with self.subTest(commit=commit):
                lookup = subprocess.run(
                    ["git", "cat-file", "-e", f"{commit}:{publication_relpath}"],
                    cwd=ROOT, capture_output=True,
                )
                self.assertNotEqual(lookup.returncode, 0)
        self.assertEqual(document["status"], "PASS_SCOPED_NATIVE_CAMPAIGN_PIPELINE")
        self.assertEqual(document["campaign_decision"]["campaign_recommendation"], "A2")
        self.assertIsNone(document["campaign_decision"]["final_selected_candidate"])
        self.assertEqual(document["campaign_decision"]["final_selection_status"], "HOLD")
        self.assertEqual(document["campaign_decision"]["release_status"], "HOLD")
        self.assertEqual(
            document["claim_boundary"],
            {
                "final_selection": "HOLD", "official": False,
                "physical": False, "power": False, "release": False,
            },
        )

    def test_policy_record_is_the_same_pinned_code_blob(self) -> None:
        policy = self.publication["policy"]
        code = self.publication["code"]
        inventory = {entry["path"]: entry for entry in code["inventory"]}
        self.assertEqual(policy["authority"], "TEAM_DEFINED_CAMPAIGN_POLICY_ONLY")
        self.assertEqual(policy["git_blob_oid"], inventory[policy["path"]]["git_blob_oid"])
        self.assertEqual(policy["sha256"], inventory[policy["path"]]["sha256"])
        self.assertEqual(policy["size_bytes"], inventory[policy["path"]]["size_bytes"])


if __name__ == "__main__":
    unittest.main()
