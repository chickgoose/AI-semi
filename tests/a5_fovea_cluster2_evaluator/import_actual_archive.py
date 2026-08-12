#!/usr/bin/env python3
"""Import the recovered W7 native archive into the A5 evaluator, fail closed.

The recovered archive predates the A5 evidence schema.  This adapter verifies
its complete 338-file result ledger, maps run labels to exact generator-v4 run
names, and delegates event accounting to evaluate_fovea_cluster2.py.  It does
not promote missing execution receipts, reset negative controls, or a missing
native-policy experiment into PASS claims.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import sys
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "a5_w7_core_evaluator", HERE / "evaluate_fovea_cluster2.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load A5 evaluator")
E = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = E
SPEC.loader.exec_module(E)

IMPORT_SCHEMA = "a5_fovea_cluster2_actual_archive_import_v1"
EXPECTED_LEDGER_COUNT = 338
CANDIDATES = {
    "fovea": {
        "id": "ganghee-native-coordinate-source-projection",
        "architecture": "fovea",
        "top": "aer_tx16_trad_rowcol_fovea",
        "define": "AER_GANGHEE_NATIVE_MODULE=aer_tx16_trad_rowcol_fovea",
        "binding": "aer_ganghee_native_binding.sv",
        "retire_lanes": 1,
    },
    "cluster2": {
        "id": "ganghee-cluster2-row-bitmap",
        "architecture": "cluster2",
        "top": "aer_tx16_trad_rowcol_fovea_cluster2",
        "define": "AER_GANGHEE_CLUSTER2_MODULE=aer_tx16_trad_rowcol_fovea_cluster2",
        "binding": "aer_ganghee_cluster2_binding.sv",
        "retire_lanes": 8,
    },
}


class ImportError(RuntimeError):
    pass


def reject_symlink_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ImportError(f"path escapes archive root: {path}") from exc
    current = root
    if current.is_symlink():
        raise ImportError(f"archive path contains a symlink: {current}")
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ImportError(f"artifact parent path contains a symlink: {current}")


def digest(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ImportError(f"cannot safely open regular file {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ImportError(f"artifact is not single-linked regular file: {path}")
        hasher = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            hasher.update(block)
        after = os.fstat(descriptor)
        stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if stable != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ImportError(f"artifact changed while read: {path}")
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


def parse_ledger_line(line: str, number: int, attempt: str) -> tuple[str, str]:
    match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
    if match is None:
        raise ImportError(f"ledger line {number} is not canonical sha256sum syntax")
    expected, original = match.groups()
    prefix = attempt.rstrip("/") + "/results/"
    if not original.startswith(prefix) or original.count("/results/") != 1:
        raise ImportError(f"ledger line {number} does not match exact provenance attempt/results prefix")
    suffix = PurePosixPath("results") / PurePosixPath(original[len(prefix):])
    if suffix.is_absolute() or ".." in suffix.parts or "xcelium.d" in suffix.parts:
        raise ImportError(f"ledger line {number} has unsafe/non-result path")
    return expected, str(suffix)


def verify_result_ledger(archive: Path, attempt: str) -> dict[str, Any]:
    if not archive.is_absolute() or archive.is_symlink() or not archive.is_dir():
        raise ImportError("archive root must be a real directory")
    for parent in (archive, *archive.parents):
        if parent.is_symlink():
            raise ImportError(f"archive parent path contains a symlink: {parent}")
    ledger = archive / "result-artifacts.sha256"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    if len(lines) != EXPECTED_LEDGER_COUNT:
        raise ImportError(f"result ledger cardinality is {len(lines)}, expected 338")
    entries: dict[str, str] = {}
    inodes: dict[tuple[int, int], str] = {}
    for number, line in enumerate(lines, 1):
        expected, relative = parse_ledger_line(line, number, attempt)
        if relative in entries:
            raise ImportError(f"duplicate ledger result path: {relative}")
        path = archive / relative
        reject_symlink_chain(archive, path)
        observed = digest(path)
        if observed != expected:
            raise ImportError(f"result SHA mismatch: {relative}")
        stat = path.stat()
        inode = (stat.st_dev, stat.st_ino)
        if inode in inodes:
            raise ImportError(f"result inode reused by {inodes[inode]} and {relative}")
        inodes[inode] = relative
        entries[relative] = expected
    actual = {
        str(path.relative_to(archive))
        for path in (archive / "results").rglob("*")
        if path.is_file() and "xcelium.d" not in path.parts
    }
    if set(entries) != actual:
        missing = sorted(actual - set(entries))
        extra = sorted(set(entries) - actual)
        raise ImportError(f"ledger/result tree mismatch missing={missing[:2]} extra={extra[:2]}")
    # Bind the rebased content independently from the stale absolute prefix.
    canonical = "".join(f"{entries[name]}  {name}\n" for name in sorted(entries))
    return {
        "count": len(entries),
        "status": "PASS_EXACT_PROVENANCE_PREFIX_AND_REBASED_RESULT_TREE",
        "ledger_sha256": digest(ledger),
        "canonical_result_tree_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "entries": entries,
    }


def parse_provenance(archive: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (archive / "provenance.txt").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("sh:") or line.startswith("TOOL:"):
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise ImportError(f"duplicate provenance key: {key}")
        values[key] = value
    required = {
        "snapshot_head", "binding_reset_quiet_arming_patch", "snapshot_archive_sha256",
        "canonical_rtl_date_kst", "attempt", "hostname", "start_utc", "finish_utc",
    }
    if not required <= set(values):
        raise ImportError("incomplete archive provenance")
    if values["binding_reset_quiet_arming_patch"] != "workspace-diff":
        raise ImportError("expected workspace-diff binding provenance caveat is absent")
    return values


def read_single_csv(path: Path) -> tuple[list[str], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = reader.fieldnames
    if fields is None or len(rows) != 1:
        raise ImportError(f"expected one-row CSV: {path}")
    return fields, rows[0]


def verify_archive_generations(archive: Path, official: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for suite in ("full50", "capacity22"):
        root = archive / f"traces-{suite}"
        index_path = root / "generation-index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        rows = index.get("runs")
        if index.get("generator_version") != "4.0" or not isinstance(rows, list):
            raise ImportError(f"archive {suite} generation-index schema mismatch")
        names = [row.get("run", {}).get("name") for row in rows if isinstance(row, dict)]
        if names != list(official[suite]):
            raise ImportError(f"archive {suite} run names/order differ from official")
        for row in rows:
            name = row["run"]["name"]
            expected = official[suite][name]
            trace = root / str(row.get("trace_file"))
            manifest = root / f"{name}.manifest.json"
            if row.get("trace_sha256") != expected.trace_sha256 or digest(trace) != expected.trace_sha256:
                raise ImportError(f"archive {suite}/{name} trace differs from official")
            manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
            if manifest_doc != row:
                raise ImportError(f"archive {suite}/{name} embedded manifest differs from index")
        result[suite] = {
            "generation_index_sha256": digest(index_path),
            "run_count": len(rows),
            "manifest_sha256": E.OFFICIAL[suite]["sha256"],
        }
    return result


def verify_elaboration(archive: Path, key: str, spec: dict[str, Any]) -> None:
    text = (archive / "results" / key / "elaborate.log").read_text(encoding="utf-8")
    required = (
        "-top aer_clean_tb", f"-define {spec['define']}",
        f"-defparam aer_clean_tb.RETIRE_LANES={spec['retire_lanes']}",
        spec["binding"], f"/{spec['top']}.v",
    )
    for token in required:
        if token not in text:
            raise ImportError(f"{key} elaboration missing {token}")


def verify_run_logs(archive: Path, official: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fatal = re.compile(r"(?:\*E,|\*F,|AER_CLEAN_TEST_FAIL|UVM_(?:ERROR|FATAL)|errors=[1-9][0-9]*)")
    checked = 0
    for key, spec in CANDIDATES.items():
        for name in official["full50"]:
            path = archive / "results" / key / "runs" / name / "trace.log"
            text = path.read_text(encoding="utf-8")
            _, summary = read_single_csv(path.with_name("trace.csv"))
            native_test = summary.get("test")
            if not native_test or summary.get("candidate") != spec["id"]:
                raise ImportError(f"{key}/{name} summary candidate/test identity mismatch")
            required = (
                f"+CANDIDATE={spec['id']}", f"AER_CLEAN_METRICS test={native_test} ",
                f"AER_CLEAN_TEST_PASS {native_test}",
            )
            if any(marker not in text for marker in required):
                raise ImportError(f"{key}/{name} log identity/PASS mismatch")
            if fatal.search(text):
                raise ImportError(f"{key}/{name} log contains fatal/error marker")
            checked += 1
    if checked != 100:
        raise ImportError(f"trace log cardinality mismatch: {checked}")
    return {"checked": checked, "status": "PASS_EXACT_CANDIDATE_TEST_AND_NO_FATAL_MARKER"}


def verify_positive_reset(archive: Path, key: str, candidate_id: str) -> dict[str, Any]:
    root = archive / "results" / key / "reset"
    _, summary = read_single_csv(root / "basic_reset_drain.csv")
    with (root / "basic_reset_drain.events.csv").open(newline="", encoding="utf-8") as stream:
        events = list(csv.DictReader(stream))
    expected = {"candidate": candidate_id, "generated": "16", "accepted": "16",
                "delivered": "16", "errors": "0"}
    if any(summary.get(name) != value for name, value in expected.items()):
        raise ImportError(f"{key} reset summary accounting mismatch")
    ids = [int(row["tb_only_event_id"]) for row in events]
    if ids != list(range(16)) or any(row.get("event_state") != "delivered" for row in events):
        raise ImportError(f"{key} reset events are incomplete/duplicated/reordered")
    log = (root / "basic_reset_drain.log").read_text(encoding="utf-8")
    for marker in (
        "AER_RESET_DRAIN_RESULT pre_generated=8 pre_accepted=8 pre_delivered=8 "
        "post_generated=8 post_accepted=8 post_delivered=8 reset_errors=0",
        "AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16",
        "AER_CLEAN_TEST_PASS basic_reset_drain",
    ):
        if marker not in log:
            raise ImportError(f"{key} reset log missing PASS marker")
    return {
        "positive_drain_reset": "PASS",
        "generated": 16, "accepted": 16, "delivered": 16, "errors": 0,
        "negative_control_present": False,
        "qualification": "HOLD_NO_NEGATIVE_CONTROL_OR_EXPLICIT_QUIET_ASSERTION_ARTIFACT",
    }


def validate_actual_run(candidate_id: str, official: Any, summary_path: Path,
                        events_path: Path) -> Any:
    """Validate the recovered TB's native one-based cycle convention."""
    _, summary = read_single_csv(summary_path)
    with events_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not E.EVENT_COLUMNS <= set(reader.fieldnames):
            raise ImportError(f"missing event columns: {events_path}")
        rows = list(reader)
    where = f"{candidate_id}/{official.suite}/{official.name}"
    expected_load_pct = (int(official.load * 1000) + 5) // 10
    expected_summary = {
        "candidate": candidate_id, "seed": str(official.seed),
        "load_pct": str(expected_load_pct), "stim_cycles": str(official.stim_cycles),
        "generated": str(len(official.trace)), "errors": "0",
        "measurement_cycles": str(official.stim_cycles),
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ImportError(f"{where} summary provenance mismatch")
    if len(rows) != len(official.trace):
        raise ImportError(f"{where} does not contain one row per generated event")
    native_test = summary.get("test")
    states: dict[str, int] = defaultdict(int)
    generated_by_source = [0] * 16
    delivered_by_source = [0] * 16
    last_accept = [-1] * 16
    last_delivery = [-1] * 16
    latencies: list[int] = []
    waits: list[int] = []
    measured = 0
    observation_end = None
    relations: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for position, (row, trace) in enumerate(zip(rows, official.trace)):
        event_id = int(row["tb_only_event_id"])
        source = int(row["logical_source"])
        occurrence = int(row["occurrence_cycle"])
        if event_id != position or event_id != int(trace["tb_only_event_id"]):
            raise ImportError(f"{where} duplicate/reordered event ID")
        if source != int(trace["logical_source"]) or occurrence != int(trace["occurrence_cycle"]) + 1:
            raise ImportError(f"{where}/{event_id} trace mapping mismatch")
        if (row.get("candidate") != candidate_id or row.get("test") != native_test or
                row.get("seed") != str(official.seed) or row.get("load_pct") != str(expected_load_pct) or
                row.get("source_count") != "16"):
            raise ImportError(f"{where}/{event_id} row provenance mismatch")
        end = int(row["observation_end_cycle"])
        if observation_end is None:
            observation_end = end
        elif observation_end != end:
            raise ImportError(f"{where} inconsistent observation end")
        deadline = trace.get("deadline")
        if deadline is not None and int(row["deadline_cycle"]) != int(deadline) + 1:
            raise ImportError(f"{where}/{event_id} deadline mapping mismatch")
        state = row.get("event_state")
        if state not in E.VALID_STATES:
            raise ImportError(f"{where}/{event_id} invalid state")
        states[state] += 1
        generated_by_source[source] += 1
        if state == "source_overrun":
            if row.get("accept_cycle") or row.get("delivery_cycle"):
                raise ImportError(f"{where}/{event_id} overrun has transport cycles")
            latency = None
        elif state == "delivered":
            accept = int(row["accept_cycle"])
            delivery = int(row["delivery_cycle"])
            if accept < occurrence or delivery < accept or delivery > end:
                raise ImportError(f"{where}/{event_id} impossible chronology")
            if accept < last_accept[source] or delivery < last_delivery[source]:
                raise ImportError(f"{where}/{event_id} per-source order violation")
            last_accept[source], last_delivery[source] = accept, delivery
            delivered_by_source[source] += 1
            latency = delivery - occurrence
            latencies.append(latency)
            waits.append(accept - occurrence)
            # Recovered TB counter covers its one-based cycles 1..stim+1.
            if delivery <= official.stim_cycles + 1:
                measured += 1
        else:
            raise ImportError(f"{where}/{event_id} pending/accepted remains after drain")
        relation = trace.get("relation_id")
        role = trace.get("relation_role")
        if relation is not None:
            if role not in ("a", "b") or role in relations[int(relation)]:
                raise ImportError(f"{where}/{event_id} malformed pair relation")
            relations[int(relation)][role] = {"state": state, "latency": latency}
    generated = len(rows)
    overrun = states["source_overrun"]
    delivered = states["delivered"]
    checks = {
        "source_overrun": overrun, "accepted": delivered, "delivered": delivered,
        "measurement_delivered": measured,
    }
    if any(int(summary.get(key, "-1")) != value for key, value in checks.items()):
        raise ImportError(f"{where} summary/event accounting mismatch")
    if generated != delivered + overrun:
        raise ImportError(f"{where} generated accounting does not close")
    throughput = measured / official.stim_cycles
    if not math.isclose(float(summary["throughput"]), throughput, rel_tol=1e-6, abs_tol=1e-6):
        raise ImportError(f"{where} fixed-window throughput mismatch")
    ratios = [delivered_by_source[index] / generated_by_source[index]
              for index in range(16) if generated_by_source[index]]
    pair_relations = {}
    for relation, parts in relations.items():
        if set(parts) != {"a", "b"}:
            raise ImportError(f"{where} incomplete pair relation")
        complete = parts["a"]["state"] == parts["b"]["state"] == "delivered"
        pair_relations[relation] = {
            "complete": complete,
            "max_latency": max(parts["a"]["latency"], parts["b"]["latency"])
            if complete else None,
        }
    return E.RunMetric(
        name=official.name, workload=official.workload, load=float(official.load),
        generated=generated, accepted=delivered, delivered=delivered, overrun=overrun,
        measurement_delivered=measured, measurement_cycles=official.stim_cycles,
        throughput=throughput, overrun_ratio=overrun / generated if generated else 0.0,
        acceptance_ratio=delivered / generated if generated else 1.0,
        p50=E.nearest_rank(latencies, 50), p95=E.nearest_rank(latencies, 95),
        p99=E.nearest_rank(latencies, 99), maximum=max(latencies) if latencies else None,
        max_wait=max(waits) if waits else 0, fairness=E.jain(ratios),
        min_source_ratio=min(ratios) if ratios else None, pair_relations=pair_relations,
    )


def evaluate_candidate(archive: Path, key: str, spec: dict[str, Any],
                       official: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, dict[str, Any]] = {"full50": {}, "capacity22": {}}
    made: dict[str, tuple[Path, Path]] = {}
    for suite in ("full50", "capacity22"):
        for name, run in official[suite].items():
            if name not in made:
                source_root = archive / "results" / key / "runs" / name
                summary = source_root / "trace.csv"
                events = source_root / "trace.events.csv"
                made[name] = summary, events
            summary, events = made[name]
            metrics[suite][name] = validate_actual_run(spec["id"], run, summary, events)
    full = metrics["full50"]
    result = {
        "correctness": {
            "full50_event_accounting": "PASS_50_OF_50",
            "capacity22_event_accounting": "PASS_22_OF_22_SUBSET_VIEW",
            "checked_invariants": [
                "generated_equals_delivered_plus_source_overrun",
                "contiguous_unique_event_ids", "trace_source_and_occurrence_match",
                "no_pending_or_accepted_after_drain", "no_phantom_transport_cycles",
                "per_source_accept_and_delivery_order", "summary_event_accounting_match",
            ],
            "reset_positive": verify_positive_reset(archive, key, spec["id"]),
        },
        "full50": E.aggregate_runs(full.values()),
        "capacity22": E.aggregate_runs(metrics["capacity22"].values()),
        "capacity": E.capacity_curve(full),
        "families": {
            label: E.aggregate_runs(run for run in full.values() if run.workload in workloads)
            for label, workloads in {
                "spatial": {"matched_spatial"},
                "moving": {"moving_hotspot", "rotating_victim"},
                "fairness_stress": {"elephant_mouse"},
            }.items()
        },
        "pairwise_mapping": E.pairwise(full),
        "native_policy": {
            "measured_in_archive": False,
            "fovea_contract": "1:5:5:1 weighted service",
            "cluster2_contract": "two lanes per row; weighted service is flattened/transformed",
            "qualification": "HOLD_NO_INDEPENDENT_CONTINUOUS_ALL_16_SOURCE_POLICY_ARTIFACT",
        },
        "identity_from_logs": {
            "top": spec["top"], "retire_lanes": spec["retire_lanes"],
            "elaborate_log_sha256": digest(archive / "results" / key / "elaborate.log"),
            "candidate_run_log_sha256": digest(archive / f"{key}-run.log"),
            "source_binding_runner_bytes_archived": False,
        },
    }
    return result


def performance_pareto(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    vectors: dict[str, dict[str, float]] = {}
    for candidate_id, value in metrics.items():
        pair = value["pairwise_mapping"]
        curve = value["capacity"]["curve"]
        max_load = max(row["load"] for row in curve)
        knee = value["capacity"]["knee_load"]
        vectors[candidate_id] = {
            "capacity_knee": float(knee if knee is not None else max_load + 0.001),
            "capacity22_epc": value["capacity22"]["fixed_window_event_per_cycle"],
            "full50_epc": value["full50"]["fixed_window_event_per_cycle"],
            "negative_full50_overrun": -value["full50"]["overrun_ratio"],
            "negative_full50_worst_run_p99": -float(value["full50"]["worst_run_p99_e2e_latency"]),
            "negative_full50_max_wait": -float(value["full50"]["max_request_wait"]),
            "full50_worst_fairness": value["full50"]["worst_demand_normalized_fairness"],
            "full50_min_source_delivery": value["full50"]["min_source_delivery_ratio"],
            "spatial_epc": value["families"]["spatial"]["fixed_window_event_per_cycle"],
            "negative_spatial_overrun": -value["families"]["spatial"]["overrun_ratio"],
            "negative_spatial_worst_p99": -float(value["families"]["spatial"]["worst_run_p99_e2e_latency"]),
            "moving_epc": value["families"]["moving"]["fixed_window_event_per_cycle"],
            "negative_moving_overrun": -value["families"]["moving"]["overrun_ratio"],
            "negative_moving_worst_p99": -float(value["families"]["moving"]["worst_run_p99_e2e_latency"]),
            "pairwise_worst_completion": min(pair["identity"]["completion_ratio"], pair["affine"]["completion_ratio"]),
            "negative_pairwise_worst_p99": -float(max(pair["identity"]["p99_pair_max_latency"], pair["affine"]["p99_pair_max_latency"])),
            "negative_mapping_churn": -float(pair["relation_completion_churn"]),
        }
    dominated = {name: [] for name in vectors}
    for name, vector in vectors.items():
        for other, other_vector in vectors.items():
            if name == other:
                continue
            if (all(other_vector[axis] >= value for axis, value in vector.items()) and
                    any(other_vector[axis] > value for axis, value in vector.items())):
                dominated[name].append(other)
    return {
        "scope": "MEASURED_RAW_PERFORMANCE_AXES_ONLY",
        "qualification": "NOT_AN_OFFICIAL_COMMON_OR_RELEASE_GATE",
        "rule": "unweighted_all_measured_dimensions_no_worse_and_one_strictly_better",
        "dimensions_larger_is_better": vectors,
        "dominated_by": dominated,
        "frontier": sorted(name for name, by in dominated.items() if not by),
    }


def import_and_evaluate(archive: Path, generator: Path, manifest_root: Path) -> dict[str, Any]:
    if not archive.is_absolute():
        raise ImportError("archive root must be absolute")
    reject_symlink_chain(archive, archive / "provenance.txt")
    provenance = parse_provenance(archive)
    ledger = verify_result_ledger(archive, provenance["attempt"])
    with tempfile.TemporaryDirectory(prefix="a5-w7-actual-import-") as temporary_name:
        temporary = Path(temporary_name)
        official = E.materialize_official(generator, manifest_root, temporary / "official")
        generations = verify_archive_generations(archive, official)
        log_receipt = verify_run_logs(archive, official)
        metrics = {}
        for key, spec in CANDIDATES.items():
            verify_elaboration(archive, key, spec)
            metrics[spec["id"]] = evaluate_candidate(archive, key, spec, official)
    ledger_after = verify_result_ledger(archive, provenance["attempt"])
    if ledger_after != ledger:
        raise ImportError("result ledger/tree changed during evaluation")
    result = {
        "schema": IMPORT_SCHEMA,
        "status": "LOCAL_ACTUAL_ARCHIVE_PERFORMANCE_EVALUATION_COMPLETE_WITH_HOLDS",
        "archive_receipt": {
            "archive_basename": archive.name,
            "result_artifacts": {name: value for name, value in ledger.items() if name != "entries"},
            "provenance_sha256": digest(archive / "provenance.txt"),
            "generations": generations,
            "snapshot_head": provenance["snapshot_head"],
            "snapshot_archive_sha256": provenance["snapshot_archive_sha256"],
            "workspace_diff": provenance["binding_reset_quiet_arming_patch"],
            "trace_logs": log_receipt,
        },
        "importer_sha256": digest(Path(__file__).resolve()),
        "official": {suite: {"manifest_sha256": contract["sha256"], "run_count": contract["count"]}
                     for suite, contract in E.OFFICIAL.items()},
        "metrics": metrics,
        "pareto": performance_pareto(metrics),
        "decision": "SCALAR_A7_BASE_FOVEA_RAW_NATIVE_CAPACITY_CLUSTER2_OFFICIAL_RELEASE_HOLD",
        "recommendation": {
            "scalar_a7_base": "ganghee-native-coordinate-source-projection",
            "scalar_reason": "one-event interface and preservation of the native 1:5:5:1 weighted-service contract",
            "raw_native_capacity_winner": "ganghee-cluster2-row-bitmap",
            "cluster2_scope": "future redesigned parallel link only",
            "absolute_performance_or_ppa_superiority": "HOLD",
        },
        "hold_scope": [
            "non_official_receipt_absolute_paths_rebased_by_verified_results_suffix",
            "snapshot_binding_contains_workspace_diff",
            "source_binding_runner_and_simulator_bytes_not_archived; identities_are_log_descriptors",
            "reset_archive_has_positive_drain_test_but_no_negative_control_or_explicit_quiet_assertion_artifact",
            "1_5_5_1_is_not_measured_by_a_continuous_all_source_archive_artifact",
            "capacity22_is_an_exact_full50_subset_and_reuses_the_same_run_artifacts",
            "local_always_ready_digital_results_are_not_official_receipt_backpressure_or_physical_PPA",
        ],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--manifest-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ImportError(f"refusing to overwrite output: {args.output}")
        document = import_and_evaluate(args.archive.absolute(), args.generator, args.manifest_root)
        E.atomic_json(args.output, document)
        print(f"A5_W7_ACTUAL_ARCHIVE_IMPORT_PASS output={args.output}")
        print("HOLD non_official_receipt workspace_diff reset_negative_control policy_measurement physical_PPA")
        return 0
    except (ImportError, E.EvaluationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"A5_W7_ACTUAL_ARCHIVE_IMPORT_FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
