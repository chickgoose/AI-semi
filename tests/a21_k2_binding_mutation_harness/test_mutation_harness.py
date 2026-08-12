#!/usr/bin/env python3
"""Regression tests for the reusable black-box K2 mutation harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fixture_wrapper import BINDINGS, inject, simulate
from oracle import (
    BindingViolation,
    Event,
    TraceContractError,
    legacy_per_source_scoreboard_passes,
    validate_trace,
)
from run_mutation_suite import DEFAULT_REGISTRY, load_registry, run_suite
from vectors import MUTATIONS, build_vectors, vector_for


ROOT = Path(__file__).resolve().parent


class FlattenedOracleTest(unittest.TestCase):
    def test_clean_directed_trace_passes_for_both_wrapper_profiles(self) -> None:
        for binding in BINDINGS:
            for name, stimulus in build_vectors().items():
                with self.subTest(binding=binding, vector=name):
                    report = validate_trace(stimulus, simulate(stimulus, binding), binding)
                    self.assertGreaterEqual(report.accepted, report.retired)

    def test_every_required_mutation_has_exact_diagnostic_on_both_bindings(self) -> None:
        for binding in BINDINGS:
            for mutation, diagnostic in MUTATIONS.items():
                with self.subTest(binding=binding, mutation=mutation):
                    stimulus = vector_for(mutation)
                    observed = inject(simulate(stimulus, binding), mutation)
                    with self.assertRaises(BindingViolation) as caught:
                        validate_trace(stimulus, observed, binding)
                    self.assertEqual(diagnostic, caught.exception.code)

    def test_cross_source_lane_swap_defeats_per_source_scoreboard_only(self) -> None:
        accepted = [Event(0, "a:s0", 0xA000), Event(5, "b:s5", 0xB005)]
        retired = [accepted[1], accepted[0]]
        self.assertTrue(legacy_per_source_scoreboard_passes(accepted, retired))
        stimulus = vector_for("lane_swap")
        observed = inject(simulate(stimulus, BINDINGS[0]), "lane_swap")
        with self.assertRaisesRegex(BindingViolation, "^GLOBAL_ORDER_MISMATCH"):
            validate_trace(stimulus, observed, BINDINGS[0])

    def test_payload_corruption_is_not_hidden_by_matching_source(self) -> None:
        stimulus = vector_for("duplicate")
        observed = simulate(stimulus, BINDINGS[0])
        observed["cycles"][2]["outputs"][0]["payload"] ^= 1
        with self.assertRaisesRegex(BindingViolation, "^RETIRE_CONTENT_MISMATCH"):
            validate_trace(stimulus, observed, BINDINGS[0])

    def test_malformed_observation_fails_closed(self) -> None:
        stimulus = vector_for("duplicate")
        observed = simulate(stimulus, BINDINGS[0])
        observed["cycles"][2]["outputs"] = observed["cycles"][2]["outputs"][:1]
        with self.assertRaises(TraceContractError):
            validate_trace(stimulus, observed, BINDINGS[0])

    def test_wrong_binding_identity_fails_closed(self) -> None:
        stimulus = vector_for("duplicate")
        observed = simulate(stimulus, BINDINGS[0])
        with self.assertRaisesRegex(TraceContractError, "binding identity"):
            validate_trace(stimulus, observed, BINDINGS[1])


class BlackBoxRunnerTest(unittest.TestCase):
    def test_registry_names_exact_a2_a3_wrappers(self) -> None:
        rows = load_registry(DEFAULT_REGISTRY)
        self.assertEqual([row["name"] for row in rows], list(BINDINGS))

    def test_registry_rejects_unknown_command_placeholder(self) -> None:
        document = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
        document["bindings"][0]["runner"].append("{shell}")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(TraceContractError, "unknown runner placeholder"):
                load_registry(path)

    def test_process_isolation_suite_kills_twenty(self) -> None:
        report = run_suite()
        self.assertEqual("PASS", report["status"])
        self.assertEqual(20, report["killed"])
        self.assertEqual(10, report["mutations_per_binding"])
        self.assertEqual(
            set(MUTATIONS.values()),
            {row["diagnostic"] for row in report["results"]},
        )

    def test_public_trace_checker_cli(self) -> None:
        stimulus = vector_for("lane_swap")
        observed = inject(simulate(stimulus, BINDINGS[1]), "lane_swap")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stimulus_path = root / "stimulus.json"
            observed_path = root / "observed.json"
            stimulus_path.write_text(json.dumps(stimulus), encoding="utf-8")
            observed_path.write_text(json.dumps(observed), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "check_trace.py"),
                    "--binding",
                    BINDINGS[1],
                    "--stimulus",
                    str(stimulus_path),
                    "--observations",
                    str(observed_path),
                    "--expect-diagnostic",
                    "GLOBAL_ORDER_MISMATCH",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("A21_K2_EXPECTED_KILL", result.stdout)


if __name__ == "__main__":
    unittest.main()
