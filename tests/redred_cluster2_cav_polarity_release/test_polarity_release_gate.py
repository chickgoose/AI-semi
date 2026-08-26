from __future__ import annotations

from collections import deque
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


def git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, check=False,
    )
    if check and completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def raw_cycle_evidence(crlf_trace: bool = False):
    """Produce 8,503 arrivals with a replay-derived nonzero overrun split."""
    trace_lines = []
    cycle_lines = []
    queue = deque()
    delivered = overrun = 0
    for cycle in range(gate.EXPECTED_GENERATED):
        polarity = cycle & 1
        trace_lines.append("%d 0001 %04x" % (cycle, polarity))
        observed_overrun = 1 if len(queue) == 2 else 0
        retire = bool(queue) and cycle % 2 == 1
        if retire:
            hw_polarity = queue.popleft()
            lane1 = "1|0|1|%x" % hw_polarity
            delivered += 1
        else:
            lane1 = "0|0|0|0"
        cycle_lines.append(
            "CYCLE|%d|%04x|0|0|0|0|%s" % (cycle, observed_overrun, lane1)
        )
        if observed_overrun:
            overrun += 1
        else:
            queue.append(polarity)

    cycle = gate.EXPECTED_GENERATED
    while queue:
        hw_polarity = queue.popleft()
        cycle_lines.append(
            "CYCLE|%d|0000|0|0|0|0|1|0|1|%x" % (cycle, hw_polarity)
        )
        delivered += 1
        cycle += 1
    cycle_lines.append("CYCLE|%d|0000|0|0|0|0|0|0|0|0" % cycle)
    ledger_lines = [
        "SCHEMA|" + gate.RAW_LEDGER_SCHEMA,
        "SCOPE|" + gate.IDENTITY_SCOPE,
        *cycle_lines,
        "SUMMARY|%d|%d|%d|0|0|1" % (
            gate.EXPECTED_GENERATED, delivered, overrun,
        ),
    ]
    trace = ("\n".join(trace_lines) + "\n").encode("ascii")
    if crlf_trace:
        trace = trace.replace(b"\n", b"\r\n")
    ledger = ("\n".join(ledger_lines) + "\n").encode("ascii")
    return trace, ledger


