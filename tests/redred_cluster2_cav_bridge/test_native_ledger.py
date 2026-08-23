from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import benchmarks.redred_cluster2_cav_bridge.native_ledger as native_ledger_module
import tests.redred_cluster2_cav_bridge.run_native_observational as runner_module
from benchmarks.redred_cluster2_cav_bridge.native_ledger import (
    AUTHORITY_SCHEMA,
    CLEAN_GIT_AUTHORITY,
    EXPECTED_CODE_FILES,
    EXPECTED_TRACKED_CYCLEMASK,
    FILE_BYTES_AUTHORITY,
    GANGHEE_AUTHORITY_SHA256,
    GANGHEE_COMMIT,
    GANGHEE_REPOSITORY_URL,
    LEDGER_SCHEMA,
    TRACKED_CYCLEMASK_RAW_SHA256,
    TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256,
    NativeLedgerError,
    canonical_transport_outcome_jsonl,
    derive_occurrences,
    inspect_cyclemask_encoding,
    load_native_authority,
    normalized_relative_path,
    parse_native_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_cluster2_cav_bridge"
TEST_ROOT = ROOT / "tests" / "redred_cluster2_cav_bridge"
AUTHORITY = PACKAGE / "ganghee_cluster2_native_authority.json"
PARSER = PACKAGE / "native_ledger.py"
TB = TEST_ROOT / "redred_cluster2_native_observational_tb.sv"
RUNNER = TEST_ROOT / "run_native_observational.py"


def positive_cyclemask():
    return b"0 0001\n1 0001\n2 0001\n"


def positive_ledger():
    return (
        "SCHEMA|%s\n"
        "EVENT|2|0|2|OVERRUN|-|-|-|-\n"
        "EVENT|0|0|0|DELIVERED|3|1|0|0\n"
        "EVENT|1|0|1|DELIVERED|4|1|0|0\n"
        "SUMMARY|3|2|1\n" % LEDGER_SCHEMA
    ).encode("ascii")


class AuthorityTests(unittest.TestCase):
    def test_file_bytes_authority_does_not_consult_git_state(self):
        code_payload = b"module exact_bytes; endmodule\n"
        trace_payload = b"0 0001\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            code_path = root / "rtl" / "exact.v"
            code_path.parent.mkdir()
            code_path.write_bytes(code_payload)
            (root / "trace.txt").write_bytes(trace_payload)
            encoding = inspect_cyclemask_encoding(trace_payload)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    native_ledger_module,
                    "EXPECTED_CODE_FILES",
                    {"code": ("rtl/exact.v", hashlib.sha256(code_payload).hexdigest())},
                ))
                stack.enter_context(mock.patch.object(
                    native_ledger_module, "TRACKED_CYCLEMASK_PATH", "trace.txt"
                ))
                stack.enter_context(mock.patch.object(
                    native_ledger_module,
                    "TRACKED_CYCLEMASK_RAW_SHA256",
                    {"LF": encoding.raw_sha256},
                ))
                stack.enter_context(mock.patch.object(
                    native_ledger_module,
                    "TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256",
                    encoding.canonical_semantic_lf_sha256,
                ))
                stack.enter_context(mock.patch.object(
                    native_ledger_module, "load_native_authority"
                ))
                stack.enter_context(mock.patch.object(
                    native_ledger_module,
                    "_git",
                    side_effect=AssertionError("FILE_BYTES_AUTHORITY consulted Git"),
                ))
                verified = native_ledger_module.verify_faer_checkout(
                    root, root / "unused-authority.json", "trace.txt",
                    FILE_BYTES_AUTHORITY,
                )
            self.assertEqual(set(verified), {"code", "tracked_cyclemask"})

    def test_committed_authority_is_canonical_and_exact(self):
        authority = load_native_authority(AUTHORITY)
        self.assertEqual(authority["schema"], AUTHORITY_SCHEMA)
        self.assertEqual(authority["repository_url"], GANGHEE_REPOSITORY_URL)
        self.assertEqual(authority["git_commit"], GANGHEE_COMMIT)
        self.assertEqual(
            hashlib.sha256(AUTHORITY.read_bytes()).hexdigest(),
            GANGHEE_AUTHORITY_SHA256,
        )
        identities = {
            row["role"]: (row["path"], row["sha256"])
            for row in authority["code_files"]
        }
        self.assertEqual(identities, EXPECTED_CODE_FILES)
        self.assertEqual(authority["tracked_cyclemask"], EXPECTED_TRACKED_CYCLEMASK)
        self.assertEqual(
            [row["sha256"] for row in authority["tracked_cyclemask"]["accepted_raw_encodings"]],
            [TRACKED_CYCLEMASK_RAW_SHA256["LF"], TRACKED_CYCLEMASK_RAW_SHA256["CRLF"]],
        )
        self.assertEqual(
            authority["native_interface"]["retire_lanes"],
            [
                {
                    "allowed_rows": [0, 1, 2],
                    "allowed_rows_when_other_invalid": [1, 2],
                    "col_mask": {"name": "col_mask0", "width": 4},
                    "lane": 0,
                    "row": {"name": "row0", "width": 2},
                    "valid": {"name": "valid0", "width": 1},
                },
                {
                    "allowed_rows": [0, 2, 3],
                    "allowed_rows_when_other_invalid": [0, 3],
                    "col_mask": {"name": "col_mask1", "width": 4},
                    "lane": 1,
                    "row": {"name": "row1", "width": 2},
                    "valid": {"name": "valid1", "width": 1},
                },
            ],
        )

    def test_authority_tampering_and_nonrelative_paths_fail_closed(self):
        authority = json.loads(AUTHORITY.read_text(encoding="ascii"))
        mutations = []
        changed_commit = deepcopy(authority)
        changed_commit["git_commit"] = "0" * 40
        mutations.append(changed_commit)
        changed_hash = deepcopy(authority)
        changed_hash["code_files"][0]["sha256"] = "0" * 64
        mutations.append(changed_hash)
        changed_trace = deepcopy(authority)
        changed_trace["tracked_cyclemask"]["accepted_raw_encodings"][1]["sha256"] = "0" * 64
        mutations.append(changed_trace)
        changed_interface = deepcopy(authority)
        changed_interface["native_interface"]["retire_lanes"][0]["allowed_rows"] = [1, 2]
        mutations.append(changed_interface)
        boolean_width = deepcopy(authority)
        boolean_width["native_interface"]["inputs"][0]["width"] = True
        mutations.append(boolean_width)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "authority.json"
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    path.write_text(
                        json.dumps(mutation, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="ascii",
                    )
                    with self.assertRaises(NativeLedgerError):
                        load_native_authority(path)
        for path in ("/absolute", "../escape", "a/../b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(NativeLedgerError):
                normalized_relative_path(path)


class ParserPositiveTests(unittest.TestCase):
    def test_lf_and_crlf_are_explicit_raw_encodings_of_one_semantic_trace(self):
        lf = positive_cyclemask()
        crlf = lf.replace(b"\n", b"\r\n")
        lf_encoding = inspect_cyclemask_encoding(lf)
        crlf_encoding = inspect_cyclemask_encoding(crlf)
        self.assertEqual(lf_encoding.line_endings, "LF")
        self.assertEqual(crlf_encoding.line_endings, "CRLF")
        self.assertEqual(lf_encoding.canonical_lf_bytes, crlf_encoding.canonical_lf_bytes)
        self.assertEqual(
            lf_encoding.canonical_semantic_lf_sha256,
            crlf_encoding.canonical_semantic_lf_sha256,
        )
        self.assertEqual(derive_occurrences(lf), derive_occurrences(crlf))

    def test_ids_are_derived_by_cycle_then_source(self):
        occurrences = derive_occurrences(b"3 8005\n7 0010\n")
        self.assertEqual(
            [(row.event_id, row.source_index, row.occurrence_cycle) for row in occurrences],
            [(0, 0, 3), (1, 2, 3), (2, 15, 3), (3, 4, 7)],
        )

    def test_parser_replays_preedge_full_and_builds_v1_rows(self):
        rows = parse_native_ledger(positive_cyclemask(), positive_ledger())
        self.assertEqual([row["event_id"] for row in rows], [0, 1, 2])
        self.assertEqual([row["outcome"] for row in rows], ["DELIVERED", "DELIVERED", "OVERRUN"])
        self.assertEqual(rows[0]["retire_native_lane"], 1)
        self.assertIsNone(rows[2]["retire_row"])
        expected_keys = {
            "schema", "event_id", "source_index", "occurrence_cycle", "outcome",
            "retire_cycle", "retire_native_lane", "retire_row", "retire_col",
        }
        self.assertTrue(all(set(row) == expected_keys for row in rows))
        forbidden_sidebands = {
            "timestamp_ns", "polarity", "sensor_ray", "causal_pose_source_index",
            "transform_guard_valid", "window_id", "is_query",
        }
        self.assertTrue(all(not (set(row) & forbidden_sidebands) for row in rows))
        payload = canonical_transport_outcome_jsonl(rows)
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(len(payload.splitlines()), 3)

    def test_two_native_bitmaps_can_retire_eight_events_per_cycle(self):
        cyclemask = b"0 ffff\n"
        lines = ["SCHEMA|" + LEDGER_SCHEMA]
        for event_id in range(4):
            lines.append("EVENT|%d|%d|0|DELIVERED|1|0|0|%d" % (
                event_id, event_id, event_id,
            ))
        for column in range(4):
            source = 12 + column
            lines.append("EVENT|%d|%d|0|DELIVERED|1|1|3|%d" % (
                source, source, column,
            ))
        for column in range(4):
            source = 4 + column
            lines.append("EVENT|%d|%d|0|DELIVERED|2|0|1|%d" % (
                source, source, column,
            ))
        for column in range(4):
            source = 8 + column
            lines.append("EVENT|%d|%d|0|DELIVERED|2|1|2|%d" % (
                source, source, column,
            ))
        lines.append("SUMMARY|16|16|0")
        rows = parse_native_ledger(
            cyclemask, ("\n".join(lines) + "\n").encode("ascii")
        )
        self.assertEqual(len(rows), 16)

    def test_full_and_nonfull_arrival_plus_grant_edges(self):
        full_plus_grant = (
            "SCHEMA|%s\n"
            "EVENT|2|0|2|OVERRUN|-|-|-|-\n"
            "EVENT|0|0|0|DELIVERED|2|1|0|0\n"
            "EVENT|1|0|1|DELIVERED|3|1|0|0\n"
            "SUMMARY|3|2|1\n" % LEDGER_SCHEMA
        )
        nonfull_plus_grant = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|1|1|0|0\n"
            "EVENT|1|0|1|DELIVERED|2|1|0|0\n"
            "SUMMARY|2|2|0\n" % LEDGER_SCHEMA
        )
        full_rows = parse_native_ledger(
            b"0 0001\n1 0001\n2 0001\n", full_plus_grant.encode("ascii")
        )
        nonfull_rows = parse_native_ledger(
            b"0 0001\n1 0001\n", nonfull_plus_grant.encode("ascii")
        )
        self.assertEqual(full_rows[2]["outcome"], "OVERRUN")
        self.assertEqual(nonfull_rows[1]["outcome"], "DELIVERED")


class ParserMutationTests(unittest.TestCase):
    def assertLedgerRejected(self, ledger):
        with self.assertRaises(NativeLedgerError):
            parse_native_ledger(positive_cyclemask(), ledger.encode("ascii"))

    def test_id_order_coordinate_count_and_partition_mutations(self):
        base = positive_ledger().decode("ascii")
        mutations = {
            "id": base.replace("EVENT|2|0|2", "EVENT|9|0|2"),
            "order": base.replace(
                "EVENT|0|0|0|DELIVERED|3|1|0|0\n"
                "EVENT|1|0|1|DELIVERED|4|1|0|0",
                "EVENT|1|0|1|DELIVERED|4|1|0|0\n"
                "EVENT|0|0|0|DELIVERED|3|1|0|0",
            ),
            "coordinate": base.replace("DELIVERED|3|1|0|0", "DELIVERED|3|1|0|1"),
            "count": base.replace("SUMMARY|3|2|1", "SUMMARY|3|3|0"),
            "missing": base.replace("EVENT|1|0|1|DELIVERED|4|1|0|0\n", ""),
            "nonnull_overrun": base.replace("OVERRUN|-|-|-|-", "OVERRUN|2|0|0|0"),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                self.assertLedgerRejected(mutation)

    def test_impossible_single_and_two_valid_row_combinations_fail(self):
        single_lane0_row0 = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|1|0|0|0\n"
            "SUMMARY|1|1|0\n" % LEDGER_SCHEMA
        )
        two_lane_impossible = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|1|0|0|0\n"
            "EVENT|1|9|0|DELIVERED|1|1|2|1\n"
            "SUMMARY|2|2|0\n" % LEDGER_SCHEMA
        )
        with self.assertRaises(NativeLedgerError):
            parse_native_ledger(b"0 0001\n", single_lane0_row0.encode("ascii"))
        with self.assertRaises(NativeLedgerError):
            parse_native_ledger(b"0 0201\n", two_lane_impossible.encode("ascii"))

    def test_preedge_full_fifo_and_phantom_mutations(self):
        false_overrun = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|3|1|0|0\n"
            "EVENT|1|0|1|DELIVERED|4|1|0|0\n"
            "EVENT|2|0|2|DELIVERED|5|1|0|0\n"
            "SUMMARY|3|3|0\n" % LEDGER_SCHEMA
        )
        fifo_reorder = (
            "SCHEMA|%s\n"
            "EVENT|2|0|2|OVERRUN|-|-|-|-\n"
            "EVENT|1|0|1|DELIVERED|3|1|0|0\n"
            "EVENT|0|0|0|DELIVERED|4|1|0|0\n"
            "SUMMARY|3|2|1\n" % LEDGER_SCHEMA
        )
        phantom = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|0|1|0|0\n"
            "EVENT|2|0|2|OVERRUN|-|-|-|-\n"
            "EVENT|1|0|1|DELIVERED|4|1|0|0\n"
            "SUMMARY|3|2|1\n" % LEDGER_SCHEMA
        )
        for name, mutation in {
            "false_overrun": false_overrun,
            "fifo_reorder": fifo_reorder,
            "phantom": phantom,
        }.items():
            with self.subTest(name=name):
                self.assertLedgerRejected(mutation)

    def test_cyclemask_and_ledger_bytes_are_strict(self):
        for cyclemask in (
            b"00 0001\n",
            b"0 0001\n0 0002\n",
            b"0 0000\n",
            b"0 0001",
            b"0 0001\r\n1 0001\n",
            b"0 0001\n1 0001\r\n",
            b"0 0001\r1 0001\r",
        ):
            with self.subTest(cyclemask=cyclemask), self.assertRaises(NativeLedgerError):
                derive_occurrences(cyclemask)
        leading_zero = positive_ledger().replace(b"EVENT|2|", b"EVENT|02|")
        with self.assertRaises(NativeLedgerError):
            parse_native_ledger(positive_cyclemask(), leading_zero)

    def test_retirement_beyond_tb_drain_bound_fails(self):
        ledger = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|100001|1|0|0\n"
            "SUMMARY|1|1|0\n" % LEDGER_SCHEMA
        )
        with self.assertRaises(NativeLedgerError):
            parse_native_ledger(b"0 0001\n", ledger.encode("ascii"))


