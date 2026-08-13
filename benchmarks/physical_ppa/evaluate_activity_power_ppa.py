#!/usr/bin/env python3
"""Fail-closed W2 activity-power/Fmax/PPA comparison evaluator.

The evaluator authenticates every referenced byte, derives every metric and
decision, and keeps vectorless, core-only, and full-endpoint rows in distinct
cohorts.  Production evidence additionally requires an out-of-band registry;
committed synthetic fixtures can therefore exercise the evaluator but cannot
publish a candidate GO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

import validate_full_link_qualification as schema_support


SCHEMA_PATH = Path(__file__).with_name("activity_power_ppa_comparison.schema.json")
MINIMUM_COVERAGE_PERCENT = 95.0
TEST_ORIGIN = "TEST_ONLY_NOT_RTL_EVIDENCE"
REQUIRED_OPERATING_POINTS = {"sparse", "near_saturation", "loss"}
FUNCTIONAL_ROLE = "functional"
# No repository-owned production extractor/authority is frozen yet.  This
# constant deliberately has no CLI override: measured inputs remain HOLD.
PRODUCTION_PUBLICATION_ENABLED = False


class ComparisonError(ValueError):
    """Raised when comparison evidence is incomplete or cannot be trusted."""


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _same_number(left: Any, right: float) -> bool:
    return (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and math.isfinite(float(left))
        and math.isclose(float(left), right, rel_tol=1e-12, abs_tol=1e-12)
    )


def _exact_keys(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComparisonError(f"{label} must be an object")
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required)
    if missing or extra:
        raise ComparisonError(f"{label} keys mismatch: missing={missing}, extra={extra}")
    return value


class ArtifactReader:
    """Read normalized regular files under one evidence root without symlinks."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cache: dict[tuple[str, str], bytes] = {}

    def read(self, reference: Any, label: str) -> bytes:
        ref = _exact_keys(reference, {"path", "sha256"}, label)
        raw_path, digest = ref["path"], ref["sha256"]
        if not isinstance(raw_path, str) or not raw_path:
            raise ComparisonError(f"{label}.path must be a nonempty string")
        relative = Path(raw_path)
        if (
            relative.is_absolute()
            or "\\" in raw_path
            or relative.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ComparisonError(f"{label}.path must be normalized and relative")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ComparisonError(f"{label}.sha256 must be 64 lowercase hex digits")
        key = (raw_path, digest)
        if key in self.cache:
            return self.cache[key]
        current = self.root
        for part in relative.parts:
            current /= part
            try:
                info = os.lstat(current)
            except OSError as exc:
                raise ComparisonError(f"{label}.path cannot be resolved: {exc}") from exc
            if stat.S_ISLNK(info.st_mode):
                raise ComparisonError(f"{label}.path traverses a symlink")
        try:
            before = os.stat(current, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ComparisonError(f"{label}.path must be a single-link regular file")
            data = current.read_bytes()
            after = os.stat(current, follow_symlinks=False)
        except OSError as exc:
            raise ComparisonError(f"{label}.path cannot be read: {exc}") from exc
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ComparisonError(f"{label}.path changed while being read")
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise ComparisonError(f"{label}.sha256 digest mismatch ({digest} != {actual})")
        self.cache[key] = data
        return data

    def json(self, reference: Any, label: str) -> dict[str, Any]:
        try:
            value = json.loads(self.read(reference, label).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComparisonError(f"{label} must be UTF-8 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ComparisonError(f"{label} content must be an object")
        return value


def _validate_schema(record: Any) -> None:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read comparison schema: {exc}") from exc
    errors: list[str] = []
    schema_support._validate_against_schema(record, schema, schema, "$", errors)
    if errors:
        raise ComparisonError("\n".join(errors))


def _scope_hash(scope_manifest: dict[str, Any], scope_root: str) -> str:
    manifest = _exact_keys(scope_manifest, {"schema_version", "scope_root", "objects"}, "scope manifest")
    if manifest["schema_version"] != 1 or manifest["scope_root"] != scope_root:
        raise ComparisonError("scope manifest version/root mismatch")
    objects = manifest["objects"]
    if not isinstance(objects, list) or not objects:
        raise ComparisonError("scope manifest objects must be a nonempty array")
    normalized = []
    for index, item in enumerate(objects):
        row = _exact_keys(item, {"path", "bits"}, f"scope object {index}")
        if not isinstance(row["path"], str) or not row["path"]:
            raise ComparisonError("scope object path must be nonempty")
        if not row["path"].startswith(scope_root + "."):
            raise ComparisonError("scope object path must be below scope_root")
        if not isinstance(row["bits"], int) or isinstance(row["bits"], bool) or row["bits"] <= 0:
            raise ComparisonError("scope object bits must be positive")
        normalized.append({"path": row["path"], "bits": row["bits"]})
    if len({item["path"] for item in normalized}) != len(normalized):
        raise ComparisonError("scope manifest repeats an object path")
    return canonical_sha256({"scope_root": scope_root, "objects": sorted(normalized, key=lambda x: x["path"])})


def _scope_bits(scope_manifest: dict[str, Any]) -> int:
    return sum(item["bits"] for item in scope_manifest["objects"])


def _pin_bits(pin_manifest: dict[str, Any]) -> tuple[int, dict[str, int]]:
    manifest = _exact_keys(pin_manifest, {"schema_version", "pins"}, "pin inventory")
    if manifest["schema_version"] != 1 or not isinstance(manifest["pins"], list):
        raise ComparisonError("pin inventory must be schema version 1 with a pins array")
    totals = {"input": 0, "output": 0, "bidirectional": 0}
    names: set[str] = set()
    for index, item in enumerate(manifest["pins"]):
        pin = _exact_keys(item, {"name", "direction", "width", "role"}, f"pin {index}")
        if pin["name"] in names:
            raise ComparisonError("pin inventory repeats a pin name")
        names.add(pin["name"])
        if pin["direction"] not in totals or pin["role"] not in {
            "functional", "clock", "reset", "power", "ground"
        }:
            raise ComparisonError("pin inventory contains an invalid direction or role")
        if not isinstance(pin["width"], int) or isinstance(pin["width"], bool) or pin["width"] <= 0:
            raise ComparisonError("pin width must be positive")
        if pin["role"] == FUNCTIONAL_ROLE:
            totals[pin["direction"]] += pin["width"]
    functional = sum(totals.values())
    if functional <= 0:
        raise ComparisonError("pin inventory has no functional bits")
    return functional, totals


def window_binding(row: dict[str, Any]) -> dict[str, Any]:
    activity, workload, flow = row["activity"], row["workload"], row["flow"]
    return {
        "trace_sha256": workload["trace"]["sha256"],
        "waveform_sha256": activity["waveform"]["sha256"],
        "workload_id": workload["id"],
        "test_id": workload["test_id"],
        "seed": workload["seed"],
        "clock_port": flow["clock_port"],
        "clock_period_ns": flow["clock_period_ns"],
        "window_start_cycle": activity["window_start_cycle"],
        "window_end_cycle_exclusive": activity["window_end_cycle_exclusive"],
        "warmup_cycles": workload["warmup_cycles"],
        "drain_policy": workload["drain_policy"],
    }


def _validate_waveform(
    data: bytes, fmt: str, scope_root: str,
    scope_manifest: dict[str, Any], end_cycle: int,
    clock_period_ns: float, annotated_bits: int,
) -> str:
    scope_hash = _scope_hash(scope_manifest, scope_root)
    if _scope_bits(scope_manifest) != annotated_bits:
        raise ComparisonError(
            "scope manifest bits do not match annotated coverage numerator"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ComparisonError(f"{fmt} activity must be UTF-8 for canonical import") from exc
    if fmt == "vcd":
        if "$timescale 1ns $end" not in text:
            raise ComparisonError("canonical VCD must declare $timescale 1ns $end")
        scopes = re.findall(r"\$scope\s+module\s+(\S+)\s+\$end", text)
        if scope_root not in scopes:
            raise ComparisonError("VCD does not contain the bound scope_root")
        widths = [int(value) for value in re.findall(r"\$var\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\$end", text)]
        timestamps = [int(value) for value in re.findall(r"(?m)^#(\d+)\s*$", text)]
        if "$enddefinitions $end" not in text or not widths or not timestamps:
            raise ComparisonError("activity artifact is not a complete canonical VCD file")
        if sum(widths) != annotated_bits:
            raise ComparisonError("VCD variable bits do not match annotated coverage numerator")
        duration_ns = max(timestamps)
    elif fmt == "saif":
        if "(SAIFILE" not in text or "(TIMESCALE 1 ns)" not in text:
            raise ComparisonError("activity artifact is not a canonical SAIF file")
        duration = re.search(r"\(DURATION\s+(\d+)\)", text)
        if duration is None or f"(INSTANCE {scope_root}" not in text:
            raise ComparisonError("SAIF duration/scope does not match the bound scope_root")
        duration_ns = int(duration.group(1))
    else:
        raise ComparisonError("activity format must be exactly vcd or saif")
    if end_cycle * clock_period_ns > duration_ns:
        raise ComparisonError("activity window extends beyond waveform duration")
    return scope_hash


def _timing_summary(flow: dict[str, Any], reader: ArtifactReader, label: str) -> dict[str, Any]:
    points = flow["timing_points"]
    if not isinstance(points, list):
        raise ComparisonError(f"{label}.timing_points must be an array")
    parsed = []
    for index, point in enumerate(points):
        reader.read(point["netlist"], f"{label}.timing_points[{index}].netlist")
        reader.read(point["report"], f"{label}.timing_points[{index}].report")
        period = float(point["period_ns"])
        passed = (
            point["setup_wns_ns"] >= 0
            and point["hold_wns_ns"] >= 0
            and point["route_ok"] is True
            and point["unconstrained_paths"] == 0
            and point["drc_violations"] == 0
            and point["antenna_violations"] == 0
        )
        parsed.append((1000.0 / period, period, passed))
    if len({period for _, period, _ in parsed}) != len(parsed):
        raise ComparisonError(f"{label} repeats a timing period")
    if flow["analysis_class"] == "per_target_resynthesis":
        digests = [point["netlist"]["sha256"] for point in points]
        if len(digests) != len(set(digests)):
            raise ComparisonError("per-target timing points must bind distinct resynthesized netlists")
    ordered = sorted(parsed)
    if any(
        not lower[2] and upper[2] and lower[0] < upper[0]
        for lower in ordered for upper in ordered
    ):
        raise ComparisonError(f"{label} timing sweep is non-monotonic")
    passes = [point for point in ordered if point[2]]
    demonstrated = max(passes) if passes else None
    higher_fails = [point for point in ordered if demonstrated and point[0] > demonstrated[0] and not point[2]]
    first_fail = min(higher_fails) if higher_fails else None
    return {
        "fmax_lower_mhz": demonstrated[0] if demonstrated else None,
        "fmax_upper_mhz": first_fail[0] if first_fail else None,
        "bracketed": demonstrated is not None and first_fail is not None,
    }


def _cohort_binding(row: dict[str, Any]) -> dict[str, Any]:
    cohort, boundary, flow = row["cohort"], row["boundary"], row["flow"]
    workload, activity = row["workload"], row["activity"]
    return {
        "boundary_scope": cohort["boundary_scope"],
        "power_mode": cohort["power_mode"],
        "analysis_class": flow["analysis_class"],
        "flow_config_sha256": flow["flow_config"]["sha256"],
        "sdc_sha256": flow["sdc"]["sha256"],
        "library_sha256": flow["library"]["sha256"],
        "corner": flow["corner"],
        "clock_port": flow["clock_port"],
        "clock_period_ns": flow["clock_period_ns"],
        "activity_format": activity["format"] if activity is not None else None,
        "workload_id": workload["id"] if workload is not None else None,
        "workload_manifest_sha256": (
            workload["manifest"]["sha256"] if workload is not None else None
        ),
    }


def _candidate_implementation_binding(row: dict[str, Any]) -> dict[str, Any]:
    candidate, boundary, flow = row["candidate"], row["boundary"], row["flow"]
    return {
        "candidate": candidate,
        "boundary": boundary,
        "analysis_class": flow["analysis_class"],
        "flow_config": flow["flow_config"],
        "sdc": flow["sdc"],
        "library": flow["library"],
        "corner": flow["corner"],
        "clock_port": flow["clock_port"],
        "clock_period_ns": flow["clock_period_ns"],
        "area_um2": flow["area_um2"],
        "timing_points": flow["timing_points"],
    }


def _workload_point_binding(row: dict[str, Any]) -> dict[str, Any] | None:
    activity, workload = row["activity"], row["workload"]
    if activity is None or workload is None:
        return None
    return {
        "operating_point": workload["operating_point"],
        "workload_id": workload["id"],
        "test_id": workload["test_id"],
        "manifest_sha256": workload["manifest"]["sha256"],
        "trace_sha256": workload["trace"]["sha256"],
        "seed": workload["seed"],
        "window_start_cycle": activity["window_start_cycle"],
        "window_end_cycle_exclusive": activity["window_end_cycle_exclusive"],
        "measurement_cycles": activity["measurement_cycles"],
        "warmup_cycles": workload["warmup_cycles"],
        "drain_policy": workload["drain_policy"],
    }


def _load_registry(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read production registry: {exc}") from exc
    return _exact_keys(
        registry,
        {"schema_version", "minimum_coverage_percent", "candidates", "workloads"},
        "production registry",
    )


def _authorize_production(record: dict[str, Any], registry: dict[str, Any] | None) -> float:
    if record["evidence_origin"] == TEST_ORIGIN:
        return MINIMUM_COVERAGE_PERCENT
    if registry is None:
        raise ComparisonError("measured_candidate evidence requires an out-of-band production registry")
    if registry["schema_version"] != 1:
        raise ComparisonError("production registry schema_version must equal 1")
    minimum = registry["minimum_coverage_percent"]
    if (
        not isinstance(minimum, (int, float))
        or isinstance(minimum, bool)
        or not math.isfinite(float(minimum))
        or not 0 < float(minimum) <= 100
    ):
        raise ComparisonError("production registry minimum coverage is invalid")
    allowed_candidates = {
        (entry.get("id"), entry.get("commit_sha"), entry.get("bundle_sha256"))
        for entry in registry["candidates"] if isinstance(entry, dict)
    }
    allowed_workloads = {
        (entry.get("id"), entry.get("manifest_sha256"))
        for entry in registry["workloads"] if isinstance(entry, dict)
    }
    observed_candidates = {row["candidate"]["id"] for row in record["rows"]}
    registered_candidates = {entry[0] for entry in allowed_candidates}
    if observed_candidates != registered_candidates:
        raise ComparisonError(
            "comparison candidate set does not exactly match the production registry"
        )
    for row in record["rows"]:
        candidate = row["candidate"]
        identity = (candidate["id"], candidate["commit_sha"], candidate["bundle"]["sha256"])
        if identity not in allowed_candidates:
            raise ComparisonError(f"candidate {candidate['id']!r} is not production-authorized")
        workload = row["workload"]
        if workload is not None and (workload["id"], workload["manifest"]["sha256"]) not in allowed_workloads:
            raise ComparisonError(f"workload {workload['id']!r} is not production-authorized")
    return float(minimum)


def _evaluate_row(
    row: dict[str, Any], reader: ArtifactReader, minimum_coverage: float, index: int
) -> dict[str, Any]:
    label = f"rows[{index}]"
    candidate, cohort = row["candidate"], row["cohort"]
    boundary, flow = row["boundary"], row["flow"]
    for field in ("bundle",):
        reader.read(candidate[field], f"{label}.candidate.{field}")
    for field in ("flow_config", "sdc", "library"):
        reader.read(flow[field], f"{label}.flow.{field}")
    scope_manifest = reader.json(boundary["scope_manifest"], f"{label}.boundary.scope_manifest")
    calculated_scope = _scope_hash(scope_manifest, boundary["scope_root"])
    if calculated_scope != boundary["scope_sha256"]:
        raise ComparisonError(f"{label} scope_sha256 does not match canonical scope manifest")
    if boundary["synthesis_top"] != boundary["scope_root"]:
        raise ComparisonError(f"{label} synthesis_top must equal scope_root")
    pin_manifest = reader.json(boundary["pin_inventory"], f"{label}.boundary.pin_inventory")
    functional_bits, pin_split = _pin_bits(pin_manifest)
    if functional_bits != boundary["functional_pin_bits"]:
        raise ComparisonError(f"{label} functional_pin_bits does not match pin inventory")
    full = cohort["boundary_scope"] == "full_endpoint"
    includes = (boundary["includes_tx"], boundary["includes_link"], boundary["includes_rx"])
    if full and includes != (True, True, True):
        raise ComparisonError(f"{label} full_endpoint must include TX, link, and RX")
    if not full and includes == (True, True, True):
        raise ComparisonError(f"{label} core_only cannot claim the complete endpoint")

    timing = _timing_summary(flow, reader, f"{label}.flow")
    activity, vectorless_power, workload = (
        row["activity"], row["vectorless_power"], row["workload"]
    )
    mode = cohort["power_mode"]
    if mode == "vectorless_screening":
        if activity is not None or workload is not None or not isinstance(vectorless_power, dict):
            raise ComparisonError(f"{label} vectorless rows cannot carry activity/workload metrics")
        if flow["analysis_class"] != "vectorless_screening":
            raise ComparisonError(f"{label} vectorless cohort requires vectorless_screening flow")
        implementation_points = [
            point for point in flow["timing_points"]
            if _same_number(point["period_ns"], float(flow["clock_period_ns"]))
        ]
        if len(implementation_points) != 1:
            raise ComparisonError(f"{label} vectorless row requires one clock-period timing point")
        vector_report = reader.json(
            vectorless_power["power_report"], f"{label}.vectorless_power.power_report"
        )
        expected_vector_report = {
            "schema_version": 1,
            "candidate_id": candidate["id"],
            "netlist_sha256": implementation_points[0]["netlist"]["sha256"],
            "library_sha256": flow["library"]["sha256"],
            **{field: vectorless_power[field] for field in (
                "internal_power_mw", "switching_power_mw",
                "leakage_power_mw", "total_power_mw",
            )},
        }
        if vector_report != expected_vector_report:
            raise ComparisonError(f"{label} vectorless power report binding mismatch")
        vector_sum = sum(vectorless_power[field] for field in (
            "internal_power_mw", "switching_power_mw", "leakage_power_mw"
        ))
        if not _same_number(vectorless_power["total_power_mw"], vector_sum):
            raise ComparisonError(f"{label} vectorless total power does not equal components")
        metrics = {
            **timing, "area_um2": flow["area_um2"],
            "total_power_mw": vectorless_power["total_power_mw"],
            "events_per_cycle": None, "events_per_pin_cycle": None,
            "energy_nj_per_event": None, "functional_pin_bits": functional_bits,
            "pin_split": pin_split,
        }
        operating_point = None
    else:
        if not isinstance(activity, dict) or not isinstance(workload, dict) or vectorless_power is not None:
            raise ComparisonError(f"{label} activity_annotated rows require activity and workload")
        if activity["scope_root"] != boundary["scope_root"] or activity["scope_sha256"] != calculated_scope:
            raise ComparisonError(f"{label} activity scope/root is not bound to the physical boundary")
        waveform = reader.read(activity["waveform"], f"{label}.activity.waveform")
        for field in ("manifest", "trace"):
            reader.read(workload[field], f"{label}.workload.{field}")
        start, end, cycles = (
            activity["window_start_cycle"], activity["window_end_cycle_exclusive"],
            activity["measurement_cycles"],
        )
        if end - start != cycles:
            raise ComparisonError(f"{label} measurement_cycles must equal end-start")
        calculated_window = canonical_sha256(window_binding(row))
        if activity["window_sha256"] != calculated_window:
            raise ComparisonError(f"{label} window_sha256 does not match exact window binding")
        coverage = activity["coverage"]
        eligible, annotated = coverage["eligible_object_bits"], coverage["annotated_object_bits"]
        if annotated > eligible:
            raise ComparisonError(f"{label} annotated coverage exceeds eligible scope")
        calculated_percent = 100.0 * annotated / eligible
        scope_bits = _scope_bits(scope_manifest)
        if eligible != scope_bits:
            raise ComparisonError(f"{label} eligible coverage bits do not equal resolved scope bits")
        if not _same_number(coverage["percent"], calculated_percent):
            raise ComparisonError(f"{label} coverage percent does not match its numerator/denominator")
        if calculated_percent < minimum_coverage:
            raise ComparisonError(f"{label} coverage is below the trusted {minimum_coverage:g}% threshold")
        _validate_waveform(
            waveform, activity["format"], boundary["scope_root"],
            scope_manifest, end, float(flow["clock_period_ns"]), annotated,
        )
        denominator, conservation = workload["event_denominator"], workload["conservation"]
        if denominator["measurement_cycles"] != cycles or denominator["window_sha256"] != calculated_window:
            raise ComparisonError(f"{label} event denominator is not bound to the exact activity window")
        if conservation["generated"] != conservation["source_overrun"] + conservation["accepted"]:
            raise ComparisonError(f"{label} generated != source_overrun + accepted")
        if conservation["accepted"] != conservation["delivered"] + conservation["loss"]:
            raise ComparisonError(f"{label} accepted != delivered + loss")
        if denominator["count"] != conservation["delivered"]:
            raise ComparisonError(f"{label} event denominator count != delivered logical events")
        faults = (
            conservation["source_overrun"] + conservation["loss"] +
            conservation["duplicate"] + conservation["corrupt"] +
            conservation["phantom"] + conservation["late_after_drain"]
        )
        common = reader.json(workload["common_result"], f"{label}.workload.common_result")
        expected_common = {
            "schema_version": 1,
            "candidate_id": candidate["id"],
            "workload_id": workload["id"],
            "test_id": workload["test_id"],
            "seed": workload["seed"],
            "trace_sha256": workload["trace"]["sha256"],
            "window_sha256": calculated_window,
            "measurement_cycles": cycles,
            "event_denominator": denominator,
            "conservation": conservation,
        }
        if common != expected_common:
            raise ComparisonError(f"{label} common result does not exactly bind workload/window/denominator")
        implementation_points = [
            point for point in flow["timing_points"]
            if _same_number(point["period_ns"], float(flow["clock_period_ns"]))
        ]
        if len(implementation_points) != 1:
            raise ComparisonError(
                f"{label} must have exactly one timing point at the activity clock period"
            )
        implementation_point = implementation_points[0]
        if not (
            implementation_point["setup_wns_ns"] >= 0
            and implementation_point["hold_wns_ns"] >= 0
            and implementation_point["route_ok"] is True
            and implementation_point["unconstrained_paths"] == 0
            and implementation_point["drc_violations"] == 0
            and implementation_point["antenna_violations"] == 0
        ):
            raise ComparisonError(f"{label} activity-clock implementation is not a clean timing PASS")
        power = reader.json(activity["power_report"], f"{label}.activity.power_report")
        expected_power = {
            "schema_version": 1,
            "candidate_id": candidate["id"],
            "format": activity["format"],
            "waveform_sha256": activity["waveform"]["sha256"],
            "scope_sha256": calculated_scope,
            "window_sha256": calculated_window,
            "netlist_sha256": implementation_point["netlist"]["sha256"],
            "library_sha256": flow["library"]["sha256"],
            "internal_power_mw": activity["internal_power_mw"],
            "switching_power_mw": activity["switching_power_mw"],
            "leakage_power_mw": activity["leakage_power_mw"],
            "total_power_mw": activity["total_power_mw"],
        }
        if power != expected_power:
            raise ComparisonError(f"{label} power report does not exactly bind activity/scope/window/netlist/library")
        component_total = sum(activity[field] for field in (
            "internal_power_mw", "switching_power_mw", "leakage_power_mw"
        ))
        if not _same_number(activity["total_power_mw"], component_total):
            raise ComparisonError(f"{label} total power does not equal component sum")
        events = denominator["count"]
        events_per_cycle = events / cycles
        clock_mhz = 1000.0 / flow["clock_period_ns"]
        metrics = {
            **timing,
            "area_um2": flow["area_um2"],
            "total_power_mw": activity["total_power_mw"],
            "events_per_cycle": events_per_cycle,
            "events_per_pin_cycle": events / (cycles * functional_bits),
            "energy_nj_per_event": activity["total_power_mw"] / (clock_mhz * events_per_cycle),
            "functional_pin_bits": functional_bits,
            "pin_split": pin_split,
        }
        operating_point = workload["operating_point"]
        metrics["conservation_clean"] = faults == 0
    return {
        "candidate_id": candidate["id"],
        "cohort_id": canonical_sha256(_cohort_binding(row)),
        "boundary_scope": cohort["boundary_scope"],
        "power_mode": mode,
        "analysis_class": flow["analysis_class"],
        "operating_point": operating_point,
        "metrics": metrics,
    }


def evaluate(
    record: dict[str, Any], base_dir: Path, production_registry: dict[str, Any] | None = None
) -> dict[str, Any]:
    _validate_schema(record)
    minimum = _authorize_production(record, production_registry)
    reader = ArtifactReader(base_dir)
    reader.read(record["source_archive"], "$.source_archive")
    rows = [_evaluate_row(row, reader, minimum, index) for index, row in enumerate(record["rows"])]
    identities: dict[tuple[str, str, str], str] = {}
    for input_row in record["rows"]:
        candidate_id = input_row["candidate"]["id"]
        identity_key = (
            candidate_id,
            input_row["cohort"]["boundary_scope"],
            input_row["cohort"]["power_mode"],
        )
        identity = canonical_sha256(_candidate_implementation_binding(input_row))
        prior = identities.setdefault(identity_key, identity)
        if prior != identity:
            raise ComparisonError(
                f"candidate {candidate_id!r} changes implementation identity "
                "inside one boundary/power cohort"
            )
    seen: set[tuple[str, str, str | None]] = set()
    for row in rows:
        key = (row["candidate_id"], row["cohort_id"], row["operating_point"])
        if key in seen:
            raise ComparisonError(f"duplicate candidate/cohort/operating-point row: {key}")
        seen.add(key)
    cohorts: dict[str, dict[str, Any]] = {}
    for row in rows:
        cohort = cohorts.setdefault(row["cohort_id"], {
            "cohort_id": row["cohort_id"],
            "boundary_scope": row["boundary_scope"],
            "power_mode": row["power_mode"],
            "rows": [],
        })
        cohort["rows"].append(row)
    synthetic = record["evidence_origin"] == TEST_ORIGIN
    release_cohorts: set[str] = set()
    for cohort_id, cohort in cohorts.items():
        group = cohort["rows"]
        candidate_ids = {row["candidate_id"] for row in group}
        per_candidate_complete = all(
            {row["operating_point"] for row in group if row["candidate_id"] == candidate_id}
            == REQUIRED_OPERATING_POINTS
            for candidate_id in candidate_ids
        )
        point_bindings_match = all(
            len({
                canonical_sha256(_workload_point_binding(input_row))
                for input_row in record["rows"]
                if canonical_sha256(_cohort_binding(input_row)) == cohort_id
                and input_row["workload"] is not None
                and input_row["workload"]["operating_point"] == operating_point
            }) == 1
            for operating_point in REQUIRED_OPERATING_POINTS
        )
        if (
            len(candidate_ids) >= 2
            and all(row["boundary_scope"] == "full_endpoint" for row in group)
            and all(row["power_mode"] == "activity_annotated" for row in group)
            and all(row["analysis_class"] == "per_target_resynthesis" for row in group)
            and per_candidate_complete
            and point_bindings_match
            and all(row["metrics"].get("conservation_clean") is True for row in group)
            and all(row["metrics"]["bracketed"] for row in group)
        ):
            release_cohorts.add(cohort_id)
    candidate_results = []
    for candidate_id in sorted({row["candidate_id"] for row in rows}):
        candidate_rows = [row for row in rows if row["candidate_id"] == candidate_id]
        eligible_cohorts = sorted({
            row["cohort_id"] for row in candidate_rows
            if row["cohort_id"] in release_cohorts
        })
        candidate_go = (
            bool(eligible_cohorts) and not synthetic
            and PRODUCTION_PUBLICATION_ENABLED
        )
        candidate_results.append({
            "candidate_id": candidate_id,
            "candidate_go": candidate_go,
            "decision": "TEST_ONLY" if synthetic else (
                "CANDIDATE_GO" if candidate_go else "HOLD_UNAUTHENTICATED"
            ),
            "eligible_cohort_ids": eligible_cohorts,
        })
    return {
        "schema_version": 1,
        "comparison_id": record["comparison_id"],
        "evidence_origin": record["evidence_origin"],
        "source_archive_sha256": record["source_archive"]["sha256"],
        "publication_status": "TEST_ONLY" if synthetic else (
            "CANDIDATE_GO" if any(item["candidate_go"] for item in candidate_results)
            else "HOLD_UNAUTHENTICATED"
        ),
        "candidate_results": candidate_results,
        "cohorts": [cohorts[key] for key in sorted(cohorts)],
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError("comparison input must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--production-registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        record = read_json(args.input)
        result = evaluate(record, args.input.parent, _load_registry(args.production_registry))
        encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
    except ComparisonError as exc:
        print(f"NOT_COMPARABLE: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
