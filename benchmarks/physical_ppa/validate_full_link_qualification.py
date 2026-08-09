#!/usr/bin/env python3
"""Validate address-only AER full-link qualification records.

The JSON Schema documents the interchange format. This dependency-free
validator enforces the cross-field accounting invariants that JSON Schema
cannot express conveniently.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
FREE_OPERATIONS = {
    "rename",
    "bit_permutation",
    "slice",
    "concatenation",
    "constant_tie",
    "zero_extension",
}
EXCLUDED_PIN_ROLES = {"clock", "reset", "power", "ground"}
PIN_DIRECTIONS = {"input", "output", "bidirectional"}


class QualificationError(ValueError):
    """Raised when a record is incomplete or violates an accounting invariant."""


def _mapping(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _array(value: Any, path: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    return value


def _require(obj: dict[str, Any], fields: Sequence[str], path: str,
             errors: list[str]) -> None:
    for field in fields:
        if field not in obj:
            errors.append(f"{path}.{field} is required")


def _positive_int(value: Any, path: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{path} must be a positive integer")
        return 0
    return value


def _finite_nonnegative(value: Any, path: str, errors: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a finite nonnegative number")
        return 0.0
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        errors.append(f"{path} must be a finite nonnegative number")
        return 0.0
    return result


def _check_sha(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"{path} must be a 64-digit hexadecimal SHA-256")


def _pin_count(value: Any, path: str, errors: list[str]) -> int:
    pins = _array(value, path, errors)
    if not pins:
        errors.append(f"{path} must not be empty")
        return 0
    names: set[str] = set()
    count = 0
    for index, item in enumerate(pins):
        pin_path = f"{path}[{index}]"
        pin = _mapping(item, pin_path, errors)
        _require(pin, ("name", "direction", "width", "role"), pin_path, errors)
        name = pin.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{pin_path}.name must be a nonempty string")
        elif name in names:
            errors.append(f"{path} contains duplicate pin name {name!r}")
        else:
            names.add(name)
        if pin.get("direction") not in PIN_DIRECTIONS:
            errors.append(f"{pin_path}.direction is invalid")
        width = _positive_int(pin.get("width"), f"{pin_path}.width", errors)
        role = pin.get("role")
        if role not in EXCLUDED_PIN_ROLES | {"functional"}:
            errors.append(f"{pin_path}.role is invalid")
        if role == "functional":
            count += width
    return count


def _same_number(actual: Any, expected: float, path: str,
                 errors: list[str]) -> None:
    value = _finite_nonnegative(actual, path, errors)
    if not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12):
        errors.append(f"{path}={value:.12g}, expected {expected:.12g}")


def validate_record(record: Any) -> dict[str, float | int | str]:
    """Validate one parsed record and return independently computed metrics."""

    errors: list[str] = []
    root = _mapping(record, "$", errors)
    _require(
        root,
        (
            "schema_version", "qualification_id", "status", "candidate",
            "logical_contract", "tb_seam", "physical_boundary",
            "normalization", "charged_blocks", "flow", "activity", "metrics",
        ),
        "$",
        errors,
    )
    if root.get("schema_version") != 1:
        errors.append("$.schema_version must equal 1")
    if root.get("status") not in {"freeze_candidate", "frozen"}:
        errors.append("$.status must be freeze_candidate or frozen")

    candidate = _mapping(root.get("candidate"), "$.candidate", errors)
    _require(
        candidate,
        ("id", "repo_url", "commit_sha", "bundle_sha256", "synthesis_top",
         "filelist_sha256", "parameters", "defines", "include_dirs"),
        "$.candidate",
        errors,
    )
    commit = candidate.get("commit_sha")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        errors.append("$.candidate.commit_sha must be a 7-64 digit hexadecimal commit")
    for field in ("bundle_sha256", "filelist_sha256"):
        _check_sha(candidate.get(field), f"$.candidate.{field}", errors)

    logical = _mapping(root.get("logical_contract"), "$.logical_contract", errors)
    _require(
        logical,
        ("event_identity_mode", "source_count", "source_mapping",
         "one_pending_latch_per_source", "acceptance_rule", "delivery_rule"),
        "$.logical_contract",
        errors,
    )
    if logical.get("event_identity_mode") != "address_only":
        errors.append("$.logical_contract.event_identity_mode must be address_only")
    source_count = _positive_int(
        logical.get("source_count"), "$.logical_contract.source_count", errors
    )
    if logical.get("one_pending_latch_per_source") is not True:
        errors.append("$.logical_contract.one_pending_latch_per_source must be true")
    source_mapping = _mapping(
        logical.get("source_mapping"), "$.logical_contract.source_mapping", errors
    )
    if source_mapping.get("bijective") is not True:
        errors.append("$.logical_contract.source_mapping.bijective must be true")
    _check_sha(
        source_mapping.get("sha256"),
        "$.logical_contract.source_mapping.sha256",
        errors,
    )

    seam = _mapping(root.get("tb_seam"), "$.tb_seam", errors)
    if seam.get("ppa_excluded") is not True:
        errors.append("$.tb_seam.ppa_excluded must be true")
    if seam.get("arbitrary_payload") is not False:
        errors.append("$.tb_seam.arbitrary_payload must be false")
    if seam.get("tb_only_event_id_in_dut") is not False:
        errors.append("$.tb_seam.tb_only_event_id_in_dut must be false")
    addr_width = _positive_int(
        seam.get("normalized_addr_width"), "$.tb_seam.normalized_addr_width", errors
    )
    source_width = _positive_int(
        seam.get("normalized_source_width"),
        "$.tb_seam.normalized_source_width",
        errors,
    )
    minimum_source_width = max(1, (source_count - 1).bit_length())
    if source_width < minimum_source_width:
        errors.append(
            "$.tb_seam.normalized_source_width cannot represent every source"
        )
    if addr_width < source_width:
        errors.append("$.tb_seam.normalized_addr_width is narrower than source identity")

    boundary = _mapping(
        root.get("physical_boundary"), "$.physical_boundary", errors
    )
    if boundary.get("scope") != "full_link_tx_link_rx":
        errors.append("$.physical_boundary.scope must be full_link_tx_link_rx")
    for field in ("includes_tx", "includes_link", "includes_rx"):
        if boundary.get(field) is not True:
            errors.append(f"$.physical_boundary.{field} must be true")
    native_bits = _pin_count(
        boundary.get("native_boundary_pins"),
        "$.physical_boundary.native_boundary_pins",
        errors,
    )
    if boundary.get("native_functional_pin_bits") != native_bits:
        errors.append(
            "$.physical_boundary.native_functional_pin_bits does not match pin list "
            f"({boundary.get('native_functional_pin_bits')!r} != {native_bits})"
        )
    encoding = _mapping(
        boundary.get("link_encoding"), "$.physical_boundary.link_encoding", errors
    )
    if not isinstance(encoding.get("requires_runtime_decode"), bool):
        errors.append(
            "$.physical_boundary.link_encoding.requires_runtime_decode must be boolean"
        )
    link_cut = _mapping(
        boundary.get("link_cut"), "$.physical_boundary.link_cut", errors
    )
    if link_cut.get("count_each_signal_once") is not True:
        errors.append("$.physical_boundary.link_cut.count_each_signal_once must be true")
    link_bits = _pin_count(
        link_cut.get("pins"), "$.physical_boundary.link_cut.pins", errors
    )
    if link_cut.get("functional_pin_bits") != link_bits:
        errors.append(
            "$.physical_boundary.link_cut.functional_pin_bits does not match pin list "
            f"({link_cut.get('functional_pin_bits')!r} != {link_bits})"
        )

    normalization = _mapping(
        root.get("normalization"), "$.normalization", errors
    )
    if normalization.get("runtime_decode_in_tb") is not False:
        errors.append("$.normalization.runtime_decode_in_tb must be false")
    if normalization.get("uses_pending_to_disambiguate") is not False:
        errors.append("$.normalization.uses_pending_to_disambiguate must be false")
    for index, item in enumerate(
        _array(normalization.get("free_wiring"), "$.normalization.free_wiring", errors)
    ):
        mapping = _mapping(item, f"$.normalization.free_wiring[{index}]", errors)
        if mapping.get("operation") not in FREE_OPERATIONS:
            errors.append(
                f"$.normalization.free_wiring[{index}].operation is not free wiring"
            )

    block_kinds: set[str] = set()
    blocks = _array(root.get("charged_blocks"), "$.charged_blocks", errors)
    for index, item in enumerate(blocks):
        path = f"$.charged_blocks[{index}]"
        block = _mapping(item, path, errors)
        block_kinds.add(str(block.get("kind")))
        _check_sha(block.get("filelist_sha256"), f"{path}.filelist_sha256", errors)
        for field in (
            "included_in_area", "included_in_timing", "included_in_activity",
            "included_in_power",
        ):
            if block.get(field) is not True:
                errors.append(f"{path}.{field} must be true")
    for required_kind in ("tx", "rx"):
        if required_kind not in block_kinds:
            errors.append(f"$.charged_blocks must include a charged {required_kind} block")
    if encoding.get("requires_runtime_decode") is True:
        for required_kind in ("encoder", "decoder"):
            if required_kind not in block_kinds:
                errors.append(
                    "runtime link encoding requires charged encoder and decoder blocks"
                )

    flow = _mapping(root.get("flow"), "$.flow", errors)
    for field in (
        "tool_config_sha256", "sdc_sha256", "library_sha256",
        "post_elaboration_report_sha256",
    ):
        _check_sha(flow.get(field), f"$.flow.{field}", errors)
    if flow.get("unresolved_references") != 0:
        errors.append("$.flow.unresolved_references must equal 0")

    activity = _mapping(root.get("activity"), "$.activity", errors)
    for field in ("trace_sha256", "prepared_input_sha256", "activity_sha256"):
        _check_sha(activity.get(field), f"$.activity.{field}", errors)
    start = activity.get("window_start_cycle")
    end = activity.get("window_end_cycle_exclusive")
    cycles = _positive_int(
        activity.get("measurement_cycles"), "$.activity.measurement_cycles", errors
    )
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        errors.append("$.activity.window_start_cycle must be a nonnegative integer")
    if not isinstance(end, int) or isinstance(end, bool) or end <= 0:
        errors.append("$.activity.window_end_cycle_exclusive must be positive")
    if isinstance(start, int) and isinstance(end, int) and end - start != cycles:
        errors.append("$.activity.measurement_cycles must equal end_cycle - start_cycle")
    clock_mhz = _finite_nonnegative(
        activity.get("clock_mhz"), "$.activity.clock_mhz", errors
    )
    if clock_mhz <= 0.0:
        errors.append("$.activity.clock_mhz must be positive")
    delivered = _positive_int(
        activity.get("delivered_events"), "$.activity.delivered_events", errors
    )
    power_mw = _finite_nonnegative(
        activity.get("average_power_mw"), "$.activity.average_power_mw", errors
    )
    if root.get("status") == "frozen" and activity.get("power_evidence") != "activity_annotated":
        errors.append("a frozen ranked record requires activity_annotated power evidence")

    computed_events_per_cycle = delivered / cycles if cycles else 0.0
    computed_native = (
        delivered / (cycles * native_bits) if cycles and native_bits else 0.0
    )
    computed_link = (
        delivered / (cycles * link_bits) if cycles and link_bits else 0.0
    )
    computed_energy = (
        power_mw / (clock_mhz * computed_events_per_cycle)
        if clock_mhz and computed_events_per_cycle
        else 0.0
    )
    metrics = _mapping(root.get("metrics"), "$.metrics", errors)
    _same_number(
        metrics.get("events_per_cycle"),
        computed_events_per_cycle,
        "$.metrics.events_per_cycle",
        errors,
    )
    _same_number(
        metrics.get("events_per_native_pin_cycle"),
        computed_native,
        "$.metrics.events_per_native_pin_cycle",
        errors,
    )
    _same_number(
        metrics.get("events_per_link_pin_cycle"),
        computed_link,
        "$.metrics.events_per_link_pin_cycle",
        errors,
    )
    _same_number(
        metrics.get("energy_nj_per_delivered_event"),
        computed_energy,
        "$.metrics.energy_nj_per_delivered_event",
        errors,
    )

    if errors:
        raise QualificationError("\n".join(errors))
    return {
        "qualification_id": str(root.get("qualification_id")),
        "native_functional_pin_bits": native_bits,
        "link_functional_pin_bits": link_bits,
        "events_per_cycle": computed_events_per_cycle,
        "events_per_native_pin_cycle": computed_native,
        "events_per_link_pin_cycle": computed_link,
        "energy_nj_per_delivered_event": computed_energy,
    }


def read_record(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read {path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit computed JSON")
    args = parser.parse_args(argv)

    output: list[dict[str, float | int | str]] = []
    failed = False
    for path in args.records:
        try:
            output.append(validate_record(read_record(path)))
        except QualificationError as exc:
            failed = True
            print(f"{path}: NOT_QUALIFIED\n{exc}", file=sys.stderr)
    if failed:
        return 2
    if args.json:
        json.dump(output, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        for row in output:
            print(f"{row['qualification_id']}: QUALIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
