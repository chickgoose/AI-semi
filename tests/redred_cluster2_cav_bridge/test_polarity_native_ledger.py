from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks.redred_cluster2_cav_bridge.polarity_native_ledger import (
    DUPLICATE_SCOPE,
    IDENTITY_SCOPE,
    LEDGER_SCHEMA,
    PolarityNativeLedgerError,
    parse_addrpol_trace,
    verify_polarity_native_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "benchmarks" / "redred_cluster2_cav_bridge" / "polarity_native_ledger.py"


def positive_trace() -> bytes:
    # cycle 0: sources 0/4/8; cycles 1/2: source 0 again.
    return b"0 0111 0010\n1 0001 0001\n2 0001 0000\n"


def positive_ledger() -> bytes:
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


class PositiveTests(unittest.TestCase):
    def test_raw_replay_checks_polarity_overrun_conservation_and_drain(self):
        report = verify_polarity_native_ledger(positive_trace(), positive_ledger())
        self.assertEqual(
            (report.generated, report.delivered, report.overrun),
            (5, 4, 1),
        )
        self.assertEqual(report.generated, report.delivered + report.overrun)
        self.assertEqual((report.phantom, report.duplicate), (0, 0))
        self.assertTrue(report.drain_empty)
        self.assertEqual(report.observed_cycles, 5)
        self.assertEqual(report.identity_scope, IDENTITY_SCOPE)
        self.assertFalse(report.identity_order_independence_claimed)
        self.assertEqual(report.duplicate_scope, DUPLICATE_SCOPE)

    def test_uniform_crlf_trace_has_same_semantics(self):
        occurrences_lf, encoding_lf = parse_addrpol_trace(positive_trace())
        occurrences_crlf, encoding_crlf = parse_addrpol_trace(
            positive_trace().replace(b"\n", b"\r\n")
        )
        self.assertEqual(occurrences_lf, occurrences_crlf)
        self.assertEqual((encoding_lf, encoding_crlf), ("LF", "CRLF"))

    def test_equal_polarity_occurrences_pass_only_under_explicit_observability_scope(self):
        trace = b"0 0001 0001\n1 0001 0001\n"
        ledger = (
            "SCHEMA|%s\nSCOPE|%s\n"
            "CYCLE|0|0000|0|0|0|0|0|0|0|0\n"
            "CYCLE|1|0000|0|0|0|0|1|0|1|1\n"
            "CYCLE|2|0000|0|0|0|0|1|0|1|1\n"
            "CYCLE|3|0000|0|0|0|0|0|0|0|0\n"
            "SUMMARY|2|2|0|0|0|1\n" % (LEDGER_SCHEMA, IDENTITY_SCOPE)
        ).encode("ascii")
        report = verify_polarity_native_ledger(trace, ledger)
        self.assertFalse(report.identity_order_independence_claimed)
        self.assertIn("IDENTICAL_SAME_SOURCE_EQUAL_POLARITY_EVENTS_UNOBSERVABLE", report.identity_scope)

    def test_valid_lane_ignores_unselected_polarity_bits_but_checks_selected_bits(self):
        trace = b"0 0060 0060\n"
        ledger = (
            "SCHEMA|%s\nSCOPE|%s\n"
            "CYCLE|0|0000|0|0|0|0|0|0|0|0\n"
            "CYCLE|1|0000|1|1|6|7|0|0|0|0\n"
            "CYCLE|2|0000|0|0|0|0|0|0|0|0\n"
            "SUMMARY|2|2|0|0|0|1\n" % (LEDGER_SCHEMA, IDENTITY_SCOPE)
        ).encode("ascii")
        report = verify_polarity_native_ledger(trace, ledger)
        self.assertEqual((report.generated, report.delivered), (2, 2))

        selected_bit_mismatch = ledger.replace(
            b"CYCLE|1|0000|1|1|6|7", b"CYCLE|1|0000|1|1|6|3"
        )
        with self.assertRaises(PolarityNativeLedgerError):
            verify_polarity_native_ledger(trace, selected_bit_mismatch)


class MutationTests(unittest.TestCase):
    def assertRejected(self, trace: bytes, ledger: bytes) -> None:
        with self.assertRaises(PolarityNativeLedgerError):
            verify_polarity_native_ledger(trace, ledger)

    def test_raw_address_polarity_and_fifo_mutations_fail_closed(self):
        base = positive_ledger()
        mutations = {
            "hw_polarity": base.replace(
                b"CYCLE|3|0000|0|0|0|0|1|0|1|1",
                b"CYCLE|3|0000|0|0|0|0|1|0|1|0",
            ),
            "row": base.replace(
                b"CYCLE|3|0000|0|0|0|0|1|0|1|1",
                b"CYCLE|3|0000|0|0|0|0|1|3|1|1",
            ),
            "column": base.replace(
                b"CYCLE|3|0000|0|0|0|0|1|0|1|1",
                b"CYCLE|3|0000|0|0|0|0|1|0|2|2",
            ),
            "overrun": base.replace(b"CYCLE|2|0001", b"CYCLE|2|0000"),
            "scope": base.replace(b"EVENT_ID_ORDER_INDEPENDENCE_NOT_CLAIMED", b"EVENT_ID_ORDER_INDEPENDENCE_CLAIMED"),
            "summary": base.replace(b"SUMMARY|5|4|1|0|0|1", b"SUMMARY|5|5|0|0|0|1"),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                self.assertRejected(positive_trace(), mutation)

    def test_phantom_duplicate_illegal_lane_and_polarity_mask_fail(self):
        base = positive_ledger()
        mutations = {
            "phantom": base.replace(
                b"CYCLE|0|0000|0|0|0|0|0|0|0|0",
                b"CYCLE|0|0000|0|0|0|0|1|3|1|0",
            ),
            "duplicate_source": base.replace(
                b"CYCLE|2|0001|0|0|0|0|1|0|1|0",
                b"CYCLE|2|0001|1|0|1|0|1|0|1|0",
            ),
            "illegal_single_lane": base.replace(
                b"CYCLE|3|0000|0|0|0|0|1|0|1|1",
                b"CYCLE|3|0000|1|0|1|1|0|0|0|0",
            ),
            "invalid_lane_nonzero_polarity": base.replace(
                b"CYCLE|0|0000|0|0|0|0|0|0|0|0",
                b"CYCLE|0|0000|0|0|0|1|0|0|0|0",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                self.assertRejected(positive_trace(), mutation)

    def test_missing_duplicate_cycles_and_incomplete_drain_fail(self):
        base = positive_ledger()
        missing_cycle = base.replace(b"CYCLE|2|0001|0|0|0|0|1|0|1|0\n", b"")
        duplicate_cycle = base.replace(
            b"CYCLE|2|0001|0|0|0|0|1|0|1|0\n",
            b"CYCLE|2|0001|0|0|0|0|1|0|1|0\nCYCLE|2|0001|0|0|0|0|1|0|1|0\n",
        )
        incomplete_drain = base.replace(
            b"CYCLE|3|0000|0|0|0|0|1|0|1|1",
            b"CYCLE|3|0000|0|0|0|0|0|0|0|0",
        )
        for name, mutation in {
            "missing_cycle": missing_cycle,
            "duplicate_cycle": duplicate_cycle,
            "incomplete_drain": incomplete_drain,
        }.items():
            with self.subTest(name=name):
                self.assertRejected(positive_trace(), mutation)

    def test_malformed_addrpol_inputs_fail(self):
        mutations = (
            b"0 0001 0002\n",
            b"0 0001 0000\n0 0001 0001\n",
            b"0 0000 0000\n",
            b"00 0001 0000\n",
            b"0 0001 0000",
            b"0 0001 0000\r\n1 0001 0001\n",
        )
        for mutation in mutations:
            with self.subTest(trace=mutation):
                self.assertRejected(mutation, positive_ledger())


class IsolationAndCliTests(unittest.TestCase):
    def test_parser_is_python38_and_independent_of_join_runner_and_identity_parsers(self):
        source = PARSER.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(PARSER), feature_version=(3, 8))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any(
            name.startswith("benchmarks.") or name.startswith("tests.")
            for name in imported
        ))

    def test_cli_emits_only_verified_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = root / "trace.addrpol.txt"
            ledger = root / "raw.ledger"
            trace.write_bytes(positive_trace())
            ledger.write_bytes(positive_ledger())
            completed = subprocess.run(
                [sys.executable, str(PARSER), str(trace), str(ledger)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            __import__("json").loads(completed.stdout),
            asdict(verify_polarity_native_ledger(positive_trace(), positive_ledger())),
        )


if __name__ == "__main__":
    unittest.main()
