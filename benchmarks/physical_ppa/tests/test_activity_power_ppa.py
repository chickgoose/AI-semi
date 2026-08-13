import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import evaluate_activity_power_ppa as evaluator


def artifact(root, relative, content):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def json_artifact(root, relative, value):
    return artifact(root, relative, json.dumps(value, indent=2, sort_keys=True) + "\n")


def make_record(root, fmt="vcd"):
    common = {
        "archive": artifact(root, "source/server-results.tar.gz", "fixture archive\n"),
        "bundle": artifact(root, "candidate/bundle.tar", "fixture bundle\n"),
        "flow_config": artifact(root, "flow/config.tcl", "fixture flow\n"),
        "sdc": artifact(root, "flow/common.sdc", "create_clock -period 5 clk\n"),
        "library": artifact(root, "flow/cells.lib", "fixture library\n"),
        "manifest": artifact(root, "workload/manifest.json", "fixture manifest\n"),
        "trace": artifact(root, "workload/trace.jsonl", "fixture trace\n"),
    }
    scope_root = "candidate_full_endpoint"
    scope = {
        "schema_version": 1,
        "scope_root": scope_root,
        "objects": [
            {"path": f"{scope_root}.u_core.state_q", "bits": 32},
            {"path": f"{scope_root}.u_rx.valid_q", "bits": 1},
            {"path": f"{scope_root}.u_tx.data_q", "bits": 16},
        ],
    }
    scope_ref = json_artifact(root, "boundary/scope.json", scope)
    scope_sha = evaluator._scope_hash(scope, scope_root)
    pins = {
        "schema_version": 1,
        "pins": [
            {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
            {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
            {"name": "req", "direction": "input", "width": 16, "role": "functional"},
            {"name": "ready", "direction": "output", "width": 1, "role": "functional"},
            {"name": "aer", "direction": "bidirectional", "width": 8, "role": "functional"},
        ],
    }
    pins_ref = json_artifact(root, "boundary/pins.json", pins)
    waveform_text = (
        "$timescale 1ns $end\n$scope module candidate_full_endpoint $end\n"
        "$var wire 32 ! core_state $end\n$var wire 1 \" rx_valid $end\n"
        "$var wire 16 # tx_data $end\n$upscope $end\n"
        "$enddefinitions $end\n#0\n0!\n#2000\n1!\n"
        if fmt == "vcd"
        else "(SAIFILE (TIMESCALE 1 ns) (DURATION 2000) (INSTANCE candidate_full_endpoint))\n"
    )
    waveform = artifact(root, f"activity/activity.{fmt}", waveform_text)
    timing_points = []
    for name, period, setup in (("pass", 5.0, 0.1), ("fail", 4.0, -0.1)):
        timing_points.append({
            "period_ns": period,
            "setup_wns_ns": setup,
            "hold_wns_ns": 0.02,
            "route_ok": True,
            "unconstrained_paths": 0,
            "drc_violations": 0,
            "antenna_violations": 0,
            "netlist": artifact(root, f"flow/{name}.v", f"module {name}; endmodule\n"),
            "report": artifact(root, f"flow/{name}.rpt", f"setup={setup}\n"),
        })
    rows = []
    for offset, operating_point in enumerate(("sparse", "near_saturation", "loss")):
        cycles = 100
        delivered = 20 + offset * 10
        row = {
            "candidate": {
                "id": "fixture-candidate",
                "commit_sha": "1" * 40,
                "bundle": common["bundle"],
            },
            "cohort": {
                "boundary_scope": "full_endpoint",
                "power_mode": "activity_annotated",
            },
            "boundary": {
                "synthesis_top": scope_root,
                "scope_root": scope_root,
                "scope_manifest": scope_ref,
                "scope_sha256": scope_sha,
                "pin_inventory": pins_ref,
                "functional_pin_bits": 25,
                "includes_tx": True,
                "includes_link": True,
                "includes_rx": True,
            },
            "flow": {
                "analysis_class": "per_target_resynthesis",
                "flow_config": common["flow_config"],
                "sdc": common["sdc"],
                "library": common["library"],
                "corner": "slow_0p9v_125c_rcworst",
                "clock_port": "clk",
                "clock_period_ns": 5.0,
                "area_um2": 1234.5,
                "timing_points": timing_points,
            },
            "activity": {
                "format": fmt,
                "waveform": waveform,
                "scope_root": scope_root,
                "scope_sha256": scope_sha,
                "window_start_cycle": offset * 100,
                "window_end_cycle_exclusive": offset * 100 + cycles,
                "measurement_cycles": cycles,
                "window_sha256": "0" * 64,
                "coverage": {
                    "annotated_object_bits": 49,
                    "eligible_object_bits": 49,
                    "percent": 100.0,
                },
                "power_report": {},
                "internal_power_mw": 0.6,
                "switching_power_mw": 1.2,
                "leakage_power_mw": 0.2,
                "total_power_mw": 2.0,
            },
            "vectorless_power": None,
            "workload": {
                "id": "neutrality-n16-v4",
                "test_id": f"fixture-{operating_point}",
                "manifest": common["manifest"],
                "trace": common["trace"],
                "seed": 7,
                "operating_point": operating_point,
                "warmup_cycles": 10,
                "drain_policy": "drain_accepted_then_8_cycle_quiet_guard",
                "common_result": {},
                "event_denominator": {
                    "kind": "delivered_logical_events_in_exact_window",
                    "count": delivered,
                    "measurement_cycles": cycles,
                    "window_sha256": "0" * 64,
                },
                "conservation": {
                    "generated": delivered,
                    "source_overrun": 0,
                    "accepted": delivered,
                    "delivered": delivered,
                    "loss": 0,
                    "duplicate": 0,
                    "corrupt": 0,
                    "phantom": 0,
                    "late_after_drain": 0,
                },
            },
        }
        rebind(root, row, f"row-{offset}")
        rows.append(row)
    return {
        "schema_version": 1,
        "comparison_id": "w2-synthetic-self-test",
        "evidence_origin": evaluator.TEST_ORIGIN,
        "source_archive": common["archive"],
        "rows": rows,
    }


def rebind(root, row, stem):
    window_sha = evaluator.canonical_sha256(evaluator.window_binding(row))
    row["activity"]["window_sha256"] = window_sha
    row["workload"]["event_denominator"]["window_sha256"] = window_sha
    common = {
        "schema_version": 1,
        "candidate_id": row["candidate"]["id"],
        "workload_id": row["workload"]["id"],
        "test_id": row["workload"]["test_id"],
        "seed": row["workload"]["seed"],
        "trace_sha256": row["workload"]["trace"]["sha256"],
        "window_sha256": window_sha,
        "measurement_cycles": row["activity"]["measurement_cycles"],
        "event_denominator": row["workload"]["event_denominator"],
        "conservation": row["workload"]["conservation"],
    }
    row["workload"]["common_result"] = json_artifact(
        root, f"evidence/{stem}-common.json", common
    )
    activity = row["activity"]
    power = {
        "schema_version": 1,
        "candidate_id": row["candidate"]["id"],
        "format": activity["format"],
        "waveform_sha256": activity["waveform"]["sha256"],
        "scope_sha256": activity["scope_sha256"],
        "window_sha256": window_sha,
        "netlist_sha256": row["flow"]["timing_points"][0]["netlist"]["sha256"],
        "library_sha256": row["flow"]["library"]["sha256"],
        "internal_power_mw": activity["internal_power_mw"],
        "switching_power_mw": activity["switching_power_mw"],
        "leakage_power_mw": activity["leakage_power_mw"],
        "total_power_mw": activity["total_power_mw"],
    }
    row["activity"]["power_report"] = json_artifact(
        root, f"evidence/{stem}-power.json", power
    )


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


class ActivityPowerPpaTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.record = make_record(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def evaluate(self, record=None):
        return evaluator.evaluate(record or self.record, self.root)

    def test_schema_is_closed_machine_readable_v1(self):
        schema = json.loads((ROOT / "activity_power_ppa_comparison.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        record = copy.deepcopy(self.record)
        record["rows"][0]["unbound_claim"] = "candidate release"
        with self.assertRaisesRegex(evaluator.ComparisonError, "additional property"):
            self.evaluate(record)

    def test_complete_synthetic_fixture_computes_metrics_but_never_candidate_go(self):
        record = copy.deepcopy(self.record)
        beta_bundle = artifact(self.root, "candidate/beta-bundle.tar", "beta fixture\n")
        beta_rows = copy.deepcopy(record["rows"])
        for index, row in enumerate(beta_rows):
            row["candidate"] = {
                "id": "fixture-candidate-beta",
                "commit_sha": "2" * 40,
                "bundle": beta_bundle,
            }
            rebind(self.root, row, f"beta-{index}")
        record["rows"].extend(beta_rows)
        result = self.evaluate(record)
        self.assertEqual(result["publication_status"], "TEST_ONLY")
        self.assertTrue(all(not item["candidate_go"] for item in result["candidate_results"]))
        self.assertTrue(all(item["decision"] == "TEST_ONLY" for item in result["candidate_results"]))
        self.assertTrue(all(item["eligible_cohort_ids"] for item in result["candidate_results"]))
        self.assertFalse(any("GO" in value for value in strings(result)))
        rows = result["cohorts"][0]["rows"]
        self.assertEqual({row["operating_point"] for row in rows}, evaluator.REQUIRED_OPERATING_POINTS)
        self.assertAlmostEqual(rows[0]["metrics"]["fmax_lower_mhz"], 200.0)
        self.assertAlmostEqual(rows[0]["metrics"]["fmax_upper_mhz"], 250.0)
        self.assertEqual(rows[0]["metrics"]["functional_pin_bits"], 25)

    def test_vcd_and_saif_are_the_only_bound_activity_formats(self):
        self.evaluate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = make_record(root, "saif")
            self.assertEqual(evaluator.evaluate(record, root)["publication_status"], "TEST_ONLY")
        record = copy.deepcopy(self.record)
        record["rows"][0]["activity"]["format"] = "fsdb"
        with self.assertRaisesRegex(evaluator.ComparisonError, "allowed values"):
            self.evaluate(record)

    def test_waveform_digest_and_completeness_are_fail_closed(self):
        record = copy.deepcopy(self.record)
        record["rows"][0]["activity"]["waveform"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(evaluator.ComparisonError, "digest mismatch"):
            self.evaluate(record)
        record = copy.deepcopy(self.record)
        bad = artifact(self.root, "activity/truncated.vcd", "$scope x $end\n")
        record["rows"][0]["activity"]["waveform"] = bad
        rebind(self.root, record["rows"][0], "bad-wave")
        with self.assertRaisesRegex(evaluator.ComparisonError, "canonical VCD"):
            self.evaluate(record)

    def test_scope_manifest_hash_and_root_are_bound(self):
        for field, value in (("scope_sha256", "0" * 64), ("scope_root", "tb")):
            record = copy.deepcopy(self.record)
            record["rows"][0]["activity"][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(evaluator.ComparisonError, "scope"):
                    self.evaluate(record)

    def test_window_coverage_and_event_denominator_are_bound(self):
        record = copy.deepcopy(self.record)
        record["rows"][0]["activity"]["window_end_cycle_exclusive"] += 1
        with self.assertRaisesRegex(evaluator.ComparisonError, "measurement_cycles"):
            self.evaluate(record)
        record = copy.deepcopy(self.record)
        record["rows"][0]["activity"]["coverage"]["percent"] = 99.0
        with self.assertRaisesRegex(evaluator.ComparisonError, "numerator/denominator"):
            self.evaluate(record)
        record = copy.deepcopy(self.record)
        record["rows"][0]["activity"]["coverage"].update(
            {"annotated_object_bits": 46, "eligible_object_bits": 49, "percent": 100 * 46 / 49}
        )
        with self.assertRaisesRegex(evaluator.ComparisonError, "trusted 95% threshold"):
            self.evaluate(record)
        record = copy.deepcopy(self.record)
        record["rows"][0]["workload"]["event_denominator"]["count"] += 1
        with self.assertRaisesRegex(evaluator.ComparisonError, "count != delivered"):
            self.evaluate(record)

    def test_loss_duplicate_and_conservation_fail_closed(self):
        mutations = (
            ("generated", 21, "generated !="),
            ("loss", 1, "accepted !="),
            ("duplicate", 1, None),
        )
        for field, value, immediate in mutations:
            record = copy.deepcopy(self.record)
            row = record["rows"][0]
            row["workload"]["conservation"][field] = value
            rebind(self.root, row, f"mutation-{field}")
            with self.subTest(field=field):
                if immediate:
                    with self.assertRaisesRegex(evaluator.ComparisonError, immediate):
                        self.evaluate(record)
                else:
                    result = self.evaluate(record)
                    self.assertFalse(result["cohorts"][0]["rows"][0]["metrics"]["conservation_clean"])
                    self.assertEqual(result["candidate_results"][0]["eligible_cohort_ids"], [])

    def test_pin_inventory_is_recomputed(self):
        record = copy.deepcopy(self.record)
        record["rows"][0]["boundary"]["functional_pin_bits"] = 24
        with self.assertRaisesRegex(evaluator.ComparisonError, "does not match pin inventory"):
            self.evaluate(record)

    def test_vectorless_core_only_and_full_endpoint_are_separate_cohorts(self):
        record = copy.deepcopy(self.record)
        vectorless = copy.deepcopy(record["rows"][0])
        vectorless["cohort"]["power_mode"] = "vectorless_screening"
        vectorless["flow"]["analysis_class"] = "vectorless_screening"
        vectorless["activity"] = None
        vectorless["workload"] = None
        vectorless["vectorless_power"] = {
            "power_report": {},
            "internal_power_mw": 0.6,
            "switching_power_mw": 1.2,
            "leakage_power_mw": 0.2,
            "total_power_mw": 2.0,
        }
        vectorless["vectorless_power"]["power_report"] = json_artifact(
            self.root, "evidence/vectorless-power.json", {
                "schema_version": 1,
                "candidate_id": vectorless["candidate"]["id"],
                "netlist_sha256": vectorless["flow"]["timing_points"][0]["netlist"]["sha256"],
                "library_sha256": vectorless["flow"]["library"]["sha256"],
                "internal_power_mw": 0.6,
                "switching_power_mw": 1.2,
                "leakage_power_mw": 0.2,
                "total_power_mw": 2.0,
            }
        )
        core = copy.deepcopy(record["rows"][0])
        core["cohort"]["boundary_scope"] = "core_only"
        core["boundary"]["includes_link"] = False
        core["workload"]["test_id"] = "fixture-core"
        rebind(self.root, core, "core-only")
        record["rows"].extend((vectorless, core))
        result = self.evaluate(record)
        self.assertEqual(len(result["cohorts"]), 3)
        self.assertEqual(
            {(item["boundary_scope"], item["power_mode"]) for item in result["cohorts"]},
            {
                ("full_endpoint", "activity_annotated"),
                ("full_endpoint", "vectorless_screening"),
                ("core_only", "activity_annotated"),
            },
        )
        vector_row = next(
            row for cohort in result["cohorts"] for row in cohort["rows"]
            if row["power_mode"] == "vectorless_screening"
        )
        self.assertEqual(vector_row["metrics"]["total_power_mw"], 2.0)
        self.assertIsNone(vector_row["metrics"]["energy_nj_per_event"])

    def test_fixed_netlist_and_nonmonotonic_sweeps_cannot_release(self):
        record = copy.deepcopy(self.record)
        for row in record["rows"]:
            row["flow"]["analysis_class"] = "fixed_netlist_diagnostic"
        result = self.evaluate(record)
        self.assertEqual(result["candidate_results"][0]["eligible_cohort_ids"], [])
        record = copy.deepcopy(self.record)
        points = record["rows"][0]["flow"]["timing_points"]
        points[0]["setup_wns_ns"] = -0.1
        points[1]["setup_wns_ns"] = 0.1
        with self.assertRaisesRegex(evaluator.ComparisonError, "non-monotonic"):
            self.evaluate(record)

    def test_measured_origin_requires_out_of_band_registry(self):
        record = copy.deepcopy(self.record)
        record["evidence_origin"] = "measured_candidate"
        with self.assertRaisesRegex(evaluator.ComparisonError, "out-of-band production registry"):
            self.evaluate(record)

    def test_caller_registry_cannot_promote_relabelled_synthetic_rows(self):
        record = copy.deepcopy(self.record)
        beta_bundle = artifact(self.root, "candidate/beta-measured.tar", "beta measured\n")
        beta_rows = copy.deepcopy(record["rows"])
        for index, row in enumerate(beta_rows):
            row["candidate"] = {
                "id": "fixture-candidate-beta",
                "commit_sha": "2" * 40,
                "bundle": beta_bundle,
            }
            rebind(self.root, row, f"measured-beta-{index}")
        record["rows"].extend(beta_rows)
        record["evidence_origin"] = "measured_candidate"
        registry = {
            "schema_version": 1,
            "minimum_coverage_percent": 95.0,
            "candidates": [
                {
                    "id": row["candidate"]["id"],
                    "commit_sha": row["candidate"]["commit_sha"],
                    "bundle_sha256": row["candidate"]["bundle"]["sha256"],
                }
                for row in (record["rows"][0], beta_rows[0])
            ],
            "workloads": [{
                "id": record["rows"][0]["workload"]["id"],
                "manifest_sha256": record["rows"][0]["workload"]["manifest"]["sha256"],
            }],
        }
        result = evaluator.evaluate(record, self.root, registry)
        self.assertEqual(result["publication_status"], "HOLD_UNAUTHENTICATED")
        self.assertTrue(all(not row["candidate_go"] for row in result["candidate_results"]))
        self.assertTrue(all(row["decision"] == "HOLD_UNAUTHENTICATED" for row in result["candidate_results"]))

    def test_cross_candidate_trace_mismatch_cannot_form_release_cohort(self):
        record = copy.deepcopy(self.record)
        beta_bundle = artifact(self.root, "candidate/beta-mismatch.tar", "beta mismatch\n")
        beta_trace = artifact(self.root, "workload/beta-trace.jsonl", "different trace\n")
        beta_rows = copy.deepcopy(record["rows"])
        for index, row in enumerate(beta_rows):
            row["candidate"] = {
                "id": "fixture-candidate-beta",
                "commit_sha": "2" * 40,
                "bundle": beta_bundle,
            }
            row["workload"]["trace"] = beta_trace
            rebind(self.root, row, f"mismatch-beta-{index}")
        record["rows"].extend(beta_rows)
        result = self.evaluate(record)
        self.assertTrue(all(
            item["eligible_cohort_ids"] == []
            for item in result["candidate_results"]
        ))


if __name__ == "__main__":
    unittest.main()
