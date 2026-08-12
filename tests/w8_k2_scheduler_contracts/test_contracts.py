#!/usr/bin/env python3
"""Independent oracle, mutation, atomic handshake, and binding tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from binding_adapter import execute_binding, materialize_binding, run_registry
from mutation_gate import MUTATIONS, VECTORS, build_report, load_vectors, run_mutations
from oracle import (
    BATCHED_IWRR_ROWS,
    CONTRACT_BATCHED_IWRR,
    CONTRACT_PAIRED_ROW_PROPOSAL,
    CONTRACT_SCALAR_PREFIX,
    CONTRACTS,
    PAIRED_ROW_PROPOSAL_ROWS,
    FoveaState,
    WEIGHTS,
    ContractViolation,
    CycleInput,
    TwoLaneBufferedLink,
    canonical_fovea_step,
    check_batched_iwrr_contract,
    check_weight_schedule,
    flatten_committed,
    run_trace,
    validate_observation,
)


ROOT = Path(__file__).resolve().parent
IMMUTABLE_FIXTURE = ROOT / "immutable_owner_fixture.py"


class ContractTest(unittest.TestCase):
    def test_final_top3_names_include_batched_and_exclude_staggered(self) -> None:
        self.assertIn(CONTRACT_BATCHED_IWRR, CONTRACTS)
        self.assertNotIn("staggered_two_slot_epoch_k2", CONTRACTS)
        self.assertNotIn("paired_cortical_column_k2", CONTRACTS)
        self.assertIn(CONTRACT_PAIRED_ROW_PROPOSAL, CONTRACTS)

    def test_batched_iwrr_round_and_batch_semantics(self) -> None:
        self.assertEqual(
            BATCHED_IWRR_ROWS,
            (1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2),
        )
        check_batched_iwrr_contract(BATCHED_IWRR_ROWS)
        check_weight_schedule(BATCHED_IWRR_ROWS)
        observations = run_trace(
            CONTRACT_BATCHED_IWRR,
            [CycleInput(request=0xFFFF) for _ in range(6)],
        )
        self.assertEqual(
            [row for obs in observations for row in obs.nominal_rows],
            list(BATCHED_IWRR_ROWS),
        )

    def test_obsolete_batched_calendar_is_rejected_even_with_same_weights(self) -> None:
        obsolete = (0, 1, 2, 3, 1, 2, 1, 2, 1, 2, 1, 2)
        self.assertEqual(tuple(obsolete.count(row) for row in range(4)), WEIGHTS)
        with self.assertRaisesRegex(ContractViolation, "^BATCHED_IWRR_CALENDAR_MISMATCH"):
            check_batched_iwrr_contract(obsolete)

    def test_paired_design_is_honestly_narrow_row_proposal(self) -> None:
        check_weight_schedule(PAIRED_ROW_PROPOSAL_ROWS)
        self.assertEqual(PAIRED_ROW_PROPOSAL_ROWS[0:2], (0, 1))
        self.assertEqual(PAIRED_ROW_PROPOSAL_ROWS[-2:], (2, 3))

    def test_false_aggregate_is_rejected_with_exact_diagnostic(self) -> None:
        bad = list(BATCHED_IWRR_ROWS)
        bad[5] = 2
        with self.assertRaisesRegex(ContractViolation, "^FALSE_AGGREGATE_1551"):
            check_weight_schedule(bad)

    def test_all_three_full_epochs_commit_1551(self) -> None:
        vectors = load_vectors()
        for name in (
            "batched_iwrr_full_epoch",
            "paired_row_full_epoch",
            "scalar_full_epoch",
        ):
            contract, trace = vectors[name]
            committed = flatten_committed(run_trace(contract, trace))
            counts = tuple(
                sum(source // 4 == row for source in committed) for row in range(4)
            )
            self.assertEqual(len(committed), 12, name)
            self.assertEqual(counts, WEIGHTS, name)

    def test_scalar_prefix_is_two_successive_microsteps(self) -> None:
        contract, trace = load_vectors()["scalar_full_epoch"]
        expected, state = [], FoveaState()
        for cycle_input in trace:
            remaining = cycle_input.request
            for _ in range(2):
                source, state = canonical_fovea_step(remaining, state)
                if source is not None:
                    expected.append(source)
                    remaining &= ~(1 << source)
        self.assertEqual(flatten_committed(run_trace(contract, trace)), expected)

    def test_atomic_bundle_holds_count_addresses_and_policy(self) -> None:
        contract, trace = load_vectors()["calendar_atomic_hold"]
        observations = run_trace(contract, trace)
        first = observations[0]
        self.assertEqual(first.grant_count, 2)
        self.assertEqual(first.committed, (None, None))
        self.assertEqual(first.held_after, first.addresses)
        self.assertEqual(first.policy_before, first.policy_after)
        self.assertEqual(observations[1].addresses, first.addresses)
        self.assertEqual(observations[1].grant_count, first.grant_count)
        self.assertEqual(observations[1].policy_after, first.policy_after)
        self.assertEqual(observations[2].committed, first.addresses)
        self.assertEqual(observations[2].policy_after["phase"], 1)

    def test_batched_iwrr_pointer_steps_equal_valid_grants(self) -> None:
        one = run_trace(
            CONTRACT_BATCHED_IWRR, [CycleInput(request=0x0010)]
        )[0]
        self.assertEqual(one.grant_count, 1)
        self.assertEqual(one.policy_after["column_rr"], (0, 1, 0, 0))

    def test_batched_iwrr_all_empty_phase_is_waived_without_ready(self) -> None:
        observation = run_trace(
            CONTRACT_BATCHED_IWRR,
            [CycleInput(request=0x1000, bundle_ready=False)],
        )[0]
        self.assertEqual(observation.grant_count, 0)
        self.assertEqual(observation.policy_after["phase"], 1)

    def test_link_lane_stall_is_separate_and_policy_free(self) -> None:
        link = TwoLaneBufferedLink()
        link.accept_atomic((4, 11))
        first = link.step((True, False))
        self.assertEqual(first.outputs, (4, None))
        self.assertEqual(first.held_after, (None, 11))
        self.assertFalse(first.scheduler_policy_touched)
        second = link.step((True, True))
        self.assertEqual(second.outputs, (None, 11))

    def test_sparse_entitlements_are_waived_without_debt_or_borrow(self) -> None:
        contract, trace = load_vectors()["batched_sparse_debt_repay"]
        observations = run_trace(contract, trace)
        self.assertEqual(observations[0].addresses, (None, None))
        self.assertEqual(observations[1].addresses, (None, None))
        self.assertEqual(observations[2].addresses, (12, None))
        self.assertNotIn("fallback_debt", observations[2].policy_after)

    def test_reset_clears_pending_offer(self) -> None:
        contract, trace = load_vectors()["reset_pending_bundle"]
        observations = run_trace(contract, trace)
        self.assertEqual(observations[0].held_after, observations[0].addresses)
        self.assertEqual(observations[1].grant_count, 0)
        self.assertEqual(observations[1].addresses, (None, None))

    def test_all_65536_initial_masks_are_safe_for_each_contract(self) -> None:
        for contract in CONTRACTS:
            for request in range(1 << 16):
                observation = run_trace(contract, [CycleInput(request=request)])[0]
                validate_observation(observation)
                self.assertLessEqual(observation.grant_count, 2)

    def test_required_mutations_are_killed_by_actual_diagnostics(self) -> None:
        report = build_report()
        self.assertEqual(report["killed"], 10)
        for row in report["mutations"]:
            self.assertEqual(row["diagnostic"], row["actual_diagnostic"])

    def test_wrong_diagnostic_label_is_rejected(self) -> None:
        wrong = list(MUTATIONS)
        wrong[0] = type(wrong[0])(
            wrong[0].fault, wrong[0].case, "NOT_THE_ACTUAL_DIAGNOSTIC"
        )
        with self.assertRaisesRegex(ContractViolation, "MUTANT_DIAGNOSTIC_MISMATCH"):
            run_mutations(load_vectors(), wrong[:1])

    def test_vector_hash_report(self) -> None:
        self.assertEqual(
            build_report()["vector_sha256"],
            hashlib.sha256(VECTORS.read_bytes()).hexdigest(),
        )


class BindingAdapterTest(unittest.TestCase):
    def _repo_binding(self, root: Path) -> tuple[dict, str]:
        repo = root / "owner"
        repo.mkdir()
        (repo / "owner_scheduler.sv").write_text(
            "module owner_scheduler; endmodule\n", encoding="utf-8"
        )
        shutil.copy2(IMMUTABLE_FIXTURE, repo / "owner_probe.py")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "W8 Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "w8@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture"], check=True)
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        paths = ["owner_scheduler.sv", "owner_probe.py"]
        sources = [
            {"path": path, "sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest()}
            for path in paths
        ]
        tool = Path(sys.executable).resolve()
        binding = {
            "name": "fixture-owner",
            "contract": CONTRACT_BATCHED_IWRR,
            "evidence_scope": "owner_selftest",
            "owner_repo": str(repo),
            "owner_commit": commit,
            "sources": sources,
            "execution": {
                "tool": {"path": str(tool), "sha256": hashlib.sha256(tool.read_bytes()).hexdigest()},
                "artifact": "owner_probe.py",
                "argv": [],
                "env": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0"},
                "required_output": ["W8_FIXTURE_IMMUTABLE_ARTIFACT_PASS"],
            },
        }
        return binding, commit

    def test_empty_registry_is_explicit_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / "empty.json"
            registry.write_text(json.dumps({"schema_version": 3, "bindings": []}))
            report = run_registry(registry, root / "work")
        self.assertEqual(report["decision"], "SKIP_NO_OWNER_BINDINGS")

    def test_tracked_owner_registry_binds_exact_a2_and_a3_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_registry(ROOT / "owner_bindings.json", Path(temporary))
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(
            {row["commit"] for row in report["bindings"]},
            {
                "7c30d54866d81e856f9aa652db236c3a9face924",
                "632e68d247ec36a35b62dbd5c100b0a23d47cf7b",
            },
        )
        self.assertEqual(
            {row["evidence_scope"] for row in report["bindings"]},
            {"owner_selftest", "owner_model"},
        )

    def test_no_unbound_paired_column_owner_claim(self) -> None:
        registry = json.loads((ROOT / "owner_bindings.json").read_text())
        self.assertNotIn(
            "paired_cortical_column_k2",
            {binding["contract"] for binding in registry["bindings"]},
        )

    def test_exact_materialized_artifact_and_snapshot_proof_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, commit = self._repo_binding(root)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"schema_version": 3, "bindings": [binding]}))
            work = root / "work"
            work.mkdir()
            report = run_registry(registry, work)
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(report["bindings"][0]["commit"], commit)
        process = report["bindings"][0]["execution"]["process_argv"]
        self.assertEqual(process[0], binding["execution"]["tool"]["path"])
        self.assertIn("/fixture-owner/owner_probe.py", process[1])

    def test_external_unmaterialized_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            binding["execution"]["artifact"] = "not_committed.py"
            destination = root / "snapshot"
            destination.mkdir()
            with self.assertRaisesRegex(ContractViolation, "OWNER_ARTIFACT_NOT_MATERIALIZED"):
                materialize_binding(binding, destination)

    def test_unpinned_tool_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            binding["execution"]["tool"]["sha256"] = "0" * 64
            destination = root / "snapshot"
            destination.mkdir()
            with self.assertRaisesRegex(ContractViolation, "OWNER_TOOL_SHA_MISMATCH"):
                materialize_binding(binding, destination)

    def test_open_inherited_environment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            binding["execution"]["env"]["PATH"] = "/tmp"
            destination = root / "snapshot"
            destination.mkdir()
            with self.assertRaisesRegex(ContractViolation, "OWNER_ENV_NOT_CLOSED"):
                materialize_binding(binding, destination)

    def test_arbitrary_command_or_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            binding["execution"]["argv"] = ["{external_adapter}"]
            destination = root / "snapshot"
            destination.mkdir()
            with self.assertRaisesRegex(ContractViolation, "OWNER_ARGV_PLACEHOLDER_FORBIDDEN"):
                materialize_binding(binding, destination)

    def test_materialized_artifact_tamper_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            destination = root / "snapshot"
            destination.mkdir()
            snapshot, provenance = materialize_binding(binding, destination)
            (snapshot / "owner_probe.py").write_text("print('forged')\n")
            with self.assertRaisesRegex(ContractViolation, "OWNER_ARTIFACT_CHANGED_BEFORE_EXEC"):
                execute_binding(binding, snapshot, provenance)

    def test_missing_actual_owner_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self._repo_binding(root)
            binding["execution"]["required_output"] = ["OWNER_RTL_WAS_NOT_RUN"]
            destination = root / "snapshot"
            destination.mkdir()
            snapshot, provenance = materialize_binding(binding, destination)
            with self.assertRaisesRegex(ContractViolation, "OWNER_REQUIRED_OUTPUT_MISSING"):
                execute_binding(binding, snapshot, provenance)


if __name__ == "__main__":
    unittest.main(verbosity=2)
