"""Fail-closed mutations against actual candidates and production adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import ast
from pathlib import Path
import sys
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.redred_mc_wtb_predictor_stage3 import (  # noqa: E402
    dspb,
    dspb_output,
    pll_output,
    rg3,
    rg3_output,
    screen108,
    so3_pll,
)
from production_gate import (  # noqa: E402
    CANDIDATE_NAMES,
    ExactProductionGate,
    GateViolation,
    authority,
    generate_production_output,
    make_motion_fixture,
    make_noncommuting_rg3_fixture,
    reseal,
)


class ActualProductionMutationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = make_motion_fixture(2)
        cls.pristine = {
            name: generate_production_output(name, cls.fixture)
            for name in CANDIDATE_NAMES
        }
        cls.gates = {
            name: ExactProductionGate(name, cls.fixture, cls.pristine[name])
            for name in CANDIDATE_NAMES
        }

    def _assert_killed(self, candidate_name, mutant):
        self.assertNotEqual(
            mutant,
            self.pristine[candidate_name],
            "mutation had no observable effect",
        )
        with self.assertRaises(GateViolation):
            self.gates[candidate_name].validate(mutant)

    def test_actual_candidates_are_invoked_through_production_adapters(self):
        rg3_calls = []
        dspb_calls = []
        pll_calls = []
        real_rg3 = rg3_output.recover_rg3_cav
        real_dspb = dspb.DSPBModel.predict_event_cluster
        real_pll = so3_pll.SO3PLLModel.predict

        def traced_rg3(samples, timestamp_ns, edge):
            rg3_calls.append((timestamp_ns, edge))
            return real_rg3(samples, timestamp_ns, edge)

        def traced_dspb(model, events):
            dspb_calls.append(tuple(event.event_id for event in events))
            return real_dspb(model, events)

        def traced_pll(model, timestamp_ns, edge):
            pll_calls.append((timestamp_ns, edge))
            return real_pll(model, timestamp_ns, edge)

        with mock.patch.object(rg3_output, "recover_rg3_cav", side_effect=traced_rg3):
            rg3_value = generate_production_output("RG3", self.fixture)
        with mock.patch.object(dspb.DSPBModel, "predict_event_cluster", traced_dspb):
            dspb_value = generate_production_output("DSPB", self.fixture)
        with mock.patch.object(so3_pll.SO3PLLModel, "predict", traced_pll):
            pll_value = generate_production_output("PLL", self.fixture)

        self.assertTrue(rg3_calls)
        self.assertTrue(dspb_calls)
        self.assertTrue(pll_calls)
        self.gates["RG3"].validate(rg3_value)
        self.gates["DSPB"].validate(dspb_value)
        self.gates["PLL"].validate(pll_value)

    def test_noncommuting_multi_axis_rg3_transport_mutant_is_killed(self):
        fixture = make_noncommuting_rg3_fixture()
        pristine = generate_production_output("RG3", fixture)
        gate = ExactProductionGate("RG3", fixture, pristine)
        row = pristine["windows"][0]["events"][0]
        self.assertTrue(row["candidate_used"])

        # Removing body-frame transport is deliberately non-vacuous here:
        # the changing multi-axis increments make transported acceleration
        # differ from the untransported vector.
        with mock.patch.object(rg3, "_rotate_vector", side_effect=lambda _q, v: v):
            mutant = generate_production_output("RG3", fixture)
        self.assertNotEqual(mutant, pristine, "RG3 transport mutant was equivalent")
        with self.assertRaises(GateViolation):
            gate.validate(mutant)

    def test_occurrence_decision_substitution_is_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            with self.subTest(candidate=candidate_name):
                mutant = deepcopy(self.pristine[candidate_name])
                row = mutant["windows"][0]["events"][1]
                row["decision_cycle"] += 1
                self._assert_killed(candidate_name, reseal(mutant))

    def test_same_edge_pose_substitution_is_killed_after_reseal(self):
        # The event at index 1 shares the exact edge with pose ID 9.  Merely
        # listing that pose in a correctly resealed receipt must fail.
        for candidate_name in CANDIDATE_NAMES:
            with self.subTest(candidate=candidate_name):
                mutant = deepcopy(self.pristine[candidate_name])
                row = mutant["windows"][0]["events"][1]
                row["used_pose_ids"] = sorted(set(row["used_pose_ids"] + [9]))
                self._assert_killed(candidate_name, reseal(mutant))

    def test_cross_window_pll_state_carry_mutant_is_killed(self):
        shared = so3_pll.SO3PLLModel(pll_output.LOCKED_PLL_CONFIG)
        with mock.patch.object(
            pll_output,
            "SO3PLLModel",
            side_effect=lambda _config: shared,
        ):
            mutant = generate_production_output("PLL", self.fixture)
        self._assert_killed("PLL", mutant)

    def test_pll_commit_time_anchor_mutant_is_killed(self):
        real_commit = so3_pll.SO3PLLModel.commit_pose

        def commit_anchored(model, pose_id, measurement_ns, cycle, quaternion, *, valid=True):
            # A commit-time implementation incorrectly shifts the oscillator
            # anchor away from the supplied measurement timestamp.
            return real_commit(
                model,
                pose_id,
                measurement_ns + 1_000_000,
                cycle,
                quaternion,
                valid=valid,
            )

        with mock.patch.object(so3_pll.SO3PLLModel, "commit_pose", commit_anchored):
            mutant = generate_production_output("PLL", self.fixture)
        self._assert_killed("PLL", mutant)

    def test_dspb_hindcast_mutant_is_killed(self):
        real_score = dspb.DSPBModel._score_prior_functions

        def hindcast(model, pose):
            scores = real_score(model, pose)
            return tuple(
                replace(
                    score,
                    forecast_valid=True,
                    forecast_quaternion_xyzw=pose.quaternion_xyzw,
                    angular_error_rad=0.0,
                    reason="mutant_hindcast_after_pose",
                )
                if score.forecast_state_version is not None
                else score
                for score in scores
            )

        with mock.patch.object(dspb.DSPBModel, "_score_prior_functions", hindcast):
            mutant = generate_production_output("DSPB", self.fixture)
        self._assert_killed("DSPB", mutant)

    def test_dspb_stale_winner_mutant_is_killed(self):
        def stale_winner(model, functions, _credits):
            by_id = {function.expert_id: function for function in functions}
            prior = model.published_state.selected_expert_id
            if prior is not None and prior in by_id and by_id[prior].valid:
                return prior, "mutant_stale_winner"
            return dspb.E0, "mutant_forced_initial_winner"

        with mock.patch.object(dspb.DSPBModel, "_select_winner", stale_winner):
            mutant = generate_production_output("DSPB", self.fixture)
        self._assert_killed("DSPB", mutant)

    def test_unrelated_unit_ray_mutant_is_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            with self.subTest(candidate=candidate_name):
                mutant = deepcopy(self.pristine[candidate_name])
                row = next(
                    event
                    for event in mutant["windows"][0]["events"]
                    if event["candidate_used"]
                )
                x, y, z = row["world_ray"]
                row["world_ray"] = [y, -x, z]
                sealed = reseal(mutant)
                # This unit but unrelated ray satisfies the generic production
                # screen.  The independent exact replay is the stronger gate.
                candidate_authority = authority(candidate_name)
                screen108._validate_candidate_output(
                    sealed,
                    self.fixture.bundle,
                    self.fixture.baseline,
                    candidate_authority.executable_sha256,
                    candidate_authority.config_sha256,
                )
                self._assert_killed(candidate_name, sealed)

    def test_identity_executable_and_config_substitutions_are_killed(self):
        fields = (
            ("candidate_id", "MUTANT_ID"),
            ("candidate_executable_sha256", "a" * 64),
            ("candidate_config_sha256", "b" * 64),
        )
        for candidate_name in CANDIDATE_NAMES:
            for field, replacement in fields:
                with self.subTest(candidate=candidate_name, field=field):
                    mutant = deepcopy(self.pristine[candidate_name])
                    mutant[field] = replacement
                    self._assert_killed(candidate_name, reseal(mutant))

    def test_fallback_relabel_mutants_are_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            with self.subTest(candidate=candidate_name):
                mutant = deepcopy(self.pristine[candidate_name])
                fallback = next(
                    event
                    for event in mutant["windows"][0]["events"]
                    if not event["candidate_used"]
                )
                fallback["model_id"] = mutant["candidate_id"]
                self._assert_killed(candidate_name, reseal(mutant))

    def test_delete_reorder_and_duplicate_mutants_are_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            original = self.pristine[candidate_name]
            mutations = {}

            deleted = deepcopy(original)
            del deleted["windows"][0]["events"][0]
            mutations["delete"] = deleted

            reordered = deepcopy(original)
            rows = reordered["windows"][0]["events"]
            rows[0], rows[1] = rows[1], rows[0]
            mutations["reorder"] = reordered

            duplicated = deepcopy(original)
            duplicated["windows"][0]["events"].append(
                deepcopy(duplicated["windows"][0]["events"][0])
            )
            mutations["duplicate"] = duplicated

            for mutation_name, mutant in mutations.items():
                with self.subTest(candidate=candidate_name, mutation=mutation_name):
                    self._assert_killed(candidate_name, reseal(mutant))

    def test_outcome_retry_rewrite_and_append_mutants_are_killed(self):
        for candidate_name in CANDIDATE_NAMES:
            pristine = self.pristine[candidate_name]
            rewritten = deepcopy(pristine)
            rows = rewritten["windows"][0]["events"]
            fallback = next(row for row in rows if not row["candidate_used"])
            future = next(row for row in rows if row["candidate_used"])
            # Retrospectively retry an earlier fallback using a later locked
            # decision while preserving the earlier event identity.
            for field in (
                "model_id",
                "predictor_state_version",
                "used_pose_ids",
                "candidate_used",
                "fallback_reason",
                "world_ray",
            ):
                fallback[field] = deepcopy(future[field])
            with self.subTest(candidate=candidate_name, mutation="rewrite"):
                self._assert_killed(candidate_name, reseal(rewritten))

            appended = deepcopy(pristine)
            appended["windows"][0]["events"].append(
                deepcopy(appended["windows"][0]["events"][0])
            )
            with self.subTest(candidate=candidate_name, mutation="retry_append"):
                self._assert_killed(candidate_name, reseal(appended))

    def test_test_only_gate_parses_with_python38_grammar(self):
        for path in (HERE / "production_gate.py", Path(__file__)):
            with self.subTest(path=path.name):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 8),
                )


if __name__ == "__main__":
    unittest.main()
