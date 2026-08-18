from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks/redred_single_edge_campaign"
TOOL = PACKAGE / "campaign.py"
MANIFEST = PACKAGE / "campaign.json"
SCHEMA = PACKAGE / "replay_receipt.schema.json"
FULL50_MANIFEST = ROOT / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
REGISTRY = ROOT / "scripts/common_suite_official.py"
GENERATOR = ROOT / "benchmarks/clean_slate_aer/generate_trace.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


campaign = load_module("redred_single_edge_campaign", TOOL)
generator = load_module("redred_single_edge_generator", GENERATOR)
registry = load_module("redred_single_edge_registry", REGISTRY)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": digest(path),
        "size_bytes": path.stat().st_size,
    }


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json(path: Path, value: object) -> None:
    write_bytes(path, campaign.canonical(value))


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.root = base / "artifacts"
        self.root.mkdir()
        manifest_copy = self.root / "inputs/full50.manifest.json"
        registry_copy = self.root / "inputs/full50.trace_registry.py"
        write_bytes(manifest_copy, FULL50_MANIFEST.read_bytes())
        write_bytes(registry_copy, REGISTRY.read_bytes())
        generated = self.root / "inputs/traces"
        metadata = generator.generate_manifest(FULL50_MANIFEST, generated)
        windows = {
            row["name"]: row["stim_cycles"]
            for row in json.loads(FULL50_MANIFEST.read_text())["runs"]
        }
        common_files = {}
        for name, payload in (
            ("tool", b"fixture-single-edge-simulator-binary\n"),
            ("testbench", b"module a23_full_single_edge_fixture_tb; endmodule\n"),
            ("runner", b"#!/bin/sh\n# independent single-edge fixture runner\n"),
        ):
            path = self.root / f"common/{name}.artifact"
            write_bytes(path, payload)
            common_files[name] = artifact(self.root, path)
        common = {
            "cycle_semantics": campaign.EXPECTED_CYCLE_SEMANTICS,
            "tool": common_files["tool"], "tool_version": "fixture-1.0",
            "testbench": common_files["testbench"], "runner": common_files["runner"],
        }
        dataset_runs = []
        trace_events: dict[str, list[dict]] = {}
        for row in metadata:
            name = row["run"]["name"]
            trace = generated / row["trace_file"]
            prepared = self.root / f"inputs/prepared/{name}.prepared"
            write_bytes(prepared, f"prepared-single-edge-input:{name}\n".encode())
            trace_events[name] = [json.loads(line) for line in trace.read_text().splitlines()]
            dataset_runs.append({
                "name": name, "trace_sha256": row["trace_sha256"],
                "fixed_window_cycles": windows[name], "window_start_cycle": 0,
                "window_end_cycle_exclusive": windows[name],
                "trace": artifact(self.root, trace),
                "prepared_input": artifact(self.root, prepared),
            })
        candidates = {}
        for candidate, internal_latency in (("A2", 2), ("A3", 1)):
            inventory_sources = []
            for logical_path in campaign.EXPECTED_RTL_SOURCES[candidate]:
                rtl_source = self.root / f"candidates/{candidate}/rtl_snapshot/{logical_path}"
                rtl_bytes = subprocess.run(
                    ["git", "-C", str(ROOT), "show",
                     f"{campaign.EXPECTED_RTL_COMMIT}:{logical_path}"],
                    stdout=subprocess.PIPE, check=True,
                ).stdout
                write_bytes(rtl_source, rtl_bytes)
                inventory_sources.append({
                    "logical_path": logical_path,
                    "artifact": artifact(self.root, rtl_source),
                })
            inventory_path = self.root / f"candidates/{candidate}/rtl_inventory.json"
            write_json(inventory_path, {
                "schema": "redred_single_edge_rtl_inventory_v1",
                "candidate_id": candidate, "interface": "single_edge",
                "sources": inventory_sources,
            })
            candidate_runs = []
            for binding in dataset_runs:
                name = binding["name"]
                accepted_order = 0
                occurrence_latencies = []
                internal_latencies = []
                events = []
                fixed_retired = 0
                overruns = 0
                for source_event in trace_events[name]:
                    event_id = source_event["tb_only_event_id"]
                    source = source_event["logical_source"]
                    occurrence = source_event["occurrence_cycle"]
                    if event_id % 17 == 0:
                        overruns += 1
                        events.append({
                            "tb_only_event_id": event_id, "logical_source": source,
                            "occurrence_cycle": occurrence, "accept_cycle": None,
                            "retire_cycle": None, "retired_logical_source": None,
                            "accept_order": None, "retire_order": None,
                            "event_state": "source_overrun",
                        })
                    else:
                        accept_cycle = occurrence + 1
                        retire_cycle = accept_cycle + internal_latency
                        occurrence_latencies.append(1)
                        internal_latencies.append(internal_latency)
                        if retire_cycle < binding["window_end_cycle_exclusive"]:
                            fixed_retired += 1
                        events.append({
                            "tb_only_event_id": event_id, "logical_source": source,
                            "occurrence_cycle": occurrence, "accept_cycle": accept_cycle,
                            "retire_cycle": retire_cycle, "retired_logical_source": source,
                            "accept_order": accepted_order, "retire_order": accepted_order,
                            "event_state": "retired",
                        })
                        accepted_order += 1
                case = self.root / f"candidates/{candidate}/runs/{name}"
                events_path = case / "events.jsonl"
                write_bytes(
                    events_path,
                    b"".join(
                        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                        for row in events
                    ),
                )
                summary_path = case / "summary.json"
                write_json(summary_path, {
                    "schema": "a23_full_single_edge_run_summary_v1",
                    "candidate_id": candidate, "trace": name,
                    "generated": len(events), "source_overrun": overruns,
                    "accepted": accepted_order, "retired": accepted_order,
                    "fixed_window_retired": fixed_retired,
                    "fixed_window_cycles": binding["fixed_window_cycles"],
                    "occurrence_to_accept": campaign.latency_summary(occurrence_latencies),
                    "accept_to_retire": campaign.latency_summary(internal_latencies),
                    "hard_errors": {key: 0 for key in campaign.EXPECTED_HARD_ERRORS},
                })
                log_path = case / "simulator.log"
                write_bytes(
                    log_path,
                    f"SINGLE_EDGE_REPLAY_PASS candidate={candidate} trace={name}\n".encode(),
                )
                candidate_runs.append({
                    "name": name, "trace_sha256": binding["trace_sha256"],
                    "prepared_input_sha256": binding["prepared_input"]["sha256"],
                    "fixed_window_cycles": binding["fixed_window_cycles"],
                    "window_start_cycle": binding["window_start_cycle"],
                    "window_end_cycle_exclusive": binding["window_end_cycle_exclusive"],
                    "events": artifact(self.root, events_path),
                    "summary": artifact(self.root, summary_path),
                    "simulator_log": artifact(self.root, log_path),
                })
            candidates[candidate] = {
                "candidate_id": candidate,
                "semantic_role": campaign.EXPECTED_ROLES[candidate],
                "endpoint_id": campaign.EXPECTED_ENDPOINTS[candidate],
                "common_binding_sha256": campaign.object_sha256(common),
                "rtl_inventory": artifact(self.root, inventory_path),
                "runs": candidate_runs,
            }
        self.receipt = {
            "schema": campaign.EXPECTED_RECEIPT_SCHEMA, "status": "PASS",
            "evidence_class": campaign.EXPECTED_EVIDENCE_CLASS,
            "campaign_id": campaign.EXPECTED_CAMPAIGN_ID,
            "producer": {
                "id": campaign.EXPECTED_PRODUCER_ID,
                "path": campaign.EXPECTED_PRODUCER_PATH,
                "evidence_class": campaign.EXPECTED_EVIDENCE_CLASS,
                "rtl_source_commit": campaign.EXPECTED_RTL_COMMIT,
            },
            "interface": {
                "id": "single_edge", "clock_edge": "posedge_only",
                "transport": "one_retirement_per_rising_edge",
                "p6_used": False, "parallel_used": False,
                "boundary": "synchronous_source_admission_through_synchronous_retirement",
                "acceptance_observation": "actual_atomic_scheduler_commit",
                "retirement_observation": "actual_single_edge_receiver_retire_valid_and_address",
            },
            "dataset": {
                "id": "full50", "display_name": "team-defined synthetic full50",
                "source_class": "TEAM_DEFINED_SYNTHETIC", "organizer_official": False,
                "run_count": 50, "manifest": artifact(self.root, manifest_copy),
                "trace_registry": artifact(self.root, registry_copy), "runs": dataset_runs,
            },
            "common_binding": common,
            "evidence_lineage": {
                "replay_kind": "A23_FULL_SINGLE_EDGE_REPLAY_ACTUAL_RTL",
                "independent_execution": True,
                "borrowed_p6_results": False, "borrowed_parallel_results": False,
                "source_result_paths": [],
            },
            "candidates": candidates,
        }
        self.receipt_path = self.base / "receipt.json"
        self.write_receipt()

    def write_receipt(self) -> None:
        write_json(self.receipt_path, self.receipt)

    def evaluate(self) -> dict:
        self.write_receipt()
        return campaign.evaluate(
            MANIFEST, ROOT, SCHEMA, digest(SCHEMA), self.receipt_path,
            digest(self.receipt_path), self.root,
        )


class SingleEdgeCampaignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="redred-single-edge-campaign.")
        cls.fixture = Fixture(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.saved_receipt = copy.deepcopy(self.fixture.receipt)

    def tearDown(self) -> None:
        self.fixture.receipt = self.saved_receipt

    def test_committed_state_is_hold_without_replay_inputs(self) -> None:
        report = campaign.evaluate(MANIFEST, ROOT, None, None, None, None, None)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["single_edge_digital_gate"], "HOLD_NO_ACTUAL_REPLAY_ARTIFACTS")
        self.assertFalse(report["dataset"]["organizer_official"])
        self.assertEqual(report["dataset"]["display_name"], "team-defined synthetic full50")
        self.assertEqual(report["evidence_lineage"], {
            "p6_results": "FORBIDDEN", "parallel_results": "FORBIDDEN",
        })

    def test_cli_hold_exit_and_allow_hold_do_not_change_status(self) -> None:
        command = ["python3", str(TOOL), "evaluate"]
        held = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        allowed = subprocess.run(command + ["--allow-hold"], cwd=ROOT, text=True,
                                 capture_output=True, check=False)
        self.assertEqual(held.returncode, 3)
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(json.loads(held.stdout)["status"], "HOLD")
        self.assertEqual(json.loads(allowed.stdout)["status"], "HOLD")

    def test_partial_explicit_input_tuple_fails_closed(self) -> None:
        with self.assertRaisesRegex(campaign.CampaignError, "must be supplied together"):
            campaign.evaluate(MANIFEST, ROOT, SCHEMA, digest(SCHEMA), None, None, None)

    def test_exact_producer_format_fixture_recomputes_counts_and_both_latency_classes(self) -> None:
        report = self.fixture.evaluate()
        self.assertEqual(report["status"], "EVIDENCE_COMPLETE")
        self.assertEqual(report["single_edge_digital_gate"], "GO")
        self.assertEqual(report["system_release"], "HOLD_OUTSIDE_DIGITAL_CAMPAIGN_SCOPE")
        self.assertGreater(report["verified_artifact_count"], 250)
        a2 = report["candidates"]["A2"]
        a3 = report["candidates"]["A3"]
        self.assertEqual(a2["totals"]["generated"], a3["totals"]["generated"])
        self.assertEqual(
            a2["totals"]["generated"],
            a2["totals"]["source_overrun"] + a2["totals"]["accepted"],
        )
        self.assertEqual(a2["totals"]["accepted"], a2["totals"]["retired"])
        self.assertEqual(a2["occurrence_to_accept"]["mean"], 1.0)
        self.assertEqual(a2["accept_to_retire"]["mean"], 2.0)
        self.assertEqual(a3["accept_to_retire"]["mean"], 1.0)
        self.assertEqual(a2["common_binding_sha256"], a3["common_binding_sha256"])

    def test_schema_and_receipt_hashes_are_caller_immutable(self) -> None:
        with self.assertRaisesRegex(campaign.CampaignError, "schema SHA-256 mismatch"):
            campaign.evaluate(
                MANIFEST, ROOT, SCHEMA, "0" * 64, self.fixture.receipt_path,
                digest(self.fixture.receipt_path), self.fixture.root,
            )
        with self.assertRaisesRegex(campaign.CampaignError, "receipt SHA-256 mismatch"):
            campaign.evaluate(
                MANIFEST, ROOT, SCHEMA, digest(SCHEMA), self.fixture.receipt_path,
                "0" * 64, self.fixture.root,
            )

    def test_unknown_field_bool_counter_and_candidate_expansion_fail(self) -> None:
        cases = [
            (lambda d: d.update({"unknown": 1}), "replay receipt keys differ"),
            (lambda d: d.__setitem__("evidence_class", "TEST_ONLY_FIXTURE"),
             "evidence_class differs"),
            (lambda d: d["producer"].__setitem__("rtl_source_commit", "0" * 40),
             "exact A23 producer/RTL commit"),
            (lambda d: d["dataset"].__setitem__("run_count", True), "bool is forbidden"),
            (lambda d: d["candidates"].update({"A4": copy.deepcopy(d["candidates"]["A3"])}),
             "exactly ordered A2,A3"),
        ]
        for mutate, pattern in cases:
            self.fixture.receipt = copy.deepcopy(self.saved_receipt)
            mutate(self.fixture.receipt)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(campaign.CampaignError, pattern):
                self.fixture.evaluate()

    def test_full50_cannot_be_relabelled_as_official_or_supplied(self) -> None:
        for key, value in (("organizer_official", True), ("source_class", "ORGANIZER_SUPPLIED")):
            self.fixture.receipt = copy.deepcopy(self.saved_receipt)
            self.fixture.receipt["dataset"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                    campaign.CampaignError, "relabeled.*official"):
                self.fixture.evaluate()

    def test_p6_and_parallel_borrowing_are_fatal(self) -> None:
        cases = [
            (lambda d: d["interface"].__setitem__("p6_used", True), "single-edge boundary"),
            (lambda d: d["interface"].__setitem__("parallel_used", True), "single-edge boundary"),
            (lambda d: d["evidence_lineage"].__setitem__("borrowed_p6_results", True),
             "borrows or aliases"),
            (lambda d: d["evidence_lineage"].__setitem__("source_result_paths", [
                "tests/a23_full_p6_replay/result.json"]), "borrows or aliases"),
        ]
        for mutate, pattern in cases:
            self.fixture.receipt = copy.deepcopy(self.saved_receipt)
            mutate(self.fixture.receipt)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(campaign.CampaignError, pattern):
                self.fixture.evaluate()

    def test_a2_a3_trace_prepared_window_and_common_tool_must_match(self) -> None:
        cases = [
            ("trace_sha256", "0" * 64, "common A2/A3 trace/window binding"),
            ("prepared_input_sha256", "0" * 64, "common A2/A3 trace/window binding"),
            ("fixed_window_cycles", 511, "common A2/A3 trace/window binding"),
        ]
        for key, value, pattern in cases:
            self.fixture.receipt = copy.deepcopy(self.saved_receipt)
            self.fixture.receipt["candidates"]["A2"]["runs"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(campaign.CampaignError, pattern):
                self.fixture.evaluate()
        self.fixture.receipt = copy.deepcopy(self.saved_receipt)
        self.fixture.receipt["candidates"]["A2"]["common_binding_sha256"] = "0" * 64
        with self.assertRaisesRegex(campaign.CampaignError, "common tool-TB binding differs"):
            self.fixture.evaluate()

    def test_missing_symlinked_and_tampered_artifacts_fail_closed(self) -> None:
        run = self.fixture.receipt["candidates"]["A2"]["runs"][0]
        path = self.fixture.root / run["simulator_log"]["path"]
        original = path.read_bytes()
        try:
            path.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(campaign.CampaignError, "size mismatch|SHA-256 mismatch"):
                self.fixture.evaluate()
        finally:
            path.write_bytes(original)
        self.fixture.receipt = copy.deepcopy(self.saved_receipt)
        self.fixture.receipt["candidates"]["A2"]["runs"][0]["events"]["path"] = "absent/events.jsonl"
        with self.assertRaisesRegex(campaign.CampaignError, "escapes or is absent"):
            self.fixture.evaluate()
        self.fixture.receipt = copy.deepcopy(self.saved_receipt)
        link = self.fixture.root / "linked-case"
        link.symlink_to(path.parent, target_is_directory=True)
        try:
            linked_ref = copy.deepcopy(
                self.fixture.receipt["candidates"]["A2"]["runs"][0]["simulator_log"]
            )
            linked_ref["path"] = "linked-case/simulator.log"
            self.fixture.receipt["candidates"]["A2"]["runs"][0]["simulator_log"] = linked_ref
            with self.assertRaisesRegex(campaign.CampaignError, "traverses a symlink"):
                self.fixture.evaluate()
        finally:
            link.unlink()

    def test_event_state_identity_order_and_latency_tampering_fail(self) -> None:
        run = self.fixture.receipt["candidates"]["A2"]["runs"][0]
        events_path = self.fixture.root / run["events"]["path"]
        original = events_path.read_bytes()
        rows = [json.loads(line) for line in original.decode().splitlines()]
        retired = next(row for row in rows if row["event_state"] == "retired")
        retired["retire_order"] += 1
        write_bytes(
            events_path,
            b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                     for row in rows),
        )
        run["events"] = artifact(self.fixture.root, events_path)
        try:
            with self.assertRaisesRegex(campaign.CampaignError, "identity, order, or cycles differ"):
                self.fixture.evaluate()
        finally:
            events_path.write_bytes(original)

    def test_summary_conservation_latency_and_hard_errors_are_recomputed(self) -> None:
        base_run = self.fixture.receipt["candidates"]["A2"]["runs"][0]
        summary_path = self.fixture.root / base_run["summary"]["path"]
        original = summary_path.read_bytes()
        cases = [
            (lambda d: d.__setitem__("accepted", d["accepted"] - 1), "summary accepted differs"),
            (lambda d: d["occurrence_to_accept"].__setitem__("mean", 99.0),
             "differs from recomputed event latency"),
            (lambda d: d["hard_errors"].__setitem__("phantom", 1), "hard error phantom is nonzero"),
        ]
        for mutate, pattern in cases:
            self.fixture.receipt = copy.deepcopy(self.saved_receipt)
            summary = json.loads(original)
            mutate(summary)
            write_json(summary_path, summary)
            self.fixture.receipt["candidates"]["A2"]["runs"][0]["summary"] = artifact(
                self.fixture.root, summary_path
            )
            with self.subTest(pattern=pattern), self.assertRaisesRegex(campaign.CampaignError, pattern):
                self.fixture.evaluate()
        summary_path.write_bytes(original)

    def test_rtl_inventory_rejects_p6_or_parallel_source_lineage(self) -> None:
        self.fixture.receipt = copy.deepcopy(self.saved_receipt)
        reference = self.fixture.receipt["candidates"]["A2"]["rtl_inventory"]
        path = self.fixture.root / reference["path"]
        original = path.read_bytes()
        inventory = json.loads(original)
        inventory["sources"][0]["logical_path"] = "rtl/a2_p6_parallel_top.sv"
        write_json(path, inventory)
        self.fixture.receipt["candidates"]["A2"]["rtl_inventory"] = artifact(self.fixture.root, path)
        try:
            with self.assertRaisesRegex(campaign.CampaignError, "forbidden lineage"):
                self.fixture.evaluate()
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