class ReleaseFixture:
    def __init__(self, root: Path, crlf_trace: bool = False):
        self.root = root
        git(root, "init", "-q")
        git(root, "config", "user.name", "Polarity Gate Test")
        git(root, "config", "user.email", "polarity-gate@example.invalid")
        self.source_commit = gate.SOURCE_COMMIT
        self.paths = {
            "filelist": "release/polarity_v1.f",
            "arbiter2": gate.EXPECTED_SOURCE_PATHS[0],
            "arbiter4": gate.EXPECTED_SOURCE_PATHS[1],
            "rtl": gate.EXPECTED_SOURCE_PATHS[2],
            "tb": "tests/redred_cluster2_cav_bridge/redred_cluster2_polarity_v1_native_observational_tb.sv",
            "runner": "tests/redred_cluster2_cav_bridge/run_polarity_v1_native_observational.py",
            "trace": "common_traces_uzh/uzh_shapes_rotation_patch.addrpol.txt",
            "ledger": "evidence/uzh_shapes_rotation_patch.polarity_native_cycle.ledger",
            "verifier": "benchmarks/redred_cluster2_cav_bridge/polarity_native_ledger.py",
            "receipt": "evidence/polarity_v1_release_receipt.json",
            "integration": "evidence/polarity_v1_integration_authority.json",
            "manifest": gate.DEFAULT_MANIFEST,
        }
        self._create(crlf_trace)

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
            "role": role, "scope": scope, "path": path,
            "sha256": gate.sha256(raw), "text": self.text(raw, crlf),
        }

    def source_binding(self, path: str) -> dict:
        raw = (self.root / path).read_bytes()
        return {"path": path, "sha256": gate.sha256(raw), "text": self.text(raw)}

    def _create(self, crlf_trace: bool) -> None:
        write(self.root, self.paths["arbiter2"], b"module arbiter2; endmodule\n")
        write(self.root, self.paths["arbiter4"], b"module arbiter4_tree; endmodule\n")
        rtl = (
            "module %s(input polarity_in, output pol_mask0, output pol_mask1);\n"
            "assign pol_mask0=polarity_in; assign pol_mask1=polarity_in; endmodule\n"
            % gate.EXPECTED_TOP
        ).encode("ascii")
        write(self.root, self.paths["rtl"], rtl)
        filelist = ("\n".join(gate.EXPECTED_SOURCE_PATHS) + "\n").encode("ascii")
        write(self.root, self.paths["filelist"], filelist)

        tb = (
            "module %s; logic polarity_in; logic [3:0] pol_mask0, pol_mask1;\n"
            "%s dut(.polarity_in(polarity_in),.pol_mask0(pol_mask0),.pol_mask1(pol_mask1));\n"
            '// $fatal(1, "polarity mismatch");\n'
            '// $display("REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%%0d delivered=%%0d overrun=%%0d polarity_checked=%%0d");\n'
            "endmodule\n" % (gate.EXPECTED_TB_TOP, gate.EXPECTED_TOP)
        ).encode("ascii")
        write(self.root, self.paths["tb"], tb)
        trace, ledger = raw_cycle_evidence(crlf_trace)
        write(self.root, self.paths["trace"], trace)
        write(self.root, self.paths["ledger"], ledger)
        verifier = (
            'LEDGER_SCHEMA = "%s"\nIDENTITY_SCOPE = "%s"\n'
            "def verify_polarity_native_ledger(trace_payload, ledger_payload):\n"
            "    raise NotImplementedError\n" % (gate.RAW_LEDGER_SCHEMA, gate.IDENTITY_SCOPE)
        ).encode("ascii")
        write(self.root, self.paths["verifier"], verifier)
        runner = (
            'PINNED_COMMIT = "%s"\nTRACE_SHA256 = "%s"\nTB_SHA256 = "%s"\n'
            'PASS = "POLARITY_V1_NATIVE_PASS commit=%%s simulator=%%s events=%%d output_root=%%s"\n'
            % (gate.SOURCE_COMMIT, gate.sha256(trace), gate.sha256(tb))
        ).encode("ascii")
        write(self.root, self.paths["runner"], runner)

        preliminary = {
            "rtl": self.binding("polarity_v1_rtl", self.paths["rtl"], "source"),
            "tb": self.binding("polarity_v1_tb", self.paths["tb"], "receipt"),
            "runner": self.binding("polarity_v1_runner", self.paths["runner"], "receipt"),
            "trace": self.binding("polarity_v1_trace", self.paths["trace"], "source", crlf_trace),
            "ledger": self.binding("polarity_v1_cycle_ledger", self.paths["ledger"], "receipt"),
            "verifier": self.binding("polarity_v1_independent_verifier", self.paths["verifier"], "receipt"),
        }
        self.report = dict(gate.verify_raw_cycle_evidence(trace, ledger))
        receipt_roles = (
            "rtl", "tb", "runner", "trace", "ledger", "verifier",
        )
        receipt = {
            "schema": gate.RECEIPT_SCHEMA,
            "status": "PASS",
            "source": {"repository": gate.EXPECTED_SOURCE_REPOSITORY, "commit": self.source_commit},
            "compatibility": {
                "reference_commit": gate.VERIFIER_REFERENCE_COMMIT,
                "raw_ledger_schema": gate.RAW_LEDGER_SCHEMA,
                "identity_scope": gate.IDENTITY_SCOPE,
                "counts_source": "INDEPENDENT_RAW_TRACE_PLUS_CYCLE_LEDGER_REPLAY",
            },
            "bindings": {preliminary[key]["role"]: preliminary[key]["sha256"] for key in receipt_roles},
            "report": self.report,
        }
        write(self.root, self.paths["receipt"], gate.canonical_json(receipt))
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "create independently replayed receipt")
        self.receipt_commit = git(self.root, "rev-parse", "HEAD")

        receipt_binding = self.binding("polarity_v1_receipt", self.paths["receipt"], "receipt")
        artifact_bindings = [*preliminary.values(), receipt_binding]
        integration_authority = {
            "schema": gate.INTEGRATION_SCHEMA,
            "status": "GO",
            "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
            "source": {"repository": gate.EXPECTED_SOURCE_REPOSITORY, "commit": self.source_commit},
            "receipt_commit": self.receipt_commit,
            "top": gate.EXPECTED_TOP,
            "filelist_sha256": gate.sha256(filelist),
            "bindings": {item["role"]: item["sha256"] for item in artifact_bindings},
            "verification_report_sha256": gate._report_digest(self.report),
            "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
        }
        write(self.root, self.paths["integration"], gate.canonical_json(integration_authority))
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "publish explicit integration authority")
        self.integration_commit = git(self.root, "rev-parse", "HEAD")

        artifacts = [
            *artifact_bindings,
            self.binding("integration_authority", self.paths["integration"], "integration"),
        ]
        self.document = {
            "schema": gate.SCHEMA,
            "authority": {
                "source_repository": gate.EXPECTED_SOURCE_REPOSITORY,
                "source_commit": self.source_commit,
                "receipt_commit": self.receipt_commit,
            },
            "source": {
                "top": gate.EXPECTED_TOP,
                "filelist": self.source_binding(self.paths["filelist"]),
                "files": [self.source_binding(path) for path in gate.EXPECTED_SOURCE_PATHS],
            },
            "artifacts": artifacts,
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
    def fixture(self, crlf_trace: bool = False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return ReleaseFixture(Path(temporary.name), crlf_trace)

    def reject(self, fixture: ReleaseFixture, mutation, pattern: str) -> None:
        document = copy.deepcopy(fixture.document)
        mutation(document)
        with self.assertRaisesRegex(gate.ReleaseHold, pattern):
            gate.validate_release(fixture.root, document)

    def test_complete_release_derives_conserving_nonzero_overrun(self) -> None:
        fixture = self.fixture()
        result = gate.validate_release(fixture.root, fixture.document)
        self.assertEqual(result["status"], "GO")
        self.assertEqual(result["generated"], 8503)
        self.assertGreater(result["overrun"], 0)
        self.assertLess(result["delivered"], 8503)
        self.assertEqual(result["delivered"] + result["overrun"], 8503)
        self.assertNotIn("counts", fixture.document)

    def test_external_source_commit_is_pinned_but_not_required_locally(self) -> None:
        fixture = self.fixture()
        completed = subprocess.run(
            ["git", "-C", str(fixture.root), "cat-file", "-e", gate.SOURCE_COMMIT + "^{commit}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(gate.validate_release(fixture.root, fixture.document)["status"], "GO")

    def test_declared_crlf_trace_is_replayed_but_cycle_ledger_must_be_lf(self) -> None:
        fixture = self.fixture(crlf_trace=True)
        self.assertEqual(gate.validate_release(fixture.root, fixture.document)["status"], "GO")
        trace, ledger = raw_cycle_evidence()
        with self.assertRaisesRegex(gate.ReleaseHold, "canonical LF"):
            gate.verify_raw_cycle_evidence(trace, ledger.replace(b"\n", b"\r\n"))

    def test_current_tree_and_missing_actual_receipt_hold(self) -> None:
        result = gate.evaluate_repository(ROOT)
        self.assertEqual(result["status"], "HOLD")
        fixture = self.fixture()
        (fixture.root / fixture.paths["receipt"]).unlink()
        missing = gate.evaluate_repository(fixture.root)
        self.assertEqual(missing["status"], "HOLD")
        self.assertIn("polarity_v1_receipt is unavailable", missing["reason"])

    def test_event_jsonl_or_false_summary_cannot_supply_release_counts(self) -> None:
        with self.assertRaisesRegex(gate.ReleaseHold, "not canonical"):
            gate.verify_raw_cycle_evidence(
                b'{"event_id":0}\n', b'{"event_id":0,"outcome":"DELIVERED"}\n'
            )
        trace, ledger = raw_cycle_evidence()
        false_summary = ledger.rsplit(b"SUMMARY|", 1)[0] + b"SUMMARY|8503|8503|0|0|0|1\n"
        with self.assertRaisesRegex(gate.ReleaseHold, "summary differs"):
            gate.verify_raw_cycle_evidence(trace, false_summary)

    def test_polarity_is_checked_against_raw_fifo_front(self) -> None:
        trace, ledger = raw_cycle_evidence()
        lines = ledger.decode("ascii").splitlines()
        for index, line in enumerate(lines):
            if line.startswith("CYCLE|") and line.split("|")[7] == "1":
                fields = line.split("|")
                fields[10] = "1" if fields[10] == "0" else "0"
                lines[index] = "|".join(fields)
                break
        mutated = ("\n".join(lines) + "\n").encode("ascii")
        with self.assertRaisesRegex(gate.ReleaseHold, "hw_polarity differs"):
            gate.verify_raw_cycle_evidence(trace, mutated)

    def test_manifest_cannot_predeclare_counts_or_omit_roles(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture, lambda value: value.update({"counts": {"delivered": 8503}}),
            "release manifest keys differ",
        )
        self.reject(fixture, lambda value: value["artifacts"].pop(), "role inventory differs")

    def test_receipt_report_must_equal_independent_replay(self) -> None:
        fixture = self.fixture()
        receipt_path = fixture.root / fixture.paths["receipt"]
        receipt = json.loads(receipt_path.read_bytes())
        receipt["report"]["delivered"] += 1
        receipt_path.write_bytes(gate.canonical_json(receipt))
        document = copy.deepcopy(fixture.document)
        binding = next(item for item in document["artifacts"] if item["role"] == "polarity_v1_receipt")
        binding["sha256"] = gate.sha256(receipt_path.read_bytes())
        binding["text"] = fixture.text(receipt_path.read_bytes())
        with self.assertRaisesRegex(gate.ReleaseHold, "report differs from independently replayed"):
            gate.validate_release(fixture.root, document)

    def test_actual_observational_tb_and_runner_tokens_are_required(self) -> None:
        fixture = self.fixture()
        runner_role = next(index for index, item in enumerate(fixture.document["artifacts"]) if item["role"] == "polarity_v1_runner")
        self.reject(
            fixture,
            lambda value: value["artifacts"][runner_role]["text"].update({"semantic_lf_sha256": "00" * 32}),
            "semantic LF hash differs",
        )
        runner = (fixture.root / fixture.paths["runner"]).read_text()
        self.assertIn("POLARITY_V1_NATIVE_PASS commit=%s simulator=%s events=%d output_root=%s", runner)

    def test_source_filelist_is_exact_ordered_and_wildcard_free(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture,
            lambda value: value["source"]["files"][0].update({"path": "rtl/*.v"}),
            "explicit normalized path",
        )
        self.reject(
            fixture, lambda value: value["source"]["files"].reverse(),
            "exact polarity-v1 source closure",
        )

    def test_local_receipt_and_integration_commit_authority_is_enforced(self) -> None:
        fixture = self.fixture()
        self.reject(
            fixture, lambda value: value["integration"].update({"commit": "22" * 20}),
            "git authority check failed",
        )
        self.reject(
            fixture, lambda value: value["integration"].update({"release_authority": False}),
            "explicit integration authority differs",
        )

    def test_duplicate_json_nonfinite_and_cli_hold_fail_closed(self) -> None:
        with self.assertRaisesRegex(gate.ReleaseHold, "duplicate JSON key"):
            gate.parse_canonical_json(b'{"a":1,"a":2}\n', "duplicate")
        with self.assertRaisesRegex(gate.ReleaseHold, "non-finite"):
            gate.parse_canonical_json(b'{"a":NaN}\n', "nonfinite")
        completed = subprocess.run(
            [sys.executable, str(GATE_PATH), "--root", str(ROOT), "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "HOLD")


if __name__ == "__main__":
    unittest.main()
