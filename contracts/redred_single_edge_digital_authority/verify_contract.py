#!/usr/bin/env python3
"""Fail-closed verifier for the eb298fe A2/A3 full50 RTL replay.

The archived campaign is useful for team selection, but it was rerun with a
locally edited pin file and has no controlled-producer attestation.  This
verifier therefore authenticates bytes and recomputes the digital invariants
without allowing the result to become release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


CONTRACT_SCHEMA = "redred-single-edge-digital-authority-contract-v1"
BINDING_SCHEMA = "redred-single-edge-digital-authority-binding-v1"
PINS_SCHEMA = "a23_full_single_edge_replay_pins_v1"
RESULT_SCHEMA = "a23_full_single_edge_replay_result_v1"
EVIDENCE_CLASS = "LOCALLY_RERUN_HASH_BOUND_DIAGNOSTIC_NOT_PRODUCER_AUTHENTICATED"
MAXIMUM_DECISION = "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE"
CANONICAL_ARCHIVE_SHA256 = "c795ef5653cc9666c8912e553430e4f1987fdc8078b86d61c7853597cf30b930"
CANONICAL_PINS_SHA256 = "a5a117640b234a0e5f8535778ff2029c32f93546b66fe363f60ee2182cced920"
CANONICAL_RESULT_SHA256 = "096f6a784f26ba1406d712c6ccdd85c7a7dae2ecacad817077b708d47fe20f13"
PRIOR_RESULT_SHA256 = "e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4"
SOURCE_COMMIT = "eb298fe1416a4312269a6f9232e1445f8958dda2"
SOURCE_TREE = "21afcca7052889d953a4801531f9f9c31b3c3be5"
INTEGRATION_COMMIT = "bfb4b998049bbf9c66c4af9ffabba2c8ff096363"
INTEGRATION_TREE = "be2cf4ca78e2f02f59a77b748a8a226047ea3e7e"
MANIFEST_SHA256 = "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9"
EXPECTED_INVARIANTS = [
    "generated = source_overrun + accepted",
    "accepted = retired after bounded drain",
    "accepted event identity retires exactly once in global acceptance order",
    "reset occurs only after externally observed clean drain",
    "all literal error mutations compile, execute, and are killed",
]
HEX64 = set("0123456789abcdef")


class ContractError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_keys(value: Any, expected: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{where} keys differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}")


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_bytes(data: bytes, where: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON in {where}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object in {where}")
    return value


def load_file(path: Path, where: str) -> tuple[bytes, dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read {where}: {exc}") from exc
    return data, load_json_bytes(data, where)


def require_sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in HEX64 for c in value):
        raise ContractError(f"{where} must be 64 lowercase hexadecimal digits")
    return value


def safe_relative(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContractError(f"{where} must be a nonempty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ContractError(f"unsafe path in {where}: {value!r}")
    return value


def canonical_binding_sha() -> str:
    # Kept in a function so mutation tests can exercise the binding validator.
    return "9441a2495cc43bea5923a58bede040361cb629f59281ef44ee417003bd61f0b8"


def validate_contract(document: dict[str, Any]) -> None:
    exact_keys(document, {"schema", "contract_id", "decision", "binding",
                          "required_invariants", "selection_use", "evidence_class",
                          "maximum_release_decision"}, "contract")
    if document["schema"] != CONTRACT_SCHEMA:
        raise ContractError("contract schema differs")
    if document["contract_id"] != "REDRED_LATEST_PHYSICAL_RTL_FULL50_REPLAY":
        raise ContractError("contract id differs")
    if document["decision"] != "DIAGNOSTIC_VERIFY_REQUIRED":
        raise ContractError("contract decision differs")
    exact_keys(document["binding"], {"path", "sha256"}, "contract.binding")
    if document["binding"] != {"path": "evidence_binding.json",
                               "sha256": canonical_binding_sha()}:
        raise ContractError("canonical binding pin differs")
    if document["required_invariants"] != EXPECTED_INVARIANTS:
        raise ContractError("required invariants differ")
    if document["selection_use"] != "TEAM_DIAGNOSTIC_METRICS_ELIGIBLE":
        raise ContractError("selection-use boundary differs")
    if document["evidence_class"] != EVIDENCE_CLASS:
        raise ContractError("evidence class differs")
    if document["maximum_release_decision"] != MAXIMUM_DECISION:
        raise ContractError("contract illegally raises the release ceiling")


def validate_binding(document: dict[str, Any]) -> set[str]:
    exact_keys(document, {"schema", "archive", "members", "rtl_authority",
                          "campaign", "expected", "prior_canonical_result",
                          "evidence_class"}, "binding")
    if document["schema"] != BINDING_SCHEMA or document["evidence_class"] != EVIDENCE_CLASS:
        raise ContractError("binding identity differs")
    archive = document["archive"]
    exact_keys(archive, {"path", "sha256", "size_bytes"}, "binding.archive")
    safe_relative(archive["path"], "binding.archive.path")
    if archive["sha256"] != CANONICAL_ARCHIVE_SHA256 or archive["size_bytes"] != 163840:
        raise ContractError("canonical archive identity differs")
    exact_keys(document["members"], {"pins", "result"}, "binding.members")
    expected_members = {"pins": ("pins.json", CANONICAL_PINS_SHA256),
                        "result": ("result.json", CANONICAL_RESULT_SHA256)}
    required: set[str] = set()
    for role, (path, digest) in expected_members.items():
        entry = document["members"][role]
        exact_keys(entry, {"path", "sha256"}, f"binding.members.{role}")
        if entry != {"path": path, "sha256": digest}:
            raise ContractError(f"canonical {role} member pin differs")
        required.add(safe_relative(path, f"binding.members.{role}.path"))
    expected_authority = {
        "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
        "integration_commit": INTEGRATION_COMMIT, "integration_tree": INTEGRATION_TREE,
    }
    if document["rtl_authority"] != expected_authority:
        raise ContractError("RTL authority differs")
    campaign = document["campaign"]
    exact_keys(campaign, {"suite", "manifest_sha256", "actual_rtl_executions",
                          "reset_executions", "mutation_activation_executions",
                          "mutation_executions", "receipt_only_executions"},
               "binding.campaign")
    if campaign != {
        "suite": "generator-v4-full50", "manifest_sha256": MANIFEST_SHA256,
        "actual_rtl_executions": 100, "reset_executions": 2,
        "mutation_activation_executions": 2, "mutation_executions": 8,
        "receipt_only_executions": 0,
    }:
        raise ContractError("campaign accounting differs")
    exact_keys(document["expected"], {"a2", "a3"}, "binding.expected")
    for owner in ("a2", "a3"):
        expected = document["expected"][owner]
        exact_keys(expected, {"generated", "source_overrun", "accepted", "retired",
                              "fixed_window_cycles", "fixed_window_retired",
                              "fixed_window_events_per_cycle", "occurrence_to_accept_max",
                              "accept_to_retire_max"}, f"binding.expected.{owner}")
    prior = document["prior_canonical_result"]
    exact_keys(prior, {"path", "sha256", "full50_rows_must_match_exactly"},
               "binding.prior_canonical_result")
    safe_relative(prior["path"], "binding.prior_canonical_result.path")
    if prior["sha256"] != PRIOR_RESULT_SHA256 or prior["full50_rows_must_match_exactly"] is not True:
        raise ContractError("prior canonical result pin differs")
    return required


def read_archive(path: Path, expected_size: int, expected_sha: str,
                 required: set[str]) -> dict[str, bytes]:
    data = path.read_bytes()
    if len(data) != expected_size or sha256(data) != expected_sha:
        raise ContractError("archive byte identity differs")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(path, "r:") as archive:
            infos = archive.getmembers()
            names = [info.name for info in infos]
            if len(names) != len(set(names)):
                raise ContractError("archive has duplicate members")
            if set(names) != required:
                raise ContractError("archive member set differs")
            for info in infos:
                safe_relative(info.name, "archive member")
                if not info.isfile() or info.issym() or info.islnk():
                    raise ContractError(f"archive member is not a regular file: {info.name}")
                stream = archive.extractfile(info)
                if stream is None:
                    raise ContractError(f"cannot read archive member: {info.name}")
                members[info.name] = stream.read()
    except (tarfile.TarError, OSError) as exc:
        raise ContractError(f"cannot read archive: {exc}") from exc
    return members


def git(repo: Path, *args: str) -> bytes:
    run = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, check=False)
    if run.returncode:
        raise ContractError(
            f"git {' '.join(args)} failed: {run.stderr.decode(errors='replace').strip()}")
    return run.stdout


def verify_git_authority(repo: Path, pins: dict[str, Any]) -> None:
    provenance = pins.get("rtl_provenance")
    expected = {"source_commit": SOURCE_COMMIT, "integration_commit": INTEGRATION_COMMIT,
                "source_tree": SOURCE_TREE, "integration_tree": INTEGRATION_TREE}
    if provenance != expected:
        raise ContractError("pins RTL provenance differs")
    if git(repo, "rev-parse", f"{SOURCE_COMMIT}^{{tree}}").decode().strip() != SOURCE_TREE:
        raise ContractError("source tree does not match source commit")
    if git(repo, "rev-parse", f"{INTEGRATION_COMMIT}^{{tree}}").decode().strip() != INTEGRATION_TREE:
        raise ContractError("integration tree does not match integration commit")
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", SOURCE_COMMIT,
                               INTEGRATION_COMMIT], cwd=repo, check=False)
    if ancestry.returncode:
        raise ContractError("source commit is not an ancestor of integration commit")
    files = pins.get("files")
    if not isinstance(files, dict) or not files:
        raise ContractError("pins.files must be a nonempty object")
    for path, digest in files.items():
        safe_relative(path, f"pins.files.{path}")
        require_sha(digest, f"pins.files.{path}")
        commit = SOURCE_COMMIT if path.startswith("rtl/") else "c8eee77785bedb01fe23b4f9faf9daa40522a549"
        if sha256(git(repo, "show", f"{commit}:{path}")) != digest:
            raise ContractError(f"git-bound file hash differs: {path}")
        if path.startswith("rtl/") and sha256(git(repo, "show", f"{INTEGRATION_COMMIT}:{path}")) != digest:
            raise ContractError(f"integration RTL file hash differs: {path}")


def validate_pins(pins: dict[str, Any]) -> None:
    exact_keys(pins, {"schema", "integration_state", "rtl_provenance", "files", "owners",
                      "mutation_anchor_sha256", "mutations", "tools"}, "pins")
    if pins["schema"] != PINS_SCHEMA or pins["integration_state"] != "LOCKED_ACTUAL_SINGLE_EDGE_RTL":
        raise ContractError("pins identity differs")
    exact_keys(pins["owners"], {"a2", "a3"}, "pins.owners")
    exact_keys(pins["mutations"], {"a2", "a3"}, "pins.mutations")
    for owner in ("a2", "a3"):
        if set(pins["mutations"][owner]) != {"drop", "duplicate", "reorder", "reset_escape"}:
            raise ContractError(f"{owner} mutation inventory differs")
    if set(pins["tools"]) != {"python", "verilator", "verilator_bin", "make", "cxx"}:
        raise ContractError("tool inventory differs")


def require_nonnegative_int(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractError(f"{where} must be a nonnegative integer")
    return value


def validate_latency(value: Any, count: int, where: str) -> None:
    exact_keys(value, {"count", "max", "mean", "p50", "p95", "p99"}, where)
    if value["count"] != count:
        raise ContractError(f"{where}.count differs from accepted")
    for field in ("count", "max", "p50", "p95", "p99"):
        require_nonnegative_int(value[field], f"{where}.{field}")
    if not isinstance(value["mean"], (int, float)) or isinstance(value["mean"], bool) \
            or not math.isfinite(value["mean"]) or value["mean"] < 0:
        raise ContractError(f"{where}.mean must be finite and nonnegative")
    if not (value["p50"] <= value["p95"] <= value["p99"] <= value["max"]):
        raise ContractError(f"{where} percentile ordering differs")


def validate_run(run: dict[str, Any], where: str, *, reset: bool | None = None) -> None:
    for field in ("generated", "source_overrun", "accepted", "retired",
                  "fixed_window_cycles", "fixed_window_retired", "count2_commits"):
        if field not in run:
            raise ContractError(f"{where}.{field} missing")
        require_nonnegative_int(run[field], f"{where}.{field}")
    if run["generated"] != run["source_overrun"] + run["accepted"]:
        raise ContractError(f"{where}: generated conservation fails")
    if run["accepted"] != run["retired"]:
        raise ContractError(f"{where}: accepted/retired exact-once conservation fails")
    if run["fixed_window_retired"] > run["retired"]:
        raise ContractError(f"{where}: fixed-window retired exceeds retired")
    validate_latency(run.get("occurrence_to_accept"), run["accepted"],
                     f"{where}.occurrence_to_accept")
    validate_latency(run.get("accept_to_retire"), run["accepted"],
                     f"{where}.accept_to_retire")
    if reset is True and (run.get("reset_test") != 1 or run.get("pre_reset_clean_drain") != 1):
        raise ContractError(f"{where}: reset was not preceded by observed clean drain")


def verify_result(result: dict[str, Any], pins: dict[str, Any], binding: dict[str, Any],
                  prior: dict[str, Any]) -> dict[str, Any]:
    exact_keys(result, {"schema", "status", "boundary", "acceptance_observation",
                        "event_identity_scope", "retirement_scoreboard", "conservation",
                        "source_overrun_semantics", "reset_qualification", "generator",
                        "execution_accounting", "provenance", "owners", "mutations",
                        "qualification"}, "result")
    if result["schema"] != RESULT_SCHEMA or result["status"] != "PASS":
        raise ContractError("result identity/status differs")
    if result["conservation"] != ["generated = source_overrun + accepted",
                                  "after bounded drain: accepted = retired"]:
        raise ContractError("result conservation declaration differs")
    if result["reset_qualification"] != "reset_only_after_external_clean_drain_and_no_protocol_error":
        raise ContractError("reset qualification differs")
    if result["qualification"] != {"CDC_RDC": "HOLD", "physical": "HOLD", "power": "HOLD",
                                      "single_edge_digital_RTL": "GO"}:
        raise ContractError("qualification boundary differs")
    generator = result["generator"]
    if generator.get("full50_manifest_sha256") != MANIFEST_SHA256 or generator.get("trace_count") != 50:
        raise ContractError("generator manifest/count differs")
    campaign = binding["campaign"]
    expected_accounting = {
        "owners": 2,
        "full50_actual_RTL_executions": campaign["actual_rtl_executions"],
        "reset_actual_RTL_executions": campaign["reset_executions"],
        "mutation_activation_actual_RTL_executions": campaign["mutation_activation_executions"],
        "mutation_actual_RTL_executions": campaign["mutation_executions"],
        "receipt_only_executions": campaign["receipt_only_executions"],
    }
    if result["execution_accounting"] != expected_accounting:
        raise ContractError("execution accounting differs")
    provenance = result["provenance"]
    exact_keys(provenance, {"package_commit", "pins_path", "pins_sha256", "verified_files",
                            "verified_tools", "actual_rtl_git"}, "result.provenance")
    if provenance["pins_sha256"] != CANONICAL_PINS_SHA256 or provenance["actual_rtl_git"] != {
        **binding["rtl_authority"], "verified_rtl_paths": sorted(
            path for path in pins["files"] if path.startswith("rtl/"))}:
        raise ContractError("result RTL/pins provenance differs")
    if provenance["verified_files"] != pins["files"]:
        raise ContractError("result verified-file inventory differs from pins")
    expected_tools = {name: {"path": tool["path"], "sha256": tool["sha256"],
                             "version": tool["version"]}
                      for name, tool in pins["tools"].items()}
    if provenance["verified_tools"] != expected_tools:
        raise ContractError("result verified-tool inventory differs from pins")
    exact_keys(result["owners"], {"a2", "a3"}, "result.owners")
    metrics: dict[str, Any] = {}
    for owner in ("a2", "a3"):
        entry = result["owners"][owner]
        exact_keys(entry, {"baseline_build_log_sha256", "full50", "reset",
                           "mutation_activation"}, f"result.owners.{owner}")
        full = entry["full50"]
        exact_keys(full, {"actual_execution_count", "aggregate", "runs"},
                   f"result.owners.{owner}.full50")
        runs = full["runs"]
        if not isinstance(runs, dict) or len(runs) != 50 or list(runs) != sorted(runs):
            raise ContractError(f"{owner} full50 run inventory must be 50 sorted names")
        if full["actual_execution_count"] != 50:
            raise ContractError(f"{owner} actual full50 execution count differs")
        totals = Counter()
        for name, run in runs.items():
            validate_run(run, f"{owner}.runs.{name}")
            for field in ("generated", "source_overrun", "accepted", "retired",
                          "fixed_window_cycles", "fixed_window_retired", "count2_commits"):
                totals[field] += run[field]
        aggregate = full["aggregate"]
        exact_keys(aggregate, {"actual_execution_count", "totals", "fixed_window_events_per_cycle",
                               "occurrence_to_accept", "accept_to_retire"},
                   f"result.owners.{owner}.full50.aggregate")
        if aggregate["actual_execution_count"] != 50 or aggregate["totals"] != dict(totals):
            raise ContractError(f"{owner} aggregate totals do not recompute from runs")
        validate_latency(aggregate["occurrence_to_accept"], totals["accepted"],
                         f"{owner}.aggregate.occurrence_to_accept")
        validate_latency(aggregate["accept_to_retire"], totals["accepted"],
                         f"{owner}.aggregate.accept_to_retire")
        throughput = totals["fixed_window_retired"] / totals["fixed_window_cycles"]
        if abs(aggregate["fixed_window_events_per_cycle"] - round(throughput, 9)) > 1e-12:
            raise ContractError(f"{owner} fixed-window throughput does not recompute")
        expected = binding["expected"][owner]
        observed = {
            "generated": totals["generated"], "source_overrun": totals["source_overrun"],
            "accepted": totals["accepted"], "retired": totals["retired"],
            "fixed_window_cycles": totals["fixed_window_cycles"],
            "fixed_window_retired": totals["fixed_window_retired"],
            "fixed_window_events_per_cycle": aggregate["fixed_window_events_per_cycle"],
            "occurrence_to_accept_max": aggregate["occurrence_to_accept"]["max"],
            "accept_to_retire_max": aggregate["accept_to_retire"]["max"],
        }
        if observed != expected:
            raise ContractError(f"{owner} expected metrics differ")
        validate_run(entry["reset"], f"{owner}.reset", reset=True)
        validate_run(entry["mutation_activation"], f"{owner}.mutation_activation")
        if full != prior["owners"][owner]["full50"]:
            raise ContractError(f"{owner} full50 rows differ from prior canonical campaign")
        metrics[owner] = {**observed,
                          "occurrence_to_accept_mean": aggregate["occurrence_to_accept"]["mean"],
                          "accept_to_retire_mean": aggregate["accept_to_retire"]["mean"]}
    mutations = result["mutations"]
    if not isinstance(mutations, list) or len(mutations) != 8:
        raise ContractError("exactly eight mutation results are required")
    inventory = Counter()
    for index, mutation in enumerate(mutations):
        required = {"owner", "mutation", "compiled_successfully", "executed", "killed",
                    "exit_code", "first_diagnostic", "actual_endpoint_RTL_source_rewrite",
                    "build_log_sha256", "simulation_log_sha256", "source_identity"}
        exact_keys(mutation, required, f"result.mutations[{index}]")
        key = (mutation["owner"], mutation["mutation"])
        inventory[key] += 1
        if mutation["compiled_successfully"] is not True or mutation["executed"] is not True \
                or mutation["killed"] is not True \
                or mutation["actual_endpoint_RTL_source_rewrite"] is not True:
            raise ContractError(f"mutation was not compiled, executed, rewritten, and killed: {key}")
        if not isinstance(mutation["exit_code"], int) or mutation["exit_code"] == 0:
            raise ContractError(f"mutation unexpectedly passed: {key}")
        if not isinstance(mutation["first_diagnostic"], str) \
                or not mutation["first_diagnostic"].endswith("_FAIL"):
            raise ContractError(f"mutation has no fail diagnostic: {key}")
    expected_inventory = Counter((owner, name) for owner in ("a2", "a3")
                                 for name in ("drop", "duplicate", "reorder", "reset_escape"))
    if inventory != expected_inventory:
        raise ContractError("mutation owner/type inventory differs")
    return metrics


def verify(repo: Path, contract_path: Path, binding_path: Path,
           archive_override: Path | None = None) -> dict[str, Any]:
    _, contract = load_file(contract_path, "contract")
    binding_bytes, binding = load_file(binding_path, "binding")
    validate_contract(contract)
    if sha256(binding_bytes) != canonical_binding_sha():
        raise ContractError("binding byte identity differs")
    required = validate_binding(binding)
    archive_entry = binding["archive"]
    archive_path = archive_override or repo / archive_entry["path"]
    members = read_archive(archive_path, archive_entry["size_bytes"], archive_entry["sha256"], required)
    for role, entry in binding["members"].items():
        if sha256(members[entry["path"]]) != entry["sha256"]:
            raise ContractError(f"{role} member hash differs")
    pins = load_json_bytes(members["pins.json"], "archive pins")
    result = load_json_bytes(members["result.json"], "archive result")
    validate_pins(pins)
    verify_git_authority(repo, pins)
    prior_entry = binding["prior_canonical_result"]
    prior_bytes, prior = load_file(repo / prior_entry["path"], "prior canonical result")
    if sha256(prior_bytes) != prior_entry["sha256"]:
        raise ContractError("prior canonical result byte identity differs")
    metrics = verify_result(result, pins, binding, prior)
    return {
        "schema": "redred-single-edge-digital-authority-verification-v1",
        "digital_rtl_diagnostic_status": "PASS",
        "latest_rtl_source_commit": SOURCE_COMMIT,
        "actual_rtl_executions": 100,
        "reset_executions": 2,
        "mutation_activation_executions": 2,
        "mutation_executions": 8,
        "accepted_event_exact_once": True,
        "full50_matches_prior_canonical_exactly": True,
        "metrics": metrics,
        "team_diagnostic_selection_eligible": True,
        "producer_authenticated": False,
        "controlled_freshness_verified": False,
        "final_digital_release_gate": "HOLD",
        "maximum_release_decision": MAXIMUM_DECISION,
        "decision": "DIAGNOSTIC_PASS_RELEASE_HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    contract_dir = Path(__file__).resolve().parent
    try:
        result = verify(repo, contract_dir / "contract.json",
                        contract_dir / "evidence_binding.json", args.archive)
    except (ContractError, OSError) as exc:
        print(f"REDRED_SINGLE_EDGE_DIGITAL_AUTHORITY_FAIL: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    print("REDRED_SINGLE_EDGE_DIGITAL_AUTHORITY_DIAGNOSTIC_PASS_RELEASE_HOLD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
