"""Fail-closed mutations against actual native adapters and screen projection."""

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
    screen_projection,
    so3_pll,
)
import production_gate  # noqa: E402
from production_gate import (  # noqa: E402
    CANDIDATE_NAMES,
    ExactProductionGate,
    NativeReplayViolation,
    ScreenContractViolation,
    ScreenReplayViolation,
    authority,
    generate_production_output,
    make_fallback_taxonomy_fixture,
    make_motion_fixture,
    make_noncommuting_rg3_fixture,
    project_verified_native,
    reseal_native_envelope,
    reseal_pll_transitions,
    reseal_screen_output,
    verify_native_output,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256  # noqa: E402


class ActualProductionMutationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = make_motion_fixture(2)
        cls.native = {
            name: generate_production_output(name, cls.fixture)
            for name in CANDIDATE_NAMES
        }
        cls.gates = {
            name: ExactProductionGate(name, cls.fixture, cls.native[name])
            for name in CANDIDATE_NAMES
        }
        cls.screen = {
            name: deepcopy(cls.gates[name].expected.screen_output)
            for name in CANDIDATE_NAMES
        }

    def _assert_native_killed(self, candidate_name, mutant):
        self.assertNotEqual(
            mutant,
            self.native[candidate_name],
            "native mutation had no observable effect",
        )
        if candidate_name == "RG3":
            with self.assertRaisesRegex(
                NativeReplayViolation,
                "RG3 native output differs from deterministic exact replay",
            ):
                self.gates[candidate_name].validate_native(mutant)
        elif candidate_name == "DSPB":
            with self.assertRaisesRegex(
                dspb_output.DSPBOutputError,
                "DSPB candidate output differs from locked replay",
            ):
                self.gates[candidate_name].validate_native(mutant)
        elif candidate_name == "PLL":
            with self.assertRaisesRegex(
                pll_output.PLLOutputError,
                "PLL candidate output differs from exact native replay",
            ):
                self.gates[candidate_name].validate_native(mutant)
        else:  # pragma: no cover - test helper fail-closed guard
            self.fail("unclassified candidate")

    def _assert_screen_killed(self, candidate_name, mutant):
        self.assertNotEqual(
            mutant,
            self.screen[candidate_name],
            "screen mutation had no observable effect",
        )
        try:
            self.gates[candidate_name].validate_screen(mutant)
        except (ScreenContractViolation, ScreenReplayViolation):
            return
        self.fail("projected mutation escaped classified screen gates")

    def test_native_verify_projection_screen_order_is_explicit(self):
        real_verify = production_gate.verify_native_output
        real_project = screen_projection.project_native_output
        real_screen = screen108._validate_candidate_output
        for candidate_name in CANDIDATE_NAMES:
            calls = []

            def traced_verify(name, fixture, value):
                calls.append("native_verify")
                return real_verify(name, fixture, value)

            def traced_project(value):
                calls.append("project")
                return real_project(value)

            def traced_screen(*args, **kwargs):
                calls.append("screen108")
                return real_screen(*args, **kwargs)

            with self.subTest(candidate=candidate_name):
                with mock.patch.object(
                    production_gate,
                    "verify_native_output",
                    side_effect=traced_verify,
                ), mock.patch.object(
                    screen_projection,
                    "project_native_output",
                    side_effect=traced_project,
                ), mock.patch.object(
                    screen108,
                    "_validate_candidate_output",
                    side_effect=traced_screen,
                ):
                    project_verified_native(
                        candidate_name,
                        self.fixture,
                        self.native[candidate_name],
                    )
                self.assertEqual(calls, ["native_verify", "project", "screen108"])

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
        self.gates["RG3"].validate_native(rg3_value)
        self.gates["DSPB"].validate_native(dspb_value)
        self.gates["PLL"].validate_native(pll_value)

    def test_projection_is_typed_exact_and_deterministic(self):
        for candidate_name in CANDIDATE_NAMES:
            native = self.native[candidate_name]
            verify_native_output(candidate_name, self.fixture, native)
            first = screen_projection.project_native_output(native)
            verify_native_output(candidate_name, self.fixture, native)
            second = screen_projection.project_native_output(native)
            with self.subTest(candidate=candidate_name):
                self.assertIs(type(first), screen_projection.ScreenProjection)
                self.assertEqual(first, second)
                self.assertEqual(first.screen_output, self.screen[candidate_name])

    def test_projected_fallback_taxonomy_is_exact_and_detail_stays_native(self):
        sensor_reasons = {"no_occurrence_pose", "invalid_pose", "stale_pose"}
        fixture = make_fallback_taxonomy_fixture()
        for candidate_name in CANDIDATE_NAMES:
            native_output = generate_production_output(candidate_name, fixture)
            verified = ExactProductionGate(
                candidate_name, fixture, native_output
            ).expected
            receipt = verified.projection.projection_receipt
            self.assertIsInstance(receipt, dict)
            routes = {
                event["route"]
                for window in verified.screen_output["windows"]
                for event in window["events"]
            }
            self.assertIn("fresh_zoh", routes)
            self.assertIn("current_cav", routes)
            for native_window, screen_window, receipt_window in zip(
                verified.native_output["windows"],
                verified.screen_output["windows"],
                receipt["windows"],
            ):
                for native, screen, binding in zip(
                    native_window["events"],
                    screen_window["events"],
                    receipt_window["event_bindings"],
                ):
                    route = screen["route"]
                    with self.subTest(
                        candidate=candidate_name,
                        event_id=screen["event_id"],
                        route=route,
                    ):
                        if route == "candidate":
                            self.assertIsNone(screen["fallback_reason"])
                        elif route == "current_cav":
                            self.assertEqual(
                                screen["fallback_reason"], "candidate_failure"
                            )
                            detail = native.get("candidate_failure_reason")
                            if not detail:
                                detail = native.get("fallback_reason")
                            self.assertIsInstance(detail, str)
                            self.assertTrue(detail)
                            self.assertNotEqual(screen["fallback_reason"], detail)
                            self.assertEqual(
                                binding["source_decision_sha256"],
                                native["decision_sha256"],
                            )
                            self.assertEqual(
                                binding["projected_decision_sha256"],
                                screen["decision_sha256"],
                            )
                        elif route == "fresh_zoh":
                            self.assertEqual(
                                screen["fallback_reason"], "fresh_zoh_fallback"
                            )
                        elif route == "sensor_fixed":
                            self.assertIn(screen["fallback_reason"], sensor_reasons)
                        else:  # pragma: no cover - exact route allowlist guard
                            self.fail("projection emitted an unclassified route")

    def test_noncommuting_multi_axis_rg3_transport_mutant_is_killed(self):
        fixture = make_noncommuting_rg3_fixture()
        pristine = generate_production_output("RG3", fixture)
        gate = ExactProductionGate("RG3", fixture, pristine)
        self.assertTrue(pristine["windows"][0]["events"][0]["candidate_used"])

        with mock.patch.object(rg3, "_rotate_vector", side_effect=lambda _q, v: v):
            mutant = generate_production_output("RG3", fixture)
        self.assertNotEqual(mutant, pristine, "RG3 transport mutant was equivalent")
        with self.assertRaisesRegex(
            NativeReplayViolation,
            "RG3 native output differs from deterministic exact replay",
        ):
            gate.validate_native(mutant)

    def test_cross_window_pll_state_carry_is_classified_before_output(self):
        shared = so3_pll.SO3PLLModel(pll_output.LOCKED_PLL_CONFIG)
        with mock.patch.object(
            pll_output,
            "SO3PLLModel",
            side_effect=lambda _config: shared,
        ):
            with self.assertRaisesRegex(
                pll_output.PLLOutputError,
                "escaped the current reset generation",
            ):
                generate_production_output("PLL", self.fixture)

    def test_pll_commit_time_anchor_mutant_is_killed_by_exact_native_replay(self):
        real_commit = so3_pll.SO3PLLModel.commit_pose

        def commit_anchored(model, pose_id, measurement_ns, cycle, quaternion, *, valid=True):
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
        self._assert_native_killed("PLL", mutant)

    def test_dspb_hindcast_mutant_is_killed_by_exact_native_replay(self):
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
        self._assert_native_killed("DSPB", mutant)

    def test_dspb_stale_winner_mutant_is_killed_by_exact_native_replay(self):
        def stale_winner(model, functions, _credits):
            by_id = {function.expert_id: function for function in functions}
            prior = model.published_state.selected_expert_id
            if prior is not None and prior in by_id and by_id[prior].valid:
                return prior, "mutant_stale_winner"
            return dspb.E0, "mutant_forced_initial_winner"

        with mock.patch.object(dspb.DSPBModel, "_select_winner", stale_winner):
            mutant = generate_production_output("DSPB", self.fixture)
        self._assert_native_killed("DSPB", mutant)

    def test_candidate_specific_rich_evidence_mutations_are_killed(self):
        rg3_mutant = deepcopy(self.native["RG3"])
        rg3_mutant["candidate_config_sha256"] = "a" * 64
        self._assert_native_killed("RG3", reseal_native_envelope(rg3_mutant))

        dspb_mutant = deepcopy(self.native["DSPB"])
        dspb_mutant["candidate_config"]["max_horizon_ns"] += 1
        dspb_mutant["candidate_config_sha256"] = canonical_sha256(
            dspb_mutant["candidate_config"]
        )
        self._assert_native_killed("DSPB", reseal_native_envelope(dspb_mutant))

        pll_mutant = deepcopy(self.native["PLL"])
        transition = pll_mutant["windows"][0]["state_transitions"][0]
        transition["forecast_generation_cycle"] = 0
        self._assert_native_killed("PLL", reseal_pll_transitions(pll_mutant))

    def test_unrelated_unit_ray_mutations_use_projected_screen_output(self):
        for candidate_name in CANDIDATE_NAMES:
            mutant = deepcopy(self.screen[candidate_name])
            row = next(
                event
                for event in mutant["windows"][0]["events"]
                if event["candidate_used"]
            )
            x, y, z = row["world_ray"]
            row["world_ray"] = [y, -x, z]
            sealed = reseal_screen_output(mutant)
            candidate_authority = authority(candidate_name)
            with self.subTest(candidate=candidate_name):
                # The generic contract accepts this unrelated unit ray.  The
                # exact projected replay must still reject it.
                screen108._validate_candidate_output(
                    sealed,
                    self.fixture.bundle,
                    self.fixture.baseline,
                    candidate_authority.executable_sha256,
                    candidate_authority.config_sha256,
                )
                with self.assertRaisesRegex(
                    ScreenReplayViolation,
                    "differs from pristine locked projection",
                ):
                    self.gates[candidate_name].validate_screen(sealed)

    def test_route_pose_edge_and_event_id_mutations_are_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            mutations = {}

            route = deepcopy(self.screen[candidate_name])
            candidate = next(
                row for row in route["windows"][0]["events"]
                if row["candidate_used"]
            )
            candidate["route"] = "current_cav"
            mutations["route"] = route

            pose = deepcopy(self.screen[candidate_name])
            pose["windows"][0]["events"][1]["used_pose_ids"].append(9)
            pose["windows"][0]["events"][1]["used_pose_ids"] = sorted(set(
                pose["windows"][0]["events"][1]["used_pose_ids"]
            ))
            mutations["same_edge_pose"] = pose

            edge = deepcopy(self.screen[candidate_name])
            edge_row = edge["windows"][0]["events"][1]
            edge_row["occurrence_cycle"] += 1
            edge_row["decision_cycle"] += 1
            mutations["edge"] = edge

            event_id = deepcopy(self.screen[candidate_name])
            event_id["windows"][0]["events"][0]["event_id"] += 100_000
            mutations["event_id"] = event_id

            for mutation_name, mutant in mutations.items():
                with self.subTest(candidate=candidate_name, mutation=mutation_name):
                    self._assert_screen_killed(
                        candidate_name, reseal_screen_output(mutant)
                    )

    def test_identity_executable_and_config_substitutions_are_killed(self):
        fields = (
            ("candidate_id", "MUTANT_ID"),
            ("candidate_executable_sha256", "a" * 64),
            ("candidate_config_sha256", "b" * 64),
        )
        for candidate_name in CANDIDATE_NAMES:
            for field, replacement in fields:
                mutant = deepcopy(self.screen[candidate_name])
                mutant[field] = replacement
                with self.subTest(candidate=candidate_name, field=field):
                    self._assert_screen_killed(
                        candidate_name, reseal_screen_output(mutant)
                    )

    def test_fallback_relabel_mutants_are_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            mutant = deepcopy(self.screen[candidate_name])
            fallback = next(
                event
                for event in mutant["windows"][0]["events"]
                if not event["candidate_used"]
            )
            fallback["model_id"] = mutant["candidate_id"]
            with self.subTest(candidate=candidate_name):
                self._assert_screen_killed(
                    candidate_name, reseal_screen_output(mutant)
                )

            reason_mutant = deepcopy(self.screen[candidate_name])
            sensor_fixed = next(
                event
                for event in reason_mutant["windows"][0]["events"]
                if event["route"] == "sensor_fixed"
            )
            self.assertEqual(sensor_fixed["fallback_reason"], "stale_pose")
            sensor_fixed["fallback_reason"] = "invalid_pose"
            with self.subTest(candidate=candidate_name, mutation="baseline_reason"):
                self._assert_screen_killed(
                    candidate_name, reseal_screen_output(reason_mutant)
                )

    def test_delete_reorder_and_duplicate_mutants_are_killed_after_reseal(self):
        for candidate_name in CANDIDATE_NAMES:
            original = self.screen[candidate_name]
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
                    self._assert_screen_killed(
                        candidate_name, reseal_screen_output(mutant)
                    )

    def test_outcome_retry_rewrite_and_append_mutants_are_killed(self):
        for candidate_name in CANDIDATE_NAMES:
            pristine = self.screen[candidate_name]
            rewritten = deepcopy(pristine)
            rows = rewritten["windows"][0]["events"]
            fallback = next(row for row in rows if not row["candidate_used"])
            future = next(row for row in rows if row["candidate_used"])
            for field in (
                "model_id",
                "predictor_state_version",
                "used_pose_ids",
                "route",
                "candidate_attempted",
                "candidate_used",
                "fallback_reason",
                "world_ray",
            ):
                fallback[field] = deepcopy(future[field])
            with self.subTest(candidate=candidate_name, mutation="rewrite"):
                self._assert_screen_killed(
                    candidate_name, reseal_screen_output(rewritten)
                )

            appended = deepcopy(pristine)
            appended["windows"][0]["events"].append(
                deepcopy(appended["windows"][0]["events"][0])
            )
            with self.subTest(candidate=candidate_name, mutation="retry_append"):
                self._assert_screen_killed(
                    candidate_name, reseal_screen_output(appended)
                )

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
