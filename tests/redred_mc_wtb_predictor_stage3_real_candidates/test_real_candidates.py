"""Cross-candidate integration and mutation tests on shared analytic streams."""

from __future__ import annotations

import ast
from dataclasses import replace
import inspect
import os
from pathlib import Path
import subprocess
import sys
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.redred_mc_wtb_pose_recovery import RecoveryMode  # noqa: E402
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import (  # noqa: E402
    DSPBModel,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import (  # noqa: E402
    recover_rg3_cav,
)
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import (  # noqa: E402
    SO3PLLModel,
)
from harness import (  # noqa: E402
    CANDIDATE_NAMES,
    IntegrationViolation,
    assert_identity_order_exact_once,
    ordered_events,
    quaternion_equivalent,
    reference_fallback,
    run_all_candidates,
)
from scenarios import (  # noqa: E402
    dropout_stream,
    fallback_stream,
    near_pi_stream,
    same_edge_stream,
    stop_reversal_stream,
)


def _reference_mode(mode: RecoveryMode) -> str:
    if mode is RecoveryMode.CAV:
        return "CAV"
    if mode is RecoveryMode.ZOH:
        return "ZOH"
    return "BYPASS"


class CrossCandidateCausalityTests(unittest.TestCase):
    def test_same_edge_pose_mutation_cannot_change_sealed_cluster(self) -> None:
        ordinary = run_all_candidates(same_edge_stream(2.0))
        adversarial = run_all_candidates(same_edge_stream(40.0))

        for candidate in CANDIDATE_NAMES:
            with self.subTest(candidate=candidate):
                before = ordinary[candidate]
                changed = adversarial[candidate]
                self.assertEqual(before.decisions[:2], changed.decisions[:2])
                self.assertTrue(all(
                    21 not in decision.used_commit_cycles
                    for decision in before.decisions[:2]
                ))
                self.assertFalse(quaternion_equivalent(
                    before.decisions[2].quaternion_xyzw,
                    changed.decisions[2].quaternion_xyzw,
                ))

    def test_equal_timestamp_cluster_consumes_one_atomic_snapshot(self) -> None:
        results = run_all_candidates(same_edge_stream())
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                first, second = result.decisions[:2]
                self.assertNotEqual(first.event_id, second.event_id)
                self.assertEqual(first.timestamp_ns, second.timestamp_ns)
                self.assertEqual(first.decision_cycle, second.decision_cycle)
                self.assertEqual(first.mode, second.mode)
                self.assertEqual(first.candidate_used, second.candidate_used)
                self.assertEqual(first.quaternion_xyzw, second.quaternion_xyzw)
                self.assertEqual(first.used_pose_ids, second.used_pose_ids)
                self.assertEqual(first.used_commit_cycles, second.used_commit_cycles)
                self.assertEqual(first.state_version, second.state_version)
                self.assertEqual(first.reason, second.reason)

    def test_pose_feedback_publication_is_future_only(self) -> None:
        results = run_all_candidates(same_edge_stream())
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                receipt = next(item for item in result.pose_receipts if item.pose_id == 2)
                self.assertTrue(receipt.accepted)
                self.assertEqual(receipt.commit_cycle, 21)
                self.assertEqual(receipt.effective_cycle, 22)
                self.assertNotIn(2, result.decisions[0].used_pose_ids)
                self.assertNotIn(2, result.decisions[1].used_pose_ids)
                self.assertIn(2, result.decisions[2].used_pose_ids)

        self.assertEqual(results["DSPB"].decisions[0].state_version, 2)
        self.assertEqual(results["DSPB"].decisions[2].state_version, 3)
        self.assertEqual(results["SO3_PLL"].decisions[0].state_version, 1)
        self.assertEqual(results["SO3_PLL"].decisions[2].state_version, 2)

    def test_exact_cav_zoh_and_bypass_fallback_for_every_candidate(self) -> None:
        stream = fallback_stream()
        events = ordered_events(stream)
        results = run_all_candidates(stream)
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    tuple(decision.mode for decision in result.decisions),
                    ("BYPASS", "ZOH", "CAV"),
                )
                self.assertTrue(all(not decision.candidate_used for decision in result.decisions))
                for event, decision in zip(events, result.decisions):
                    reference = reference_fallback(stream, event)
                    self.assertEqual(decision.mode, _reference_mode(reference.mode))
                    self.assertEqual(decision.quaternion_xyzw, reference.quaternion_xyzw)
                    self.assertEqual(decision.used_commit_cycles, reference.used_commit_cycles)


