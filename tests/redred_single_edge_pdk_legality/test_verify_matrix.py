#!/usr/bin/env python3
"""Mutation tests for the strict single-edge source-legality audit."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("redred_pdk_verify", HERE / "verify_matrix.py")
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def canonical_matrix() -> dict:
    return json.loads((HERE / "legality_matrix.json").read_text(encoding="utf-8"))


class MatrixMutationTest(unittest.TestCase):
    def reject(self, mutation, pattern: str) -> None:
        value = canonical_matrix()
        mutation(value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "matrix.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(verify.AuditError, pattern):
                verify.validate(path)

    def test_committed_matrix_and_git_object_source_closure_pass(self) -> None:
        value = verify.validate()
        gates = {gate["id"]: gate for gate in value["gates"]}
        self.assertEqual(value["decision"], "HOLD")
        self.assertEqual(value["audited_rtl"]["source_structure_status"], "PASS")
        self.assertEqual(value["audited_rtl"]["mapped_structure_status"], "HOLD")
        self.assertEqual(value["audited_rtl"]["organizer_approval_status"], "HOLD")
        self.assertEqual(
            value["current_goal_policy_pin"]["fallback_integrated_digital"],
            "PASS_BOUNDED_ACTUAL_RTL_SYNTHETIC_AND_PUBLIC_PROJECTED",
        )
        self.assertEqual(gates["G06_FALLBACK_CANONICAL_DIGITAL"]["status"], "GO")
        self.assertNotIn(
            "integrated fallback digital evidence is missing",
            gates["G06_FALLBACK_CANONICAL_DIGITAL"]["current_reason"].lower(),
        )

    def test_duplicate_json_keys_are_rejected(self) -> None:
        payload = (HERE / "legality_matrix.json").read_text(encoding="utf-8")
        payload = payload.replace('"schema":', '"schema":"duplicate", "schema":', 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(verify.AuditError, "duplicate JSON key"):
                verify.validate(path)

    def test_unknown_top_nested_and_gate_keys_are_rejected(self) -> None:
        cases = (
            lambda value: value.update({"unknown": True}),
            lambda value: value["decision_rule"].update({"unknown": True}),
            lambda value: value["gates"][0].update({"unknown": True}),
            lambda value: value["audited_rtl"]["expanded_sources"][0].update(
                {"unknown": True}),
            lambda value: value["audited_rtl"]["expected_posedge_by_source"][0].update(
                {"unknown": True}),
            lambda value: value["expected_external_artifacts"][0].update(
                {"unknown": True}),
            lambda value: value["current_goal_policy_pin"].update({"unknown": True}),
            lambda value: value["current_goal_policy_pin"][
                "pinned_legality_publication"
            ].update({"unknown": True}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "keys differ")

    def test_audit_commit_filelists_sources_and_hashes_are_immutable(self) -> None:
        cases = (
            lambda value: value.update({"audit_source_commit": "0" * 40}),
            lambda value: value.update({"audit_integrated_commit": "0" * 40}),
            lambda value: value["audited_rtl"].update({"source_commit": "0" * 40}),
            lambda value: value["audited_rtl"].update({"integrated_commit": "0" * 40}),
            lambda value: value["audited_rtl"]["root_filelists"].__setitem__(
                0, "rtl/technology/physical_staging/filelists/a2_generic.f"),
            lambda value: value["audited_rtl"]["filelists"][0].update(
                {"sha256": "0" * 64}),
            lambda value: value["audited_rtl"]["filelists"].reverse(),
            lambda value: value["audited_rtl"]["expanded_sources"].pop(),
            lambda value: value["audited_rtl"]["expanded_sources"].reverse(),
            lambda value: value["audited_rtl"]["expanded_sources"][0].update(
                {"path": "rtl/technology/p6/w2_p6_pair_tx_tech.sv"}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "audit target|filelist|source identity|source inventory")

    def test_superseded_baseline_cannot_regain_source_pass_authority(self) -> None:
        cases = (
            lambda value: value.update(
                {"audit_source_commit": verify.SUPERSEDED_BASELINE_COMMIT}),
            lambda value: value.update(
                {"audit_integrated_commit": verify.SUPERSEDED_BASELINE_COMMIT}),
            lambda value: value["audited_rtl"].update(
                {"source_commit": verify.SUPERSEDED_BASELINE_COMMIT}),
            lambda value: value["audited_rtl"].update(
                {"integrated_commit": verify.SUPERSEDED_BASELINE_COMMIT}),
            lambda value: value["audited_rtl"].update(
                {"supersedes_commit": verify.HARDENED_SOURCE_COMMIT}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "audit target|superseded baseline")

    def test_exact_per_source_posedge_inventory_is_immutable(self) -> None:
        cases = (
            lambda value: value["audited_rtl"].update(
                {"expected_posedge_event_count": 6}),
            lambda value: value["audited_rtl"]["expected_posedge_by_source"][1][
                "clocks"
            ].clear(),
            lambda value: value["audited_rtl"]["expected_posedge_by_source"].pop(),
            lambda value: value["audited_rtl"]["expected_posedge_by_source"].reverse(),
            lambda value: value["audited_rtl"]["expected_posedge_by_source"][0].update(
                {"path": "rtl/technology/p6/w2_p6_pair_tx_tech.sv"}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "posedge inventory|posedge total")

    def test_source_pass_cannot_promote_mapped_organizer_or_g05(self) -> None:
        cases = (
            lambda value: value["audited_rtl"].update({"mapped_structure_status": "GO"}),
            lambda value: value["audited_rtl"].update({"organizer_approval_status": "GO"}),
            lambda value: value["audited_rtl"].update({"claim_limit": "RELEASE_GO"}),
            lambda value: value["gates"][4].update({"status": "GO"}),
            lambda value: value["gates"][4].update({"current_reason": "mapped PASS"}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "source PASS escaped|aggregate decision|G05")

    def test_scoped_canonical_pass_cannot_be_demoted_or_promote_release(self) -> None:
        cases = (
            lambda value: value["current_goal_policy_pin"].update(
                {"fallback_integrated_digital": "HOLD_MISSING"}),
            lambda value: value["current_goal_policy_pin"].update(
                {"result_authority": "EVIDENCE"}),
            lambda value: value["current_goal_policy_pin"].update(
                {"evidence_qualified": True}),
            lambda value: value["current_goal_policy_pin"].update(
                {"release_qualified": True}),
            lambda value: value["current_goal_policy_pin"].update(
                {"canonical_digital_dependency": "GO"}),
            lambda value: value["current_goal_policy_pin"].update(
                {"mapped_pdk_legality": "GO"}),
            lambda value: value["current_goal_policy_pin"].update(
                {"organizer_pdk_legality": "GO"}),
            lambda value: value["current_goal_policy_pin"][
                "pinned_legality_publication"
            ].update({"sha256": "0" * 64}),
            lambda value: value["current_goal_policy_pin"].update(
                {"pin_semantics": "CURRENT_PACKAGE_SELF_HASH"}),
            lambda value: value["gates"][5].update({"status": "HOLD"}),
            lambda value: value["gates"][5].update(
                {"current_reason": "integrated fallback digital evidence is missing"}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(
                    mutation,
                    "goal policy pin|aggregate decision|G06",
                )

    def test_external_paths_hashes_presence_and_team_authority_are_pinned(self) -> None:
        cases = (
            lambda value: value["expected_external_artifacts"][0].update(
                {"server_path": "/tmp/fake-gsclib.tgz"}),
            lambda value: value["expected_external_artifacts"][0].update(
                {"sha256": "0" * 64}),
            lambda value: value["expected_external_artifacts"][0].update(
                {"present_in_checkout": True}),
            lambda value: value["expected_external_artifacts"][2].update(
                {"recorded_pvt": [9.9, 9.9, 999.0]}),
            lambda value: value["repository_evidence"][3].update(
                {"authority": "ORGANIZER_PRIMARY"}),
            lambda value: value["local_test_doubles"][0].update(
                {"sha256": verify.EXPECTED_EXTERNAL["SETUP_LIBERTY"][1]}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "external artifact|authority|fixture")

    def test_repository_path_traversal_is_rejected(self) -> None:
        self.reject(
            lambda value: value["repository_evidence"][0].update(
                {"path": "../AI_SEMI_QNA_REDRED_GOAL_20260819.md"}),
            "traversal",
        )


class StructuralMutationTest(unittest.TestCase):
    def test_opposite_edge_and_forbidden_primitive_mutants_fail(self) -> None:
        mutants = {
            "negedge": "module m(input logic clk_i); always_ff @(negedge clk_i) ; endmodule",
            "oddr": "module m; ODDR cell(); endmodule",
            "iddr": "module m; IDDR cell(); endmodule",
            "negedge_cell": "module m; DFFNSRX1 cell(); endmodule",
            "clock_gate_cell": "module m; TLATNTSCAX2 cell(); endmodule",
            "vendor_cell": "module m; vendor_technology_cell cell(); endmodule",
            "udp": "primitive p(output q, input d); endprimitive",
            "latch": "module m; always_latch begin end endmodule",
        }
        for name, source in mutants.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(verify.AuditError, "forbidden|opposite-edge"):
                    verify.scan_source(f"{name}.sv", source.encode())

    def test_generated_gated_and_forwarded_clock_mutants_fail(self) -> None:
        mutants = {
            "gated_event": (
                "module m(input logic clk_i,en); always_ff @(posedge clk_i & en) ; endmodule"
            ),
            "derived_event": (
                "module m(input logic clk_i); logic derived_clk; "
                "assign derived_clk=clk_i; always_ff @(posedge derived_clk) ; endmodule"
            ),
            "forwarded_port": (
                "module m(input logic clk_i, output logic link_clk_o); "
                "assign link_clk_o=clk_i; endmodule"
            ),
            "generated_sdc": "module m; create_generated_clock generated; endmodule",
        }
        for name, source in mutants.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    verify.AuditError, "gated|generated|forwarded|undeclared|forbidden"
                ):
                    verify.scan_source(f"{name}.sv", source.encode())

    def test_comments_and_strings_do_not_create_false_forbidden_hits(self) -> None:
        source = b'''module m(input logic clk_i);
          // negedge ODDR generated clock
          always_ff @(posedge clk_i) $display("IDDR vendor primitive");
        endmodule
        '''
        self.assertEqual(verify.scan_source("clean.sv", source), ["clk_i"])


class FilelistMutationTest(unittest.TestCase):
    def expand(self, blobs: dict[str, bytes]) -> tuple[list[str], list[str]]:
        return verify.expand_filelists(("root.f",), lambda path: blobs[path])

    def test_traversal_absolute_options_cycles_and_missing_sources_fail(self) -> None:
        cases = (
            {"root.f": b"../escape.sv\n"},
            {"root.f": b"/absolute/source.sv\n"},
            {"root.f": b"+define+UNTRACKED\n"},
            {"root.f": b"-f child.f\n", "child.f": b"-f root.f\n"},
            {"root.f": b"-f child.f extra\n"},
        )
        for blobs in cases:
            with self.subTest(blobs=blobs):
                with self.assertRaises((verify.AuditError, KeyError)):
                    self.expand(blobs)


class LocalArtifactMutationTest(unittest.TestCase):
    def populate_fixtures(self, root: Path) -> None:
        for relative in verify.EXPECTED_FIXTURES:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(verify.ROOT / relative, target)

    def test_symlinked_repository_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("payload", encoding="utf-8")
            (root / "link").symlink_to(target)
            with self.assertRaisesRegex(verify.AuditError, "symlink"):
                verify.local_regular_file(root, "link", "evidence")

    def test_undeclared_pdk_named_checkout_file_is_rejected(self) -> None:
        value = canonical_matrix()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate_fixtures(root)
            extra = root / "undeclared" / "slow_vdd1v0_basicCells.lib"
            extra.parent.mkdir(parents=True)
            extra.write_text("not real and not declared", encoding="utf-8")
            with self.assertRaisesRegex(verify.AuditError, "undeclared real-or-fixture"):
                verify.validate_external_and_fixtures(value, root)

    def test_symlinked_pdk_named_checkout_file_is_rejected(self) -> None:
        value = canonical_matrix()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate_fixtures(root)
            extra = root / "undeclared" / "gsclib045_tech.lef"
            extra.parent.mkdir(parents=True)
            extra.symlink_to(root / next(iter(verify.EXPECTED_FIXTURES)))
            with self.assertRaisesRegex(verify.AuditError, "symlink"):
                verify.validate_external_and_fixtures(value, root)


if __name__ == "__main__":
    unittest.main()
