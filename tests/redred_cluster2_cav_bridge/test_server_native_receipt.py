from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import unittest

from benchmarks.redred_cluster2_cav_bridge.native_ledger import (
    canonical_transport_outcome_jsonl,
    inspect_cyclemask_encoding,
    parse_native_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_cluster2_cav_bridge"
RECEIPT_PATH = PACKAGE / "server_native_observation_receipt.json"
AUTHORITY_PATH = PACKAGE / "ganghee_cluster2_native_authority.json"
TB_PATH = (
    ROOT
    / "tests"
    / "redred_cluster2_cav_bridge"
    / "redred_cluster2_native_observational_tb.sv"
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


class ServerNativeReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(
            RECEIPT_PATH.read_text(encoding="ascii"),
            object_pairs_hook=unique_object,
        )
        bundle_relative = cls.receipt["artifact_bundle"]["path"]
        cls.assert_normalized_relative(bundle_relative)
        cls.bundle_path = ROOT / Path(*PurePosixPath(bundle_relative).parts)
        cls.bundle_bytes = cls.bundle_path.read_bytes()
        with tarfile.open(fileobj=io.BytesIO(cls.bundle_bytes), mode="r:gz") as archive:
            names = archive.getnames()
            if names != [
                "native_ledger.psv",
                "transport_outcomes.jsonl",
                "run.log",
                "xrun.log",
                "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt",
                "faer_snapshot/rtl/arbiter2.v",
                "faer_snapshot/rtl/arbiter4_tree.v",
                "faer_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v",
                "bridge_snapshot/redred_cluster2_native_observational_tb.sv",
                "owner-native-ca446aa.stdout",
                "owner-native-ca446aa.exitcode",
                "owner-bridge-commit-ca446aa.txt",
            ]:
                raise AssertionError("server evidence bundle member order differs")
            cls.artifacts = {}
            for name in names:
                member = archive.getmember(name)
                if not member.isfile():
                    raise AssertionError("server evidence member is not regular")
                stream = archive.extractfile(member)
                if stream is None:
                    raise AssertionError("server evidence member is unreadable")
                cls.artifacts[name] = stream.read()

    @staticmethod
    def assert_normalized_relative(value):
        if type(value) is not str or not value or "\\" in value:
            raise AssertionError("receipt path is not normalized relative POSIX")
        path = PurePosixPath(value)
        if path.is_absolute() or value != path.as_posix() or any(
            part in ("", ".", "..") for part in path.parts
        ):
            raise AssertionError("receipt path is not normalized relative POSIX")

    def test_bundle_and_member_digests_are_exact(self):
        self.assertEqual(
            sha256(self.bundle_bytes), self.receipt["artifact_bundle"]["sha256"]
        )
        expected = self.receipt["artifact_digests"]
        self.assertEqual(
            {
                "bridge_commit_txt_sha256": sha256(
                    self.artifacts["owner-bridge-commit-ca446aa.txt"]
                ),
                "compiled_arbiter2_sha256": sha256(
                    self.artifacts["faer_snapshot/rtl/arbiter2.v"]
                ),
                "compiled_arbiter4_tree_sha256": sha256(
                    self.artifacts["faer_snapshot/rtl/arbiter4_tree.v"]
                ),
                "compiled_cluster2_steal_buf_rtl_sha256": sha256(
                    self.artifacts[
                        "faer_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v"
                    ]
                ),
                "native_ledger_psv_sha256": sha256(self.artifacts["native_ledger.psv"]),
                "cyclemask_input_sha256": sha256(
                    self.artifacts[
                        "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
                    ]
                ),
                "outer_runner_exitcode_sha256": sha256(
                    self.artifacts["owner-native-ca446aa.exitcode"]
                ),
                "outer_runner_stdout_sha256": sha256(
                    self.artifacts["owner-native-ca446aa.stdout"]
                ),
                "observational_tb_sha256": sha256(
                    self.artifacts[
                        "bridge_snapshot/redred_cluster2_native_observational_tb.sv"
                    ]
                ),
                "run_log_sha256": sha256(self.artifacts["run.log"]),
                "transport_outcomes_jsonl_sha256": sha256(
                    self.artifacts["transport_outcomes.jsonl"]
                ),
                "xrun_log_sha256": sha256(self.artifacts["xrun.log"]),
            },
            expected,
        )

    def test_counts_conservation_and_completion_are_recomputed(self):
        ledger_lines = self.artifacts["native_ledger.psv"].decode("ascii").splitlines()
        self.assertEqual(
            ledger_lines[0],
            "SCHEMA|redred.cluster2_cav_bridge.native_ledger/v1",
        )
        event_lines = ledger_lines[1:-1]
        delivered = sum("|DELIVERED|" in line for line in event_lines)
        overrun = sum("|OVERRUN|" in line for line in event_lines)
        counts = self.receipt["counts"]
        self.assertEqual(len(ledger_lines), counts["native_ledger_lines"])
        self.assertEqual(len(event_lines), counts["generated"])
        self.assertEqual(delivered, counts["delivered"])
        self.assertEqual(overrun, counts["overrun"])
        self.assertEqual(counts["generated"], delivered + overrun)
        self.assertEqual(
            ledger_lines[-1],
            "SUMMARY|%d|%d|%d" % (counts["generated"], delivered, overrun),
        )

        outcomes = self.artifacts["transport_outcomes.jsonl"].splitlines()
        self.assertEqual(len(outcomes), counts["transport_outcome_rows"])
        for line in outcomes:
            value = json.loads(line.decode("ascii"), object_pairs_hook=unique_object)
            self.assertEqual(
                value["schema"],
                "redred.cluster2_cav_bridge.transport_outcome/v1",
            )

        cyclemask = self.artifacts[
            "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
        ]
        replayed = parse_native_ledger(cyclemask, self.artifacts["native_ledger.psv"])
        self.assertEqual(
            canonical_transport_outcome_jsonl(replayed),
            self.artifacts["transport_outcomes.jsonl"],
        )

        pass_line = (
            "REDRED_CLUSTER2_NATIVE_LEDGER_PASS generated=8503 "
            "delivered=8503 overrun=0"
        )
        self.assertEqual(
            self.artifacts["run.log"].decode("utf-8").splitlines().count(pass_line),
            1,
        )
        self.assertEqual(self.artifacts["owner-native-ca446aa.exitcode"], b"0\n")
        outer_lines = self.artifacts["owner-native-ca446aa.stdout"].decode("utf-8").splitlines()
        self.assertEqual(
            sum(line.startswith("NATIVE_OBSERVATIONAL_PASS ") for line in outer_lines),
            1,
        )
        locale_warning = "/bin/sh: warning: setlocale: LC_ALL: cannot change locale (C.UTF-8)"
        self.assertEqual(outer_lines.count(locale_warning), 9)
        self.assertEqual(
            self.receipt["execution"]["outer_runner_environment_warnings"],
            {"setlocale_C_UTF_8": 9},
        )

    def test_xrun_severities_and_scope_are_exact(self):
        xrun_text = self.artifacts["xrun.log"].decode("utf-8")
        self.assertEqual(xrun_text.count("*W,DLCPTH"), 165)
        self.assertEqual(xrun_text.count("*E,"), 0)
        self.assertEqual(xrun_text.count("*F,"), 0)
        self.assertIn("xrun(64)\t23.09-s013", xrun_text)
        self.assertEqual(self.receipt["execution"]["status"], "PASS")
        self.assertEqual(self.receipt["execution"]["warnings_by_code"], {"DLCPTH": 165})
        self.assertEqual(self.receipt["scope"]["cav_functional"], "HOLD_NOT_EVALUATED")
        self.assertEqual(self.receipt["scope"]["ppa"], "HOLD_NOT_EVALUATED")
        self.assertEqual(self.receipt["scope"]["wire_complete_cav"], "HOLD_NOT_EVALUATED")
        post_run = self.receipt["post_run_observations"]
        self.assertEqual(post_run["faer_global_zero_write"], "NOT_CLAIMED")
        self.assertEqual(post_run["outer_runner_exit_code"], 0)
        self.assertEqual(post_run["outer_runner_final_pass_count"], 1)
        self.assertEqual(post_run["post_run_scoped_authority_reverification"], "PASS")

    def test_receipt_schema_types_and_outer_tokens_are_exact(self):
        self.assertEqual(
            set(self.receipt),
            {
                "artifact_bundle",
                "artifact_digests",
                "authority",
                "counts",
                "execution",
                "input_authority",
                "invariants",
                "post_run_observations",
                "schema",
                "scope",
                "server_reported_completed_at",
            },
        )
        self.assertEqual(
            self.receipt["schema"],
            "redred.cluster2_cav_bridge.server_native_observation_receipt/v1",
        )
        self.assertEqual(
            set(self.receipt["authority"]),
            {"bridge_commit", "ganghee_content_provenance_commit", "mode", "repository", "source_repository"},
        )
        self.assertEqual(
            set(self.receipt["counts"]),
            {"delivered", "generated", "native_ledger_lines", "overrun", "transport_outcome_rows"},
        )
        for value in self.receipt["counts"].values():
            self.assertIs(type(value), int)
        self.assertIs(type(self.receipt["execution"]["errors"]), int)
        self.assertIs(type(self.receipt["execution"]["fatals"]), int)
        self.assertIs(
            type(self.receipt["execution"]["warnings_by_code"]["DLCPTH"]), int
        )
        for key, value in self.receipt["invariants"].items():
            if key.endswith("_holds") or key.startswith("no_") or key.endswith("_exact"):
                self.assertIs(type(value), bool)
        self.assertEqual(
            self.receipt["scope"],
            {
                "arrival_semantics": "one-cycle native pulse; overrun is terminal for this observational scope",
                "cav_functional": "HOLD_NOT_EVALUATED",
                "common_held_valid_seam": "HOLD_NOT_EVALUATED",
                "physical_time_interpretation": "HOLD; 1 ms dataset bins are workload indices, not 2 ns hardware timestamps",
                "ppa": "HOLD_NOT_EVALUATED",
                "uzh_raw_source_reproduction": "SEPARATE_AUDIT_NOT_BOUND_TO_THIS_RECEIPT",
                "wire_complete_cav": "HOLD_NOT_EVALUATED",
            },
        )

        outer_lines = self.artifacts["owner-native-ca446aa.stdout"].decode("utf-8").splitlines()
        pass_lines = [line for line in outer_lines if line.startswith("NATIVE_OBSERVATIONAL_PASS ")]
        cyclemask = self.receipt["input_authority"]["cyclemask"]
        expected_prefix = (
            "NATIVE_OBSERVATIONAL_PASS authority_mode=%s simulator=xrun events=%d "
            "cyclemask_encoding=%s raw_sha256=%s semantic_lf_sha256=%s output_root="
            % (
                self.receipt["authority"]["mode"],
                self.receipt["counts"]["generated"],
                cyclemask["line_endings"],
                cyclemask["raw_sha256"],
                cyclemask["canonical_semantic_lf_sha256"],
            )
        )
        self.assertEqual(len(pass_lines), 1)
        self.assertTrue(pass_lines[0].startswith(expected_prefix + "/tmp/redred-cluster2-native-"))

    def test_authority_paths_hashes_and_commits_are_bound(self):
        self.assertEqual(
            self.receipt["authority"]["bridge_commit"],
            "ca446aa45f4b838435fda9f47dade925f7951e5b",
        )
        self.assertEqual(
            self.artifacts["owner-bridge-commit-ca446aa.txt"],
            (self.receipt["authority"]["bridge_commit"] + "\n").encode("ascii"),
        )
        authority = json.loads(AUTHORITY_PATH.read_text(encoding="ascii"))
        receipt_files = self.receipt["input_authority"]["code_files"]
        for row in receipt_files:
            self.assert_normalized_relative(row["path"])
        self.assertEqual(
            {(row["path"], row["sha256"]) for row in receipt_files},
            {(row["path"], row["sha256"]) for row in authority["code_files"]},
        )
        self.assertEqual(len(receipt_files), len({row["path"] for row in receipt_files}))
        compiled_by_path = {
            "rtl/arbiter2.v": self.artifacts["faer_snapshot/rtl/arbiter2.v"],
            "rtl/arbiter4_tree.v": self.artifacts["faer_snapshot/rtl/arbiter4_tree.v"],
            "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v": self.artifacts[
                "faer_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v"
            ],
        }
        for row in receipt_files:
            if row["path"] in compiled_by_path:
                self.assertEqual(sha256(compiled_by_path[row["path"]]), row["sha256"])
        self.assertEqual(
            self.artifacts["bridge_snapshot/redred_cluster2_native_observational_tb.sv"],
            TB_PATH.read_bytes(),
        )
        cyclemask = self.receipt["input_authority"]["cyclemask"]
        self.assert_normalized_relative(cyclemask["path"])
        bundled_cyclemask = self.artifacts[
            "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
        ]
        encoding = inspect_cyclemask_encoding(bundled_cyclemask)
        self.assertEqual(cyclemask["line_endings"], encoding.line_endings)
        self.assertEqual(cyclemask["raw_sha256"], encoding.raw_sha256)
        self.assertEqual(
            cyclemask["canonical_semantic_lf_sha256"],
            encoding.canonical_semantic_lf_sha256,
        )
        self.assertEqual(
            cyclemask["raw_sha256"],
            authority["tracked_cyclemask"]["accepted_raw_encodings"][1]["sha256"],
        )
        self.assertEqual(
            cyclemask["canonical_semantic_lf_sha256"],
            authority["tracked_cyclemask"]["canonical_semantic_lf_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
