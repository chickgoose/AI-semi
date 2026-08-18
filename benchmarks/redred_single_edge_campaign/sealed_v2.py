#!/usr/bin/env python3
"""Independent validator for version-two REDRED sealed replay tuples."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
from typing import Any


MAX_BUNDLE_BYTES = 1_000_000_000
MAX_MEMBER_BYTES = 600_000_000
MAX_TOTAL_MEMBER_BYTES = 2_000_000_000
MAX_MEMBERS = 4096
PASS_SENTINEL = "A23_SE_ACTUAL_RTL_PASS"
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
EVENT_FIELDS = (
    "owner", "run", "event_id", "logical_source", "occurrence_cycle",
    "accept_cycle", "retire_cycle", "deadline_cycle", "accept_ordinal",
    "retire_ordinal", "event_state",
)
SUMMARY_FIELDS = (
    "owner", "run", "generated", "source_overrun", "accepted", "retired",
    "fixed_window_retired", "measurement_start_cycle", "measurement_end_cycle",
    "observation_cycles", "count2_commits", "reset_test",
    "pre_reset_clean_drain", "protocol_error",
)
LATENCY_KEYS = {"count", "mean", "p50", "p95", "p99", "max"}
RUN_METRIC_KEYS = {
    "generated", "source_overrun", "accepted", "retired",
    "fixed_window_retired", "measurement_start_cycle", "measurement_end_cycle",
    "observation_cycles", "count2_commits", "reset_test",
    "pre_reset_clean_drain", "protocol_error", "occurrence_to_accept",
    "accept_to_retire", "fixed_window_events_per_cycle",
}


class SealedTupleError(RuntimeError):
    """A tuple is unsafe, incomplete, ambiguous, or semantically false."""


SLOT_IDENTITIES = {
    "synthetic_v2": {
        "publication_schema": "redred_single_edge_synthetic_publication_v2",
        "evidence_class": "REDRED_SINGLE_EDGE_SYNTHETIC_ACTUAL_RTL_SEALED_V2",
        "status": "PASS", "source_class": "TEAM_DEFINED_SYNTHETIC",
        "canonical_redred_traffic": True,
    },
    "public_v2": {
        "publication_schema": "redred_single_edge_public_projected_publication_v2",
        "evidence_class": "REDRED_SINGLE_EDGE_PUBLIC_PROJECTED_ACTUAL_RTL_SEALED_V2",
        "status": "PUBLIC_PROJECTED_EXTENSION", "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "canonical_redred_traffic": False,
    },
}


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SealedTupleError(f"{label} must be an object")
    if set(value) != keys:
        raise SealedTupleError(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def strict_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int or int/float coercions."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(strict_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def load_json_bytes(data: bytes, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SealedTupleError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise SealedTupleError(f"{label} contains non-standard JSON constant: {value}")

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SealedTupleError(f"cannot decode {label}: {error}") from error


def canonical_semantic(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SealedTupleError(f"{label} must be lowercase SHA-256")
    return value


def git_oid(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SealedTupleError(f"{label} must be a Git object ID")
    return value


def uint(value: Any, label: str, *, positive: bool = False) -> int:
    floor = 1 if positive else 0
    if type(value) is not int or value < floor:
        raise SealedTupleError(f"{label} must be an integer >= {floor}")
    return value


def safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SealedTupleError(f"{label} must be a canonical POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) \
            or path.as_posix() != value:
        raise SealedTupleError(f"{label} is unsafe or noncanonical: {value!r}")
    return value


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def stable_file(path: Path, label: str) -> tuple[Path, bytes, tuple[int, int, int, int, int]]:
    if ".." in path.parts:
        raise SealedTupleError(f"{label} aliases through '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise SealedTupleError(f"{label} traverses a symlink")
    try:
        resolved = absolute.resolve(strict=True)
        before = resolved.stat()
    except OSError as error:
        raise SealedTupleError(f"{label} is missing") from error
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SealedTupleError(f"{label} must be one unaliased regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                before.st_dev, before.st_ino, before.st_size,
            ):
                raise SealedTupleError(f"{label} changed before read")
            chunks: list[bytes] = []
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SealedTupleError(f"cannot read {label}: {error}") from error
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened, field) != getattr(after, field) for field in fields):
        raise SealedTupleError(f"{label} changed while read")
    return resolved, b"".join(chunks), (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    )


def recheck_file(resolved: Path, identity: tuple[int, int, int, int, int], label: str) -> None:
    try:
        if not resolved.is_file() or resolved.is_symlink() or _file_identity(resolved) != identity:
            raise SealedTupleError(f"{label} changed after validation")
    except OSError as error:
        raise SealedTupleError(f"{label} changed after validation") from error


def archive_members(bundle: bytes) -> dict[str, bytes]:
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise SealedTupleError("sealed bundle exceeds compressed size limit")
    members: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            for member in archive:
                name = safe_relative(member.name, "archive member")
                if name in members:
                    raise SealedTupleError(f"duplicate archive member: {name}")
                if not member.isfile() or member.issym() or member.islnk():
                    raise SealedTupleError(f"archive member is not regular: {name}")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise SealedTupleError(f"archive member size is unsafe: {name}")
                total += member.size
                if total > MAX_TOTAL_MEMBER_BYTES or len(members) >= MAX_MEMBERS:
                    raise SealedTupleError("sealed bundle exceeds expansion limits")
                source = archive.extractfile(member)
                if source is None:
                    raise SealedTupleError(f"cannot read archive member: {name}")
                data = source.read(member.size + 1)
                if len(data) != member.size:
                    raise SealedTupleError(f"archive member size differs: {name}")
                members[name] = data
    except (tarfile.TarError, OSError) as error:
        raise SealedTupleError(f"cannot read sealed bundle: {error}") from error
    return members


def parse_int(value: str, label: str) -> int:
    if not INTEGER.fullmatch(value):
        raise SealedTupleError(f"{label} is not a canonical integer")
    return int(value)


def csv_records(data: bytes, fields: tuple[str, ...], label: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    except UnicodeDecodeError as error:
        raise SealedTupleError(f"{label} is not UTF-8 CSV") from error
    if tuple(reader.fieldnames or ()) != fields:
        raise SealedTupleError(f"{label} CSV header/order differs")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise SealedTupleError(f"{label} contains malformed rows")
    return rows


def jsonl_records(data: bytes, label: str) -> list[dict[str, Any]]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SealedTupleError(f"{label} is not UTF-8 JSONL") from error
    rows = []
    keys = {"event_id", "logical_source", "occurrence_cycle", "deadline_cycle"}
    for index, line in enumerate(lines):
        row = exact(load_json_bytes(line.encode(), f"{label}:{index+1}"), keys, label)
        if row["event_id"] != index:
            raise SealedTupleError(f"{label} event identity/order differs")
        for key in keys:
            if type(row[key]) is not int:
                raise SealedTupleError(f"{label}.{key} must be an integer")
        if not 0 <= row["logical_source"] < 16 or row["occurrence_cycle"] < 0 \
                or row["deadline_cycle"] < row["occurrence_cycle"]:
            raise SealedTupleError(f"{label} event provenance differs")
        rows.append(row)
    return rows


def validate_prepared(data: bytes, trace: list[dict[str, Any]], label: str) -> None:
    try:
        lines = data.decode("utf-8").splitlines()
        header = [int(value) for value in lines[0].split()]
    except (UnicodeDecodeError, ValueError, IndexError) as error:
        raise SealedTupleError(f"{label} is not a prepared trace") from error
    if len(header) != 9 or header[0] != 4 or header[1] != len(trace) \
            or len(lines) != len(trace) + 1:
        raise SealedTupleError(f"{label} header/cardinality differs")
    for index, (line, source) in enumerate(zip(lines[1:], trace)):
        try:
            values = [int(value) for value in line.split()]
        except ValueError as error:
            raise SealedTupleError(f"{label} row {index} is malformed") from error
        expected = [source["occurrence_cycle"], index, source["logical_source"],
                    source["logical_source"], source["deadline_cycle"]]
        if values != expected:
            raise SealedTupleError(f"{label} row {index} differs from source trace")


def latency(values: list[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(.50), "p95": percentile(.95),
        "p99": percentile(.99), "max": ordered[-1],
    }


def validate_run(
    owner: str, run: str, event_data: bytes, summary_data: bytes,
    trace: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[int], list[int]]:
    events = csv_records(event_data, EVENT_FIELDS, f"{owner}/{run}/events")
    summaries = csv_records(summary_data, SUMMARY_FIELDS, f"{owner}/{run}/summary")
    if len(summaries) != 1:
        raise SealedTupleError(f"{owner}/{run} summary cardinality differs")
    summary = summaries[0]
    if summary["owner"] != owner or summary["run"] != run:
        raise SealedTupleError(f"{owner}/{run} summary identity differs")
    if trace is not None and len(trace) != len(events):
        raise SealedTupleError(f"{owner}/{run} trace/event cardinality differs")
    occurrence_accept: list[int] = []
    accept_retire: list[int] = []
    accept_ordinals: list[int] = []
    retire_ordinals: list[int] = []
    accept_timeline: list[tuple[int, int]] = []
    retire_timeline: list[tuple[int, int]] = []
    source_busy_through = [-1] * 16
    previous_occurrence = -1
    overruns = retired = fixed = 0
    start = parse_int(summary["measurement_start_cycle"], "measurement_start_cycle")
    end = parse_int(summary["measurement_end_cycle"], "measurement_end_cycle")
    observation = parse_int(summary["observation_cycles"], "observation_cycles")
    if start < 0 or end < start or observation < end:
        raise SealedTupleError(f"{owner}/{run} measurement interval differs")
    for expected_id, event in enumerate(events):
        if event["owner"] != owner or event["run"] != run \
                or parse_int(event["event_id"], "event_id") != expected_id:
            raise SealedTupleError(f"{owner}/{run} event identity/order differs")
        source = parse_int(event["logical_source"], "logical_source")
        occurrence = parse_int(event["occurrence_cycle"], "occurrence_cycle")
        deadline = parse_int(event["deadline_cycle"], "deadline_cycle")
        accept = parse_int(event["accept_cycle"], "accept_cycle")
        retire = parse_int(event["retire_cycle"], "retire_cycle")
        accept_ordinal = parse_int(event["accept_ordinal"], "accept_ordinal")
        retire_ordinal = parse_int(event["retire_ordinal"], "retire_ordinal")
        if not 0 <= source < 16 or occurrence < 0 or deadline < occurrence:
            raise SealedTupleError(f"{owner}/{run} event provenance differs")
        if occurrence < previous_occurrence:
            raise SealedTupleError(f"{owner}/{run} occurrence order differs")
        previous_occurrence = occurrence
        if trace is not None:
            expected = trace[expected_id]
            if (source, occurrence, deadline) != (
                expected["logical_source"], expected["occurrence_cycle"],
                expected["deadline_cycle"],
            ):
                raise SealedTupleError(f"{owner}/{run} event differs from source trace")
        if event["event_state"] == "source_overrun":
            if (accept, retire, accept_ordinal, retire_ordinal) != (-1, -1, -1, -1):
                raise SealedTupleError(f"{owner}/{run} overrun carries endpoint identity")
            if occurrence > source_busy_through[source]:
                raise SealedTupleError(f"{owner}/{run} source-latch replay differs")
            overruns += 1
        elif event["event_state"] == "retired":
            if not occurrence <= accept <= retire <= observation or accept_ordinal < 0 \
                    or retire_ordinal != accept_ordinal:
                raise SealedTupleError(f"{owner}/{run} retired timing/order differs")
            if occurrence <= source_busy_through[source]:
                raise SealedTupleError(f"{owner}/{run} source-latch replay differs")
            source_busy_through[source] = accept
            accept_ordinals.append(accept_ordinal)
            retire_ordinals.append(retire_ordinal)
            accept_timeline.append((accept, accept_ordinal))
            retire_timeline.append((retire, retire_ordinal))
            occurrence_accept.append(accept - occurrence)
            accept_retire.append(retire - accept)
            retired += 1
            if start <= retire <= end:
                fixed += 1
        else:
            raise SealedTupleError(f"{owner}/{run} contains nonterminal event")
    if sorted(accept_ordinals) != list(range(retired)) \
            or sorted(retire_ordinals) != list(range(retired)):
        raise SealedTupleError(f"{owner}/{run} acceptance/retirement ordinal closure differs")
    if [ordinal for _, ordinal in sorted(accept_timeline)] != list(range(retired)) \
            or [ordinal for _, ordinal in sorted(retire_timeline)] != list(range(retired)):
        raise SealedTupleError(f"{owner}/{run} acceptance/retirement temporal order differs")
    numeric_keys = (
        "generated", "source_overrun", "accepted", "retired",
        "fixed_window_retired", "count2_commits", "reset_test",
        "pre_reset_clean_drain", "protocol_error",
    )
    numeric = {key: parse_int(summary[key], f"summary.{key}") for key in numeric_keys}
    if any(value < 0 for value in numeric.values()):
        raise SealedTupleError(f"{owner}/{run} summary contains negative count")
    expected_counts = {
        "generated": len(events), "source_overrun": overruns,
        "accepted": retired, "retired": retired, "fixed_window_retired": fixed,
    }
    if any(numeric[key] != value for key, value in expected_counts.items()) \
            or numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise SealedTupleError(f"{owner}/{run} conservation differs")
    width = end - start + 1
    metrics = {
        **numeric, "measurement_start_cycle": start, "measurement_end_cycle": end,
        "observation_cycles": observation,
        "occurrence_to_accept": latency(occurrence_accept),
        "accept_to_retire": latency(accept_retire),
        "fixed_window_events_per_cycle": round(fixed / width, 9),
    }
    return metrics, occurrence_accept, accept_retire


def validate_tuple(
    publication_path: Path, bundle_path: Path, binding: dict[str, Any], kind: str,
) -> dict[str, Any]:
    binding = exact(binding, {
        "publication_sha256", "publication_size_bytes", "publication_schema",
        "evidence_class", "status", "source_class", "canonical_redred_traffic",
        "official_contest_traffic", "p6_evidence_used", "release_status",
        "selection_status", "producer", "rtl", "bundle_sha256",
        "bundle_size_bytes", "manifest_schema", "manifest_member",
        "manifest_sha256", "entry_count", "result_schema", "result_member",
        "result_sha256", "result_semantic_sha256", "result_size_bytes", "owners", "traffic_runs",
        "reset_run", "activation_run", "mutations", "diagnostics",
    }, f"{kind} binding")
    producer = exact(binding["producer"], {
        "commit", "tree", "verifier_sha256", "schema_sha256", "runner_sha256",
        "testbench_sha256", "tool_pins_sha256", "inventory",
    }, f"{kind} producer binding")
    rtl = exact(binding["rtl"], {
        "source_commit", "source_tree", "integration_commit", "integration_tree",
        "inventory",
    }, f"{kind} RTL binding")
    for key in ("commit", "tree"):
        if not isinstance(producer[key], str) or not re.fullmatch(r"[0-9a-f]{40}", producer[key]):
            raise SealedTupleError(f"{kind} producer.{key} must be a Git object ID")
    for key in ("verifier_sha256", "schema_sha256", "runner_sha256", "testbench_sha256", "tool_pins_sha256"):
        sha(producer[key], f"{kind} producer.{key}")
    for key, value in rtl.items():
        if key != "inventory" and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)):
            raise SealedTupleError(f"{kind} rtl.{key} must be a Git object ID")
    if kind not in SLOT_IDENTITIES:
        raise SealedTupleError(f"unknown sealed tuple slot: {kind}")
    slot = SLOT_IDENTITIES[kind]
    for key, expected in slot.items():
        if not strict_equal(binding[key], expected):
            raise SealedTupleError(f"{kind} binding classification/schema differs")
    for key in ("official_contest_traffic", "p6_evidence_used"):
        if type(binding[key]) is not bool or binding[key] is not False:
            raise SealedTupleError(f"{kind} binding disallows {key}")
    if binding["release_status"] != "HOLD" or binding["selection_status"] != "HOLD":
        raise SealedTupleError(f"{kind} binding release/selection differs")

    repo_root = Path(__file__).resolve().parents[2]

    def git_output(*arguments: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo_root), *arguments],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise SealedTupleError(f"{kind} producer provenance is not resolvable") from error

    def verify_git_pair(commit: str, tree: str, label: str, inventory: Any) -> None:
        if git_output("cat-file", "-t", commit) != "commit" \
                or git_output("cat-file", "-t", tree) != "tree" \
                or git_output("rev-parse", f"{commit}^{{tree}}") != tree:
            raise SealedTupleError(f"{kind} {label} commit/tree relationship differs")
        if not isinstance(inventory, list) or not inventory or len(inventory) != len({
            row.get("path") for row in inventory if isinstance(row, dict)
        }):
            raise SealedTupleError(f"{kind} {label} inventory is malformed")
        for row in inventory:
            row = exact(row, {"path", "blob_sha256"}, f"{kind} {label} inventory row")
            path = safe_relative(row["path"], f"{kind} {label} inventory path")
            blob = git_oid(row["blob_sha256"], f"{kind} {label} inventory blob")
            if git_output("rev-parse", f"{commit}:{path}") != blob \
                    or git_output("cat-file", "-t", blob) != "blob":
                raise SealedTupleError(f"{kind} {label} inventory bytes differ: {path}")

    verify_git_pair(producer["commit"], producer["tree"], "producer", producer["inventory"])
    verify_git_pair(rtl["source_commit"], rtl["source_tree"], "RTL source", rtl["inventory"])
    verify_git_pair(rtl["integration_commit"], rtl["integration_tree"], "RTL integration", rtl["inventory"])
    def roster(value: Any, label: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value or len(value) != len(set(value)):
            raise SealedTupleError(f"{kind} {label} roster is malformed")
        for item in value:
            if not isinstance(item, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", item):
                raise SealedTupleError(f"{kind} {label} name is unsafe")
        return tuple(value)

    owners = roster(binding["owners"], "owner")
    traffic = roster(binding["traffic_runs"], "traffic run")
    mutation_names = roster(binding["mutations"], "mutation")
    reset_run = binding["reset_run"]
    activation_run = binding["activation_run"]
    if owners != ("a2", "a3") or not isinstance(reset_run, str) \
            or not isinstance(activation_run, str) \
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", reset_run) \
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", activation_run):
        raise SealedTupleError(f"{kind} bound owner/run roster is malformed")
    all_runs = (*traffic, reset_run, activation_run)
    if len(set(all_runs)) != len(all_runs):
        raise SealedTupleError(f"{kind} bound run roster overlaps")
    diagnostics = exact(binding["diagnostics"], set(mutation_names), f"{kind} diagnostics")
    if any(not isinstance(value, str) or not value or "\n" in value for value in diagnostics.values()):
        raise SealedTupleError(f"{kind} mutation diagnostics are malformed")
    publication_resolved, publication_data, publication_identity = stable_file(
        publication_path, f"{kind} publication",
    )
    bundle_resolved, bundle_data, bundle_identity = stable_file(bundle_path, f"{kind} bundle")
    if publication_resolved == bundle_resolved or publication_resolved.samefile(bundle_resolved):
        raise SealedTupleError(f"{kind} publication and bundle are aliases")
    if len(publication_data) != uint(binding["publication_size_bytes"], "publication size", positive=True) \
            or digest(publication_data) != sha(binding["publication_sha256"], "publication SHA"):
        raise SealedTupleError(f"{kind} publication binding differs")
    if len(bundle_data) != uint(binding["bundle_size_bytes"], "bundle size", positive=True) \
            or digest(bundle_data) != sha(binding["bundle_sha256"], "bundle SHA"):
        raise SealedTupleError(f"{kind} bundle binding differs")
    publication = exact(load_json_bytes(publication_data, f"{kind} publication"), {
        "schema", "evidence_class", "status", "source_class",
        "canonical_redred_traffic", "official_contest_traffic", "p6_evidence_used",
        "release_status", "selection_status", "producer", "rtl", "bundle", "result",
    }, f"{kind} publication")
    expected_scalars = {
        key: binding[key] for key in (
            "publication_schema", "evidence_class", "status", "source_class",
            "canonical_redred_traffic", "official_contest_traffic", "p6_evidence_used",
            "release_status", "selection_status",
        )
    }
    expected_scalars["schema"] = expected_scalars.pop("publication_schema")
    if any(not strict_equal(publication[key], value) for key, value in expected_scalars.items()) \
            or not strict_equal(publication["producer"], binding["producer"]) \
            or not strict_equal(publication["rtl"], binding["rtl"]):
        raise SealedTupleError(f"{kind} publication identity/classification differs")
    manifest_member = safe_relative(binding["manifest_member"], "manifest member")
    result_member = safe_relative(binding["result_member"], "result member")
    expected_members = {manifest_member, result_member}
    expected_members.update(f"inputs/{run}.jsonl" for run in traffic)
    expected_members.update(f"prepared/{run}.trace" for run in traffic)
    for owner in owners:
        for run in all_runs:
            base = f"runs/{owner}/{run}"
            expected_members.update({
                f"{base}/events.csv", f"{base}/summary.csv", f"{base}/simulation.log",
            })
        expected_members.update(f"mutations/{owner}/{mutation}.log" for mutation in mutation_names)
    expected_bundle = {
        "sha256": binding["bundle_sha256"], "size_bytes": binding["bundle_size_bytes"],
        "manifest_member": manifest_member, "manifest_sha256": binding["manifest_sha256"],
        "entry_count": binding["entry_count"],
    }
    expected_result = {
        "member": result_member, "sha256": binding["result_sha256"],
        "semantic_sha256": binding["result_semantic_sha256"],
        "size_bytes": binding["result_size_bytes"],
    }
    if not strict_equal(publication["bundle"], expected_bundle) \
            or not strict_equal(publication["result"], expected_result):
        raise SealedTupleError(f"{kind} publication seal differs")
    members = archive_members(bundle_data)
    if len(members) != uint(binding["entry_count"], "entry count", positive=True):
        raise SealedTupleError(f"{kind} bundle entry count differs")
    if set(members) != expected_members:
        raise SealedTupleError(f"{kind} bundle member roster differs")
    if manifest_member not in members or result_member not in members:
        raise SealedTupleError(f"{kind} bundle lacks manifest or result")
    if digest(members[manifest_member]) != sha(binding["manifest_sha256"], "manifest SHA"):
        raise SealedTupleError(f"{kind} manifest binding differs")
    manifest = exact(load_json_bytes(members[manifest_member], f"{kind} manifest"), {
        "schema", "evidence_class", "entries",
    }, f"{kind} manifest")
    if manifest["schema"] != binding["manifest_schema"] or \
            manifest["evidence_class"] != binding["evidence_class"]:
        raise SealedTupleError(f"{kind} manifest identity differs")
    entries = manifest["entries"]
    if not isinstance(entries, dict) or set(entries) != set(members) - {manifest_member}:
        raise SealedTupleError(f"{kind} manifest closure differs")
    for path, metadata in entries.items():
        safe_relative(path, "manifest entry")
        metadata = exact(metadata, {"sha256", "size_bytes"}, f"entry {path}")
        if len(members[path]) != uint(metadata["size_bytes"], f"entry {path} size", positive=True) \
                or digest(members[path]) != sha(metadata["sha256"], f"entry {path} SHA"):
            raise SealedTupleError(f"{kind} manifest member bytes differ: {path}")
    result_data = members[result_member]
    if len(result_data) != uint(binding["result_size_bytes"], "result size", positive=True) \
            or digest(result_data) != binding["result_sha256"]:
        raise SealedTupleError(f"{kind} result raw hash differs")
    result = load_json_bytes(result_data, f"{kind} result")
    if digest(canonical_semantic(result)) != binding["result_semantic_sha256"]:
        raise SealedTupleError(f"{kind} result semantic hash differs")
    result = exact(result, {
        "schema", "evidence_class", "status", "source_class",
        "canonical_redred_traffic", "official_contest_traffic", "p6_evidence_used",
        "release_status", "selection_status", "owners", "mutations",
    }, f"{kind} result")
    for key in (
        "evidence_class", "status", "source_class", "canonical_redred_traffic",
        "official_contest_traffic", "p6_evidence_used", "release_status", "selection_status",
    ):
        if not strict_equal(result[key], binding[key]):
            raise SealedTupleError(f"{kind} result classification differs: {key}")
    if result["schema"] != binding["result_schema"]:
        raise SealedTupleError(f"{kind} result schema differs")
    result_owners = exact(result["owners"], set(owners), f"{kind} owners")
    computed_owners: dict[str, Any] = {}
    for owner in owners:
        owner_result = exact(result_owners[owner], {"runs", "aggregate"}, f"{kind}.{owner}")
        result_runs = exact(owner_result["runs"], set(all_runs), f"{kind}.{owner}.runs")
        occurrence_all: list[int] = []
        internal_all: list[int] = []
        calculated: dict[str, dict[str, Any]] = {}
        for run in all_runs:
            trace = None
            if run in traffic:
                trace_member = f"inputs/{run}.jsonl"
                prepared_member = f"prepared/{run}.trace"
                if trace_member not in members or prepared_member not in members:
                    raise SealedTupleError(f"{kind} lacks source/prepared input: {run}")
                trace = jsonl_records(members[trace_member], f"{kind} trace {run}")
                validate_prepared(members[prepared_member], trace, f"{kind} prepared {run}")
            base = f"runs/{owner}/{run}"
            required = (f"{base}/events.csv", f"{base}/summary.csv", f"{base}/simulation.log")
            if any(path not in members for path in required):
                raise SealedTupleError(f"{kind} lacks run artifacts: {owner}/{run}")
            metrics, occurrence, internal = validate_run(
                owner, run, members[required[0]], members[required[1]], trace,
            )
            log = members[required[2]].decode("utf-8", errors="strict")
            if log.count(PASS_SENTINEL) != 1:
                raise SealedTupleError(f"{kind} run lacks exactly one PASS: {owner}/{run}")
            if run == binding["reset_run"]:
                if metrics["reset_test"] != 1 or metrics["pre_reset_clean_drain"] != 1 \
                        or metrics["protocol_error"] != 0:
                    raise SealedTupleError(f"{kind} reset clean-drain proof differs: {owner}")
            elif metrics["reset_test"] != 0 or metrics["protocol_error"] != 0:
                raise SealedTupleError(f"{kind} non-reset run classification differs")
            if run == binding["activation_run"] and metrics["count2_commits"] < 1:
                raise SealedTupleError(f"{kind} mutation activation is vacuous: {owner}")
            claim = exact(result_runs[run], RUN_METRIC_KEYS, f"{kind}.{owner}.{run}")
            if not strict_equal(claim, metrics):
                raise SealedTupleError(f"{kind} result run claim differs: {owner}/{run}")
            calculated[run] = metrics
            if run in traffic:
                occurrence_all.extend(occurrence)
                internal_all.extend(internal)
        totals = {
            key: sum(calculated[run][key] for run in traffic)
            for key in ("generated", "source_overrun", "accepted", "retired", "fixed_window_retired")
        }
        total_cycles = sum(
            calculated[run]["measurement_end_cycle"] - calculated[run]["measurement_start_cycle"] + 1
            for run in traffic
        )
        aggregate = {
            "run_count": len(traffic), "totals": totals,
            "occurrence_to_accept": latency(occurrence_all),
            "accept_to_retire": latency(internal_all),
            "fixed_window_events_per_cycle": round(totals["fixed_window_retired"] / total_cycles, 9),
        }
        if not strict_equal(owner_result["aggregate"], aggregate):
            raise SealedTupleError(f"{kind} aggregate differs: {owner}")
        computed_owners[owner] = aggregate
    expected_mutations = [(owner, mutation) for owner in owners for mutation in mutation_names]
    mutations = result["mutations"]
    if not isinstance(mutations, list) or len(mutations) != len(expected_mutations):
        raise SealedTupleError(f"{kind} mutation roster differs")
    for row, (owner, mutation) in zip(mutations, expected_mutations):
        row = exact(row, {"owner", "mutation", "killed", "first_diagnostic", "log_sha256"}, "mutation")
        path = f"mutations/{owner}/{mutation}.log"
        if not strict_equal(row, {
            "owner": owner, "mutation": mutation, "killed": True,
            "first_diagnostic": diagnostics[mutation],
            "log_sha256": digest(members.get(path, b"")),
        }) or path not in members:
            raise SealedTupleError(f"{kind} mutation claim differs: {owner}/{mutation}")
        text = members[path].decode("utf-8", errors="strict")
        if diagnostics[mutation] not in text or PASS_SENTINEL in text:
            raise SealedTupleError(f"{kind} mutation log semantics differ: {owner}/{mutation}")
    recheck_file(publication_resolved, publication_identity, f"{kind} publication")
    recheck_file(bundle_resolved, bundle_identity, f"{kind} bundle")
    return {
        "status": "PASS", "evidence_class": binding["evidence_class"],
        "source_class": binding["source_class"], "owners": computed_owners,
        "traffic_run_count": len(traffic), "bundle_sha256": digest(bundle_data),
        "publication_sha256": digest(publication_data),
    }