class SourceGuardTests(unittest.TestCase):
    def test_tb_identity_state_is_observational_only_and_overrun_is_preedge(self):
        source = TB.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(TB.read_bytes()).hexdigest(),
            runner_module.OBSERVATIONAL_TB_SHA256,
        )
        instance_start = source.index(
            "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf dut ("
        )
        instance_end = source.index("  );", instance_start)
        port_map = source[instance_start:instance_end]
        self.assertIn(".arrival(arrival)", port_map)
        self.assertNotIn("event_id", port_map)
        self.assertNotIn("fifo_", port_map)
        sample = source.index("sampled_overrun = overrun;", source.index("while (have_next)"))
        edge = source.index("@(posedge clk);", sample)
        self.assertLess(sample, edge)
        self.assertNotRegex(source, r"arrival\s*=.*(?:fifo|event_id)")
        self.assertIn("fifo_event_id [0:15][0:1]", source)
        self.assertIn("pre-edge overrun differs from arrival-and-full", source)
        native_check = source.index("task automatic check_native_lanes;")
        first_lane_logic = source.index("if (valid0 &&", native_check)
        for signal in ("valid0", "row0", "col_mask0", "valid1", "row1", "col_mask1"):
            with self.subTest(native_signal=signal):
                known_check = source.index(
                    "if ((^%s) === 1'bx)" % signal, native_check
                )
                self.assertLess(known_check, first_lane_logic)
        self.assertEqual(source.count("if ((^sampled_overrun) === 1'bx)"), 2)
        for assignment, use_marker in (
            (
                source.index("sampled_overrun = overrun;", source.index("while (have_next)")),
                "if ((sampled_overrun & ~arrival)",
            ),
            (
                source.index(
                    "sampled_overrun = overrun;",
                    source.index("while ((total_fifo_count()"),
                ),
                "if (sampled_overrun != 16'b0)",
            ),
        ):
            known_check = source.index("if ((^sampled_overrun) === 1'bx)", assignment)
            first_use = source.index(use_marker, known_check)
            self.assertLess(assignment, known_check)
            self.assertLess(known_check, first_use)
        id_order = source.index(
            "for (source_index = 0; source_index < 16; source_index = source_index + 1)",
            source.index("if (next_cycle == cycle_number)"),
        )
        id_assignment = source.index(
            "current_event_id[source_index] = next_event_id;", id_order
        )
        id_increment = source.index(
            "next_event_id = next_event_id + 1;", id_assignment
        )
        self.assertLess(id_order, id_assignment)
        self.assertLess(id_assignment, id_increment)
        self.assertIn("accepted_mask = arrival & ~sampled_overrun;", source)
        retirement = source.index("sample_retirement();", source.index("while (have_next)"))
        current_admission = source.index(
            "// Current-edge admissions become observable only after old events pop.",
            retirement,
        )
        self.assertLess(retirement, current_admission)
        self.assertIn("generated_count != delivered_count + overrun_count", source)
        self.assertIn("native lanes selected the same row", source)
        self.assertIn("native lanes selected impossible row pair", source)
        self.assertIn("TB-only FIFO is not empty after drain", source)

    def test_runner_verifies_before_compile_and_never_mutates_ganghee(self):
        source = RUNNER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any(
            "scor" in name.lower() or "evaluat" in name.lower()
            for name in imported
        ))
        authority_check = source.index("verified = verify_faer_checkout(")
        simulator_select = source.index("selected = _select_simulator(")
        compile_call = source.index("_run([", simulator_select)
        self.assertLess(authority_check, simulator_select)
        self.assertLess(simulator_select, compile_call)
        self.assertIn("staged, trace_encoding = _stage_verified_files(verified, output_root)", source)
        self.assertIn('staged["cluster2_steal_buf_rtl"]', source)
        self.assertEqual(source.count("verify_faer_checkout("), 2)
        self.assertIn("run_lines.count(expected_pass) != 1", source)
        self.assertIn("FILE_BYTES_AUTHORITY", source)
        self.assertIn("CLEAN_GIT_AUTHORITY", source)
        self.assertIn('choices=("auto", "xrun", "verilator", "iverilog")', source)
        self.assertIn("/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun", source)
        self.assertIn('"-timescale", "1ns/1ps"', source)
        self.assertIn("trace_destination.write_bytes(trace_payload)", source)
        self.assertIn('staged["tracked_cyclemask"] = trace_destination', source)
        self.assertIn('staged["observational_tb"] = tb_destination', source)
        self.assertIn('staged_tb_path = staged["observational_tb"]', source)
        self.assertNotIn("*rtl_paths,\n                TB_PATH,", source)
        self.assertIn('cwd=str(output_root)', source)
        self.assertIn('simulator_environment["TMPDIR"] = str(temporary_root)', source)
        self.assertIn("sys.dont_write_bytecode = True", source)
        self.assertEqual(source.count("_run(["), 5)
        self.assertEqual(source.count("], run_log, output_root)"), 3)
        self.assertEqual(source.count("], compile_log, output_root)"), 2)
        self.assertIn("_assert_post_run_state(", source)
        self.assertIn("_read_xrun_completion_log(tool_log, output_root)", source)
        self.assertIn('expected_path = output_root / "xrun.log"', source)
        self.assertNotIn('["git", "-C"', source)
        self.assertIn(
            '["git", "status", "--porcelain", "--untracked-files=all", "-z"]',
            source,
        )
        self.assertNotIn('"--porcelain=v1"', source)
        self.assertIn('cwd=str(root)', source)
        for forbidden in ("git clone", "git fetch", "git checkout", "git reset"):
            self.assertNotIn(forbidden, source)
        self.assertIn("normalized path relative to faer_root", source)

    def test_parser_has_no_scorer_evaluator_or_source_event_dependency(self):
        source = PARSER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any(
            "scor" in name.lower() or "evaluat" in name.lower()
            for name in imported
        ))
        self.assertNotIn("source_event", source)
        self.assertNotIn("current_cav", source)
        tb_source = TB.read_text(encoding="utf-8")
        for forbidden in (
            "timestamp_ns", "polarity", "sensor_ray", "causal_pose",
            "transform_guard", "window_id", "is_query",
        ):
            self.assertNotIn(forbidden, tb_source)


