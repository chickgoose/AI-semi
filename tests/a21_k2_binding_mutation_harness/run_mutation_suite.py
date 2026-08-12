#!/usr/bin/env python3
"""Run all required mutations against registered black-box K2 wrappers."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from oracle import BindingViolation, TraceContractError, validate_trace
from vectors import MUTATIONS, vector_for


ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "bindings.json"
REGISTRY_SCHEMA = "a21_k2_binding_registry_v1"
PLACEHOLDERS = {"python", "root", "binding", "mutation", "stimulus", "output"}


def load_registry(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TraceContractError(f"cannot read binding registry: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != REGISTRY_SCHEMA:
        raise TraceContractError(f"binding registry schema must be {REGISTRY_SCHEMA}")
    rows = document.get("bindings")
    if not isinstance(rows, list) or not rows:
        raise TraceContractError("binding registry must not be empty")
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "name", "owner_offer", "drain_observation", "runner"
        }:
            raise TraceContractError("binding registry fields mismatch")
        if not isinstance(row["name"], str) or not row["name"] or row["name"] in names:
            raise TraceContractError("binding names must be unique nonempty strings")
        names.add(row["name"])
        if not isinstance(row["runner"], list) or not row["runner"] or any(
            not isinstance(token, str) or not token for token in row["runner"]
        ):
            raise TraceContractError(f"{row['name']}: runner must be a token array")
        for token in row["runner"]:
            for fragment in token.split("{")[1:]:
                placeholder = fragment.split("}", 1)[0]
                if placeholder not in PLACEHOLDERS:
                    raise TraceContractError(f"{row['name']}: unknown runner placeholder {placeholder}")
    return rows


def expand_runner(
    binding: dict[str, Any], mutation: str, stimulus: Path, output: Path
) -> list[str]:
    values = {
        "python": sys.executable,
        "root": str(ROOT),
        "binding": binding["name"],
        "mutation": mutation,
        "stimulus": str(stimulus),
        "output": str(output),
    }
    return [token.format(**values) for token in binding["runner"]]


def execute_black_box(
    binding: dict[str, Any], mutation: str, stimulus: dict[str, Any], work: Path,
    *, case_name: str | None = None,
) -> dict[str, Any]:
    case_dir = work / binding["name"] / (case_name or mutation)
    case_dir.mkdir(parents=True, exist_ok=False)
    stimulus_path = case_dir / "stimulus.json"
    output_path = case_dir / "observations.json"
    stimulus_path.write_text(json.dumps(stimulus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "0",
    }
    process = subprocess.run(
        expand_runner(binding, mutation, stimulus_path, output_path),
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise TraceContractError(
            f"{binding['name']}/{mutation}: runner exit={process.returncode} "
            f"stderr={process.stderr.strip()}"
        )
    if not output_path.is_file():
        raise TraceContractError(f"{binding['name']}/{mutation}: runner produced no trace")
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TraceContractError(f"{binding['name']}/{mutation}: invalid runner JSON") from error


def run_suite(registry_path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    bindings = load_registry(registry_path)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="a21-k2-mutations-") as temporary:
        work = Path(temporary)
        for binding in bindings:
            # Each case has its own clean control.  This prevents a mutant from
            # being credited when its directed stimulus is itself invalid.
            for mutation, diagnostic in MUTATIONS.items():
                stimulus = vector_for(mutation)
                clean = execute_black_box(
                    binding, "none", stimulus, work, case_name=f"clean-{mutation}"
                )
                validate_trace(stimulus, clean, binding["name"])
                mutant = execute_black_box(binding, mutation, stimulus, work)
                try:
                    validate_trace(stimulus, mutant, binding["name"])
                except BindingViolation as error:
                    if error.code != diagnostic:
                        raise TraceContractError(
                            f"{binding['name']}/{mutation}: expected diagnostic={diagnostic} "
                            f"actual={error.code}"
                        ) from error
                    rows.append({
                        "binding": binding["name"],
                        "mutation": mutation,
                        "diagnostic": error.code,
                        "cycle": error.cycle,
                        "status": "KILLED",
                    })
                else:
                    raise TraceContractError(f"{binding['name']}/{mutation}: mutant survived")
    return {
        "schema": "a21_k2_binding_mutation_report_v1",
        "bindings": [binding["name"] for binding in bindings],
        "mutations_per_binding": len(MUTATIONS),
        "killed": len(rows),
        "results": rows,
        "oracle": "independent_flattened_global_accept_retire_order",
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = run_suite(args.registry)
    except (TraceContractError, OSError) as error:
        print(f"A21_K2_BINDING_MUTATION_FAIL {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"A21_K2_BINDING_MUTATION_PASS bindings={len(report['bindings'])} "
        f"killed={report['killed']} mutations_per_binding={report['mutations_per_binding']} "
        "oracle=flattened-global"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
