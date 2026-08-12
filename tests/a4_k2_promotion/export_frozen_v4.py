#!/usr/bin/env python3
"""Export exact generator-v4 traces as cycle-explicit atomic-K2 vectors."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


SCHEMA = "a4_frozen_v4_k2_transaction_bundle_v1"
VECTOR_VERSION = 1
SOURCE_COUNT = 16
RETIRE_LANES = 2
DEFAULT_DRAIN_CYCLES = 32
GENERATOR_VERSION = "4.0"
PINNED = {
    "generator_sha256": "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    "official_sha256": "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33",
    "full50_manifest_sha256": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22_manifest_sha256": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
TRACE_KEYS = {
    "occurrence_cycle", "tb_only_event_id", "logical_source", "x", "y",
    "polarity", "event_type", "relation_id", "relation_role", "deadline",
}


class ExportError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"not a regular provenance file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExportError(f"cannot read JSON {path}: {error}") from error


def load_official(a1_repo: Path) -> tuple[Any, Path, Path]:
    if not a1_repo.is_absolute():
        raise ExportError("A1 repository path must be absolute")
    generator = a1_repo / "benchmarks/clean_slate_aer/generate_trace.py"
    official_path = a1_repo / "scripts/common_suite_official.py"
    if file_sha256(generator) != PINNED["generator_sha256"]:
        raise ExportError("generator-v4 source SHA-256 mismatch")
    if file_sha256(official_path) != PINNED["official_sha256"]:
        raise ExportError("official-suite source SHA-256 mismatch")
    specification = importlib.util.spec_from_file_location(
        "a4_k2_pinned_common_suite_official", official_path)
    if specification is None or specification.loader is None:
        raise ExportError("cannot import official suite specification")
    official = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(official)
    if official.GENERATOR_VERSION != GENERATOR_VERSION:
        raise ExportError("official suite does not name generator-v4")
    return official, generator, official_path


def manifest_path(a1_repo: Path, official: Any, suite: str) -> Path:
    path = a1_repo / "benchmarks/clean_slate_aer" / official.SUITES[suite]["manifest_name"]
    expected = PINNED[f"{suite}_manifest_sha256"]
    if file_sha256(path) != expected or official.SUITES[suite]["manifest_sha256"] != expected:
        raise ExportError(f"{suite}: frozen manifest SHA-256 mismatch")
    return path


def generate_suite(generator: Path, manifest: Path, trace_root: Path) -> None:
    if trace_root.exists():
        raise ExportError(f"refusing to reuse trace directory: {trace_root}")
    result = subprocess.run(
        [sys.executable, "-B", str(generator), "--manifest", str(manifest),
         "--output-dir", str(trace_root)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise ExportError(f"generator-v4 failed for {manifest.name}:\n{result.stdout}")


def validate_generation(trace_root: Path, suite: str, manifest: Path,
                        official: Any) -> list[dict[str, Any]]:
    index_path = trace_root / "generation-index.json"
    index = load_json(index_path)
    if not isinstance(index, dict) or set(index) != {
            "schema_version", "generator_version", "input_manifest", "runs"}:
        raise ExportError(f"{suite}: generation index schema mismatch")
    if (index["schema_version"] != 1 or index["generator_version"] != GENERATOR_VERSION or
            index["input_manifest"] != manifest.name):
        raise ExportError(f"{suite}: generation index provenance mismatch")
    runs = index["runs"]
    expected_names = list(official.SUITES[suite]["names"])
    if not isinstance(runs, list) or [item.get("run", {}).get("name") for item in runs] != expected_names:
        raise ExportError(f"{suite}: exact run order/cardinality mismatch")
    if len(expected_names) != len(set(expected_names)):
        raise ExportError(f"{suite}: duplicate official run name")
    for metadata in runs:
        name = metadata["run"]["name"]
        trace_file = metadata.get("trace_file")
        if not isinstance(trace_file, str) or Path(trace_file).name != trace_file:
            raise ExportError(f"{suite}/{name}: invalid trace filename")
        trace = trace_root / trace_file
        expected_sha = official.TRACE_SHA256[name]
        if metadata.get("trace_sha256") != expected_sha or file_sha256(trace) != expected_sha:
            raise ExportError(f"{suite}/{name}: frozen trace SHA-256 mismatch")
        metadata_path = trace_root / f"{name}.manifest.json"
        if load_json(metadata_path) != metadata:
            raise ExportError(f"{suite}/{name}: per-run metadata differs from index")
        if (metadata.get("generator_version") != GENERATOR_VERSION or
                metadata.get("event_identity_mode") != "address_only" or
                metadata.get("dut_address_fields") != ["logical_source"] or
                metadata.get("dut_payload_fields") != [] or
                metadata["run"].get("sink") != {"mode": "always"}):
            raise ExportError(f"{suite}/{name}: trace semantics are not frozen address-only/always-ready")
    return runs


def read_trace(path: Path, stim_cycles: int) -> list[tuple[int, int, int]]:
    occurrences: list[tuple[int, int, int]] = []
    seen_ids: set[int] = set()
    seen_source_cycle: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExportError(f"{path}:{line_number}: invalid JSON") from error
            if set(event) != TRACE_KEYS:
                raise ExportError(f"{path}:{line_number}: frozen event schema mismatch")
            cycle = event["occurrence_cycle"]
            source = event["logical_source"]
            event_id = event["tb_only_event_id"]
            if not isinstance(cycle, int) or not 0 <= cycle < stim_cycles:
                raise ExportError(f"{path}:{line_number}: occurrence outside stimulus window")
            if not isinstance(source, int) or not 0 <= source < SOURCE_COUNT:
                raise ExportError(f"{path}:{line_number}: invalid logical source")
            if not isinstance(event_id, int) or event_id < 0 or event_id in seen_ids:
                raise ExportError(f"{path}:{line_number}: duplicate/invalid TB event identity")
            if (cycle, source) in seen_source_cycle:
                raise ExportError(f"{path}:{line_number}: repeated source occurrence in one cycle")
            seen_ids.add(event_id)
            seen_source_cycle.add((cycle, source))
            occurrences.append((cycle, source, event_id))
    if sorted(seen_ids) != list(range(len(seen_ids))):
        raise ExportError(f"{path}: TB event identities are not contiguous from zero")
    if occurrences != sorted(occurrences, key=lambda item: (item[0], item[2])):
        raise ExportError(f"{path}: trace occurrence order changed")
    # Concurrent source assertions have no serial ordering at the normalized
    # bitmap boundary.  Canonicalize a cycle by source so a vector round-trip
    # does not invent an event-ID ordering among simultaneous occurrences.
    return sorted(occurrences, key=lambda item: (item[0], item[1]))


def encode_vector(path: Path, cycles: list[dict[str, Any]], expected_generated: int,
                  measurement_window: tuple[int, int], expected_measurement_generated: int,
                  max_accept_retire_latency: int = 0) -> None:
    start, end = measurement_window
    lines = [f"{VECTOR_VERSION} {len(cycles)} {expected_generated} {start} {end} "
             f"{expected_measurement_generated} {max_accept_retire_latency}"]
    for item in cycles:
        encoded = [0] * SOURCE_COUNT
        mask = 0
        for occurrence in item["occurrences"]:
            source = occurrence["source"]
            if encoded[source]:
                raise ExportError(f"cycle {item['cycle']}: duplicate source in vector")
            encoded[source] = occurrence["event_id"] + 1
            mask |= 1 << source
        fields = [item["cycle"], int(item["reset_n"]), int(item["bundle_ready"]), mask, *encoded]
        lines.append(" ".join(str(field) for field in fields))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write("\n".join(lines) + "\n")


def parse_vector(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        header = [int(field) for field in lines[0].split()]
    except (OSError, UnicodeError, ValueError, IndexError) as error:
        raise ExportError(f"{path}: malformed vector header") from error
    if len(header) != 7 or header[0] != VECTOR_VERSION:
        raise ExportError(f"{path}: vector version/header mismatch")
    _, cycle_count, expected_generated, start, end, expected_measured, max_transport = header
    if cycle_count != len(lines) - 1 or not 0 <= start <= end <= cycle_count:
        raise ExportError(f"{path}: vector cardinality/window mismatch")
    occurrences: list[tuple[int, int, int]] = []
    reset_cycles: list[int] = []
    for expected_cycle, line in enumerate(lines[1:]):
        try:
            fields = [int(field) for field in line.split()]
        except ValueError as error:
            raise ExportError(f"{path}: cycle {expected_cycle} has a non-integer field") from error
        if len(fields) != 4 + SOURCE_COUNT:
            raise ExportError(f"{path}: cycle {expected_cycle} field count mismatch")
        cycle, reset_n, ready, mask, *event_codes = fields
        if cycle != expected_cycle or reset_n not in (0, 1) or ready not in (0, 1):
            raise ExportError(f"{path}: cycle/index/control mismatch at {expected_cycle}")
        observed_mask = 0
        for source, code in enumerate(event_codes):
            if code < 0:
                raise ExportError(f"{path}: negative event code")
            if code:
                observed_mask |= 1 << source
                occurrences.append((cycle, source, code - 1))
        if mask != observed_mask:
            raise ExportError(f"{path}: occurrence mask mismatch at cycle {cycle}")
        if not reset_n:
            reset_cycles.append(cycle)
    if len(occurrences) != expected_generated:
        raise ExportError(f"{path}: expected generated count mismatch")
    measured = sum(start <= cycle < end for cycle, _, _ in occurrences)
    if measured != expected_measured:
        raise ExportError(f"{path}: measurement generated count mismatch")
    return {
        "cycle_count": cycle_count,
        "expected_generated": expected_generated,
        "measurement_window": [start, end],
        "expected_measurement_generated": expected_measured,
        "max_accept_retire_latency": max_transport,
        "occurrences": occurrences,
        "reset_cycles": reset_cycles,
    }


def make_trace_run(suite: str, metadata: dict[str, Any], trace_root: Path,
                   vector_root: Path, drain_cycles: int) -> dict[str, Any]:
    name = metadata["run"]["name"]
    stim_cycles = int(metadata["run"]["stim_cycles"])
    trace = trace_root / metadata["trace_file"]
    occurrences = read_trace(trace, stim_cycles)
    by_cycle: dict[int, list[dict[str, int]]] = {}
    for cycle, source, event_id in occurrences:
        by_cycle.setdefault(cycle, []).append({"source": source, "event_id": event_id})
    cycles = [{
        "cycle": cycle, "reset_n": True, "bundle_ready": True,
        "occurrences": by_cycle.get(cycle, []),
    } for cycle in range(stim_cycles + drain_cycles)]
    relative = Path(suite) / f"{name}.a4k2v"
    vector = vector_root / relative
    encode_vector(vector, cycles, len(occurrences), (0, stim_cycles), len(occurrences))
    return {
        "suite": suite,
        "name": name,
        "origin": "exact_generator_v4",
        "trace_file": metadata["trace_file"],
        "trace_sha256": metadata["trace_sha256"],
        "generation_event_count": metadata["event_count"],
        "expected_generated_events": len(occurrences),
        "measurement_window": {"start_cycle": 0, "end_cycle_exclusive": stim_cycles},
        "expected_measurement_generated_events": len(occurrences),
        "drain_cycles": drain_cycles,
        "cycle_count": len(cycles),
        "vector_file": relative.as_posix(),
        "vector_sha256": file_sha256(vector),
        "occurrence_stream_sha256": object_sha256(occurrences),
        "run_provenance_sha256": object_sha256(metadata),
    }


def make_reset_drain_run(vector_root: Path) -> dict[str, Any]:
    # Four live occurrences (plus one overrun) are aborted by reset.  Two
    # post-reset sentinels must then accept and retire before the quiet guard.
    schedule = {
        0: [(0, 0), (4, 1)],
        1: [(0, 2), (9, 3)],
        2: [(15, 4)],
        6: [(2, 5), (10, 6)],
    }
    reset_cycles = {3, 4}
    stalled_cycles = {0, 1, 2}
    cycle_count = 16
    cycles = [{
        "cycle": cycle,
        "reset_n": cycle not in reset_cycles,
        "bundle_ready": cycle not in stalled_cycles,
        "occurrences": [{"source": source, "event_id": event_id}
                        for source, event_id in schedule.get(cycle, [])],
    } for cycle in range(cycle_count)]
    relative = Path("directed") / "reset_drain.a4k2v"
    vector = vector_root / relative
    encode_vector(vector, cycles, 7, (6, 16), 2)
    parsed = parse_vector(vector)
    return {
        "suite": "directed",
        "name": "reset_drain",
        "origin": "a4_directed_reset_drain",
        "trace_file": None,
        "trace_sha256": None,
        "generation_event_count": 7,
        "expected_generated_events": 7,
        "measurement_window": {"start_cycle": 6, "end_cycle_exclusive": 16},
        "expected_measurement_generated_events": 2,
        "expected_reset_cycles": [3, 4],
        "expected_reset_aborted_events": 4,
        "expected_source_overrun_events": 1,
        "drain_cycles": 8,
        "cycle_count": cycle_count,
        "vector_file": relative.as_posix(),
        "vector_sha256": file_sha256(vector),
        "occurrence_stream_sha256": object_sha256(parsed["occurrences"]),
        "run_provenance_sha256": None,
    }


def build_export(a1_repo: Path, trace_root: Path, vector_root: Path,
                 drain_cycles: int = DEFAULT_DRAIN_CYCLES) -> dict[str, Any]:
    if drain_cycles < SOURCE_COUNT:
        raise ExportError("drain allowance must be at least N=16 cycles")
    if trace_root.exists() or vector_root.exists():
        raise ExportError("trace/vector output roots must not already exist")
    official, generator, official_path = load_official(a1_repo)
    trace_root.mkdir(parents=True)
    vector_root.mkdir(parents=True)
    suites: dict[str, Any] = {}
    all_runs: list[dict[str, Any]] = []
    full_trace_sha: dict[str, str] = {}
    for suite in ("full50", "capacity22"):
        manifest = manifest_path(a1_repo, official, suite)
        generated = trace_root / suite
        generate_suite(generator, manifest, generated)
        metadata_rows = validate_generation(generated, suite, manifest, official)
        records = [make_trace_run(suite, metadata, generated, vector_root, drain_cycles)
                   for metadata in metadata_rows]
        if suite == "full50":
            full_trace_sha = {record["name"]: record["trace_sha256"] for record in records}
        else:
            for record in records:
                if full_trace_sha.get(record["name"]) != record["trace_sha256"]:
                    raise ExportError(f"capacity22/{record['name']}: not the exact full50 subset trace")
        suites[suite] = {
            "manifest_name": manifest.name,
            "manifest_sha256": file_sha256(manifest),
            "generation_index_sha256": file_sha256(generated / "generation-index.json"),
            "run_count": len(records),
            "expected_generated_events": sum(record["expected_generated_events"] for record in records),
            "runs": records,
        }
        all_runs.extend(records)
    directed = make_reset_drain_run(vector_root)
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "source_count": SOURCE_COUNT,
        "retire_lanes": RETIRE_LANES,
        "boundary": {
            "name": "a4_normalized_atomic_k2_transaction",
            "acceptance": "ordered grant_count/address bundle commits atomically",
            "retirement": "ordered normalized lanes; TB-only event identity is transport sidecar only",
            "source_capacity": "one pending occurrence per logical source",
            "cycle_semantics": "occurrences enter source latches before the indexed active edge",
            "measurement": "half-open occurrence/retirement cycle window",
            "max_accept_to_retire_latency_cycles": 0,
        },
        "provenance": {
            "official_source_commit": official.SOURCE_COMMIT,
            "generator_version": GENERATOR_VERSION,
            "generator_sha256": file_sha256(generator),
            "official_spec_sha256": file_sha256(official_path),
            "capacity22_is_exact_full50_subset": True,
        },
        "suites": suites,
        "directed_runs": [directed],
        "run_count": len(all_runs) + 1,
    }
    document["bundle_sha256"] = object_sha256(document)
    return document


def verify_export(document: dict[str, Any], vector_root: Path,
                  trace_root: Path | None = None) -> None:
    claimed = document.get("bundle_sha256")
    unhashed = copy.deepcopy(document)
    unhashed.pop("bundle_sha256", None)
    if claimed != object_sha256(unhashed):
        raise ExportError("vector bundle SHA-256 mismatch")
    if (document.get("schema") != SCHEMA or document.get("source_count") != SOURCE_COUNT or
            document.get("retire_lanes") != RETIRE_LANES):
        raise ExportError("vector bundle schema/boundary mismatch")
    records = [record for suite in ("full50", "capacity22")
               for record in document["suites"][suite]["runs"]]
    records.extend(document.get("directed_runs", []))
    if len(records) != document.get("run_count"):
        raise ExportError("vector bundle run count mismatch")
    for record in records:
        relative = Path(record["vector_file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ExportError(f"{record['name']}: vector path escapes root")
        vector = vector_root / relative
        if file_sha256(vector) != record["vector_sha256"]:
            raise ExportError(f"{record['name']}: vector SHA-256 mismatch")
        parsed = parse_vector(vector)
        if (parsed["cycle_count"] != record["cycle_count"] or
                parsed["expected_generated"] != record["expected_generated_events"] or
                parsed["measurement_window"] != [record["measurement_window"]["start_cycle"],
                                                   record["measurement_window"]["end_cycle_exclusive"]] or
                parsed["expected_measurement_generated"] !=
                record["expected_measurement_generated_events"] or
                object_sha256(parsed["occurrences"]) != record["occurrence_stream_sha256"]):
            raise ExportError(f"{record['name']}: vector semantics differ from bundle")
        if trace_root is not None and record["origin"] == "exact_generator_v4":
            trace = trace_root / record["suite"] / record["trace_file"]
            expected = read_trace(trace, record["measurement_window"]["end_cycle_exclusive"])
            if parsed["occurrences"] != expected:
                raise ExportError(f"{record['suite']}/{record['name']}: vector time/source/identity shift")


def write_new(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a1-repo", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--vector-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--drain-cycles", type=int, default=DEFAULT_DRAIN_CYCLES)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"error: refusing to overwrite {args.output}", file=sys.stderr)
        return 2
    try:
        document = build_export(args.a1_repo.resolve(), args.trace_root, args.vector_root,
                                args.drain_cycles)
        verify_export(document, args.vector_root, args.trace_root)
        write_new(args.output, document)
    except (ExportError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"A4_K2_VECTOR_EXPORT_PASS full50=50 capacity22=22 directed=1 sha256={document['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