class RunnerIsolationTests(unittest.TestCase):
    def test_run_forces_output_cwd_and_private_tmpdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary).resolve()
            log_path = output_root / "probe.log"
            output = runner_module._run(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.getcwd()); print(os.environ['TMPDIR'])",
                ],
                log_path,
                output_root,
            )
            self.assertEqual(
                output.splitlines(),
                [str(output_root), str(output_root / "tmp")],
            )
            self.assertEqual(log_path.read_text(encoding="utf-8"), output)

    def test_stage_preserves_crlf_trace_and_pins_private_tb_copy(self):
        trace_payload = b"0 0001\r\n"
        tb_payload = b"module observational_copy; endmodule\n"
        trace_encoding = inspect_cyclemask_encoding(trace_payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source_trace = root / "source.cyclemask"
            source_tb = root / "source_tb.sv"
            output_root = root / "output"
            source_trace.write_bytes(trace_payload)
            source_tb.write_bytes(tb_payload)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    runner_module, "EXPECTED_CODE_FILES", {}
                ))
                stack.enter_context(mock.patch.object(
                    runner_module, "TRACKED_CYCLEMASK_PATH", "trace.cyclemask"
                ))
                stack.enter_context(mock.patch.object(
                    runner_module,
                    "TRACKED_CYCLEMASK_RAW_SHA256",
                    {"CRLF": trace_encoding.raw_sha256},
                ))
                stack.enter_context(mock.patch.object(
                    runner_module,
                    "TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256",
                    trace_encoding.canonical_semantic_lf_sha256,
                ))
                stack.enter_context(mock.patch.object(
                    runner_module, "TB_PATH", source_tb
                ))
                stack.enter_context(mock.patch.object(
                    runner_module,
                    "OBSERVATIONAL_TB_SHA256",
                    hashlib.sha256(tb_payload).hexdigest(),
                ))
                staged, observed = runner_module._stage_verified_files(
                    {"tracked_cyclemask": source_trace}, output_root
                )
            self.assertEqual(observed, trace_encoding)
            self.assertEqual(staged["tracked_cyclemask"].read_bytes(), trace_payload)
            self.assertEqual(staged["observational_tb"].read_bytes(), tb_payload)
            self.assertIn(output_root, staged["tracked_cyclemask"].parents)
            self.assertIn(output_root, staged["observational_tb"].parents)


if __name__ == "__main__":
    unittest.main()