class DynamicAndFaultStreamTests(unittest.TestCase):
    def _assert_all_fallbacks_exact(self, stream, result) -> None:
        by_id = {event.event_id: event for event in ordered_events(stream)}
        for decision in result.decisions:
            if decision.candidate_used:
                continue
            reference = reference_fallback(stream, by_id[decision.event_id])
            self.assertEqual(decision.mode, _reference_mode(reference.mode))
            self.assertEqual(decision.quaternion_xyzw, reference.quaternion_xyzw)
            self.assertEqual(decision.used_commit_cycles, reference.used_commit_cycles)

    def test_stop_and_reversal_are_causal_and_conserve_every_event(self) -> None:
        stream = stop_reversal_stream()
        results = run_all_candidates(stream)
        expected_ids = tuple(event.event_id for event in ordered_events(stream))
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                self.assertEqual(tuple(item.event_id for item in result.decisions), expected_ids)
                self._assert_all_fallbacks_exact(stream, result)
                self.assertTrue(all(
                    decision.quaternion_xyzw is None
                    or quaternion_equivalent(decision.quaternion_xyzw, decision.quaternion_xyzw)
                    for decision in result.decisions
                ))

        rg3 = results["RG3"].decisions
        self.assertEqual(rg3[4].reason, "stationary_pose_step")
        self.assertEqual(rg3[6].reason, "direction_gate")
        pll = results["SO3_PLL"].decisions
        self.assertTrue(pll[4].candidate_used)
        self.assertTrue(pll[6].candidate_used)

    def test_dropout_stays_bypass_through_same_edge_then_recovers_causally(self) -> None:
        stream = dropout_stream()
        results = run_all_candidates(stream)
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                self.assertEqual(tuple(item.mode for item in result.decisions[:2]), ("BYPASS", "BYPASS"))
                self.assertNotIn(3, result.decisions[1].used_pose_ids)
                self.assertEqual(result.decisions[2].mode, "CAV")
                self.assertIn(3, result.decisions[2].used_pose_ids)
                self._assert_all_fallbacks_exact(stream, result)

        pll_receipt = next(
            item for item in results["SO3_PLL"].pose_receipts if item.pose_id == 3
        )
        self.assertEqual(pll_receipt.reason, "pose_gap")
        self.assertEqual(results["RG3"].decisions[2].reason, "pose_cadence_out_of_bounds")

    def test_near_pi_fault_never_becomes_candidate_geometry(self) -> None:
        stream = near_pi_stream()
        results = run_all_candidates(stream)
        for candidate, result in results.items():
            with self.subTest(candidate=candidate):
                same_edge, future = result.decisions
                self.assertNotIn(2, same_edge.used_pose_ids)
                self.assertFalse(future.candidate_used)
                self.assertEqual(future.mode, "CAV")
                self._assert_all_fallbacks_exact(stream, result)

        self.assertEqual(results["RG3"].decisions[1].reason, "near_pi_pose_step")
        dspb_receipt = next(
            item for item in results["DSPB"].pose_receipts if item.pose_id == 2
        )
        self.assertIn("near-pi margin", dspb_receipt.reason)
        pll_receipt = next(
            item for item in results["SO3_PLL"].pose_receipts if item.pose_id == 2
        )
        self.assertEqual(pll_receipt.reason, "near_pi_residual")


