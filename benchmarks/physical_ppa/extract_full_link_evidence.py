#!/usr/bin/env python3
"""Extract canonical physical/activity evidence from frozen raw reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence


FIELD_TYPES = {
    "area": {"mapped_cell_count": int, "area_um2": float},
    "stage": {"pipeline_stage_count": int},
    "setup": {"setup_wns_ns": float},
    "hold": {"hold_wns_ns": float},
    "route": {"detailed_route_completed": bool},
    "elaboration": {"unresolved_references": int},
    "unconstrained": {"unconstrained_paths": int},
    "drc": {"drc_violations": int},
    "activity": {
        "candidate_id": str, "test_id": str, "seed": int,
        "hierarchy_root": str, "format": str, "coverage_percent": float,
        "clock_port": str, "clock_period_ns": float, "clock_mhz": float,
        "window_start_cycle": int, "window_end_cycle_exclusive": int,
        "measurement_cycles": int,
    },
    "power": {
        "candidate_id": str, "test_id": str, "seed": int,
        "measurement_cycles": int, "clock_port": str,
        "clock_period_ns": float, "clock_mhz": float,
        "average_power_mw": float, "errors": int,
    },
    "common_result": {
        "candidate_id": str, "test_id": str, "seed": int,
        "measurement_cycles": int, "delivered_events": int, "errors": int,
    },
}


class EvidenceError(ValueError):
    """Raised when raw evidence is incomplete or noncanonical."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_raw(evidence_type: str, raw_data: bytes) -> dict[str, Any]:
    fields = FIELD_TYPES.get(evidence_type)
    if fields is None:
        raise EvidenceError(f"unsupported evidence type {evidence_type!r}")
    try:
        lines = raw_data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EvidenceError("raw report must be UTF-8") from exc
    raw_values: dict[str, str] = {}
    for index, line in enumerate(lines, 1):
        if not line or line != line.strip() or line.count("=") != 1:
            raise EvidenceError(f"raw report line {index} is not canonical key=value")
        key, value = line.split("=", 1)
        if key in raw_values:
            raise EvidenceError(f"raw report repeats {key!r}")
        raw_values[key] = value
    if set(raw_values) != set(fields):
        raise EvidenceError(
            f"raw report fields mismatch: missing={sorted(set(fields)-set(raw_values))!r}, "
            f"extra={sorted(set(raw_values)-set(fields))!r}"
        )
    result: dict[str, Any] = {}
    for key, converter in fields.items():
        value = raw_values[key]
        try:
            if converter is bool:
                if value not in {"true", "false"}:
                    raise ValueError
                parsed: Any = value == "true"
            else:
                parsed = converter(value)
        except ValueError as exc:
            raise EvidenceError(f"raw report field {key!r} has invalid value") from exc
        if isinstance(parsed, float) and not math.isfinite(parsed):
            raise EvidenceError(f"raw report field {key!r} must be finite")
        result[key] = parsed
    return result


SDC_CLOCK_RE = re.compile(
    r"^create_clock\s+-name\s+(\S+)\s+-period\s+([0-9]+(?:\.[0-9]+)?)\s+"
    r"\[get_ports\s+([A-Za-z_$][\w$]*)\]\s*$"
)
SDC_CLOCK_COMMAND_RE = re.compile(r"(?<![A-Za-z0-9_$])create_(?:generated_)?clock\b")


def parse_sdc_clock(raw_data: bytes) -> dict[str, Any]:
    try:
        decoded = raw_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("SDC must be UTF-8") from exc
    # The qualification contract accepts one deliberately narrow clock form.
    # Scan all uncommented text first so an additional/noncanonical clock
    # command cannot disappear merely because it fails the canonical regex.
    lines = [line.split("#", 1)[0].strip() for line in decoded.splitlines()]
    lines = [line for line in lines if line]
    clock_commands = SDC_CLOCK_COMMAND_RE.findall("\n".join(lines))
    if len(clock_commands) != 1 or clock_commands[0] != "create_clock":
        raise EvidenceError(
            "SDC must contain exactly one create_clock command and no "
            "create_generated_clock command"
        )
    matches = [SDC_CLOCK_RE.fullmatch(line) for line in lines]
    clocks = [match for match in matches if match is not None]
    if len(clocks) != 1:
        raise EvidenceError("SDC must contain exactly one canonical create_clock")
    name, period, port = clocks[0].groups()
    parsed_period = float(period)
    if parsed_period <= 0 or not math.isfinite(parsed_period):
        raise EvidenceError("SDC clock period must be finite and positive")
    return {"clock_name": name, "clock_port": port, "clock_period_ns": parsed_period}


def produce_evidence(
    *, evidence_type: str, raw_data: bytes, raw_path: str,
    flow_manifest: dict[str, str],
    context_inputs: list[tuple[str, dict[str, str]]], output_path: str,
    extractor_sha256: str,
) -> dict[str, Any]:
    inputs = [
        {"role": "raw_report", "path": raw_path, "sha256": sha256(raw_data)},
        {"role": "flow_manifest", **flow_manifest},
    ]
    inputs.extend({"role": role, **reference} for role, reference in context_inputs)
    command = [
        "python3", "extract_full_link_evidence.py", "--type", evidence_type,
        "--raw-report", raw_path, "--flow-manifest", flow_manifest["path"],
    ]
    for role, reference in context_inputs:
        command.extend(["--bind", role, reference["path"], reference["sha256"]])
    command.extend(["--output", output_path])
    return {
        "schema_version": 1,
        "evidence_type": evidence_type,
        "producer": {
            "tool": "a8-flow-owned-full-link-evidence",
            "extractor_sha256": extractor_sha256,
            "command": command,
            "inputs": inputs,
        },
        "values": parse_raw(evidence_type, raw_data),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", required=True, choices=sorted(FIELD_TYPES))
    parser.add_argument("--raw-report", required=True)
    parser.add_argument("--flow-manifest", required=True)
    parser.add_argument("--bind", action="append", nargs=3, default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    raw_path = Path(args.raw_report)
    try:
        context_inputs = []
        for role, path, digest in args.bind:
            actual = sha256(Path(path).read_bytes())
            if actual != digest.lower():
                raise EvidenceError(
                    f"context input {role!r} digest mismatch ({digest!r} != {actual})"
                )
            context_inputs.append((role, {"path": path, "sha256": digest.lower()}))
        value = produce_evidence(
            evidence_type=args.type,
            raw_data=raw_path.read_bytes(),
            raw_path=args.raw_report,
            flow_manifest={
                "path": args.flow_manifest,
                "sha256": sha256(Path(args.flow_manifest).read_bytes()),
            },
            context_inputs=context_inputs,
            output_path=args.output,
            extractor_sha256=sha256(Path(__file__).read_bytes()),
        )
        Path(args.output).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, EvidenceError) as exc:
        print(f"NOT_EXTRACTED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
