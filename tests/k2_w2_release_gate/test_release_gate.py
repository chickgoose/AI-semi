from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "physical/k2_w2_release_gate/release_gate.py"
SPEC = importlib.util.spec_from_file_location("k2_w2_release_gate", GATE_PATH)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def token(label: str) -> str:
    return digest(label.encode("ascii"))


def suite_workloads(suite: str) -> list[str]:
    capacity = ["pairwise_contention", "mixed_phase_always_ready", "phase_transition"]
    capacity += ["uniform"] * (22 - len(capacity))
    if suite == "capacity22":
        return capacity
    return capacity + ["timing_pair"] + ["uniform"] * 27


def trace_index(suite: str) -> list[dict[str, str]]:
    return [
        {"name": f"{workload}_{index}", "workload": workload,
         "trace_sha256": token(f"official-{index}-trace")}
        for index, workload in enumerate(suite_workloads(suite))
    ]


class ReleaseBundle:
    KEY_ID = "w2-test-release-key"
    SECRET = bytes.fromhex("42" * 32)

    def __init__(self, root: Path):
        self.root = root
        self.release_id = "k2-final-ranking-20260813"
        self.campaign = {
            "campaign_id": "k2-w2-production-20260813",
            "generation": 7,
            "nonce": "ab" * 32,
            "cohort_id": GATE.EXPECTED_COHORT,
            "candidate_ids": list(GATE.EXPECTED_CANDIDATES),
            "candidate_commits": {
                "fovea_a7": "77" * 20,
                "a2_p6": "22" * 20,
                "a3_p6": "33" * 20,
            },
            "provenance": {
                "server_environment": {
                    "environment_id": "server-210.126.11.79-20260813",
                    "contract_sha256": token("server-contract"),
                },
                "technology": {
                    "setup_liberty_sha256": token("slow-liberty"),
                    "hold_liberty_sha256": token("fast-liberty"),
                    "tech_lef_sha256": token("tech-lef"),
                    "cell_lef_sha256": token("cell-lef"),
                    "shared_qrc_sha256": token("gpdk045-qrc"),
                },
                "pvt": {
                    "setup": {
                        "process": "slow", "voltage_v": "0.9", "temperature_c": "125",
                        "operating_condition": "slow_vdd1v0_125c",
                    },
                    "hold": {
                        "process": "fast", "voltage_v": "1.1", "temperature_c": "-40",
                        "operating_condition": "fast_vdd1v0_m40c",
                    },
                    "shared_rc_corner": "gpdk045_typical_shared",
                },
                "sdc": {
                    "constraint_set_id": "k2_w2_multiclock_full_link",
                    "sha256": token("multiclock-sdc"),
                    "clock_schema": "k2_w2_multiclock_full_link_v6",
                    "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS),
                },
                "load": {
                    "model_id": "identical_external_link_load_v1",
                    "sha256": token("load-model"), "output_load_pf": "0.010",
                },
                "staged_manifest": {
                    "schema": "k2_w2_tech_staged_compositions_v1",
                    "sha256": token("staged-manifest"),
                    "repository_commit": "13" * 20,
                    "normalized_boundary_sha256": token("normalized-non-link-boundary"),
                    "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS),
                    "functional_candidate_manifest_sha256": {
                        candidate: token(candidate + "-candidate-manifest")
                        for candidate in GATE.EXPECTED_CANDIDATES
                    },
                },
                "workload": {
                    "suite_id": "aer-clean-v4-full50-cap22",
                    "generator_version": 4, "full_run_count": 50,
                    "capacity_run_count": 22,
                    "full_manifest_sha256": token("full50-manifest"),
                    "capacity_manifest_sha256": token("capacity22-manifest"),
                    "trace_bundle_sha256": token("trace-bundle"),
                    "full_trace_index_sha256": digest(canonical(trace_index("full50"))),
                    "capacity_trace_index_sha256": digest(canonical(trace_index("capacity22"))),
                    "simulator": {
                        "identity": "verilator",
                        "executable_sha256": token("verilator-executable"),
                        "version_sha256": token("verilator-version"),
                    },
                    "tool_bundles": {
                        "runner": {
                            candidate: {"identity": f"{candidate}_official_runner",
                                        "bundle_sha256": token(candidate + "-runner")}
                            for candidate in GATE.EXPECTED_CANDIDATES
                        },
                        "generator": {"identity": "generator_v4",
                                      "bundle_sha256": token("generator-v4")},
                        "analyzers": {
                            name: {"identity": name,
                                   "bundle_sha256": token(name + "-analyzer-tool")}
                            for name in GATE.COMMON_ANALYZER_WORKLOADS
                        },
                    },
                },
            },
        }
        self.receipts: dict[str, dict] = {}
        self.receipt_paths: dict[str, Path] = {}
        self.point_docs: dict[tuple[str, str, str], dict] = {}
        self.point_paths: dict[tuple[str, str, str], Path] = {}
        self.functional_docs: dict[tuple[str, str], dict] = {}
        self.functional_paths: dict[tuple[str, str], Path] = {}
        self.manifest_path = root / "release-manifest.json"
        self.keyring_path = root / "release-keyring.json"
        self._build()

    def candidate_results(self) -> dict[str, dict[str, str]]:
        return {candidate: {"status": "PASS"}
                for candidate in self.campaign["candidate_ids"]}

    def base(self, role: str) -> dict:
        return {
            "schema": GATE.ROLE_SCHEMAS[role],
            "role": role,
            "receipt_id": f"{role}-receipt-generation7",
            "status": "PASS",
            "release_binding": copy.deepcopy(self.campaign),
            "candidate_results": self.candidate_results(),
        }

    def _point_base(self, candidate: str, period: str, role: str) -> dict:
        return {
            "schema": GATE.POINT_RECEIPT_SCHEMAS[role],
            "role": role,
            "receipt_id": f"{candidate}-{period.replace('.', 'p')}-{role}",
            "status": "COMPLETE",
            "release_binding": copy.deepcopy(self.campaign),
            "candidate_id": candidate,
            "top": GATE.EXPECTED_TOPS[candidate],
            "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS[candidate]),
            "period_ns": period,
        }

    def _make_point_receipts(self, candidate: str, period: str, offset: int) -> dict[str, dict]:
        prefix = f"{candidate}-{period}"
        innovus = self._point_base(candidate, period, "innovus")
        innovus.update({
            "clean_exit": True,
            "postroute_netlist_sha256": token(prefix + "-netlist"),
            "database_sha256": token(prefix + "-database"),
            "tool_log_sha256": token(prefix + "-tool-log"),
        })
        sta = self._point_base(candidate, period, "sta")
        setup_wns = {"0.8": f"-{100 + offset}", "1.0": f"{20 + offset}",
                     "1.2": f"{80 + offset}"}[period]
        setup_wns = str(int(setup_wns) / 1000)
        setup_failed = period == "0.8"
        sta["checks"] = {}
        for check in ("setup", "hold", "recovery", "removal"):
            failed = check == "setup" and setup_failed
            wns = setup_wns if check == "setup" else "0.050"
            sta["checks"][check] = {
                "report_sha256": token(prefix + "-" + check),
                "wns_ns": wns,
                "tns_ns": wns if failed else "0.000",
                "violations": 1 if failed else 0,
            }
        drc = self._point_base(candidate, period, "drc")
        drc["checks"] = {
            name: {"report_sha256": token(prefix + "-" + name), "violations": 0}
            for name in ("drc", "antenna")
        }
        connectivity = self._point_base(candidate, period, "connectivity")
        connectivity["checks"] = {
            name: {"report_sha256": token(prefix + "-" + name),
                   "opens": 0, "shorts": 0, "unconnected": 0}
            for name in ("signal", "pg")
        }
        return {"innovus": innovus, "sta": sta, "drc": drc,
                "connectivity": connectivity}

    def _write_point(self, candidate: str, period: str, role: str) -> dict[str, str]:
        key = (candidate, period, role)
        path = self.root / "receipts" / "postroute" / candidate / period / f"{role}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(self.point_docs[key]))
        self.point_paths[key] = path
        return {"path": path.relative_to(self.root).as_posix(),
                "sha256": digest(path.read_bytes())}

    def sweep(self, candidate: str, offset: int) -> dict:
        points = []
        for period in ("0.8", "1.0", "1.2"):
            documents = self._make_point_receipts(candidate, period, offset)
            refs = {}
            for role, document in documents.items():
                self.point_docs[(candidate, period, role)] = document
                refs[role] = self._write_point(candidate, period, role)
            points.append({"period_ns": period, "receipts": refs})
        return {
            "status": "MONOTONIC_QUALIFIED", "points": points,
            "qualified_bracket": {
                "last_fail_period_ns": "0.8", "first_pass_period_ns": "1.0"},
            "selected_period_ns": "1.0", "cherry_pick_forbidden": True,
        }

    def _common_receipt(self, candidate: str, suite: str) -> dict:
        count = 50 if suite == "full50" else 22
        manifest_sha = (self.campaign["provenance"]["workload"]["full_manifest_sha256"]
                        if suite == "full50" else
                        self.campaign["provenance"]["workload"]["capacity_manifest_sha256"])
        workloads = suite_workloads(suite)
        runs = []
        for index, workload in enumerate(workloads):
            prefix = f"{candidate}-{suite}-{index}"
            row = {
                "name": f"{workload}_{index}", "workload": workload,
                "run_manifest": {"sha256": token(prefix + "-manifest")},
                "trace": {"sha256": token(f"official-{index}-trace")},
                "result": {"sha256": token(prefix + "-result")},
                "execution_sidecar": {"sha256": token(prefix + "-sidecar")},
            }
            if workload in GATE.COMMON_ANALYZER_WORKLOADS:
                row["analyzer"] = {"sha256": token(prefix + "-analyzer")}
            runs.append(row)
        return {
            "receipt_schema_version": GATE.COMMON_RECEIPT_SCHEMA_VERSION,
            "status": "PASS", "suite": suite, "candidate": candidate,
            "validated_run_count": count, "generated_at_utc": "2026-08-13T00:00:00+00:00",
            "official_source_commit": GATE.COMMON_SOURCE_COMMIT,
            "attempt": {"sha256": token(candidate + suite + "-attempt")},
            "candidate_manifest_sha256": self.campaign["provenance"]["staged_manifest"][
                "functional_candidate_manifest_sha256"][candidate],
            "tools": {
                name: copy.deepcopy(
                    self.campaign["provenance"]["workload"]["tool_bundles"]["runner"][candidate]
                    if name == "runner" else
                    self.campaign["provenance"]["workload"]["tool_bundles"]["generator"]
                    if name == "generator" else
                    self.campaign["provenance"]["workload"]["tool_bundles"]["analyzers"][name])
                for name in GATE.COMMON_REQUIRED_TOOLS[suite]
            },
            "simulator": copy.deepcopy(self.campaign["provenance"]["workload"]["simulator"]),
            "execution_identity": {"sha256": token(candidate + suite + "-execution")},
            "compile_manifest": {"sha256": token(candidate + suite + "-compile-manifest")},
            "compile_log": {"sha256": token(candidate + suite + "-compile-log")},
            "inputs": {
                "official_manifest": {"sha256": manifest_sha},
                "generation_index": {"sha256": token(suite + "-generation-index")},
                "artifact_manifest": {"sha256": token(candidate + suite + "-artifacts")},
            },
            "runs": runs,
        }

    def _write_functional_doc(self, candidate: str, kind: str, document: dict) -> dict[str, str]:
        key = (candidate, kind)
        path = self.root / "receipts" / "functional" / candidate / f"{kind}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(document))
        self.functional_docs[key] = document
        self.functional_paths[key] = path
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest(path.read_bytes())}

    def _build(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        provenance = self.campaign["provenance"]

        server = self.base("server_environment")
        server["environment"] = {
            **copy.deepcopy(provenance["server_environment"]),
            "qualification_status": "PROVEN",
            "tools": {
                "genus": {"version": "23.14-s090_1",
                          "executable_sha256": token("genus-executable")},
                "innovus": {"version": "23.14-s088_1",
                            "executable_sha256": token("innovus-executable")},
            },
            "technology": copy.deepcopy(provenance["technology"]),
            "pvt": copy.deepcopy(provenance["pvt"]),
            "final_cohort_only": True,
        }
        self.receipts["server_environment"] = server

        staged = self.base("tech_staged_manifest")
        staged["manifest"] = {
            **copy.deepcopy(provenance["staged_manifest"]),
            "candidate_ids": list(GATE.EXPECTED_CANDIDATES),
            "tops": copy.deepcopy(GATE.EXPECTED_TOPS),
            "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS),
            "link_ports_preserved": True,
        }
        staged["candidate_results"] = {
            candidate: {"status": "PASS", "top": GATE.EXPECTED_TOPS[candidate],
                        "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS[candidate]),
                        "link_ports": GATE.EXPECTED_LINK_PORTS[candidate],
                        "link_bits": GATE.EXPECTED_LINK_BITS[candidate]}
            for candidate in GATE.EXPECTED_CANDIDATES
        }
        self.receipts["tech_staged_manifest"] = staged

        genus = self.base("genus")
        genus.update({
            "boundary_cohort": GATE.EXPECTED_COHORT,
            "source_origin": "tech_staged_repository_exact",
            "staged_manifest_sha256": provenance["staged_manifest"]["sha256"],
            "server_environment_contract_sha256":
                provenance["server_environment"]["contract_sha256"],
        })
        genus["candidate_results"] = {
            candidate: {
                "status": "PASS", "top": GATE.EXPECTED_TOPS[candidate],
                "top_ports": copy.deepcopy(GATE.EXPECTED_TOP_PORTS[candidate]),
                "mapped_netlist_sha256": token(candidate + "-mapped-netlist"),
                "mapped_sdc_sha256": token(candidate + "-mapped-sdc"),
                "constraint_set_sha256": provenance["sdc"]["sha256"],
                "report_receipt_sha256": token(candidate + "-genus-report"),
                "mapped_smoke_sha256": token(candidate + "-mapped-smoke"),
            }
            for candidate in GATE.EXPECTED_CANDIDATES
        }
        self.receipts["genus"] = genus

        innovus = self.base("innovus")
        innovus["frequency_sweeps"] = {
            candidate: self.sweep(candidate, index)
            for index, candidate in enumerate(GATE.EXPECTED_CANDIDATES)
        }
        self.receipts["innovus"] = innovus

        activity = self.base("activity_power")
        activity["activity"] = {
            "mode": "SAIF",
            "measurement": {
                "trace_bundle_sha256": provenance["workload"]["trace_bundle_sha256"],
                "workload_window_id": "full50-cap22-retire-window",
                "window_start_cycle": 100, "window_end_cycle_exclusive": 1100,
                "measurement_cycles": 1000, "clock_period_ns": "1.0",
            },
            "authentication": {
                "method": "BOUNDARY_HMAC_SHA256", "boundary_role": "boundary",
                "scope": "ENTIRE_ACTIVITY_POWER_RECEIPT_SHA256",
            },
        }
        activity["candidate_results"] = {}
        for index, candidate in enumerate(GATE.EXPECTED_CANDIDATES):
            total = str(1 + index)
            activity["candidate_results"][candidate] = {
                "status": "PASS", "vcd_sha256": token(candidate + "-vcd"),
                "saif_sha256": token(candidate + "-saif"),
                "activity_window_sha256": token(candidate + "-activity-window"),
                "saif_conversion_receipt_sha256": token(candidate + "-vcd-to-saif"),
                "activity_window": copy.deepcopy(activity["activity"]["measurement"]),
                "power_report_sha256": token(candidate + "-power-report"),
                "scope_sha256": token(candidate + "-power-scope"),
                "postroute_netlist_sha256": token(candidate + "-1.0-netlist"),
                "spef_sha256": token(candidate + "-spef"),
                "physical_stage": "INNOVUS_POST_ROUTE_EXTRACTED",
                "coverage_percent": 99.0 + index / 10,
                "retired_events": 1000, "total_power_mw": total,
                "dynamic_power_mw": str(0.8 + index), "leakage_power_mw": "0.2",
                "energy_pj_per_event": total,
            }
        self.receipts["activity_power"] = activity

        functional = self.base("functional_loss")
        functional["claim_boundary"] = {
            "loss_accounting": "GO", "accepted_event_conservation": "GO",
            "official_common_receipt": "GO", "workspace_diff": False,
            "ppa_usage": "FORBIDDEN",
        }
        functional["measurement"] = {
            "trace_bundle_sha256": provenance["workload"]["trace_bundle_sha256"],
            "full_manifest_sha256": provenance["workload"]["full_manifest_sha256"],
            "capacity_manifest_sha256": provenance["workload"]["capacity_manifest_sha256"],
            "full_run_count": 50, "capacity_run_count": 22,
        }
        functional["candidate_results"] = {
            candidate: {
                "status": "PASS",
                "official_receipts": {
                    "full50": self._write_functional_doc(
                        candidate, "full50", self._common_receipt(candidate, "full50")),
                    "capacity22": self._write_functional_doc(
                        candidate, "capacity22", self._common_receipt(candidate, "capacity22")),
                    "basic_reset": self._write_functional_doc(candidate, "basic_reset", {
                        "schema": "k2_basic_reset_receipt_v1", "status": "PASS",
                        "receipt_id": f"{candidate}-basic-reset", "candidate": candidate,
                        "release_binding": copy.deepcopy(self.campaign),
                    }),
                },
                "generated": 1200, "source_overrun": 200,
                "accepted": 1000, "delivered": 1000, "errors": 0,
            } for candidate in GATE.EXPECTED_CANDIDATES
        }
        self.receipts["functional_loss"] = functional

        for role in GATE.ATTESTED_ROLES:
            path = self.root / "receipts" / f"{role}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical(self.receipts[role]))
            self.receipt_paths[role] = path
        self._write_boundary()
        self._write_keyring()
        self._write_manifest()

    def _attested_hashes(self) -> dict[str, str]:
        return {role: digest(self.receipt_paths[role].read_bytes())
                for role in GATE.ATTESTED_ROLES}

    def _write_boundary(self, *, resign: bool = True) -> None:
        boundary = self.base("boundary")
        boundary["common_non_link_seam_sha256"] = \
            self.campaign["provenance"]["staged_manifest"]["normalized_boundary_sha256"]
        boundary["seam_policy"] = {
            "common_non_link_seam_identical": True,
            "hidden_storage": False, "link_outputs_retained": True,
        }
        boundary["candidate_results"] = {}
        for candidate in GATE.EXPECTED_CANDIDATES:
            bits = GATE.EXPECTED_LINK_BITS[candidate]
            boundary["candidate_results"][candidate] = {
                "status": "PASS", "top": GATE.EXPECTED_TOPS[candidate],
                "clock_contract": {
                    "schema": "k2_w2_multiclock_full_link_v6",
                    "input_clocks": ["ref_clk_i", "sample_clk_i"],
                    "generated_clocks": ["link_clk_o"], "gated_clocks": ["link_clk_o"],
                },
                "link_cut": {
                    "marker": "AER_LINK_CUT", "ports": GATE.EXPECTED_LINK_PORTS[candidate],
                    "physical_link_bits": bits, "native_boundary_link_bits": 0,
                    "link_cut_accounted_bits": bits, "total_accounted_link_bits": bits,
                    "tx_rx_same_nets_connected": True, "external_load_applied_once": True,
                },
            }
        payload = {
            "schema": GATE.BOUNDARY_ATTESTATION_SCHEMA,
            "release_id": self.release_id, "campaign": copy.deepcopy(self.campaign),
            "receipt_sha256": self._attested_hashes(),
            "boundary_body_sha256": digest(canonical(boundary)),
        }
        mac = hmac.new(self.SECRET, canonical(payload), hashlib.sha256).hexdigest()
        boundary["attestation"] = {
            "algorithm": "hmac-sha256", "key_id": self.KEY_ID,
            "payload": payload, "mac_sha256": mac if resign else "00" * 32,
        }
        self.receipts["boundary"] = boundary
        path = self.root / "receipts" / "boundary.json"
        path.write_bytes(canonical(boundary))
        self.receipt_paths["boundary"] = path

    def _write_keyring(self) -> None:
        self.keyring_path.write_bytes(canonical({
            "schema": GATE.KEYRING_SCHEMA,
            "keys": {self.KEY_ID: {
                "algorithm": "hmac-sha256", "secret_hex": self.SECRET.hex()}},
        }))

    def _write_manifest(self) -> None:
        references = []
        for role in GATE.ROLES:
            path = self.receipt_paths[role]
            references.append({"role": role, "path": path.relative_to(self.root).as_posix(),
                               "sha256": digest(path.read_bytes())})
        self.manifest_path.write_bytes(canonical({
            "schema": GATE.MANIFEST_SCHEMA, "release_id": self.release_id,
            "campaign": copy.deepcopy(self.campaign), "receipts": references,
        }))

    def rewrite(self, role: str, *, resign_boundary: bool = True,
                rewrite_manifest: bool = True) -> None:
        self.receipt_paths[role].write_bytes(canonical(self.receipts[role]))
        if role != "boundary" and resign_boundary:
            self._write_boundary()
        if rewrite_manifest:
            self._write_manifest()

    def rewrite_point(self, candidate: str, period: str, role: str) -> None:
        reference = self._write_point(candidate, period, role)
        sweep = self.receipts["innovus"]["frequency_sweeps"][candidate]
        point = next(row for row in sweep["points"] if row["period_ns"] == period)
        point["receipts"][role] = reference
        self.rewrite("innovus")

    def mutate_campaign_everywhere(self, transform) -> None:
        transform(self.campaign)
        for role in GATE.ATTESTED_ROLES:
            self.receipts[role]["release_binding"] = copy.deepcopy(self.campaign)
        for key, document in self.point_docs.items():
            document["release_binding"] = copy.deepcopy(self.campaign)
            self._write_point(*key)
        for candidate, sweep in self.receipts["innovus"]["frequency_sweeps"].items():
            for point in sweep["points"]:
                for role in GATE.POINT_RECEIPT_SCHEMAS:
                    path = self.point_paths[(candidate, point["period_ns"], role)]
                    point["receipts"][role]["sha256"] = digest(path.read_bytes())
        for role in GATE.ATTESTED_ROLES:
            self.receipt_paths[role].write_bytes(canonical(self.receipts[role]))
        self._write_boundary()
        self._write_manifest()


class ReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="k2-w2-release-")
        self.root = Path(self.temp.name)
        self.bundle = ReleaseBundle(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def gate(self) -> dict:
        return GATE.load_and_gate(
            self.root, self.bundle.manifest_path, self.bundle.keyring_path,
            digest(self.bundle.keyring_path.read_bytes()))

    def reject(self, fragment: str) -> None:
        with self.assertRaisesRegex(GATE.ReleaseGateError, fragment):
            self.gate()

    def cli_command(self, output: Path) -> list[str]:
        return [
            sys.executable, str(GATE_PATH), "--bundle-root", str(self.root),
            "--manifest", str(self.bundle.manifest_path),
            "--keyring", str(self.bundle.keyring_path),
            "--keyring-sha256", digest(self.bundle.keyring_path.read_bytes()),
            "--output", str(output),
        ]

    def setUp_rebuild(self) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="k2-w2-release-")
        self.root = Path(self.temp.name)
        self.bundle = ReleaseBundle(self.root)

    def test_valid_exact_campaign_permits_ranking_without_metrics(self) -> None:
        result = self.gate()
        self.assertEqual(result["status"], "RANKING_PERMITTED")
        self.assertEqual(result["candidate_ids"], GATE.EXPECTED_CANDIDATES)
        self.assertEqual(len(result["auxiliary_receipt_sha256"]), 45)
        serialized = canonical(result)
        for forbidden in (b"wns_ns", b"power_mw", b"energy_pj", b"ranking_order"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(serialized, canonical(self.gate()))

    def test_missing_duplicate_and_changed_top_receipts_fail_closed(self) -> None:
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["receipts"] = manifest["receipts"][:-1]
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("receipt role inventory mismatch")

        self.setUp_rebuild()
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["receipts"][-1] = copy.deepcopy(manifest["receipts"][0])
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("duplicate receipt role")

        self.setUp_rebuild()
        path = self.bundle.receipt_paths["genus"]
        path.write_bytes(path.read_bytes() + b" ")
        self.reject("SHA256 mismatch")

    def test_exact_two_candidate_fabrication_is_rejected_even_when_resigned(self) -> None:
        candidates = ["fovea_a7", "a2_p6"]
        self.bundle.campaign["candidate_ids"] = candidates
        self.bundle.campaign["candidate_commits"].pop("a3_p6")
        for role in GATE.ATTESTED_ROLES:
            self.bundle.receipts[role]["release_binding"] = copy.deepcopy(self.bundle.campaign)
            self.bundle.receipts[role]["candidate_results"].pop("a3_p6", None)
            self.bundle.receipt_paths[role].write_bytes(canonical(self.bundle.receipts[role]))
        self.bundle._write_boundary()
        self.bundle._write_manifest()
        self.reject("exact ordered final three-candidate set")

    def test_genus_v1_cross_schema_bypass_is_rejected(self) -> None:
        self.bundle.receipts["genus"]["schema"] = "k2_w2_genus_receipt_v1"
        self.bundle.rewrite("genus")
        self.reject("schema mismatch")

    def test_server_env_hold_and_raw_diagnostic_cohort_are_not_promoted(self) -> None:
        self.bundle.receipts["server_environment"]["environment"]["qualification_status"] = "HOLD"
        self.bundle.rewrite("server_environment")
        self.reject("not PROVEN")
        self.setUp_rebuild()
        self.bundle.campaign["cohort_id"] = "raw_core_only"
        self.bundle.mutate_campaign_everywhere(lambda _: None)
        self.reject("not the final tech-staged cohort")

    def test_staged_manifest_wrong_set_or_removed_link_port_is_rejected(self) -> None:
        manifest = self.bundle.receipts["tech_staged_manifest"]["manifest"]
        manifest["candidate_ids"] = ["fovea_a7", "a2_p6"]
        self.bundle.rewrite("tech_staged_manifest")
        self.reject("exact normalized three-top set")

    def test_canonical_top_signature_and_all_legacy_aliases_are_rejected(self) -> None:
        for alias, direction, canonical_name in (
            ("load_i", "inputs", "source_pending_i[15:0]"),
            ("pending_i[15:0]", "inputs", "source_pending_i[15:0]"),
            ("source_ready_o[15:0]", "outputs", "source_accept_o[15:0]"),
            ("protocol_fault_o", "outputs", "protocol_error_o"),
        ):
            with self.subTest(alias=alias):
                ports = copy.deepcopy(GATE.EXPECTED_TOP_PORTS["a2_p6"])
                ports[direction][ports[direction].index(canonical_name)] = alias
                with self.assertRaisesRegex(GATE.ReleaseGateError,
                                            "canonical final top signature|forbidden"):
                    GATE.validate_top_ports(ports, "a2_p6", "mutant")

        self.setUp_rebuild()
        campaign = copy.deepcopy(self.bundle.campaign)
        campaign["provenance"]["sdc"]["top_ports"]["fovea_a7"]["inputs"][3] = \
            "pending_i[15:0]"
        with self.assertRaisesRegex(GATE.ReleaseGateError, "canonical final top signature"):
            GATE.validate_campaign(campaign, "mutant_campaign")

        self.setUp_rebuild()
        row = self.bundle.receipts["tech_staged_manifest"]["candidate_results"]["a2_p6"]
        row["top_ports"]["outputs"][-1] = "protocol_fault_o"
        self.bundle.rewrite("tech_staged_manifest")
        self.reject("canonical final top signature")

        self.setUp_rebuild()
        row = self.bundle.receipts["genus"]["candidate_results"]["a2_p6"]
        row["top_ports"]["outputs"][2] = "link_data_o[1:0]"
        self.bundle.rewrite("genus")
        self.reject("canonical final top signature")

        self.setUp_rebuild()
        point = self.bundle.point_docs[("a3_p6", "1.0", "innovus")]
        point["top_ports"]["outputs"][-1] = "protocol_fault_o"
        self.bundle.rewrite_point("a3_p6", "1.0", "innovus")
        self.reject("canonical final top signature")
        self.setUp_rebuild()
        self.bundle.receipts["tech_staged_manifest"]["manifest"]["link_ports_preserved"] = False
        self.bundle.rewrite("tech_staged_manifest")
        self.reject("exact normalized three-top set")

    def test_postroute_point_missing_receipt_and_fake_sentinel_are_rejected(self) -> None:
        point = self.bundle.receipts["innovus"]["frequency_sweeps"]["a2_p6"]["points"][0]
        point["receipts"].pop("connectivity")
        self.bundle.rewrite("innovus")
        self.reject("must contain Innovus/STA/DRC/connectivity")
        self.setUp_rebuild()
        document = self.bundle.point_docs[("a2_p6", "1.0", "innovus")]
        document["clean_exit"] = False
        document["fake_pass_sentinel"] = True
        self.bundle.rewrite_point("a2_p6", "1.0", "innovus")
        self.reject("did not exit cleanly")

    def test_fabricated_sweep_booleans_cannot_replace_actual_receipts(self) -> None:
        sweep = self.bundle.receipts["innovus"]["frequency_sweeps"]["a3_p6"]
        sweep["points"] = [
            {"period_ns": "0.8", "wns_ns": "-0.1", "qualified": False},
            {"period_ns": "1.0", "wns_ns": "0.1", "qualified": True},
        ]
        self.bundle.rewrite("innovus")
        self.reject("key mismatch")

    def test_nonmonotonic_actual_sta_and_cherry_pick_are_rejected(self) -> None:
        sta = self.bundle.point_docs[("a3_p6", "1.2", "sta")]
        sta["checks"]["setup"].update({"wns_ns": "0.010", "tns_ns": "0.000",
                                         "violations": 0})
        self.bundle.rewrite_point("a3_p6", "1.2", "sta")
        self.reject("non-monotonic Fmax slack")
        self.setUp_rebuild()
        self.bundle.receipts["innovus"]["frequency_sweeps"]["a3_p6"][
            "selected_period_ns"] = "1.2"
        self.bundle.rewrite("innovus")
        self.reject("cherry-picked")

    def test_sta_hold_recovery_removal_drc_and_connectivity_fail_closed(self) -> None:
        for role, mutate, diagnostic in (
            ("sta", lambda doc: doc["checks"]["recovery"].update(
                {"wns_ns": "-0.1", "tns_ns": "-0.1", "violations": 1}),
             "does not contain both a fail and a pass|pass-to-fail|cherry-picked"),
            ("drc", lambda doc: doc["checks"]["drc"].__setitem__("violations", 1),
             "does not contain both a fail and a pass|pass-to-fail|cherry-picked"),
            ("connectivity", lambda doc: doc["checks"]["signal"].__setitem__("opens", 1),
             "does not contain both a fail and a pass|pass-to-fail|cherry-picked"),
        ):
            with self.subTest(role=role):
                self.setUp_rebuild()
                mutate(self.bundle.point_docs[("fovea_a7", "1.0", role)])
                self.bundle.rewrite_point("fovea_a7", "1.0", role)
                self.reject(diagnostic)

    def test_fabricated_activity_and_vectorless_power_are_rejected(self) -> None:
        row = self.bundle.receipts["activity_power"]["candidate_results"]["a3_p6"]
        row["power_report_sha256"] = row["saif_sha256"]
        self.bundle.rewrite("activity_power")
        self.reject("reuses candidate evidence hashes")
        self.setUp_rebuild()
        row = self.bundle.receipts["activity_power"]["candidate_results"]["a3_p6"]
        row["total_power_mw"] = "99"
        self.bundle.rewrite("activity_power")
        self.reject("component total is inconsistent")
        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["activity"]["mode"] = "VECTORLESS"
        self.bundle.rewrite("activity_power")
        self.reject("must be SAIF")
        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["activity"]["authentication"][
            "method"] = "SELF_ASSERTED"
        self.bundle.rewrite("activity_power")
        self.reject("unauthenticated")

    def test_activity_trace_window_retire_coverage_and_energy_are_required(self) -> None:
        measurement = self.bundle.receipts["activity_power"]["activity"]["measurement"]
        measurement["trace_bundle_sha256"] = token("other-trace")
        self.bundle.rewrite("activity_power")
        self.reject("trace differs")
        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["candidate_results"]["a2_p6"][
            "retired_events"] = 0
        self.bundle.rewrite("activity_power")
        self.reject("integer >= 1")
        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["candidate_results"]["a2_p6"][
            "coverage_percent"] = 0
        self.bundle.rewrite("activity_power")
        self.reject("coverage is invalid")
        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["candidate_results"]["a2_p6"][
            "energy_pj_per_event"] = "999"
        self.bundle.rewrite("activity_power")
        self.reject("energy/event is not derived")

    def test_every_final_evidence_class_is_mandatory(self) -> None:
        for role in ("genus", "innovus", "activity_power", "functional_loss"):
            with self.subTest(role=role):
                self.setUp_rebuild()
                manifest = json.loads(self.bundle.manifest_path.read_text())
                manifest["receipts"] = [row for row in manifest["receipts"]
                                        if row["role"] != role]
                self.bundle.manifest_path.write_bytes(canonical(manifest))
                self.reject("receipt role inventory mismatch")

        for field in ("vcd_sha256", "saif_sha256", "activity_window_sha256",
                      "saif_conversion_receipt_sha256", "power_report_sha256"):
            with self.subTest(activity_field=field):
                self.setUp_rebuild()
                del self.bundle.receipts["activity_power"]["candidate_results"]["a2_p6"][field]
                self.bundle.rewrite("activity_power")
                self.reject("key mismatch")

        self.setUp_rebuild()
        del self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]["full50"]
        self.bundle.rewrite("functional_loss")
        self.reject("official receipt closure is incomplete")

        self.setUp_rebuild()
        del self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]["basic_reset"]
        self.bundle.rewrite("functional_loss")
        self.reject("official receipt closure is incomplete")

        self.setUp_rebuild()
        del self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]["capacity22"]
        self.bundle.rewrite("functional_loss")
        self.reject("official receipt closure is incomplete")

        self.setUp_rebuild()
        self.bundle.receipts["innovus"]["frequency_sweeps"]["a2_p6"][
            "qualified_bracket"] = None
        self.bundle.rewrite("innovus")
        self.reject("must be an object")

    def test_official_common_receipt_count_manifest_and_analyzers_fail_closed(self) -> None:
        document = self.bundle.functional_docs[("a2_p6", "full50")]
        document["validated_run_count"] = 49
        path = self.bundle.functional_paths[("a2_p6", "full50")]
        path.write_bytes(canonical(document))
        references = self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]
        references["full50"]["sha256"] = digest(path.read_bytes())
        self.bundle.rewrite("functional_loss")
        self.reject("identity/status/count mismatch")

        self.setUp_rebuild()
        document = self.bundle.functional_docs[("a2_p6", "capacity22")]
        document["inputs"]["official_manifest"]["sha256"] = token("wrong-capacity-manifest")
        path = self.bundle.functional_paths[("a2_p6", "capacity22")]
        path.write_bytes(canonical(document))
        references = self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]
        references["capacity22"]["sha256"] = digest(path.read_bytes())
        self.bundle.rewrite("functional_loss")
        self.reject("official manifest differs")

        self.setUp_rebuild()
        document = self.bundle.functional_docs[("a2_p6", "full50")]
        analyzer_run = next(row for row in document["runs"]
                            if row["workload"] == "timing_pair")
        del analyzer_run["analyzer"]
        path = self.bundle.functional_paths[("a2_p6", "full50")]
        path.write_bytes(canonical(document))
        references = self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "official_receipts"]
        references["full50"]["sha256"] = digest(path.read_bytes())
        self.bundle.rewrite("functional_loss")
        self.reject("analyzer closure mismatch")

    def test_common_receipt_source_runner_simulator_and_trace_identity_are_bound(self) -> None:
        cases = (
            ("candidate source/binding manifest", lambda doc: doc.__setitem__(
                "candidate_manifest_sha256", token("substituted-candidate-manifest"))),
            ("runner/generator/analyzers", lambda doc: doc["tools"]["runner"].__setitem__(
                "bundle_sha256", token("substituted-runner"))),
            ("simulator differs", lambda doc: doc["simulator"].__setitem__(
                "version_sha256", token("substituted-simulator-version"))),
            ("ordered trace identity", lambda doc: doc["runs"][0]["trace"].__setitem__(
                "sha256", token("substituted-trace"))),
        )
        for diagnostic, mutate in cases:
            with self.subTest(diagnostic=diagnostic):
                self.setUp_rebuild()
                document = self.bundle.functional_docs[("a3_p6", "full50")]
                mutate(document)
                path = self.bundle.functional_paths[("a3_p6", "full50")]
                path.write_bytes(canonical(document))
                references = self.bundle.receipts["functional_loss"]["candidate_results"][
                    "a3_p6"]["official_receipts"]
                references["full50"]["sha256"] = digest(path.read_bytes())
                self.bundle.rewrite("functional_loss")
                self.reject(diagnostic)

        self.setUp_rebuild()
        document = self.bundle.functional_docs[("a3_p6", "capacity22")]
        document["runs"][0]["name"] = "fabricated_capacity_only_run"
        mutated_index = [
            {"name": row["name"], "workload": row["workload"],
             "trace_sha256": row["trace"]["sha256"]}
            for row in document["runs"]
        ]
        self.bundle.campaign["provenance"]["workload"][
            "capacity_trace_index_sha256"] = digest(canonical(mutated_index))
        for candidate in GATE.EXPECTED_CANDIDATES:
            candidate_document = self.bundle.functional_docs[(candidate, "capacity22")]
            candidate_document["runs"][0]["name"] = "fabricated_capacity_only_run"
            path = self.bundle.functional_paths[(candidate, "capacity22")]
            path.write_bytes(canonical(candidate_document))
            self.bundle.receipts["functional_loss"]["candidate_results"][candidate][
                "official_receipts"]["capacity22"]["sha256"] = digest(path.read_bytes())
        self.bundle.mutate_campaign_everywhere(lambda _: None)
        self.reject("not an exact full50 subset view")

    def test_candidate_activity_window_and_selected_physical_netlist_must_match(self) -> None:
        row = self.bundle.receipts["activity_power"]["candidate_results"]["a3_p6"]
        row["activity_window"]["window_start_cycle"] = 101
        self.bundle.rewrite("activity_power")
        self.reject("window differs")

        self.setUp_rebuild()
        row = self.bundle.receipts["activity_power"]["candidate_results"]["a3_p6"]
        row["postroute_netlist_sha256"] = token("unselected-netlist")
        self.bundle.rewrite("activity_power")
        self.reject("not bound to selected post-route")

        self.setUp_rebuild()
        row = self.bundle.receipts["activity_power"]["candidate_results"]["a3_p6"]
        row["physical_stage"] = "GENUS_VECTORLESS"
        self.bundle.rewrite("activity_power")
        self.reject("not bound to selected post-route")

        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["activity"]["measurement"][
            "clock_period_ns"] = "1.2"
        for candidate in GATE.EXPECTED_CANDIDATES:
            self.bundle.receipts["activity_power"]["candidate_results"][candidate][
                "activity_window"]["clock_period_ns"] = "1.2"
        self.bundle.rewrite("activity_power")
        self.reject("not bound to selected post-route")

    def test_workspace_diff_loss_and_loss_conservation_are_rejected(self) -> None:
        claim = self.bundle.receipts["functional_loss"]["claim_boundary"]
        claim["official_common_receipt"] = "HOLD_WORKSPACE_DIFF_NON_OFFICIAL"
        claim["workspace_diff"] = True
        self.bundle.rewrite("functional_loss")
        self.reject("non-official")
        self.setUp_rebuild()
        self.bundle.receipts["functional_loss"]["candidate_results"]["a2_p6"][
            "delivered"] = 999
        self.bundle.rewrite("functional_loss")
        self.reject("conservation failed")

    def test_link_cut_exactly_once_prevents_double_count_and_omission(self) -> None:
        cut = self.bundle.receipts["boundary"]["candidate_results"]["a2_p6"]["link_cut"]
        cut["native_boundary_link_bits"] = 6
        cut["total_accounted_link_bits"] = 12
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("omitted or doubled")
        self.setUp_rebuild()
        cut = self.bundle.receipts["boundary"]["candidate_results"]["a2_p6"]["link_cut"]
        cut["link_cut_accounted_bits"] = 0
        cut["total_accounted_link_bits"] = 0
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("omitted or doubled")

    def test_old_single_clock_boundary_and_hidden_adapter_are_rejected(self) -> None:
        clocks = self.bundle.receipts["boundary"]["candidate_results"]["fovea_a7"][
            "clock_contract"]
        clocks["schema"] = "k2_w2_single_clock_v5"
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("multi-clock contract mismatch")
        self.setUp_rebuild()
        self.bundle.receipts["boundary"]["seam_policy"]["hidden_storage"] = True
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("hidden adaptation")

    def test_cross_provenance_and_stale_generation_are_rejected(self) -> None:
        self.bundle.receipts["innovus"]["release_binding"]["provenance"]["sdc"][
            "sha256"] = token("other-sdc")
        self.bundle.rewrite("innovus")
        self.reject("stale or belongs")
        self.setUp_rebuild()
        self.bundle.receipts["server_environment"]["environment"]["technology"][
            "hold_liberty_sha256"] = token("other-fast-lib")
        self.bundle.rewrite("server_environment")
        self.reject("technology differs")
        self.setUp_rebuild()
        self.bundle.receipts["genus"]["release_binding"]["generation"] = 6
        self.bundle.rewrite("genus")
        self.reject("stale or belongs")

    def test_missing_duplicate_and_changed_nested_receipts_fail_closed(self) -> None:
        path = self.bundle.point_paths[("a2_p6", "1.0", "sta")]
        path.unlink()
        self.reject("cannot read")
        self.setUp_rebuild()
        point = self.bundle.receipts["innovus"]["frequency_sweeps"]["a2_p6"]["points"][1]
        point["receipts"]["sta"] = copy.deepcopy(point["receipts"]["innovus"])
        self.bundle.rewrite("innovus")
        self.reject("duplicate receipt path")
        self.setUp_rebuild()
        path = self.bundle.point_paths[("a2_p6", "1.0", "sta")]
        path.write_bytes(path.read_bytes() + b" ")
        self.reject("SHA256 mismatch")

    def test_boundary_authentication_and_out_of_band_keyring_pin(self) -> None:
        self.bundle.receipts["activity_power"]["candidate_results"]["a2_p6"][
            "coverage_percent"] = 98.0
        self.bundle.rewrite("activity_power", resign_boundary=False)
        self.reject("does not bind every upstream receipt")
        self.setUp_rebuild()
        self.bundle.receipts["boundary"]["attestation"]["mac_sha256"] = "00" * 32
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("MAC mismatch")
        self.setUp_rebuild()
        with self.assertRaisesRegex(GATE.ReleaseGateError, "out-of-band trusted SHA256"):
            GATE.load_and_gate(self.root, self.bundle.manifest_path, self.bundle.keyring_path,
                               "00" * 32)

    def test_cli_exit_contract_and_exclusive_output(self) -> None:
        output = self.root / "permit.json"
        passed = subprocess.run(self.cli_command(output), text=True, capture_output=True,
                                check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(output.read_text())["status"], "RANKING_PERMITTED")
        repeated = subprocess.run(self.cli_command(output), text=True, capture_output=True,
                                  check=False)
        self.assertEqual(repeated.returncode, 1)

        self.setUp_rebuild()
        self.bundle.receipts["innovus"]["status"] = "HOLD"
        self.bundle.rewrite("innovus")
        output = self.root / "hold.json"
        held = subprocess.run(self.cli_command(output), text=True, capture_output=True,
                              check=False)
        self.assertEqual(held.returncode, 2)
        result = json.loads(output.read_text())
        self.assertEqual(result["status"], "RANKING_HOLD")
        self.assertIn("innovus receipt status is not PASS", result["diagnostic"])


if __name__ == "__main__":
    unittest.main()