class ReplayAndMutationTests(unittest.TestCase):
    def test_every_candidate_replays_byte_deterministically(self) -> None:
        stream = stop_reversal_stream()
        first = run_all_candidates(stream)
        second = run_all_candidates(stream)
        self.assertEqual(first, second)
        for candidate in CANDIDATE_NAMES:
            self.assertEqual(first[candidate].replay_sha256, second[candidate].replay_sha256)
            self.assertEqual(len(first[candidate].replay_sha256), 64)

    def test_missing_duplicate_and_reordered_decision_mutants_are_killed(self) -> None:
        stream = stop_reversal_stream()
        for candidate, result in run_all_candidates(stream).items():
            decisions = result.decisions
            with self.subTest(candidate=candidate, mutation="missing"):
                with self.assertRaisesRegex(IntegrationViolation, "cardinality"):
                    assert_identity_order_exact_once(stream, decisions[:-1])
            with self.subTest(candidate=candidate, mutation="duplicate"):
                mutant = decisions[:-1] + (decisions[0],)
                with self.assertRaises(IntegrationViolation):
                    assert_identity_order_exact_once(stream, mutant)
            with self.subTest(candidate=candidate, mutation="reorder"):
                mutant = (decisions[1], decisions[0]) + decisions[2:]
                with self.assertRaisesRegex(IntegrationViolation, "order"):
                    assert_identity_order_exact_once(stream, mutant)

    def test_identity_rewrite_mutant_is_killed(self) -> None:
        stream = same_edge_stream()
        for candidate, result in run_all_candidates(stream).items():
            mutant = (replace(result.decisions[0], event_id=999),) + result.decisions[1:]
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(IntegrationViolation, "identity or order"):
                    assert_identity_order_exact_once(stream, mutant)


class ScoreBlindBoundaryTests(unittest.TestCase):
    def test_public_candidate_runtime_signatures_have_no_scorer_or_label_input(self) -> None:
        callables = (
            recover_rg3_cav,
            DSPBModel.commit_pose,
            DSPBModel.predict_event,
            DSPBModel.predict_event_cluster,
            SO3PLLModel.commit_pose,
            SO3PLLModel.predict,
        )
        forbidden = (
            "loss",
            "scorer",
            "score_value",
            "label",
            "selector",
            "truth",
            "oracle",
            "window",
            "query",
            "rank",
            "role",
        )
        for function in callables:
            parameters = tuple(inspect.signature(function).parameters)
            with self.subTest(function=function.__qualname__):
                for name in parameters:
                    self.assertFalse(any(token in name.lower() for token in forbidden), name)

    def test_candidate_modules_do_not_import_scorer_label_or_selector_modules(self) -> None:
        relatives = (
            "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
        )
        forbidden = ("selector", "evaluator", "scoring", "screen108")
        for relative in relatives:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            with self.subTest(relative=relative):
                self.assertFalse([
                    name for name in imports
                    if any(token in name.lower() for token in forbidden)
                ])

    def test_fresh_score_free_import_does_not_load_scoring_packages(self) -> None:
        script = """
import sys
from benchmarks.redred_mc_wtb_predictor_stage3 import dspb, rg3, so3_pll
forbidden = ('selector', 'stage4_evaluator', 'stage4_scoring', 'screen108')
loaded = sorted(name for name in sys.modules if any(token in name.lower() for token in forbidden))
if loaded:
    raise SystemExit('forbidden modules loaded: ' + ','.join(loaded))
"""
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, "-S", "-B", "-c", script],
            cwd=str(ROOT),
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_new_sources_parse_with_python38_grammar(self) -> None:
        for path in sorted(HERE.glob("*.py")):
            with self.subTest(path=path.name):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 8),
                )


if __name__ == "__main__":
    unittest.main()
