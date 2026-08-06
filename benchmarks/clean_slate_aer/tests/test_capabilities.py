import csv
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import capabilities


class CapabilityContractTest(unittest.TestCase):
    def setUp(self):
        fixtures = ROOT / "fixtures"
        self.minimal = fixtures / "capability_profile_native_minimal.json"
        self.incomplete = fixtures / "capability_profile_core_incomplete.json"
        self.full = fixtures / "capability_profile_native_full.json"
        self.ganghee = (
            fixtures / "capability_profile_ganghee_trad_rowcol_fovea.json"
        )
        self.baseline = fixtures / "capability_profile_baseline.json"
        self.a23 = fixtures / "capability_profile_a23_ee430.json"
        self.workloads = fixtures / "workload_capability_requirements.json"

    def _evaluate(self, profile_path):
        return capabilities.evaluate(
            capabilities.load_profile(profile_path),
            capabilities.load_workloads(self.workloads),
        )

    def test_native_minimal_runs_core_and_skips_optional(self):
        decisions = self._evaluate(self.minimal)
        self.assertEqual(decisions[0]["decision"], capabilities.DECISION_RUN)
        self.assertEqual(
            [row["decision"] for row in decisions[1:]],
            [capabilities.DECISION_SKIP] * 3,
        )
        self.assertIn("native output has no sink-ready", decisions[1]["reason"])
        self.assertTrue(decisions[1]["unsupported_optional"])
        self.assertFalse(decisions[1]["unsupported_core"])

    def test_unsupported_core_is_the_only_hard_failure(self):
        decisions = self._evaluate(self.incomplete)
        self.assertEqual(decisions[0]["decision"], capabilities.DECISION_HARD_FAIL)
        self.assertEqual(decisions[0]["unsupported_core"], "fairness")
        self.assertEqual(
            [row["decision"] for row in decisions[1:]],
            [capabilities.DECISION_SKIP] * 3,
        )

    def test_full_native_profile_runs_every_suite(self):
        self.assertEqual(
            {row["decision"] for row in self._evaluate(self.full)},
            {capabilities.DECISION_RUN},
        )

    def test_actual_candidate_profiles_make_comparable_decisions(self):
        ganghee = self._evaluate(self.ganghee)
        baseline = self._evaluate(self.baseline)
        a23 = self._evaluate(self.a23)
        self.assertEqual(
            [row["decision"] for row in ganghee],
            [capabilities.DECISION_RUN] + [capabilities.DECISION_SKIP] * 3,
        )
        expected_ready_valid = [
            capabilities.DECISION_RUN,
            capabilities.DECISION_RUN,
            capabilities.DECISION_SKIP,
            capabilities.DECISION_SKIP,
        ]
        self.assertEqual([row["decision"] for row in baseline], expected_ready_valid)
        self.assertEqual([row["decision"] for row in a23], expected_ready_valid)
        profile = capabilities.load_profile(self.ganghee)
        self.assertEqual(profile.native_interface.source_count.kind, "fixed")
        self.assertEqual(profile.native_interface.source_count.value, 16)
        self.assertTrue(profile.native_interface.source_observable)
        self.assertEqual(profile.native_interface.retire_lanes, 1)

    def test_cli_compares_multiple_actual_profiles_in_one_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.json"
            status = capabilities.main(
                [
                    "--format",
                    "json",
                    "-o",
                    str(output),
                    "--workloads",
                    str(self.workloads),
                    "--profile",
                    str(self.ganghee),
                    "--profile",
                    str(self.baseline),
                    "--profile",
                    str(self.a23),
                ]
            )
            self.assertEqual(status, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["workloads"]), 12)
            self.assertEqual(
                {row["candidate"] for row in payload["workloads"]},
                {"ganghee_trad_rowcol_fovea", "baseline", "a23-ee430"},
            )

    def test_fixed_source_count_mismatch_is_skip_not_core_failure(self):
        profile = capabilities.load_profile(self.ganghee)
        workload = capabilities.Workload(
            name="core_n64",
            suite="core",
            source_count=64,
            required_capabilities=capabilities.CORE_CAPABILITIES,
        )
        decision = capabilities.evaluate(profile, [workload])[0]
        self.assertEqual(decision["decision"], capabilities.DECISION_SKIP)
        self.assertEqual(decision["unsupported_profile"], "source_count")
        self.assertFalse(decision["unsupported_core"])

    def test_cli_exit_codes_distinguish_skip_hard_fail_and_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "decisions.json"
            base = ["--workloads", str(self.workloads), "-o", str(output)]
            self.assertEqual(
                capabilities.main(["--profile", str(self.minimal), *base]), 0
            )
            self.assertEqual(
                capabilities.main(["--profile", str(self.incomplete), *base]), 2
            )
            bad = Path(directory) / "bad.json"
            bad.write_text("{}", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    capabilities.main(["--profile", str(bad), *base]), 3
                )

    def test_csv_and_json_outputs_are_machine_readable(self):
        decisions = self._evaluate(self.minimal)
        csv_stream = io.StringIO()
        capabilities.write_csv(decisions, csv_stream)
        rows = list(csv.DictReader(io.StringIO(csv_stream.getvalue())))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["decision"], capabilities.DECISION_SKIP)

        json_stream = io.StringIO()
        capabilities.write_json(decisions, json_stream)
        payload = json.loads(json_stream.getvalue())
        self.assertEqual(payload["summary"][capabilities.DECISION_RUN], 1)
        self.assertEqual(payload["summary"][capabilities.DECISION_SKIP], 3)
        self.assertEqual(payload["summary"][capabilities.DECISION_HARD_FAIL], 0)

    def test_profile_requires_explicit_known_capability_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate": "bad",
                        "capabilities": {"sink_always_ready": {"supported": True}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                capabilities.ContractError, "undeclared capabilities"
            ):
                capabilities.load_profile(path)

    def test_core_workload_cannot_smuggle_optional_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workloads.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workloads": [
                            {
                                "name": "bad_core",
                                "suite": "core",
                                "source_count": 16,
                                "required_capabilities": ["output_backpressure"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                capabilities.ContractError, "core workload requires optional"
            ):
                capabilities.load_workloads(path)

    def test_workload_contract_must_cover_complete_core_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workloads.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workloads": [
                            {
                                "name": "incomplete_core",
                                "suite": "core",
                                "source_count": 16,
                                "required_capabilities": ["sink_always_ready"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                capabilities.ContractError, "core suite does not cover"
            ):
                capabilities.load_workloads(path)


if __name__ == "__main__":
    unittest.main()
