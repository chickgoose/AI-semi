#!/usr/bin/env python3
"""Package one proven environment and exactly three completed Genus attempts.

This launcher intentionally executes no EDA tool.  It creates one exclusive
attempt root, validates and snapshots candidates in the contract order, and
leaves an auditable failure root if any gate fails.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "contract.json"
ENV_MODULE_PATH = ROOT / "physical/k2_w2_server_env/preflight.py"
SAFE_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
SHA = re.compile(r"[0-9a-f]{64}")


class CohortError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path, *, nonempty: bool = True) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise CohortError(f"not a regular non-symlink file: {path}")
    if before.st_nlink != 1:
        raise CohortError(f"hard-linked input is forbidden: {path}")
    payload = path.read_bytes()
    after = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise CohortError(f"input changed while reading: {path}")
    if nonempty and not payload:
        raise CohortError(f"empty required input: {path}")
    return payload, after


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload, _ = stable_read(path)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CohortError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise CohortError(f"JSON root is not an object: {path}")
    return value, payload


def write_exclusive(path: Path, payload: bytes, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def append_log(root: Path, message: str) -> None:
    log = root / "logs/launcher.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        stream.write((message + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise CohortError(f"missing or malformed SHA: {label}")
    return value


def no_symlink_path(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    if absolute != resolved:
        raise CohortError(f"{label} contains a symlink: {path}")
    return resolved


def load_environment_module():
    spec = importlib.util.spec_from_file_location("k2_w2_server_env_preflight",
                                                  ENV_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise CohortError("cannot load proven-environment verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema") != "k2_w4_server_cohort_contract_v1":
        raise CohortError("cohort contract schema mismatch")
    order = contract.get("candidate_order")
    if order != ["fovea_a7", "a2_p6", "a3_p6"] or \
            list(contract.get("designs", {})) != order:
        raise CohortError("candidate set/order is not the exact canonical three")
    if contract.get("eda_execution_allowed_by_this_launcher") is not False or \
            contract.get("physical_qualification") != \
            "HOLD_REQUIRES_SEPARATE_INNOVUS_AND_QUALIFIER":
        raise CohortError("launcher may not claim or execute physical qualification")
    environment = contract.get("environment", {})
    path = ROOT / environment.get("contract_path", "")
    payload, _ = stable_read(path)
    if digest(payload) != environment.get("contract_sha256"):
        raise CohortError("validated environment contract SHA mismatch")


def validate_environment(path: Path, contract: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    document, payload = load_json(path)
    module = load_environment_module()
    expected = contract["environment"]["contract_sha256"]
    try:
        module.verify_go_document(document, expected)
    except Exception as error:  # The provider owns its concrete error type.
        raise CohortError(f"environment receipt is not validated GO: {error}") from error
    if document.get("qualification_status") != contract["environment"]["required_status"] or \
            document.get("receipt", {}).get("decision") != \
            contract["environment"]["required_decision"]:
        raise CohortError("environment qualification state mismatch")
    return payload, document


def check_hash(path: Path, expected: Any, label: str) -> bytes:
    expected_sha = require_sha(expected, label)
    payload, _ = stable_read(path)
    if digest(payload) != expected_sha:
        raise CohortError(f"artifact SHA mismatch: {label}")
    return payload


def validate_genus_receipt(candidate: str, receipt_path: Path,
                           environment_sha: str, contract: dict[str, Any]
                           ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    receipt_path = no_symlink_path(receipt_path, f"{candidate} receipt")
    if receipt_path.name != "receipt.json":
        raise CohortError(f"{candidate} receipt basename must be receipt.json")
    attempt = no_symlink_path(receipt_path.parent, f"{candidate} attempt root")
    receipt, _ = load_json(receipt_path)
    genus = contract["genus"]
    design = contract["designs"][candidate]
    top = design["top"]
    if (receipt.get("schema") != genus["receipt_schema"] or
            receipt.get("status") != genus["receipt_status"] or
            receipt.get("design") != candidate or receipt.get("top") != top or
            receipt.get("boundary_cohort") != genus["boundary_cohort"] or
            receipt.get("source_origin") != genus["source_origin"] or
            receipt.get("ranking_policy") != genus["ranking_policy"] or
            receipt.get("claim_boundary") != genus["claim_boundary"]):
        raise CohortError(f"{candidate} Genus receipt identity/status mismatch")
    checks = receipt.get("checks", {})
    for key, expected in genus["required_checks"].items():
        if checks.get(key) != expected:
            raise CohortError(f"{candidate} qualification check failed: {key}")

    attempt_doc, _ = load_json(attempt / "attempt.json")
    check_hash(attempt / "attempt.json", receipt.get("attempt_sha256"),
               f"{candidate}:attempt.json")
    if (attempt_doc.get("schema") != genus["attempt_schema"] or
            attempt_doc.get("design") != candidate or attempt_doc.get("top") != top or
            attempt_doc.get("boundary_cohort") != genus["boundary_cohort"] or
            attempt_doc.get("source_origin") != genus["source_origin"] or
            attempt_doc.get("ranking_policy") != genus["ranking_policy"] or
            attempt_doc.get("flow_git_head") != genus["producer_commit"] or
            attempt_doc.get("source_commit") != genus["source_commit"] or
            attempt_doc.get("proven_environment", {}).get("sha256") != environment_sha):
        raise CohortError(f"{candidate} attempt/environment/source binding mismatch")
    flow_files = attempt_doc.get("flow_files_sha256", {})
    if (not isinstance(flow_files, dict) or not flow_files or
            any(not isinstance(path, str) or path.startswith("/") or
                ".." in Path(path).parts or not isinstance(value, str) or
                not SHA.fullmatch(value)
                for path, value in flow_files.items())):
        raise CohortError(f"{candidate} malformed Genus flow-file closure")
    for path, expected in {
            "physical/k2_w2_genus/run_genus.py": genus["producer_sha256"],
            "physical/k2_w2_genus/designs.json": genus["registry_sha256"]}.items():
        if flow_files.get(path) != expected:
            raise CohortError(f"{candidate} exact Genus producer/registry mismatch: {path}")
    if (receipt.get("staged_manifest") != attempt_doc.get("staged_manifest") or
            receipt.get("technology_authorities") !=
            attempt_doc.get("technology_authorities")):
        raise CohortError(f"{candidate} staged/technology authority mismatch")
    goal = receipt.get("evidence_cohorts", {}).get("goal_execution", {})
    if goal != {"cohort": genus["boundary_cohort"], "design": candidate,
                "top": top, "source_origin": genus["source_origin"],
                "source_commit": genus["source_commit"],
                "ranking_policy": genus["ranking_policy"]}:
        raise CohortError(f"{candidate} goal execution cohort mismatch")

    inventory = receipt.get("mapped_inventory", {})
    netlist_sha = require_sha(inventory.get("mapped_netlist_sha256"),
                              f"{candidate}:mapped netlist")
    anchors = {
        f"work/{top}_netlist.v": netlist_sha,
        f"work/{top}.sdf": receipt.get("mapped_sdf_sha256"),
        f"work/{top}_out.sdc": receipt.get("mapped_sdc_sha256"),
        "mapped-functional-gate.json": receipt.get("mapped_functional_gate_sha256"),
        "innovus-handoff.json": receipt.get("innovus_handoff_sha256"),
        "endpoint-connectivity-map.json": receipt.get("endpoint_leaf_inventory", {}).get(
            "connectivity_map_sha256"),
    }
    reports = receipt.get("report_sha256", {})
    if set(reports) != {f"{top}_area.rpt", f"{top}_gtiming.rpt", f"{top}_gpower.rpt"}:
        raise CohortError(f"{candidate} exact Genus report set mismatch")
    anchors.update({f"work/{name}": value for name, value in reports.items()})
    for relative, expected in anchors.items():
        check_hash(attempt / relative, expected, f"{candidate}:{relative}")

    endpoint, _ = load_json(attempt / "endpoint-connectivity-map.json")
    leaf = receipt.get("endpoint_leaf_inventory", {})
    if (endpoint.get("schema") != "k2_w2_endpoint_connectivity_map_v1" or
            endpoint.get("design") != candidate or endpoint.get("top") != top or
            endpoint.get("mapped_netlist_sha256") != netlist_sha or
            endpoint.get("leaf_counts") != design["endpoint_leaf_counts"] or
            leaf.get("leaf_counts") != design["endpoint_leaf_counts"]):
        raise CohortError(f"{candidate} endpoint connectivity contract mismatch")
    gate, _ = load_json(attempt / "mapped-functional-gate.json")
    if (gate.get("schema") != "k2_w2_mapped_functional_gate_v1" or
            gate.get("status") != "PASS" or gate.get("design") != candidate or
            gate.get("top") != top or gate.get("mapped_netlist_sha256") != netlist_sha):
        raise CohortError(f"{candidate} mapped functional qualification mismatch")
    handoff, _ = load_json(attempt / "innovus-handoff.json")
    if (handoff.get("schema") != "k2_w2_innovus_strict_sdc_handoff_v1" or
            handoff.get("design") != candidate or handoff.get("top") != top or
            handoff.get("mapped_netlist_sha256") != netlist_sha or
            handoff.get("innovus_consumption_status") !=
            "PENDING_REQUIRES_EXACT_HASH_RECEIPT"):
        raise CohortError(f"{candidate} Innovus handoff mismatch")
    log, _ = stable_read(attempt / "logs/genus.log")
    text = log.decode("utf-8", errors="replace")
    if (f"W2_GENUS_PASS top={top}" not in text or "Normal exit." not in text or
            not re.search(r"Info=\d+, Warn=\d+, Error=0, Fatal=0", text)):
        raise CohortError(f"{candidate} Genus completion log is not qualified")
    return attempt, receipt, {"producer_sha256": genus["producer_sha256"],
                              "flow_files_sha256": flow_files,
                              "flow_git_head": attempt_doc.get("flow_git_head")}


def tree_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in names:
            if (base / name).is_symlink():
                raise CohortError(f"symlink directory in Genus attempt: {base/name}")
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise CohortError(f"non-regular file in Genus attempt: {path}")
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def snapshot_attempt(source: Path, destination: Path,
                     seen_inodes: set[tuple[int, int]]) -> list[dict[str, Any]]:
    before = tree_files(source)
    inventory: list[dict[str, Any]] = []
    for path in before:
        payload, metadata = stable_read(path, nonempty=False)
        inode = (metadata.st_dev, metadata.st_ino)
        if inode in seen_inodes:
            raise CohortError(f"duplicate source inode across cohort: {path}")
        seen_inodes.add(inode)
        relative = path.relative_to(source)
        target = destination / relative
        write_exclusive(target, payload, 0o444)
        inventory.append({"path": relative.as_posix(), "sha256": digest(payload),
                          "size_bytes": len(payload)})
    if [path.relative_to(source) for path in before] != \
            [path.relative_to(source) for path in tree_files(source)]:
        raise CohortError(f"Genus attempt tree changed during snapshot: {source}")
    return inventory


def preserve_failure_logs(root: Path, candidate: str, receipt_path: Path) -> None:
    try:
        source = receipt_path.parent / "logs"
        if not source.is_dir() or source.is_symlink():
            return
        for path in tree_files(source):
            payload, _ = stable_read(path, nonempty=False)
            write_exclusive(root / "failure-source-logs" / candidate /
                            path.relative_to(source), payload)
    except (CohortError, OSError) as error:
        append_log(root, f"FAILURE_LOG_PRESERVE_HOLD candidate={candidate} error={error}")


def create_root(parent: Path, attempt: str) -> Path:
    if not SAFE_ATTEMPT.fullmatch(attempt):
        raise CohortError("unsafe attempt name")
    parent = no_symlink_path(parent, "output parent")
    if not parent.is_dir():
        raise CohortError("output parent is not a directory")
    root = parent / attempt
    root.mkdir(mode=0o755, exist_ok=False)
    return root


def parse_receipts(values: list[str], order: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        candidate, separator, path = value.partition("=")
        if not separator or candidate not in order or candidate in result or not path:
            raise CohortError(f"invalid or duplicate --genus-receipt: {value}")
        result[candidate] = Path(path)
    if list(result) != order:
        raise CohortError("Genus receipts must be supplied exactly once in canonical order")
    return result


def run(args: argparse.Namespace) -> Path:
    contract, contract_payload = load_json(CONTRACT_PATH)
    validate_contract(contract)
    root = create_root(args.output_parent, args.attempt)
    append_log(root, "COHORT_START eda_execution=false")
    current = "environment"
    current_receipt: Path | None = None
    try:
        receipts = parse_receipts(args.genus_receipt, contract["candidate_order"])
        environment_path = no_symlink_path(args.environment_receipt,
                                           "environment receipt")
        environment_payload, _ = validate_environment(environment_path, contract)
        environment_sha = digest(environment_payload)
        write_exclusive(root / "inputs/environment-receipt.json", environment_payload)
        append_log(root, f"ENVIRONMENT_GATE_PASS sha256={environment_sha}")
        common_flow: dict[str, Any] | None = None
        roots: set[Path] = set()
        seen_inodes: set[tuple[int, int]] = set()
        candidate_rows = []
        for index, candidate in enumerate(contract["candidate_order"], 1):
            current = candidate
            current_receipt = receipts[candidate]
            append_log(root, f"CANDIDATE_GATE_START index={index} candidate={candidate}")
            source, receipt, flow = validate_genus_receipt(
                candidate, current_receipt, environment_sha, contract)
            if source in roots:
                raise CohortError("Genus attempt root reused across candidates")
            roots.add(source)
            if common_flow is None:
                common_flow = flow
            elif flow != common_flow:
                raise CohortError("three Genus receipts do not share one exact flow closure")
            inventory = snapshot_attempt(source, root / "genus" / candidate,
                                         seen_inodes)
            packaged_receipt = root / "genus" / candidate / "receipt.json"
            _, _, packaged_flow = validate_genus_receipt(
                candidate, packaged_receipt, environment_sha, contract)
            if packaged_flow != flow:
                raise CohortError(f"{candidate} packaged closure differs after snapshot")
            packaged_payload, _ = stable_read(packaged_receipt)
            inventory_path = f"inventories/{candidate}.json"
            inventory_payload = canonical(inventory)
            write_exclusive(root / inventory_path, inventory_payload)
            row = {"index": index, "candidate": candidate,
                   "top": contract["designs"][candidate]["top"],
                   "source_attempt": str(source),
                   "receipt_sha256": digest(packaged_payload),
                   "artifact_count": len(inventory),
                   "artifact_inventory_path": inventory_path,
                   "artifact_inventory_sha256": digest(inventory_payload),
                   "gate": "PASS_EXACT_GENUS_RECEIPT_AND_ATTEMPT_CLOSURE"}
            candidate_rows.append(row)
            write_exclusive(root / f"gates/{index:02d}-{candidate}.json",
                            canonical(row))
            append_log(root, f"CANDIDATE_GATE_PASS index={index} candidate={candidate}")
        manifest = {
            "schema": "k2_w4_server_cohort_package_v1",
            "status": contract["completion_status"],
            "contract_sha256": digest(contract_payload),
            "environment_receipt_sha256": environment_sha,
            "candidate_order": contract["candidate_order"],
            "candidates": candidate_rows,
            "common_genus_flow": common_flow,
            "eda_launch_performed": False,
            "physical_qualification": contract["physical_qualification"],
            "downstream_rule": "EACH_CANDIDATE_REQUIRES_SEPARATE_INNOVUS_AND_FINAL_QUALIFIER_PASS"
        }
        write_exclusive(root / "cohort-manifest.json", canonical(manifest))
        write_exclusive(root / "cohort-manifest.sha256",
                        (digest(canonical(manifest)) + "  cohort-manifest.json\n").encode())
        append_log(root, "COHORT_PACKAGE_PASS candidates=3 physical_qualification=HOLD")
        return root
    except (CohortError, OSError) as error:
        if current_receipt is not None:
            preserve_failure_logs(root, current, current_receipt)
        append_log(root, f"COHORT_PACKAGE_FAIL stage={current} error={error}")
        failure = {"schema": "k2_w4_server_cohort_failure_v1", "status": "FAIL",
                   "failed_stage": current, "error": str(error),
                   "eda_launch_performed": False,
                   "physical_qualification": "NOT_QUALIFIED"}
        write_exclusive(root / "failure.json", canonical(failure))
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--genus-receipt", action="append", default=[], required=True,
                        metavar="CANDIDATE=PATH")
    parser.add_argument("--output-parent", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args(argv)
    try:
        root = run(args)
    except (CohortError, OSError) as error:
        print(f"K2_W4_SERVER_COHORT_FAIL {error}", file=sys.stderr)
        return 2
    print(f"K2_W4_SERVER_COHORT_PACKAGED root={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
