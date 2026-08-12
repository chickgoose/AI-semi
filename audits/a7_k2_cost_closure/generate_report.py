#!/usr/bin/env python3
"""Generate fail-closed K2 normalized-plus-P6 structural cost closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "a2_normalized": "audits/a7_k2_same_flow_structural/a2_batched_iwrr.json",
    "a3_normalized": "audits/a7_k2_same_flow_structural/a3_exact_scalar_prefix.json",
    "a2_integration": "audits/a7_k2_cost_closure/receipts/a2_p6_integration.json",
    "a3_integration": "audits/a7_k2_cost_closure/receipts/a3_p6_integration.json",
    "p6_endpoint": "audits/a7_k2_cost_closure/receipts/p6_endpoint.json",
}
METRICS = (
    "generic_cells", "generic_state_bits", "mapped_cells", "mapped_comb_cells",
    "mapped_state_bits", "logic_depth_levels", "fanout_proxy_max",
    "fanout_proxy_p95", "nets_fanout_ge16", "sink_pin_wire_proxy",
)
PARETO_METRICS = (
    "mapped_cells", "mapped_state_bits", "logic_depth_levels",
    "fanout_proxy_max", "fanout_proxy_p95", "nets_fanout_ge16",
    "sink_pin_wire_proxy",
)
FORBIDDEN_PHYSICAL_KEYS = {
    "area", "area_um2", "power", "power_mw", "energy", "energy_pj_per_event",
    "fmax", "fmax_mhz",
}


class ClosureError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=repo, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ClosureError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def committed_json(repo: Path, relative: str, label: str) -> tuple[dict[str, Any], str]:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or str(path) != relative:
        raise ClosureError(f"{label} path is not normalized and repository-relative")
    git(repo, "ls-files", "--error-unmatch", "--", relative)
    local = repo / path
    info = local.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ClosureError(f"{label} must be a regular non-linked committed file")
    payload = local.read_bytes()
    committed = git(repo, "show", f"HEAD:{relative}")
    if payload != committed:
        raise ClosureError(f"{label} is uncommitted or differs from HEAD: {relative}")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClosureError(f"{label} is invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ClosureError(f"{label} must be a JSON object")
    return document, digest(payload)


def exact_metrics(document: dict[str, Any], label: str) -> dict[str, int]:
    metrics = document.get("metrics")
    if not isinstance(metrics, dict):
        raise ClosureError(f"{label} has no metrics object")
    if FORBIDDEN_PHYSICAL_KEYS & set(metrics):
        raise ClosureError(f"{label} fabricates or imports unqualified physical metrics")
    result: dict[str, int] = {}
    for name in METRICS:
        value = metrics.get(name)
        if not isinstance(value, int) or value < 0:
            raise ClosureError(f"{label} metric {name} is missing or invalid")
        result[name] = value
    return result


def same(rows: list[dict[str, Any]], key: str, label: str) -> Any:
    values = [row[key] for row in rows]
    if any(value != values[0] for value in values[1:]):
        raise ClosureError(f"incomparable {label}")
    return values[0]


def validate_normalized(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ordered = [rows["a2"], rows["a3"]]
    for key, row in rows.items():
        if row.get("schema") != "a7_k2_same_flow_structural_v1":
            raise ClosureError(f"{key} normalized receipt schema mismatch")
        if row.get("candidate", {}).get("key") != key:
            raise ClosureError(f"{key} normalized candidate mismatch")
        if row.get("limits", {}).get("physical_ppa") != "HOLD_GENERIC_YOSYS_PROXY_ONLY":
            raise ClosureError(f"{key} normalized receipt lacks physical HOLD")
        exact_metrics(row, f"{key} normalized")
    methods = [row["common_method"] for row in ordered]
    return {
        "boundary": same(methods, "boundary", "normalized top boundary"),
        "flow": same(methods, "flow", "normalized Yosys recipe"),
        "tool": same([row["tool"] for row in ordered], "version", "normalized Yosys tool"),
        "tool_identity": same([row["tool"] for row in ordered],
                              "yosys_executable_sha256", "normalized Yosys executable"),
    }


def validate_integration(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ordered = [rows["a2"], rows["a3"]]
    for key, row in rows.items():
        if row.get("schema") != "a7_k2_p6_integration_cost_v1":
            raise ClosureError(f"{key} integration receipt schema mismatch")
        if row.get("candidate", {}).get("key") != key:
            raise ClosureError(f"{key} integration candidate mismatch")
        limits = row.get("limits", {})
        if (limits.get("physical_ppa") != "HOLD_GENERIC_YOSYS_PROXY_ONLY" or
                limits.get("area") != "UNAVAILABLE_NO_LIBERTY_AREA" or
                limits.get("power") != "UNAVAILABLE_NO_ACTIVITY_OR_POWER_FLOW"):
            raise ClosureError(f"{key} integration receipt lacks fail-closed PPA limits")
        closure = row.get("closure")
        if (not isinstance(closure, dict) or
                closure.get("full_composition_synthesized") is not True or
                closure.get("unlisted_rtl_allowed") is not False):
            raise ClosureError(f"{key} integration source closure is incomplete")
        components = closure.get("components")
        if not isinstance(components, list):
            raise ClosureError(f"{key} integration components are absent")
        by_role = {item.get("role"): item for item in components if isinstance(item, dict)}
        required = {"normalized_scheduler", "integration_adapter", "p6_endpoint"}
        if set(by_role) != required or len(components) != len(required):
            raise ClosureError(f"{key} integration adapter/P6 cost is missing or duplicated")
        for role in required:
            item = by_role[role]
            if item.get("charged") is not True or not isinstance(item.get("identity"), str):
                raise ClosureError(f"{key} {role} is not explicitly charged and identified")
        for role in ("integration_adapter", "p6_endpoint"):
            bits = by_role[role].get("contract_state_bits")
            if not isinstance(bits, int) or bits < 0:
                raise ClosureError(f"{key} {role} state cost is missing")
        exact_metrics(row, f"{key} integration")
    methods = [row["common_method"] for row in ordered]
    return {
        "top": same(methods, "top", "integration top"),
        "boundary": same(methods, "boundary", "integration top boundary"),
        "flow": same(methods, "flow", "integration Yosys recipe"),
        "tool": same([method["tool"] for method in methods], "version",
                     "integration Yosys tool"),
        "tool_identity": same([method["tool"] for method in methods],
                              "yosys_executable_sha256", "integration Yosys executable"),
    }


def validate_p6(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    if row.get("schema") != "a7_k2_p6_endpoint_cost_v1":
        raise ClosureError("isolated P6 receipt schema mismatch")
    component = row.get("component", {})
    if (component.get("commit_sha") != "747db00c0913a0681f482443c66e22a0f75c7373" or
            component.get("contract_state_bits") != 40 or
            not isinstance(component.get("sources"), list) or
            not component["sources"]):
        raise ClosureError("isolated P6 identity or charged state mismatch")
    limits = row.get("limits", {})
    if (limits.get("physical_ppa") != "HOLD_GENERIC_YOSYS_PROXY_ONLY" or
            limits.get("area") != "UNAVAILABLE_NO_LIBERTY_AREA" or
            limits.get("power") != "UNAVAILABLE_NO_ACTIVITY_OR_POWER_FLOW"):
        raise ClosureError("isolated P6 receipt lacks fail-closed PPA limits")
    metrics = exact_metrics(row, "isolated P6")
    if metrics["generic_state_bits"] != 40 or metrics["mapped_state_bits"] != 40:
        raise ClosureError("isolated P6 charged state is absent from its boundary")
    return row["common_method"], metrics


def pareto(rows: dict[str, dict[str, int]]) -> list[str]:
    result = []
    for key, metrics in rows.items():
        dominated = False
        for other_key, other in rows.items():
            if other_key == key:
                continue
            no_worse = all(other[name] <= metrics[name] for name in PARETO_METRICS)
            strictly_better = any(other[name] < metrics[name] for name in PARETO_METRICS)
            if no_worse and strictly_better:
                dominated = True
        if not dominated:
            result.append(key)
    return sorted(result)


def generate(repo: Path, paths: dict[str, str]) -> dict[str, Any]:
    loaded, hashes = {}, {}
    for label, relative in paths.items():
        loaded[label], hashes[label] = committed_json(repo, relative, label)
    normalized = {key: loaded[f"{key}_normalized"] for key in ("a2", "a3")}
    integration = {key: loaded[f"{key}_integration"] for key in ("a2", "a3")}
    p6 = loaded["p6_endpoint"]
    normalized_method = validate_normalized(normalized)
    integration_method = validate_integration(integration)
    p6_method, p6_metrics = validate_p6(p6)
    if normalized_method["flow"].replace("k2_common_boundary", "k2_p6_cost_boundary") != integration_method["flow"]:
        raise ClosureError("normalized and integration receipts use different Yosys recipes")
    if (normalized_method["tool"], normalized_method["tool_identity"]) != (
            integration_method["tool"], integration_method["tool_identity"]):
        raise ClosureError("normalized and integration receipts use different Yosys tools")
    if p6_method["flow"].replace(
            p6_method["top"], integration_method["top"]) != integration_method["flow"]:
        raise ClosureError("isolated P6 and full integration use different Yosys recipes")
    if (p6_method["tool"]["version"],
            p6_method["tool"]["yosys_executable_sha256"]) != (
            integration_method["tool"], integration_method["tool_identity"]):
        raise ClosureError("isolated P6 and integration use different Yosys tools")

    normalized_metrics = {key: exact_metrics(normalized[key], f"{key} normalized")
                          for key in ("a2", "a3")}
    integration_metrics = {key: exact_metrics(integration[key], f"{key} integration")
                           for key in ("a2", "a3")}
    candidates = {}
    for key in ("a2", "a3"):
        components = {item["role"]: item for item in integration[key]["closure"]["components"]}
        delta = {name: integration_metrics[key][name] - normalized_metrics[key][name]
                 for name in METRICS}
        charged_state = (components["integration_adapter"]["contract_state_bits"] +
                         components["p6_endpoint"]["contract_state_bits"])
        if delta["generic_state_bits"] != charged_state or delta["mapped_state_bits"] != charged_state:
            raise ClosureError(f"{key} adapter/P6 charged state is absent from full composition")
        candidates[key] = {
            "normalized_common_seam_metrics": normalized_metrics[key],
            "full_composition_metrics": integration_metrics[key],
            "whole_cone_delta_full_minus_normalized": delta,
            "integration_adapter_seam": {
                "charged_state_bits": components["integration_adapter"]["contract_state_bits"],
                "measured_state_residual_full_minus_normalized_minus_p6": {
                    "generic": (integration_metrics[key]["generic_state_bits"] -
                                normalized_metrics[key]["generic_state_bits"] -
                                p6_metrics["generic_state_bits"]),
                    "mapped": (integration_metrics[key]["mapped_state_bits"] -
                               normalized_metrics[key]["mapped_state_bits"] -
                               p6_metrics["mapped_state_bits"]),
                },
                "combinational_cost": "NOT_ADDITIVELY_ATTRIBUTED_WHOLE_CONE_MAPPING_INTERACTION",
            },
            "semantic_grade": normalized[key]["candidate"]["semantic_grade"],
        }
        residual = candidates[key]["integration_adapter_seam"][
            "measured_state_residual_full_minus_normalized_minus_p6"]
        expected_adapter = components["integration_adapter"]["contract_state_bits"]
        if residual != {"generic": expected_adapter, "mapped": expected_adapter}:
            raise ClosureError(f"{key} isolated adapter state boundary does not close")

    return {
        "schema": "a7_k2_cost_closure_report_v1",
        "status": "STRUCTURAL_PROXY_COMPLETE_PHYSICAL_HOLD",
        "inputs": {label: {"path": paths[label], "sha256": hashes[label]}
                   for label in sorted(paths)},
        "comparability": {
            "normalized": normalized_method, "full_p6": integration_method,
            "isolated_p6": {
                "top": p6_method["top"], "boundary": p6_method["boundary"],
                "flow": p6_method["flow"],
                "tool": p6_method["tool"]["version"],
                "tool_identity": p6_method["tool"]["yosys_executable_sha256"],
            },
            "same_recipe_and_tool": True,
            "top_boundary_rule": "identical within each normalized/full-P6 cohort",
        },
        "candidates": candidates,
        "isolated_p6_seam": {
            "metrics": p6_metrics,
            "charged_state_bits": 40,
            "shared_identically_by": ["a2", "a3"],
        },
        "pareto": {
            "normalized": pareto(normalized_metrics),
            "full_p6": pareto(integration_metrics),
            "metrics_lower_is_better": list(PARETO_METRICS),
        },
        "physical_metrics": {
            "area": None, "power": None, "energy_per_event": None, "fmax": None,
            "status": "HOLD_NO_LIBERTY_STA_ACTIVITY_OR_POWER_RECEIPT",
        },
        "interpretation": (
            "Boundary-specific headline comparison: at the normalized common seam, "
            "A2 has lower mapped state (22 < 26), while at the full-P6 boundary A3 "
            "has lower mapped state (66 < 73). Within the full-P6 headline set, "
            "A2's only win is maximum fanout (15 < 31). "
            "Full-minus-normalized values are whole-cone generic structural deltas. "
            "They charge all adapter/P6 state but are not additive physical area or power."
        ),
    }


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    for name, default in DEFAULTS.items():
        parser.add_argument("--" + name.replace("_", "-"), default=default)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in DEFAULTS}
    report = generate(args.repo_root.resolve(), paths)
    write_new(args.output, canonical(report))
    print("A7_K2_COST_CLOSURE_PASS candidates=2 physical=HOLD")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ClosureError, OSError) as error:
        raise SystemExit(f"A7_K2_COST_CLOSURE_FAIL {error}") from error
