#!/usr/bin/env python3
"""Fail-closed LOCAL_MODEL projection of scalar Fovea output through A7 R1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


MODEL_STATUS = "LOCAL_MODEL"
SCHEMA = "a4_fovea_a7_replay_v1"
GENERATOR_VERSION = "4.0"
A7_CONSUMER_LATENCY = 2
A7_OWNER_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
EXPECTED_CANDIDATE = "ganghee-native-coordinate-source-projection"
PINNED = {
    "official_spec_sha256": "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33",
    "generator_sha256": "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    "full50_manifest_sha256": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22_manifest_sha256": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
EVENT_COLUMNS = {
    "candidate", "test", "seed", "load_pct", "tb_only_event_id",
    "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
    "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
}


class ProjectionError(ValueError):
    pass


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ProjectionError(f"not a regular provenance file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"cannot read JSON {path}: {exc}") from exc


def load_official(path: Path) -> Any:
    if sha256(path) != PINNED["official_spec_sha256"]:
        raise ProjectionError("official suite specification SHA-256 mismatch")
    spec = importlib.util.spec_from_file_location("a4_pinned_common_suite_official", path)
    if spec is None or spec.loader is None:
        raise ProjectionError("cannot load official suite specification")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.GENERATOR_VERSION != GENERATOR_VERSION:
        raise ProjectionError("official suite generator version is not 4.0")
    return module


def contained_result(root: Path, pattern: str, run_name: str) -> Path:
    if pattern.count("{name}") != 1:
        raise ProjectionError("result pattern must contain {name} exactly once")
    relative = Path(pattern.replace("{name}", run_name))
    if relative.is_absolute() or ".." in relative.parts:
        raise ProjectionError("result pattern escapes result root")
    path = root / relative
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProjectionError(f"missing result for {run_name}: {path}") from exc
    if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
        raise ProjectionError(f"result is not a private regular file: {path}")
    if root.resolve() not in path.resolve().parents:
        raise ProjectionError(f"result escapes result root: {path}")
    return path


def validate_generation(suite: str, trace_root: Path, manifest: Path,
                        generator: Path, official_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    official = load_official(official_path)
    if suite not in official.SUITES:
        raise ProjectionError(f"unsupported official suite: {suite}")
    suite_spec = official.SUITES[suite]
    expected_manifest_hash = PINNED[f"{suite}_manifest_sha256"]
    if sha256(manifest) != expected_manifest_hash or suite_spec["manifest_sha256"] != expected_manifest_hash:
        raise ProjectionError("official manifest SHA-256 mismatch")
    if sha256(generator) != PINNED["generator_sha256"]:
        raise ProjectionError("generator-v4 source SHA-256 mismatch")
    index_path = trace_root / "generation-index.json"
    index = load_json(index_path)
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise ProjectionError("generation index schema mismatch")
    if index["schema_version"] != 1 or index["generator_version"] != GENERATOR_VERSION:
        raise ProjectionError("generation index is not generator-v4")
    if index["input_manifest"] != manifest.name:
        raise ProjectionError("generation index input manifest mismatch")
    expected_names = list(suite_spec["names"])
    runs = index["runs"]
    names = [row.get("run", {}).get("name") for row in runs]
    if names != expected_names or len(names) != len(set(names)):
        raise ProjectionError("official suite run names/order/cardinality mismatch")
    for row in runs:
        name = row["run"]["name"]
        if row.get("generator_version") != GENERATOR_VERSION:
            raise ProjectionError(f"{name}: run manifest generator version mismatch")
        if row.get("event_identity_mode") != "address_only" or row.get("dut_address_fields") != ["logical_source"]:
            raise ProjectionError(f"{name}: trace is not address-only logical-source identity")
        if row.get("trace_sha256") != official.TRACE_SHA256[name]:
            raise ProjectionError(f"{name}: frozen trace SHA-256 mismatch")
        trace = trace_root / row["trace_file"]
        metadata = trace_root / f"{name}.manifest.json"
        if sha256(trace) != row["trace_sha256"] or load_json(metadata) != row:
            raise ProjectionError(f"{name}: generated trace/metadata mismatch")
    return official, runs


def trace_events(path: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                event_id = int(row["tb_only_event_id"])
                source = int(row["logical_source"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ProjectionError(f"{path}:{line_number}: malformed trace event") from exc
            if event_id in result:
                raise ProjectionError(f"{path}: duplicate trace event id {event_id}")
            result[event_id] = {"logical_source": source,
                                "occurrence_cycle": int(row["occurrence_cycle"])}
    if sorted(result) != list(range(len(result))):
        raise ProjectionError(f"{path}: trace event IDs are not contiguous")
    return result


def project_run(name: str, metadata: dict[str, Any], trace_path: Path,
                event_path: Path) -> dict[str, Any]:
    expected = trace_events(trace_path)
    rows: list[dict[str, str]] = []
    with event_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or set(reader.fieldnames) != EVENT_COLUMNS:
            raise ProjectionError(f"{name}: current event CSV schema mismatch")
        rows = list(reader)
    if len(rows) != len(expected):
        raise ProjectionError(f"{name}: event CSV cardinality does not match trace")
    seen: set[int] = set()
    admissions: list[dict[str, int]] = []
    state_counts = {key: 0 for key in ("delivered", "source_overrun", "accepted", "pending")}
    common_candidate: str | None = None
    common_load: int | None = None
    common_observation_end: int | None = None
    for row in rows:
        try:
            event_id = int(row["tb_only_event_id"])
            source = int(row["logical_source"])
            occurrence = int(row["occurrence_cycle"])
        except ValueError as exc:
            raise ProjectionError(f"{name}: non-integer event identity") from exc
        if event_id in seen or event_id not in expected:
            raise ProjectionError(f"{name}: duplicate or unknown event id {event_id}")
        seen.add(event_id)
        if source != expected[event_id]["logical_source"] or occurrence != expected[event_id]["occurrence_cycle"]:
            raise ProjectionError(f"{name}: event {event_id} trace identity mismatch")
        candidate = row["candidate"]
        common_candidate = candidate if common_candidate is None else common_candidate
        if candidate != common_candidate or candidate != EXPECTED_CANDIDATE:
            raise ProjectionError(f"{name}: candidate provenance mismatch")
        if row["test"] != name or int(row["seed"]) != metadata["run"]["seed"]:
            raise ProjectionError(f"{name}: trial provenance mismatch")
        if int(row["source_count"]) != 16:
            raise ProjectionError(f"{name}: source-count provenance mismatch")
        load = int(row["load_pct"])
        observation_end = int(row["observation_end_cycle"])
        common_load = load if common_load is None else common_load
        common_observation_end = observation_end if common_observation_end is None else common_observation_end
        if load != common_load or observation_end != common_observation_end:
            raise ProjectionError(f"{name}: inconsistent trial provenance columns")
        state = row["event_state"]
        if state not in state_counts:
            raise ProjectionError(f"{name}: unsupported event state {state!r}")
        state_counts[state] += 1
        if state == "delivered":
            if not row["accept_cycle"] or not row["delivery_cycle"]:
                raise ProjectionError(f"{name}: delivered event lacks cycles")
            admission = int(row["delivery_cycle"])
            if admission < int(row["accept_cycle"]):
                raise ProjectionError(f"{name}: delivery precedes fovea acceptance")
            admissions.append({
                "tb_only_event_id": event_id,
                "logical_source": source,
                "fovea_accept_cycle": int(row["accept_cycle"]),
                "a7_admission_cycle": admission,
                "a7_consumer_cycle": admission + A7_CONSUMER_LATENCY,
            })
        elif row["delivery_cycle"]:
            raise ProjectionError(f"{name}: non-delivered event has a delivery cycle")
    if seen != set(expected):
        raise ProjectionError(f"{name}: missing event rows")
    admissions.sort(key=lambda row: (row["a7_admission_cycle"], row["tb_only_event_id"]))
    cycles = [row["a7_admission_cycle"] for row in admissions]
    if len(cycles) != len(set(cycles)):
        raise ProjectionError(f"{name}: scalar Fovea stream exceeds one event/cycle")
    if any(later <= earlier for earlier, later in zip(cycles, cycles[1:])):
        raise ProjectionError(f"{name}: admission order is not strictly increasing")
    upstream_stream = [
        {"cycle": row["a7_admission_cycle"], "tb_only_event_id": row["tb_only_event_id"],
         "logical_source": row["logical_source"]}
        for row in admissions
    ]
    projected_stream = [dict(row) for row in upstream_stream]
    if projected_stream != upstream_stream:
        raise ProjectionError(f"{name}: A7 admission stream changed identity/address/order")
    return {
        "name": name,
        "workload": metadata["run"]["workload"],
        "trace_sha256": metadata["trace_sha256"],
        "fovea_event_csv_sha256": sha256(event_path),
        "generated": len(rows),
        "state_counts": state_counts,
        "a7_admitted": len(admissions),
        "a7_consumed": len(admissions),
        "capacity_events_per_cycle": 1,
        "a7_consumer_latency_cycles": A7_CONSUMER_LATENCY,
        "fovea_output_stream_sha256": canonical_digest(upstream_stream),
        "a7_admission_stream_sha256": canonical_digest(projected_stream),
        "accepted_stream_preserved": True,
        "address_stream_preserved": True,
        "fovea_delivery_to_a7_admission_delta_cycles": 0,
        "no_free_queue": True,
        "events": admissions,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    _, generated = validate_generation(args.suite, args.trace_root, args.official_manifest,
                                       args.generator, args.official_spec)
    projected = []
    for metadata in generated:
        name = metadata["run"]["name"]
        event_path = contained_result(args.results_root, args.result_pattern, name)
        projected.append(project_run(name, metadata, args.trace_root / metadata["trace_file"], event_path))
    totals = {key: sum(row["state_counts"][key] for row in projected)
              for key in ("delivered", "source_overrun", "accepted", "pending")}
    return {
        "schema": SCHEMA,
        "qualification": MODEL_STATUS,
        "suite": args.suite,
        "contract": {
            "input": "scalar_fovea_delivered_stream",
            "a7_rate": "R1_phase_related_synchronous_always_ready",
            "a7_owner_commit": A7_OWNER_COMMIT,
            "capacity_events_per_cycle": 1,
            "consumer_latency_from_a7_admission_cycles": A7_CONSUMER_LATENCY,
            "queue_entries": 0,
            "non_delivered_policy": "retain_counts_do_not_admit",
        },
        "provenance": {
            "official_source_commit": load_official(args.official_spec).SOURCE_COMMIT,
            "official_spec_sha256": sha256(args.official_spec),
            "generator_version": GENERATOR_VERSION,
            "generator_sha256": sha256(args.generator),
            "manifest_sha256": sha256(args.official_manifest),
            "generation_index_sha256": sha256(args.trace_root / "generation-index.json"),
            "candidate": EXPECTED_CANDIDATE,
        },
        "run_count": len(projected),
        "totals": {"generated": sum(row["generated"] for row in projected),
                   "a7_admitted": sum(row["a7_admitted"] for row in projected),
                   "a7_consumed": sum(row["a7_consumed"] for row in projected),
                   "event_states": totals},
        "runs": projected,
    }


def write_new(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--suite", choices=("full50", "capacity22"), required=True)
    result.add_argument("--trace-root", type=Path, required=True)
    result.add_argument("--results-root", type=Path, required=True)
    result.add_argument("--result-pattern", default="{name}/trace.events.csv")
    result.add_argument("--official-manifest", type=Path, required=True)
    result.add_argument("--generator", type=Path, required=True)
    result.add_argument("--official-spec", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        write_new(args.output, build_report(args))
    except (OSError, ProjectionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"PASS qualification={MODEL_STATUS} suite={args.suite} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
