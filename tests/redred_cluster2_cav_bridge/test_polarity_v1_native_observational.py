from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge.polarity_native_ledger import (
    IDENTITY_SCOPE,
    LEDGER_SCHEMA,
    verify_polarity_native_ledger,
)
import tests.redred_cluster2_cav_bridge.run_polarity_v1_native_observational as runner


ROOT = Path(__file__).resolve().parents[2]
TB = (
    ROOT
    / "tests/redred_cluster2_cav_bridge"
    / "redred_cluster2_polarity_v1_native_observational_tb.sv"
)
RUNNER = (
    ROOT
    / "tests/redred_cluster2_cav_bridge"
    / "run_polarity_v1_native_observational.py"
)
UPSTREAM = Path("/tmp/ganghee-ai-semi-audit-20260825")


def positive_trace():
    return b"0 0111 0010\n1 0001 0001\n2 0001 0000\n"


def positive_raw_ledger():
    return (
        "SCHEMA|%s\n"
        "SCOPE|%s\n"
        "CYCLE|0|0000|0|0|0|0|0|0|0|0\n"
        "CYCLE|1|0000|1|1|1|1|1|2|1|0\n"
        "CYCLE|2|0001|0|0|0|0|1|0|1|0\n"
        "CYCLE|3|0000|0|0|0|0|1|0|1|1\n"
        "CYCLE|4|0000|0|0|0|0|0|0|0|0\n"
        "SUMMARY|5|4|1|0|0|1\n" % (LEDGER_SCHEMA, IDENTITY_SCOPE)
    ).encode("ascii")


