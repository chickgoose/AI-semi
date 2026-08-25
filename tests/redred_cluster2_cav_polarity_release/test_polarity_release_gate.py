from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GATE_PATH = HERE / "polarity_release_gate.py"
SPEC = importlib.util.spec_from_file_location("polarity_release_gate", GATE_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def jsonl(rows) -> bytes:
    return b"".join(gate.canonical_json(row) for row in rows)


class ReleaseFixture:
    def __init__(self, root: Path, crlf_streams: bool = False):
        self.root = root
        git(root, "init", "-q")
        git(root, "config", "user.name", "Polarity Gate Test")
        git(root, "config", "user.email", "polarity-gate@example.invalid")
        self.source_commit = "11" * 20
        self.paths = {
            "filelist": "release/polarity_v1.f",
            "arbiter2": "rtl/arbiter2.v",
            "arbiter4": "rtl/arbiter4_tree.v",
            "rtl": "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
            "tb": "tb/tb_steal_buf_polarity_v1.v",
            "trace": "evidence/polarity_v1_trace.jsonl",
            "ledger": "evidence/polarity_v1_ledger.jsonl",
            "receipt": "evidence/polarity_v1_receipt.json",
            "integration": "evidence/polarity_v1_integration_authority.json",
            "manifest": gate.DEFAULT_MANIFEST,
        }
        self._create(crlf_streams)

    @staticmethod
    def text(raw: bytes, crlf: bool = False) -> dict:
        normalized = raw.replace(b"\r\n", b"\n") if crlf else raw
        return {
            "line_endings": "CRLF" if crlf else "LF",
            "semantic_lf_sha256": gate.sha256(normalized),
        }

    def binding(self, role: str, path: str, scope: str, crlf: bool = False) -> dict:
        raw = (self.root / path).read_bytes()
        return {
            "role": role,
            "scope": scope,
            "path": path,
            "sha256": gate.sha256(raw),
            "text": self.text(raw, crlf),
        }

    def source_binding(self, path: str) -> dict:
        raw = (self.root / path).read_bytes()
        return {
            "path": path,
            "sha256": gate.sha256(raw),
            "text": self.text(raw),
        }

    def _create(self, crlf_streams: bool) -> None:
        rtl = (
            "module %s(input polarity_in, output pol_mask0, output pol_mask1);\n"
            "assign pol_mask0=polarity_in; assign pol_mask1=polarity_in; endmodule\n"
            % gate.EXPECTED_TOP
        ).encode("ascii")
        tb = (
            "module polarity_v1_tb; %s dut();\n"
            "// POLARITY_MISMATCH is fatal; POLARITY_V1_PASS is the sole pass token.\n"
            "endmodule\n" % gate.EXPECTED_TOP
        ).encode("ascii")
        write(self.root, self.paths["arbiter2"], b"module arbiter2; endmodule\n")
        write(self.root, self.paths["arbiter4"], b"module arbiter4_tree; endmodule\n")
        write(self.root, self.paths["rtl"], rtl)
        write(self.root, self.paths["tb"], tb)
        filelist = (
            self.paths["arbiter2"] + "\n" + self.paths["arbiter4"] + "\n" +
            self.paths["rtl"] + "\n"
        ).encode("ascii")
        write(self.root, self.paths["filelist"], filelist)

        trace_rows = []
        ledger_rows = []
        for event_id in range(gate.EXPECTED_EVENTS):
            source = event_id % 16
            polarity = event_id % 2
            trace_rows.append({
                "schema": gate.TRACE_SCHEMA,
                "event_id": event_id,
                "source_index": source,
                "occurrence_cycle": event_id // 2,
                "polarity": polarity,
            })
            ledger_rows.append({
                "schema": gate.LEDGER_SCHEMA,
                "event_id": event_id,
                "source_index": source,
                "outcome": "DELIVERED",
                "expected_polarity": polarity,
                "observed_polarity": polarity,
            })
        trace = jsonl(trace_rows)
        ledger = jsonl(ledger_rows)
        if crlf_streams:
            trace = trace.replace(b"\n", b"\r\n")
            ledger = ledger.replace(b"\n", b"\r\n")
        write(self.root, self.paths["trace"], trace)
        write(self.root, self.paths["ledger"], ledger)

        preliminary = {
            "rtl": self.binding("polarity_v1_rtl", self.paths["rtl"], "source"),
            "tb": self.binding("polarity_v1_tb", self.paths["tb"], "source"),
            "trace": self.binding(
                "polarity_v1_trace", self.paths["trace"], "source", crlf_streams
            ),
            "ledger": self.binding(
                "polarity_v1_ledger", self.paths["ledger"], "receipt", crlf_streams
            ),
        }
        counts = {
            "generated": gate.EXPECTED_EVENTS,
            "delivered": gate.EXPECTED_EVENTS,
            "overrun": 0,
            "polarity_mismatch": 0,
        }
        receipt = {
            "schema": gate.RECEIPT_SCHEMA,
            "status": "PASS",
            "source_commit": self.source_commit,
            "bindings": {
                "polarity_v1_rtl": preliminary["rtl"]["sha256"],
                "polarity_v1_tb": preliminary["tb"]["sha256"],
                "polarity_v1_trace": preliminary["trace"]["sha256"],
                "polarity_v1_ledger": preliminary["ledger"]["sha256"],
            },
            "counts": counts,
        }
        write(self.root, self.paths["receipt"], gate.canonical_json(receipt))
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "create polarity receipt")
        self.receipt_commit = git(self.root, "rev-parse", "HEAD")

        receipt_binding = self.binding(
            "polarity_v1_receipt", self.paths["receipt"], "receipt"
        )
        integration_binding_hashes = {
            "polarity_v1_rtl": preliminary["rtl"]["sha256"],
            "polarity_v1_tb": preliminary["tb"]["sha256"],
            "polarity_v1_trace": preliminary["trace"]["sha256"],
            "polarity_v1_ledger": preliminary["ledger"]["sha256"],
            "polarity_v1_receipt": receipt_binding["sha256"],
        }
        integration_authority = {
            "schema": gate.INTEGRATION_SCHEMA,
            "status": "GO",
            "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
            "source_commit": self.source_commit,
            "receipt_commit": self.receipt_commit,
            "top": gate.EXPECTED_TOP,
            "filelist_sha256": gate.sha256(filelist),
            "bindings": integration_binding_hashes,
            "counts": counts,
            "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
        }
        write(
            self.root,
            self.paths["integration"],
            gate.canonical_json(integration_authority),
        )
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "publish integration authority")
        self.integration_commit = git(self.root, "rev-parse", "HEAD")

        artifacts = [
            preliminary["rtl"],
            preliminary["tb"],
            preliminary["trace"],
            preliminary["ledger"],
            receipt_binding,
            self.binding(
                "integration_authority", self.paths["integration"], "integration"
            ),
        ]
        source_files = [
            self.source_binding(self.paths["arbiter2"]),
            self.source_binding(self.paths["arbiter4"]),
            self.source_binding(self.paths["rtl"]),
        ]
        filelist_binding = self.source_binding(self.paths["filelist"])
        self.document = {
            "schema": gate.SCHEMA,
            "authority": {
                "source_repository": "https://github.com/GangHeeJo/AI-SEMI",
                "source_commit": self.source_commit,
                "receipt_commit": self.receipt_commit,
            },
            "source": {
                "top": gate.EXPECTED_TOP,
                "filelist": filelist_binding,
                "files": source_files,
            },
            "artifacts": artifacts,
            "counts": counts,
            "integration": {
                "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
                "release_authority": True,
                "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
                "commit": self.integration_commit,
                "authority_artifact_role": "integration_authority",
            },
        }
        write(self.root, self.paths["manifest"], gate.canonical_json(self.document))
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "add release manifest")


