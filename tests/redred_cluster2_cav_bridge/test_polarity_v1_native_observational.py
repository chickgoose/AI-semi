from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

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
    return b"0 0001 0001\n1 0001 0000\n"


def positive_ledger():
    return (
        "SCHEMA|%s\n"
        "EVENT|0|0|0|DELIVERED|1|1|0|0|1\n"
        "EVENT|1|0|1|DELIVERED|2|1|0|0|0\n"
        "SUMMARY|2|2|0|2\n" % runner.LEDGER_SCHEMA
    ).encode("ascii")


class PolarityV1LedgerTests(unittest.TestCase):
    def test_trace_and_ledger_preserve_exact_polarity(self):
        self.assertEqual(
            runner.parse_trace(positive_trace()),
            [(0, 0, 0, 1), (1, 0, 1, 0)],
        )
        self.assertEqual(
            runner.validate_ledger(positive_trace(), positive_ledger()),
            (2, 2, 0, 2),
        )

    def test_polarity_identity_and_native_mutations_fail_closed(self):
        base = positive_ledger()
        mutations = {
            "polarity": base.replace(b"|0|0|1\n", b"|0|0|0\n", 1),
            "source": base.replace(b"EVENT|0|0|0", b"EVENT|0|1|0"),
            "coordinate": base.replace(b"|1|1|0|0|1", b"|1|1|0|1|1"),
            "duplicate": base.replace(b"EVENT|1|0|1", b"EVENT|0|0|1"),
            "summary": base.replace(b"SUMMARY|2|2|0|2", b"SUMMARY|2|2|0|1"),
            "extra": base + b"TRAILING\n",
        }
        for name, payload in mutations.items():
            with self.subTest(name=name), self.assertRaises(runner.RunnerError):
                runner.validate_ledger(positive_trace(), payload)

    def test_trace_bytes_are_strict(self):
        mutations = (
            b"0 0001 0002\n",
            b"00 0001 0001\n",
            b"0 0001 0001\r\n",
            b"0 0001 0001",
            b"0 0000 0000\n",
            b"1 0001 0001\n1 0002 0000\n",
        )
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(runner.RunnerError):
                runner.parse_trace(payload)


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
        self.assertIn('["status", "--porcelain", "--untracked-files=all", "-z"]', source)
        self.assertIn('environment["TMPDIR"] = str(temporary)', source)


class PolarityV1TestbenchTests(unittest.TestCase):
    def test_tb_is_pinned_and_checks_real_v1_polarity_path(self):
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
        self.assertIn("fifo_polarity [0:15][0:1]", source)
        self.assertIn("pol_mask_in[column] !== retired_polarity", source)
        self.assertIn("v1 pre-edge overrun differs from arrival-and-full", source)
        sample = source.index("sampled_overrun = overrun;", source.index("while (have_next)"))
        admission = source.index("@(posedge clk);", sample)
        self.assertLess(sample, admission)
        self.assertIn("polarity_checked_count != delivered_count", source)

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