class PolarityV1RawInteroperabilityTests(unittest.TestCase):
    def test_runner_uses_da329e3_raw_verifier(self):
        self.assertIs(runner.verify_polarity_native_ledger, verify_polarity_native_ledger)
        report = runner.verify_polarity_native_ledger(
            positive_trace(), positive_raw_ledger()
        )
        self.assertEqual(
            (report.generated, report.delivered, report.overrun),
            (5, 4, 1),
        )
        self.assertFalse(report.identity_order_independence_claimed)

    def test_tb_emits_exact_raw_ledger_grammar_only(self):
        source = TB.read_text(encoding="utf-8")
        self.assertIn(
            '"SCHEMA|redred.cluster2_cav_bridge.polarity_native_ledger/v1\\n"',
            source,
        )
        self.assertIn("SCOPE|" + IDENTITY_SCOPE, source)
        self.assertIn(
            '"CYCLE|%0d|%04x|%0d|%0d|%01x|%01x|%0d|%0d|%01x|%01x\\n"',
            source,
        )
        self.assertIn('"SUMMARY|%0d|%0d|%0d|0|0|1\\n"', source)
        self.assertNotIn('"EVENT|', source)
        self.assertNotIn("event_id", source)
        self.assertNotIn("fifo_event", source)
        self.assertNotIn("fifo_polarity", source)

    def test_runner_has_no_parallel_event_ledger_parser(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("report = verify_polarity_native_ledger(", source)
        self.assertNotIn("def validate_ledger", source)
        self.assertNotIn("def parse_trace", source)
        self.assertIn("identity_order_independence_claimed=false", source)


class PolarityV1SourceAuthorityTests(unittest.TestCase):
    def test_runner_pins_only_explicit_v1_sources(self):
        self.assertEqual(
            runner.RTL_SOURCES,
            (
                (
                    "rtl/arbiter2.v",
                    "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
                ),
                (
                    "rtl/arbiter4_tree.v",
                    "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
                ),
                (
                    "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
                    "20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81",
                ),
            ),
        )
        source = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn("rtl/*.v", source)
        self.assertNotIn(".glob(", source)
        self.assertNotIn(".rglob(", source)
        self.assertNotIn("polarity_v2.v", source)
        self.assertNotIn("steal_buf_pressure.v", source)
        self.assertEqual(runner.PINNED_COMMIT, "44f8918c6e0085f7b75bb90fbe6c099abe1882cc")
        self.assertEqual(runner.TRACE_SHA256, "9f682af4eb11239f0743c2f95a82e4302836ac8a02e68278b8b69464beac55c4")

    @unittest.skipUnless(UPSTREAM.is_dir(), "read-only Ganghee audit clone unavailable")
    def test_public_main_checkout_and_every_pin_match(self):
        verified, status = runner.verify_source_checkout(UPSTREAM, runner.TRACE_PATH)
        self.assertEqual(status, b"")
        self.assertEqual(set(verified), {
            runner.TRACE_PATH,
            "rtl/arbiter2.v",
            "rtl/arbiter4_tree.v",
            "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
        })
        with tempfile.TemporaryDirectory() as temporary:
            rtl, trace, tb = runner._stage(verified, Path(temporary).resolve())
            self.assertEqual(len(rtl), 3)
            self.assertEqual(hashlib.sha256(trace.read_bytes()).hexdigest(), runner.TRACE_SHA256)
            self.assertEqual(hashlib.sha256(tb.read_bytes()).hexdigest(), runner.TB_SHA256)

    def test_authority_precedes_simulator_and_git_is_read_only(self):
        source = RUNNER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any("scor" in name.lower() for name in imported))
        authority = source.index("verified, source_status = verify_source_checkout(")
        simulator = source.index("selected = _select_simulator(")
        execution = source.index("run_output = _run([", simulator)
        self.assertLess(authority, simulator)
        self.assertLess(simulator, execution)
        for forbidden in ("git clone", "git fetch", "git checkout", "git reset"):
            self.assertNotIn(forbidden, source)
        self.assertIn('["config", "--get", "remote.origin.url"]', source)
        self.assertNotIn('["remote", "get-url", "origin"]', source)
        self.assertIn('["status", "--porcelain", "--untracked-files=all", "-z"]', source)
        self.assertIn('environment["TMPDIR"] = str(temporary)', source)

    def test_old_git_origin_lookup_remains_exact_and_fail_closed(self):
        def wrong_origin(_root, arguments):
            if arguments in (["rev-parse", "HEAD"], ["rev-parse", runner.PUBLIC_REF]):
                return (runner.PINNED_COMMIT + "\n").encode("ascii")
            if arguments == ["config", "--get", "remote.origin.url"]:
                return b"https://github.com/GangHeeJo/not-AI-SEMI.git\n"
            self.fail("unexpected Git command: %r" % (arguments,))

        with mock.patch.object(runner, "_git", side_effect=wrong_origin):
            with self.assertRaisesRegex(runner.RunnerError, "origin URL differs"):
                runner.verify_source_checkout(UPSTREAM, runner.TRACE_PATH)

        def missing_origin(_root, arguments):
            if arguments in (["rev-parse", "HEAD"], ["rev-parse", runner.PUBLIC_REF]):
                return (runner.PINNED_COMMIT + "\n").encode("ascii")
            if arguments == ["config", "--get", "remote.origin.url"]:
                raise runner.RunnerError("Git provenance check failed")
            self.fail("unexpected Git command: %r" % (arguments,))

        with mock.patch.object(runner, "_git", side_effect=missing_origin):
            with self.assertRaisesRegex(runner.RunnerError, "Git provenance check failed"):
                runner.verify_source_checkout(UPSTREAM, runner.TRACE_PATH)

        self.assertEqual(
            runner._normalize_repository_url(runner.REPOSITORY_URL + ".git"),
            runner.REPOSITORY_URL,
        )
        self.assertNotEqual(
            runner._normalize_repository_url("git@github.com:GangHeeJo/AI-SEMI.git"),
            runner.REPOSITORY_URL,
        )


class PolarityV1TestbenchTests(unittest.TestCase):
    def test_tb_is_pinned_and_observes_real_v1_ports(self):
        source = TB.read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(TB.read_bytes()).hexdigest(), runner.TB_SHA256)
        self.assertIn(
            "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity dut (",
            source,
        )
        for mapping in (
            ".polarity_in(polarity_in)",
            ".pol_mask0(pol_mask0)",
            ".pol_mask1(pol_mask1)",
        ):
            self.assertIn(mapping, source)
        self.assertNotIn("steal_buf_polarity_v2", source)
        self.assertIn("sampled_overrun = overrun;", source)
        self.assertIn("record_raw_cycle();", source)
        self.assertIn("One unconditional empty-input cycle", source)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "iverilog unavailable")
    def test_tb_compiles_with_only_the_three_pinned_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "tb.vvp"
            command = [
                shutil.which("iverilog"), "-g2012", "-s", runner.TOP,
                "-o", str(executable),
            ]
            command.extend(str(UPSTREAM / path) for path, _digest in runner.RTL_SOURCES)
            command.append(str(TB))
            completed = subprocess.run(
                command, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)


if __name__ == "__main__":
    unittest.main()