class PolarityReleaseGateTests(unittest.TestCase):
    def fixture(self, crlf_streams: bool = False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return ReleaseFixture(Path(temporary.name), crlf_streams)

    def reject(self, fixture: ReleaseFixture, mutation, pattern: str) -> None:
        document = copy.deepcopy(fixture.document)
        mutation(document)
        with self.assertRaisesRegex(gate.ReleaseHold, pattern):
            gate.validate_release(fixture.root, document)

    def test_complete_lf_release_is_go(self) -> None:
        fixture = self.fixture()
        result = gate.validate_release(fixture.root, fixture.document)
        self.assertEqual(result["status"], "GO")
        self.assertEqual(result["generated"], 8503)
        self.assertEqual(result["polarity_mismatch"], 0)
        self.assertEqual(gate.evaluate_repository(fixture.root)["status"], "GO")

    def test_declared_crlf_trace_and_ledger_are_go(self) -> None:
        fixture = self.fixture(crlf_streams=True)
        self.assertEqual(
            gate.validate_release(fixture.root, fixture.document)["status"], "GO"
        )

    def test_missing_manifest_holds_current_tree(self) -> None:
        result = gate.evaluate_repository(ROOT)
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("release manifest is unavailable", result["reason"])

    def test_exact_raw_and_semantic_hashes_are_mandatory(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["artifacts"][0].update({"sha256": "00" * 32}),
            "raw SHA-256 differs",
        )
        self.reject(
            fixture,
            lambda value: value["artifacts"][2]["text"].update(
                {"semantic_lf_sha256": "00" * 32}
            ),
            "semantic LF hash differs",
        )

    def test_source_filelist_is_explicit_ordered_and_wildcard_free(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["source"]["files"][0].update(
                {"path": "rtl/*.v"}
            ),
            "explicit normalized path",
        )
        self.reject(
            fixture,
            lambda value: value["source"]["files"].reverse(),
            "filelist order differs",
        )
        self.reject(
            fixture,
            lambda value: value["source"]["files"][0].update({"path": "-f"}),
            "explicit normalized path",
        )

    def test_source_repository_authority_is_exact(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["authority"].update(
                {"source_repository": "https://github.com.example/GangHeeJo/AI-SEMI"}
            ),
            "exact Ganghee authority",
        )

    def test_source_receipt_and_integration_stages_are_distinct(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["authority"].update(
                {"source_commit": fixture.receipt_commit}
            ),
            "source commit must be distinct",
        )
        self.reject(
            fixture,
            lambda value: value["integration"].update(
                {"commit": fixture.receipt_commit}
            ),
            "receipt commit must be distinct",
        )

    def test_every_polarity_v1_role_is_required_once_at_fixed_scope(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["artifacts"].pop(),
            "role inventory differs",
        )
        self.reject(
            fixture,
            lambda value: value["artifacts"][0].update({"scope": "integration"}),
            "scope differs",
        )

    def test_declared_counts_cannot_promote_incomplete_evidence(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["counts"].update({"delivered": 8502}),
            "conservation",
        )
        self.reject(
            fixture,
            lambda value: value["counts"].update({"polarity_mismatch": 1}),
            "conservation",
        )

    def test_trace_and_ledger_recompute_polarity_and_identity(self) -> None:
        fixture = self.fixture()
        trace = (
            fixture.root / fixture.paths["trace"]
        ).read_bytes().replace(b'"polarity":0', b'"polarity":1', 1)
        ledger = (fixture.root / fixture.paths["ledger"]).read_bytes()
        with self.assertRaisesRegex(gate.ReleaseHold, "polarity binding differs"):
            gate._verify_trace_and_ledger(trace, ledger)

        one_short = ledger.rsplit(b"\n", 2)[0] + b"\n"
        with self.assertRaisesRegex(gate.ReleaseHold, "exactly 8503"):
            gate._verify_trace_and_ledger(
                (fixture.root / fixture.paths["trace"]).read_bytes(), one_short
            )

    def test_receipt_must_bind_the_same_exact_artifacts(self) -> None:
        fixture = self.fixture()
        receipt_role = next(
            value for value in fixture.document["artifacts"]
            if value["role"] == "polarity_v1_receipt"
        )
        original = receipt_role["sha256"]
        self.reject(
            fixture,
            lambda value: next(
                item for item in value["artifacts"]
                if item["role"] == "polarity_v1_receipt"
            ).update({"sha256": "00" * 32}),
            "raw SHA-256 differs",
        )
        self.assertNotEqual(original, "00" * 32)

    def test_explicit_integration_release_authority_is_mandatory(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["integration"].update({"release_authority": False}),
            "explicit integration authority differs",
        )
        self.reject(
            fixture,
            lambda value: value["integration"].update(
                {"polarity_transport": "TB_SIDE_ONLY"}
            ),
            "explicit integration authority differs",
        )

    def test_git_commit_blob_binding_is_enforced(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["integration"].update({"commit": "22" * 20}),
            "git authority check failed",
        )

    def test_duplicate_json_and_nonfinite_values_fail_closed(self) -> None:
        with self.assertRaisesRegex(gate.ReleaseHold, "duplicate JSON key"):
            gate.parse_canonical_json(b'{"a":1,"a":2}\n', "duplicate")
        with self.assertRaisesRegex(gate.ReleaseHold, "non-finite"):
            gate.parse_canonical_json(b'{"a":NaN}\n', "nonfinite")

    def test_cli_reports_hold_with_exit_two_before_artifact_cherry_picks(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(GATE_PATH), "--root", str(ROOT), "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "HOLD")


if __name__ == "__main__":
    unittest.main()
