#!/usr/bin/env python3
"""Extend the A6 same-flow A2/A3 audit with exact A4 K2 evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "audits/a6_a23_k2_p6_same_flow"
BASE_RUNNER = BASE / "run.py"
BASE_REGISTRY = BASE / "registry.json"
REGISTRY = HERE / "registry.json"
WRAPPER = HERE / "a4_wrapper.sv"
BASE_TARGETS = ("a2_k2", "a3_k2", "a2_p6", "a3_p6")


def load_base():
    spec = importlib.util.spec_from_file_location("a6_a23_same_flow", BASE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pinned A6 base runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validate_registry(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise base.AuditError("A4 registry must be a regular non-symlink file")
    document = base.strict_json(path)
    if document.get("schema") != "a6-a234-k2-p6-extension-registry-v1":
        raise base.AuditError("A4 extension registry schema mismatch")
    if document.get("p6", {}).get("a4_p6", "not-null") is not None:
        raise base.AuditError("A4 P6 result must remain null")
    if document.get("p6", {}).get("status") != "HOLD_NO_A4_INTEGRATED_P6_TOP":
        raise base.AuditError("A4 P6 HOLD status mismatch")
    target = document.get("target", {})
    if target.get("key") != "a4_k2":
        raise base.AuditError("A4 target key mismatch")
    if set(target) != {"key", "commit", "top", "expected_modules", "sources"}:
        raise base.AuditError("A4 target fields are incomplete or unexpected")
    if not target["sources"]:
        raise base.AuditError("A4 source inventory is empty")
    return document


def run_a4(document: dict[str, Any], registry_path: Path, yosys: Path,
           environment: dict[str, str], tool: dict[str, str], work: Path
           ) -> dict[str, Any]:
    target = document["target"]
    commit = target["commit"]
    if base.resolve_commit(commit) != commit:
        raise base.AuditError("A4 commit is not an exact full identity")

    source_dir = work / "sources"
    source_dir.mkdir(parents=True, exist_ok=False)
    source_paths: list[Path] = []
    source_rows: list[dict[str, str]] = []
    digests: set[str] = set()
    for index, (path, expected) in enumerate(target["sources"].items()):
        logical = PurePosixPath(path)
        if logical.is_absolute() or ".." in logical.parts or str(logical) != path:
            raise base.AuditError(f"A4 source path is not normalized relative: {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise base.AuditError(f"invalid A4 source SHA syntax: {path}")
        if expected in digests:
            raise base.AuditError(f"duplicate A4 source blob: {path}")
        digests.add(expected)
        data = base.git_bytes(commit, path)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise base.AuditError(f"A4 source SHA mismatch: {path}")
        local = source_dir / f"{index:02d}_{Path(path).name}"
        local.write_bytes(data)
        source_paths.append(local)
        source_rows.append({"path": path, "sha256": actual})
    source_paths.append(WRAPPER)

    metrics, warnings = base.synthesize(
        "a4_k2", target, source_paths, yosys, environment, work / "synthesis")
    if metrics["hierarchy"]["reachable_modules"] != sorted(target["expected_modules"]):
        raise base.AuditError("A4 reachable hierarchy does not close exactly")
    return {
        "schema": "a6-a234-k2-p6-same-flow-result-v1",
        "target": "a4_k2",
        "commit": commit,
        "top": target["top"],
        "boundary": "normalized_atomic_K2_scheduler",
        "included_components": ["scheduler", "A6 normalized observation wrapper"],
        "source_inventory_closed": True,
        "sources": source_rows,
        "source_bundle_sha256": hashlib.sha256(canonical(source_rows)).hexdigest(),
        "registry_sha256": sha256(registry_path),
        "wrapper_sha256": sha256(WRAPPER),
        "extension_runner_sha256": sha256(Path(__file__).resolve()),
        "base_runner_sha256": sha256(BASE_RUNNER),
        "flow": base.FLOW,
        "metrics": metrics,
        "warnings": warnings,
        "p6": {
            "metrics": None,
            "status": document["p6"]["status"],
            "reason": document["p6"]["reason"],
        },
        "tool": tool,
        "qualification": {
            "digital_structural_proxy": "PASS",
            "functional_equivalence": "NOT_TESTED_BY_THIS_RUNNER",
            "physical_ppa": "HOLD_GENERIC_YOSYS_ONLY",
        },
    }


def generate(output: Path, registry_path: Path, yosys: Path,
             environment: dict[str, str], tool: dict[str, str],
             initialize_empty: bool = False) -> None:
    if output.exists():
        if (not initialize_empty or not output.is_dir() or
                any(output.iterdir()) or output.resolve() != (HERE / "results").resolve()):
            raise base.AuditError(f"refusing existing output: {output}")
    document = validate_registry(registry_path)
    with tempfile.TemporaryDirectory(prefix="a6-a234-same-flow-") as text:
        work = Path(text)
        base_output = work / "base-results"
        base.generate(base_output, BASE_REGISTRY, yosys, environment, tool)
        a4 = run_a4(document, registry_path, yosys, environment, tool, work / "a4")

        output.mkdir(parents=True, exist_ok=initialize_empty)
        base_results: dict[str, dict[str, Any]] = {}
        for target in BASE_TARGETS:
            payload = (base_output / f"{target}.json").read_bytes()
            committed = (BASE / "results" / f"{target}.json").read_bytes()
            if payload != committed:
                raise base.AuditError(f"base result changed for {target}")
            (output / f"{target}.json").write_bytes(payload)
            base_results[target] = json.loads(payload)
        (output / "a4_k2.json").write_bytes(canonical(a4))

        summary_targets = {
            key: base_results[key]["metrics"]
            for key in ("a2_k2", "a3_k2")
        }
        summary_targets["a4_k2"] = a4["metrics"]
        summary_p6 = {
            "a2_p6": base_results["a2_p6"]["metrics"],
            "a3_p6": base_results["a3_p6"]["metrics"],
            "a4_p6": None,
        }
        summary = {
            "schema": "a6-a234-k2-p6-same-flow-summary-v1",
            "k2_targets": summary_targets,
            "p6_targets": summary_p6,
            "p6_status": {
                "a2_p6": "DIGITAL_STRUCTURAL_PROXY_PASS",
                "a3_p6": "DIGITAL_STRUCTURAL_PROXY_PASS",
                "a4_p6": document["p6"]["status"],
            },
            "a4_p6_reason": document["p6"]["reason"],
            "comparison_rule": (
                "A2/A3/A4 K2 share one normalized atomic scheduler boundary. "
                "Only A2/A3 have committed integrated P6 tops; A4 P6 is null."
            ),
            "base_result_byte_identity": True,
            "flow": base.FLOW,
            "tool": tool,
            "physical_ppa": "HOLD_GENERIC_YOSYS_ONLY",
        }
        (output / "summary.json").write_bytes(canonical(summary))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--initialize-canonical-empty-dir", action="store_true")
    args = parser.parse_args()
    yosys, environment, tool = base.tool_identity(args.yosys)
    generate(args.output_dir, args.registry, yosys, environment, tool,
             args.initialize_canonical_empty_dir)
    print(f"A6_A234_K2_P6_SAME_FLOW_PASS output={args.output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except base.AuditError as error:
        raise SystemExit(f"A6_A234_K2_P6_SAME_FLOW_FAIL {error}") from error
