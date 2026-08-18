#!/usr/bin/env python3
"""Fail-closed v2 actual-RTL producer for the UZH public projection."""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Callable, Iterable
import zlib


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
LEGACY_REPLAY = PROJECT / "tests/a23_full_single_edge_replay"
PINS = PACKAGE / "pins.json"
BASE_PINS = LEGACY_REPLAY / "pins.json"
DEFAULT_PROJECTION = Path("/tmp/redred-uzh-shapes-projection-f59c10e")
DEFAULT_VERILATOR = Path("/tmp/a7-toolchain/usr/bin/verilator")
SCENARIOS = ("1x", "64x", "256x")
OWNERS = ("a2", "a3")
MUTATIONS = ("drop", "duplicate", "reorder", "reset_escape")
PROJECTION_NAMES = (
    "COMPLETE.json", "LICENSE.txt", "projected_events.jsonl", "receipt.json",
    "trace_1x.jsonl", "trace_64x.jsonl", "trace_256x.jsonl",
)
TRACE_FIELDS = {
    "occurrence_cycle", "tb_only_event_id", "logical_source", "x", "y",
    "polarity", "event_type", "relation_id", "relation_role", "deadline",
}
CLASSIFICATION = {
    "status": "PUBLIC_PROJECTED_EXTENSION",
    "release_status": "HOLD",
    "selection_status": "HOLD",
    "canonical_redred_traffic": False,
    "official_redred_traffic": False,
    "p6_evidence_used": False,
}
EXPECTED_DIAGNOSTIC = {
    "drop": "A23_SE_DROP_FAIL",
    "duplicate": "A23_SE_DUPLICATE_FAIL",
    "reorder": "A23_SE_REORDER_FAIL",
    "reset_escape": "A23_SE_RESET_ESCAPE_FAIL",
}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")

sys.path.insert(0, str(LEGACY_REPLAY))
import run_replay as base  # noqa: E402


class PublicV2Error(RuntimeError):
    """Any contract violation aborts without publishing evidence."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def pretty(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                   allow_nan=False) + "\n"
    ).encode("ascii")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:-1]:
        current /= component
        try:
            info = current.lstat()
        except OSError as error:
            raise PublicV2Error(f"cannot inspect path component: {current}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise PublicV2Error(f"symlink path component is forbidden: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise PublicV2Error(f"non-directory path component: {current}")


def stable_read(
    path: Path, label: str, *, after_open: Callable[[], None] | None = None,
) -> bytes:
    """Read one single-link regular file and detect path/fd identity races."""
    reject_symlink_components(path)
    try:
        before = path.lstat()
    except OSError as error:
        raise PublicV2Error(f"cannot inspect {label}: {error}") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PublicV2Error(f"{label} must be a non-symlink regular file")
    if before.st_nlink != 1:
        raise PublicV2Error(f"{label} must have exactly one hard link")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise PublicV2Error(f"cannot securely open {label}: {error}") from error
    try:
        opened = os.fstat(fd)
        if _identity(opened) != _identity(before):
            raise PublicV2Error(f"{label} changed while being opened")
        if after_open is not None:
            after_open()
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        after_path = path.lstat()
    except OSError as error:
        raise PublicV2Error(f"{label} disappeared during read: {error}") from error
    if (_identity(after_fd) != _identity(opened) or
            _identity(after_path) != _identity(opened)):
        raise PublicV2Error(f"{label} changed during read")
    return b"".join(chunks)


def load_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PublicV2Error(f"invalid JSON for {label}: {error}") from error
    if not isinstance(value, dict):
        raise PublicV2Error(f"JSON root is not an object: {label}")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    return load_json_bytes(stable_read(path, label), label)


def require_classification(document: dict[str, Any], layer: str) -> None:
    for field, expected in CLASSIFICATION.items():
        if document.get(field) != expected or type(document.get(field)) is not type(expected):
            raise PublicV2Error(f"{layer} {field} must be exactly {expected!r}")


def require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise PublicV2Error(f"invalid SHA-256: {label}")
    return value


def require_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX40.fullmatch(value) is None:
        raise PublicV2Error(f"invalid Git commit: {label}")
    process = subprocess.run(
        ["git", "cat-file", "-e", f"{value}^{{commit}}"], cwd=PROJECT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise PublicV2Error(f"Git commit is unavailable: {label}")
    return value


def git_bytes(commit: str, relative: str) -> bytes:
    process = subprocess.run(
        ["git", "show", f"{commit}:{relative}"], cwd=PROJECT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise PublicV2Error(f"Git object lacks {relative} at {commit}")
    return process.stdout


def validate_pins(document: dict[str, Any], *, permit_unbound: bool = False) -> None:
    if document.get("schema") != "a23_public_projected_v2_pins_v2":
        raise PublicV2Error("pins schema mismatch")
    require_classification(document, "pins")
    if document.get("evidence_class") != "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL":
        raise PublicV2Error("pins evidence class mismatch")
    if document.get("reset_scope") != "CLEAN_DRAIN_ONLY":
        raise PublicV2Error("pins reset scope must be CLEAN_DRAIN_ONLY")
    if document.get("identity_accounting") != {
        "unique_projected_window_events": 1100,
        "scenario_retimings": 3,
        "pooled_3300_unique_events": False,
    }:
        raise PublicV2Error("pins identity accounting mismatch")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or tuple(row.get("id") for row in scenarios) != SCENARIOS:
        raise PublicV2Error("pins scenario order mismatch")
    if any(row.get("event_count") != 1100 for row in scenarios):
        raise PublicV2Error("pins scenario event count mismatch")
    provenance = document.get("commit_provenance")
    if not isinstance(provenance, dict):
        raise PublicV2Error("pins commit provenance missing")
    require_commit(provenance.get("integration_commit"), "integration_commit")
    source = provenance.get("execution_source_commit")
    if permit_unbound and source == "UNBOUND_UNTIL_IMPLEMENTATION_COMMIT":
        pass
    else:
        require_commit(source, "execution_source_commit")
    contract = provenance.get("publication_commit_contract")
    if contract != {
        "binding_layer": "PUBLICATION_RECORD_ONLY",
        "meaning": "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS",
        "self_referential_commit_claim": False,
    }:
        raise PublicV2Error("publication commit contract mismatch")
    if provenance.get("p6_evidence_used") is not False:
        raise PublicV2Error("pins commit provenance P6 must be false")
    if document.get("export_inventory") != {
        "schema": "a23_public_projected_v2_closed_inventory_v2",
        "entry_count_excluding_manifest": 80,
        "extra_entries_allowed": False,
    }:
        raise PublicV2Error("closed export inventory contract mismatch")


def load_pins(*, permit_unbound: bool = False) -> dict[str, Any]:
    document = load_json(PINS, "v2 pins")
    validate_pins(document, permit_unbound=permit_unbound)
    return document


def exact_projection_payloads(projection_dir: Path) -> dict[str, bytes]:
    reject_symlink_components(projection_dir / "sentinel")
    try:
        root_info = projection_dir.lstat()
    except OSError as error:
        raise PublicV2Error(f"projection directory unavailable: {error}") from error
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise PublicV2Error("projection root must be a non-symlink directory")
    try:
        names = tuple(sorted(os.listdir(projection_dir)))
    except OSError as error:
        raise PublicV2Error(f"cannot inventory projection directory: {error}") from error
    if names != tuple(sorted(PROJECTION_NAMES)):
        raise PublicV2Error("projection directory is not the exact closed seven-name inventory")
    payloads = {name: stable_read(projection_dir / name, f"projection/{name}") for name in PROJECTION_NAMES}
    if tuple(sorted(os.listdir(projection_dir))) != tuple(sorted(PROJECTION_NAMES)):
        raise PublicV2Error("projection directory changed during exact inventory read")
    return payloads


def parse_trace(payload: bytes, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    if digest_bytes(payload) != scenario.get("trace_sha256"):
        raise PublicV2Error(f"wrong projected trace hash: {scenario.get('id')}")
    rows: list[dict[str, Any]] = []
    previous = (-1, -1)
    for number, raw in enumerate(payload.splitlines(keepends=True), start=1):
        if not raw.endswith(b"\n"):
            raise PublicV2Error(f"projected trace lacks LF: {scenario['id']}:{number}")
        try:
            row = json.loads(raw[:-1].decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PublicV2Error(f"invalid projected JSONL: {scenario['id']}:{number}") from error
        if not isinstance(row, dict) or set(row) != TRACE_FIELDS:
            raise PublicV2Error(f"projected trace field mismatch: {scenario['id']}:{number}")
        event_id = len(rows)
        cycle, source = row["occurrence_cycle"], row["logical_source"]
        if row["tb_only_event_id"] != event_id:
            raise PublicV2Error(f"projected event order mismatch: {scenario['id']}:{number}")
        if (isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 0 or
                isinstance(source, bool) or not isinstance(source, int) or not 0 <= source < 16):
            raise PublicV2Error(f"projected scalar mismatch: {scenario['id']}:{number}")
        if (row["x"] + 4 * row["y"] != source or row["polarity"] not in (-1, 1) or
                row["event_type"] != "public_projected_event" or
                row["relation_id"] is not None or row["relation_role"] is not None or
                isinstance(row["deadline"], bool) or not isinstance(row["deadline"], int) or
                row["deadline"] < cycle):
            raise PublicV2Error(f"projected semantics mismatch: {scenario['id']}:{number}")
        if (cycle, event_id) < previous:
            raise PublicV2Error(f"projected occurrence order mismatch: {scenario['id']}:{number}")
        previous = (cycle, event_id)
        rows.append(row)
    if len(rows) != 1100 or rows[0]["occurrence_cycle"] != scenario.get("first_cycle") or rows[-1]["occurrence_cycle"] != scenario.get("last_cycle"):
        raise PublicV2Error(f"projected trace bounds/count mismatch: {scenario['id']}")
    return rows


def verify_projection_package(
    projection_dir: Path, pins: dict[str, Any],
) -> tuple[dict[str, bytes], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    payloads = exact_projection_payloads(projection_dir)
    if digest_bytes(payloads["receipt.json"]) != pins.get("projection_receipt_sha256"):
        raise PublicV2Error("wrong projection receipt hash")
    if digest_bytes(payloads["COMPLETE.json"]) != pins.get("projection_completion_sha256"):
        raise PublicV2Error("wrong projection completion hash")
    receipt = load_json_bytes(payloads["receipt.json"], "projection receipt")
    completion = load_json_bytes(payloads["COMPLETE.json"], "projection completion")
    for document, label in ((receipt, "projection receipt"), (completion, "projection completion")):
        if document.get("release_status") != "HOLD":
            raise PublicV2Error(f"{label} release status changed")
    if (receipt.get("status") != "PUBLIC_PROJECTED_EXTENSION_UNREPLAYED" or
            receipt.get("canonical_redred_traffic") is not False or
            receipt.get("official_redred_traffic") is not False or
            receipt.get("lineage", {}).get("p6_evidence_used") is not False or
            receipt.get("lineage", {}).get("actual_replay_receipt") is not None):
        raise PublicV2Error("projection receipt lineage/classification changed")
    if completion.get("actual_replay_receipt_sha256") is not None:
        raise PublicV2Error("projection completion was already replay-bound")
    if receipt.get("conservation", {}).get("projected_events") != 1100:
        raise PublicV2Error("projection receipt event count changed")
    expected_artifacts = set(PROJECTION_NAMES) - {"COMPLETE.json", "receipt.json"}
    if set(receipt.get("artifacts", {})) != expected_artifacts:
        raise PublicV2Error("projection receipt artifact inventory changed")
    for name in expected_artifacts:
        row = receipt["artifacts"][name]
        if row.get("sha256") != digest_bytes(payloads[name]) or row.get("size_bytes") != len(payloads[name]):
            raise PublicV2Error(f"projection receipt artifact binding changed: {name}")
    traces: dict[str, list[dict[str, Any]]] = {}
    identity: bytes | None = None
    for scenario in pins["scenarios"]:
        rows = parse_trace(payloads[scenario["trace_file"]], scenario)
        fingerprint = canonical([
            [row["tb_only_event_id"], row["logical_source"], row["x"], row["y"], row["polarity"]]
            for row in rows
        ])
        if identity is None:
            identity = fingerprint
        elif identity != fingerprint:
            raise PublicV2Error("scenario identities/order differ")
        traces[scenario["id"]] = rows
    return payloads, traces, receipt


def verify_sources(pins: dict[str, Any], *, allow_dirty: bool) -> tuple[str, str]:
    if digest_bytes(stable_read(BASE_PINS, "hardened replay pins")) != pins.get("hardened_replay_pins_sha256"):
        raise PublicV2Error("hardened replay pins changed")
    provenance = pins["commit_provenance"]
    integration = provenance["integration_commit"]
    execution_source = provenance["execution_source_commit"]
    files = pins.get("files")
    if not isinstance(files, dict) or not files:
        raise PublicV2Error("v2 source file pins missing")
    for relative, expected in files.items():
        require_hash(expected, f"files/{relative}")
        payload = stable_read(PROJECT / relative, f"source/{relative}")
        if digest_bytes(payload) != expected:
            raise PublicV2Error(f"v2 source SHA mismatch: {relative}")
        if digest_bytes(git_bytes(execution_source, relative)) != expected:
            raise PublicV2Error(f"execution-source commit bytes mismatch: {relative}")
    if not allow_dirty:
        selected = sorted(set(files) | {str(PINS.relative_to(PROJECT)), str(BASE_PINS.relative_to(PROJECT))})
        changed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *selected],
            cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        if changed:
            raise PublicV2Error("v2 execution inputs are dirty")
    execution_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()
    require_commit(execution_commit, "execution_commit")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", integration, execution_commit],
        cwd=PROJECT, check=False,
    ).returncode:
        raise PublicV2Error("integration commit is not an ancestor of execution commit")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", execution_source, execution_commit],
        cwd=PROJECT, check=False,
    ).returncode:
        raise PublicV2Error("execution-source commit is not an ancestor of execution commit")
    return execution_commit, execution_source


INSTRUMENT_ANCHORS = (
    (
        "  integer record_retire [0:MAX_EVENTS-1];\n",
        "  integer record_retire [0:MAX_EVENTS-1];\n"
        "  integer record_accept_ordinal [0:MAX_EVENTS-1];\n"
        "  integer record_retire_ordinal [0:MAX_EVENTS-1];\n",
    ),
    (
        "      record_retire[event_identity] = -1;\n",
        "      record_retire[event_identity] = -1;\n"
        "      record_accept_ordinal[event_identity] = -1;\n"
        "      record_retire_ordinal[event_identity] = -1;\n",
    ),
    (
        "      record_accept[event_identity] = global_cycle;\n      record_state[event_identity] = 2;\n",
        "      record_accept[event_identity] = global_cycle;\n"
        "      record_accept_ordinal[event_identity] = accepted_count;\n"
        "      record_state[event_identity] = 2;\n",
    ),
    (
        "      record_retire[event_identity] = global_cycle;\n      record_state[event_identity] = 3;\n",
        "      record_retire[event_identity] = global_cycle;\n"
        "      record_retire_ordinal[event_identity] = retired_count;\n"
        "      record_state[event_identity] = 3;\n",
    ),
    (
        '        "owner,trace,tb_event_id,logical_source,occurrence_cycle,accept_cycle,retire_cycle,deadline_cycle,event_state");\n',
        '        "owner,trace,tb_event_id,logical_source,occurrence_cycle,accept_cycle,retire_cycle,accept_sequence_ordinal,retire_sequence_ordinal,deadline_cycle,event_state");\n',
    ),
    (
        '        $fdisplay(event_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%s",\n'
        '          owner_name, trace_name, record_trace_id[index], record_source[index],\n'
        '          record_occurrence[index], record_accept[index], record_retire[index],\n'
        '          record_deadline[index], state_name);\n',
        '        $fdisplay(event_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%s",\n'
        '          owner_name, trace_name, record_trace_id[index], record_source[index],\n'
        '          record_occurrence[index], record_accept[index], record_retire[index],\n'
        '          record_accept_ordinal[index], record_retire_ordinal[index],\n'
        '          record_deadline[index], state_name);\n',
    ),
)


def instrument_testbench(work: Path) -> tuple[Path, dict[str, Any]]:
    original = stable_read(base.TB, "pinned replay testbench")
    try:
        text = original.decode("utf-8")
    except UnicodeError as error:
        raise PublicV2Error("pinned replay testbench is not UTF-8") from error
    for old, new in INSTRUMENT_ANCHORS:
        if text.count(old) != 1:
            raise PublicV2Error("ordinal instrumentation anchor is not unique")
        text = text.replace(old, new)
    destination = work / "instrumentation/a23_public_projected_v2_tb.sv"
    destination.parent.mkdir(parents=True)
    destination.write_text(text, encoding="utf-8")
    payload = stable_read(destination, "instrumented testbench")
    return destination, {
        "kind": "OBSERVER_ONLY_SEQUENCE_ORDINAL_INSTRUMENTATION",
        "base_testbench_sha256": digest_bytes(original),
        "instrumented_testbench_sha256": digest_bytes(payload),
        "actual_RTL_modified": False,
        "accept_ordinal_definition": "zero-based order of accept_addr0 then accept_addr1 calls",
        "retire_ordinal_definition": "zero-based order of retire_addr0 then retire_addr1 calls",
    }


def compile_simulator(
    work: Path, verilator: Path, document: dict[str, Any], owner: str,
    sources: list[Path], testbench: Path, mutation: str = "none",
) -> tuple[Path, Path]:
    build = work / "build" / owner / mutation
    build.mkdir(parents=True, exist_ok=False)
    binary = build / "sim"
    config = document["owners"][owner]
    command = [
        str(verilator), "--binary", "--timing", "--assert", "-Wall",
        "-Wno-fatal", "-Wno-BLKSEQ", "-Wno-WIDTHEXPAND",
        "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL", "-Wno-SYNCASYNCNET",
        f"-D{config['define']}", "--top-module", "a23_full_single_edge_replay_tb",
        "--Mdir", str(build), "-o", "sim", *[str(path) for path in sources],
        str(PROJECT / config["wrapper"]), str(testbench),
    ]
    log = work / f"logs/build-{owner}-{mutation}.log"
    environment = os.environ.copy()
    environment["MAKE"] = document["tools"]["make"]["path"]
    environment["CXX"] = document["tools"]["cxx"]["path"]
    base.run(command, cwd=PROJECT, log=log, env=environment)
    if not binary.is_file():
        raise PublicV2Error(f"Verilator did not create simulator: {owner}/{mutation}")
    return binary, log


EVENT_FIELDS_V2 = {
    "owner", "trace", "tb_event_id", "logical_source", "occurrence_cycle",
    "accept_cycle", "retire_cycle", "accept_sequence_ordinal",
    "retire_sequence_ordinal", "deadline_cycle", "event_state",
}


def _latency(values: Iterable[int]) -> dict[str, Any]:
    return base.latency_summary(values)


def parse_run_v2(
    summary_path: Path, event_path: Path, owner: str, trace: str,
    expected_reset: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary_payload = stable_read(summary_path, f"summary/{owner}/{trace}")
    event_payload = stable_read(event_path, f"events/{owner}/{trace}")
    summary_reader = csv.DictReader(io.StringIO(summary_payload.decode("ascii")))
    expected_summary = {
        "owner", "trace", "generated", "source_overrun", "accepted", "retired",
        "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
        "count2_commits", "reset_test", "pre_reset_clean_drain",
    }
    if set(summary_reader.fieldnames or ()) != expected_summary:
        raise PublicV2Error("summary schema mismatch")
    summary_rows = list(summary_reader)
    if len(summary_rows) != 1:
        raise PublicV2Error("summary cardinality mismatch")
    summary = summary_rows[0]
    event_reader = csv.DictReader(io.StringIO(event_payload.decode("ascii")))
    if set(event_reader.fieldnames or ()) != EVENT_FIELDS_V2:
        raise PublicV2Error("ordinal event schema mismatch")
    events = list(event_reader)
    if summary["owner"] != owner or summary["trace"] != trace:
        raise PublicV2Error("summary owner/trace mismatch")
    occurrence_accept: list[int] = []
    accept_retire: list[int] = []
    retained: list[dict[str, Any]] = []
    overruns = 0
    for expected_id, event in enumerate(events):
        if event["owner"] != owner or event["trace"] != trace or int(event["tb_event_id"]) != expected_id:
            raise PublicV2Error("event identity/order mismatch")
        occurrence = int(event["occurrence_cycle"])
        source = int(event["logical_source"])
        if not 0 <= source < 16 or occurrence < 0:
            raise PublicV2Error("event provenance mismatch")
        if event["event_state"] == "source_overrun":
            if any(int(event[field]) != -1 for field in (
                "accept_cycle", "retire_cycle", "accept_sequence_ordinal", "retire_sequence_ordinal",
            )):
                raise PublicV2Error("source overrun carries endpoint/ordinal data")
            overruns += 1
        elif event["event_state"] == "retired":
            accept, retire = int(event["accept_cycle"]), int(event["retire_cycle"])
            accept_ordinal = int(event["accept_sequence_ordinal"])
            retire_ordinal = int(event["retire_sequence_ordinal"])
            if not occurrence <= accept <= retire or min(accept_ordinal, retire_ordinal) < 0:
                raise PublicV2Error("retired event timing/ordinal mismatch")
            occurrence_accept.append(accept - occurrence)
            accept_retire.append(retire - accept)
            retained.append({
                "tb_event_id": expected_id,
                "logical_source": source,
                "occurrence_cycle": occurrence,
                "accept_cycle": accept,
                "accept_sequence_ordinal": accept_ordinal,
                "retire_cycle": retire,
                "retire_sequence_ordinal": retire_ordinal,
            })
        else:
            raise PublicV2Error("nonterminal event in passing run")
    by_accept = sorted(retained, key=lambda row: row["accept_sequence_ordinal"])
    by_retire = sorted(retained, key=lambda row: row["retire_sequence_ordinal"])
    expected_ordinals = list(range(len(retained)))
    if [row["accept_sequence_ordinal"] for row in by_accept] != expected_ordinals:
        raise PublicV2Error("accept ordinals are not exact contiguous sequence")
    if [row["retire_sequence_ordinal"] for row in by_retire] != expected_ordinals:
        raise PublicV2Error("retire ordinals are not exact contiguous sequence")
    if [row["tb_event_id"] for row in by_accept] != [row["tb_event_id"] for row in by_retire]:
        raise PublicV2Error("retirement sequence differs from acceptance sequence")
    numeric = {key: int(summary[key]) for key in expected_summary - {"owner", "trace"}}
    if numeric["generated"] != len(events) or numeric["source_overrun"] != overruns:
        raise PublicV2Error("summary occurrence accounting mismatch")
    if numeric["accepted"] != len(retained) or numeric["retired"] != len(retained):
        raise PublicV2Error("summary endpoint accounting mismatch")
    if numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise PublicV2Error("generated conservation mismatch")
    if numeric["reset_test"] != int(expected_reset) or numeric["pre_reset_clean_drain"] != int(expected_reset):
        raise PublicV2Error("reset scope/provenance mismatch")
    artifact = {
        **numeric,
        "occurrence_to_accept": _latency(occurrence_accept),
        "accept_to_retire": _latency(accept_retire),
        "fixed_window_events_per_cycle": round(
            numeric["fixed_window_retired"] / max(1, numeric["fixed_window_cycles"]), 9,
        ),
        "summary_sha256": digest_bytes(summary_payload),
        "events_sha256": digest_bytes(event_payload),
        "sequence": {
            "schema": "a23_accept_retire_sequence_ordinals_v2",
            "accepted_count": len(retained),
            "retired_count": len(retained),
            "same_cycle_order_reconstructable": True,
            "accept_order_sha256": digest_bytes(canonical(by_accept)),
            "retire_order_sha256": digest_bytes(canonical(by_retire)),
        },
    }
    return artifact, by_accept


def execute_case_v2(
    work: Path, binary: Path, owner: str, trace_name: str, mode: str,
    trace: Path | None, mutation: str = "none", expect_success: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None, list[dict[str, Any]], Path]:
    case = work / "artifacts" / owner / mutation / trace_name
    case.mkdir(parents=True, exist_ok=True)
    events, summary = case / "events.csv", case / "summary.csv"
    command = [
        str(binary), f"+OWNER={owner}", f"+TRACE_NAME={trace_name}",
        f"+MODE={mode}", f"+MUTATION={mutation}", f"+EVENT_OUTPUT={events}",
        f"+SUMMARY_OUTPUT={summary}",
    ]
    if trace is not None:
        command.append(f"+TRACE_FILE={trace}")
    log = case / "simulation.log"
    process = base.run(command, cwd=PROJECT, log=log, expect_success=expect_success)
    if not expect_success:
        return process, None, [], log
    if "A23_SE_ACTUAL_RTL_PASS" not in process.stdout:
        raise PublicV2Error(f"missing actual-RTL PASS sentinel: {owner}/{trace_name}")
    artifact, sequence = parse_run_v2(summary, events, owner, trace_name, mode == "reset")
    return process, artifact, sequence, log


def prepare_inputs(
    work: Path, payloads: dict[str, bytes], pins: dict[str, Any],
    traces: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    source_root = work / "source-projection"
    source_root.mkdir()
    for name, payload in payloads.items():
        (source_root / name).write_bytes(payload)
    prepared: dict[str, dict[str, Any]] = {}
    for scenario in pins["scenarios"]:
        scenario_id = scenario["id"]
        trace_path = source_root / scenario["trace_file"]
        manifest_path = work / "prepared" / f"{scenario_id}.manifest.json"
        output_path = work / "prepared" / f"{scenario_id}.trace"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "trace_file": trace_path.name,
            "trace_sha256": scenario["trace_sha256"],
            "event_count": 1100,
            "event_identity_mode": "address_only",
            "report_group": f"public_projected_v2_{scenario_id}",
            "run": {
                "name": f"public_projected_v2_{scenario_id}",
                "geometry": {"width": 4, "height": 4},
                "stim_cycles": scenario["last_cycle"] + 1,
                "load": "0.0", "sink": {"mode": "always"},
                "seed": f"uzh_shapes_{scenario_id}",
            },
        }
        manifest_path.write_bytes(pretty(manifest))
        base.run(
            [sys.executable, str(base.PREPARER), "--trace", str(trace_path),
             "--run-manifest", str(manifest_path), "--output", str(output_path),
             "--addr-width", "4"],
            cwd=PROJECT, log=work / f"logs/prepare-public-v2-{scenario_id}.log",
        )
        output_payload = stable_read(output_path, f"prepared/{scenario_id}")
        lines = output_payload.decode("ascii").splitlines()
        encoded = [tuple(map(int, line.split())) for line in lines[1:]]
        expected = [
            (row["occurrence_cycle"], row["tb_only_event_id"], row["logical_source"],
             row["logical_source"], row["deadline"])
            for row in traces[scenario_id]
        ]
        if len(lines) != 1101 or encoded != expected:
            raise PublicV2Error(f"preparer changed projected identity/order: {scenario_id}")
        prepared[scenario_id] = {
            "path": output_path,
            "sha256": digest_bytes(output_payload),
            "manifest_path": manifest_path,
            "manifest_sha256": digest_bytes(stable_read(manifest_path, f"manifest/{scenario_id}")),
        }
    return prepared


SEMANTIC_DEFINITION = {
    "schema": "a23_public_projected_semantic_reproducibility_definition_v2",
    "canonicalization": {
        "name": "PYTHON_JSON_SORT_KEYS_COMPACT_ASCII_V1",
        "sort_keys": True,
        "separators": [",", ":"],
        "ensure_ascii": True,
        "allow_nan": False,
    },
    "digest": "SHA-256",
    "view": "CLOSED_EXPLICIT_SEMANTIC_VIEW_V2",
    "included": [
        "classification", "identity_accounting", "execution_accounting",
        "reset_scope", "projection", "owners.scenarios.accounting_latency_sequence",
        "owners.reset.accounting_latency_sequence", "owners.mutation_activation.accounting",
        "mutations.outcome_and_source_identity", "commit_provenance",
        "observer_instrumentation_semantics", "toolchain_identity_claim_scope",
    ],
    "excluded": [
        {"field": "artifact_and_log_sha256", "reason": "absolute temporary build paths may differ"},
        {"field": "publication_commit", "reason": "publication sealing follows payload production"},
        {"field": "execution_commit", "reason": "may differ across byte-identical rerun packaging commits"},
    ],
}


def _semantic_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(artifact[key])
        for key in (
            "generated", "source_overrun", "accepted", "retired",
            "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
            "count2_commits", "reset_test", "pre_reset_clean_drain",
            "occurrence_to_accept", "accept_to_retire", "fixed_window_events_per_cycle",
            "sequence",
        )
    }


def semantic_view(result: dict[str, Any]) -> dict[str, Any]:
    require_classification(result, "result")
    owners: dict[str, Any] = {}
    for owner in OWNERS:
        source = result["owners"][owner]
        owners[owner] = {
            "scenarios": {
                scenario: {
                    **_semantic_artifact(source["scenarios"][scenario]),
                    "source_trace_sha256": source["scenarios"][scenario]["source_trace_sha256"],
                    "prepared_trace_sha256": source["scenarios"][scenario]["prepared_trace_sha256"],
                    "prepared_manifest_sha256": source["scenarios"][scenario]["prepared_manifest_sha256"],
                    "sequence_artifact_sha256": source["scenarios"][scenario]["sequence_artifact_sha256"],
                }
                for scenario in SCENARIOS
            },
            "reset": _semantic_artifact(source["reset"]),
            "mutation_activation": {
                key: source["mutation_activation"][key]
                for key in ("generated", "source_overrun", "accepted", "retired", "count2_commits")
            },
        }
    mutations = [{
        key: mutation[key]
        for key in (
            "owner", "mutation", "compiled_successfully", "executed", "killed",
            "exit_code", "first_diagnostic", "actual_endpoint_RTL_source_rewrite",
            "source_identity",
        )
    } for mutation in result["mutations"]]
    provenance = result["provenance"]
    return {
        "schema": "a23_public_projected_semantic_view_v2",
        **{key: result[key] for key in CLASSIFICATION},
        "evidence_class": result["evidence_class"],
        "reset_scope": result["reset_scope"],
        "identity_accounting": result["identity_accounting"],
        "execution_accounting": result["execution_accounting"],
        "projection": result["projection"],
        "owners": owners,
        "mutations": mutations,
        "commit_provenance": {
            "integration_commit": provenance["integration_commit"],
            "execution_source_commit": provenance["execution_source_commit"],
            "hardened_actual_RTL_source_commit": provenance["hardened_actual_RTL_source_commit"],
            "hardened_actual_RTL_integration_commit": provenance["hardened_actual_RTL_integration_commit"],
        },
        "observer_instrumentation": result["observer_instrumentation"],
        "toolchain_claim_scope": result["toolchain"]["claim_scope"],
    }


def semantic_sha256(result: dict[str, Any]) -> str:
    return digest_bytes(canonical(semantic_view(result)))


def expected_export_names() -> tuple[str, ...]:
    names = [f"inputs/{name}" for name in PROJECTION_NAMES]
    names += [f"run/prepared/{scenario}.manifest.json" for scenario in SCENARIOS]
    names += [f"run/prepared/{scenario}.trace" for scenario in SCENARIOS]
    for owner in OWNERS:
        for scenario in SCENARIOS:
            case = f"run/artifacts/{owner}/none/public_projected_v2_{scenario}"
            names += [f"{case}/events.csv", f"{case}/simulation.log", f"{case}/summary.csv"]
        for trace in ("reset_drain_epochs", "public_projected_v2_mutation_activation"):
            case = f"run/artifacts/{owner}/none/{trace}"
            names += [f"{case}/events.csv", f"{case}/simulation.log", f"{case}/summary.csv"]
        for mutation in MUTATIONS:
            trace = "reset_drain_epochs" if mutation == "reset_escape" else "public_projected_v2_mutation_activation"
            names.append(f"run/artifacts/{owner}/{mutation}/{trace}/simulation.log")
            names.append(f"run/mutated-rtl/{owner}/{mutation}/w2_single_edge_pair_rx.sv")
        for mutation in ("none", *MUTATIONS):
            names.append(f"run/logs/build-{owner}-{mutation}.log")
        for scenario in SCENARIOS:
            names.append(f"run/sequences/{owner}/{scenario}.jsonl")
    names += [f"run/logs/prepare-public-v2-{scenario}.log" for scenario in SCENARIOS]
    names.append("run/instrumentation/a23_public_projected_v2_tb.sv")
    names.append("result/public_projected_v2_result.json")
    result = tuple(sorted(names))
    if len(result) != 80 or len(set(result)) != 80:
        raise AssertionError("v2 closed inventory definition must contain exactly 80 unique names")
    return result


def export_sources(
    work: Path, projection_payloads: dict[str, bytes], result_path: Path,
) -> dict[str, bytes]:
    entries = {f"inputs/{name}": payload for name, payload in projection_payloads.items()}
    for arcname in expected_export_names():
        if arcname.startswith("inputs/"):
            continue
        if arcname == "result/public_projected_v2_result.json":
            path = result_path
        elif arcname.startswith("run/"):
            path = work / arcname.removeprefix("run/")
        else:
            raise AssertionError(arcname)
        entries[arcname] = stable_read(path, f"export/{arcname}")
    if tuple(sorted(entries)) != expected_export_names():
        raise PublicV2Error("export inputs do not match exact v2 inventory")
    # Fail on unlisted retained output files, symlinks, or directories masquerading as files.
    actual: set[str] = set()
    for root_name in ("prepared", "artifacts", "mutated-rtl", "logs", "sequences", "instrumentation"):
        root = work / root_name
        for directory, directories, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in directories:
                if (directory_path / name).is_symlink():
                    raise PublicV2Error("symlink retained-output directory is forbidden")
            for name in files:
                relative = (directory_path / name).relative_to(work)
                actual.add(f"run/{relative}")
    expected_run = {name for name in expected_export_names() if name.startswith("run/")}
    if actual != expected_run:
        raise PublicV2Error("retained run outputs differ from exact v2 inventory")
    return entries


def build_manifest(entries: dict[str, bytes], result: dict[str, Any]) -> dict[str, Any]:
    manifest = {
        "schema": "a23_public_projected_v2_export_manifest_v2",
        **CLASSIFICATION,
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "reset_scope": "CLEAN_DRAIN_ONLY",
        "identity_accounting": {
            "unique_projected_window_events": 1100,
            "scenario_retimings": list(SCENARIOS),
            "pooled_3300_unique_events": False,
        },
        "commit_provenance": {
            "integration_commit": result["provenance"]["integration_commit"],
            "execution_source_commit": result["provenance"]["execution_source_commit"],
            "execution_commit": result["provenance"]["execution_commit"],
            "publication_commit_binding_layer": "PUBLICATION_RECORD_ONLY",
            "p6_evidence_used": False,
        },
        "inventory": {
            "schema": "a23_public_projected_v2_closed_inventory_v2",
            "entry_count_excluding_manifest": 80,
            "extra_entries_allowed": False,
            "ordered_names": list(expected_export_names()),
        },
        "semantic_reproducibility": result["semantic_reproducibility"],
        "entries": {
            name: {"size_bytes": len(payload), "sha256": digest_bytes(payload)}
            for name, payload in sorted(entries.items())
        },
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "a23_public_projected_v2_export_manifest_v2":
        raise PublicV2Error("manifest schema mismatch")
    require_classification(manifest, "manifest")
    if manifest.get("reset_scope") != "CLEAN_DRAIN_ONLY":
        raise PublicV2Error("manifest reset scope mismatch")
    if manifest.get("commit_provenance", {}).get("p6_evidence_used") is not False:
        raise PublicV2Error("manifest commit provenance P6 must be false")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict) or inventory.get("ordered_names") != list(expected_export_names()) or inventory.get("entry_count_excluding_manifest") != 80 or inventory.get("extra_entries_allowed") is not False:
        raise PublicV2Error("manifest closed inventory mismatch")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or tuple(sorted(entries)) != expected_export_names():
        raise PublicV2Error("manifest entry map mismatch")
    for name, row in entries.items():
        if not isinstance(row, dict) or set(row) != {"size_bytes", "sha256"}:
            raise PublicV2Error(f"manifest entry schema mismatch: {name}")
        if isinstance(row["size_bytes"], bool) or not isinstance(row["size_bytes"], int) or row["size_bytes"] < 0:
            raise PublicV2Error(f"manifest entry size mismatch: {name}")
        require_hash(row["sha256"], f"manifest/{name}")


def write_and_reopen_archive(
    entries: dict[str, bytes], manifest: dict[str, Any], bundle_path: Path,
) -> tuple[str, str]:
    manifest_payload = pretty(manifest)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with bundle_path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, payload in [("MANIFEST.json", manifest_payload), *sorted(entries.items())]:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.uid, info.gid, info.mtime = len(payload), 0o444, 0, 0, 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(payload))
    archive_payload = stable_read(bundle_path, "final export archive")
    validate_archive_bytes(archive_payload)
    return digest_bytes(archive_payload), digest_bytes(manifest_payload)


def validate_archive_bytes(payload: bytes) -> dict[str, Any]:
    try:
        # Exhaust the complete gzip member first. Tar readers may stop at the
        # tar end marker without consuming (and therefore checking) gzip CRC,
        # size, trailer, or trailing bytes.
        tar_payload = gzip.decompress(payload)
        with tarfile.open(fileobj=io.BytesIO(tar_payload), mode="r:") as archive:
            members = archive.getmembers()
            expected_names = ("MANIFEST.json", *expected_export_names())
            if tuple(member.name for member in members) != expected_names:
                raise PublicV2Error("reopened archive name/order mismatch")
            if len({member.name for member in members}) != len(members):
                raise PublicV2Error("reopened archive contains duplicate names")
            extracted: dict[str, bytes] = {}
            for member in members:
                if (not member.isfile() or member.mode != 0o444 or member.uid != 0 or
                        member.gid != 0 or member.mtime != 0 or member.uname or member.gname):
                    raise PublicV2Error(f"reopened archive metadata/type mismatch: {member.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise PublicV2Error(f"cannot reopen archive member: {member.name}")
                extracted[member.name] = stream.read()
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        raise PublicV2Error(f"cannot reopen final archive: {error}") from error
    manifest = load_json_bytes(extracted.pop("MANIFEST.json"), "archive manifest")
    validate_manifest(manifest)
    for name, row in manifest["entries"].items():
        member = extracted.get(name)
        if member is None or len(member) != row["size_bytes"] or digest_bytes(member) != row["sha256"]:
            raise PublicV2Error(f"reopened archive member binding mismatch: {name}")
    return manifest


def validate_result(result: dict[str, Any]) -> None:
    if result.get("schema") != "a23_public_projected_v2_result_v2":
        raise PublicV2Error("result schema mismatch")
    require_classification(result, "result")
    if result.get("evidence_class") != "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL":
        raise PublicV2Error("result evidence class mismatch")
    if result.get("reset_scope") != "CLEAN_DRAIN_ONLY":
        raise PublicV2Error("result reset scope mismatch")
    if result.get("projection", {}).get("p6_evidence_used") is not False:
        raise PublicV2Error("result projection P6 must be false")
    if result.get("provenance", {}).get("p6_evidence_used") is not False:
        raise PublicV2Error("result provenance P6 must be false")
    if result.get("identity_accounting") != {
        "unique_projected_window_events": 1100,
        "scenario_retimings": list(SCENARIOS),
        "pooled_3300_unique_events": False,
    }:
        raise PublicV2Error("result identity accounting mismatch")
    if set(result.get("owners", {})) != set(OWNERS):
        raise PublicV2Error("result owner roster mismatch")
    if len(result.get("mutations", [])) != 8:
        raise PublicV2Error("result must retain exactly eight mutant outcomes")
    for owner in OWNERS:
        owner_result = result["owners"][owner]
        if set(owner_result.get("scenarios", {})) != set(SCENARIOS):
            raise PublicV2Error("result scenario roster mismatch")
        for scenario in SCENARIOS:
            artifact = owner_result["scenarios"][scenario]
            if artifact.get("generated") != 1100:
                raise PublicV2Error("result lost projected source occurrences")
            if artifact.get("generated") != artifact.get("source_overrun") + artifact.get("accepted"):
                raise PublicV2Error("result occurrence accounting mismatch")
            if artifact.get("accepted") != artifact.get("retired"):
                raise PublicV2Error("result endpoint accounting mismatch")
            sequence = artifact.get("sequence", {})
            if (sequence.get("accepted_count") != artifact["accepted"] or
                    sequence.get("retired_count") != artifact["retired"] or
                    sequence.get("same_cycle_order_reconstructable") is not True):
                raise PublicV2Error("result sequence ordinal accounting mismatch")
        reset = owner_result.get("reset", {})
        if reset.get("reset_test") != 1 or reset.get("pre_reset_clean_drain") != 1:
            raise PublicV2Error("result reset exceeds or violates clean-drain scope")
    semantic = result.get("semantic_reproducibility")
    if not isinstance(semantic, dict) or semantic.get("definition") != SEMANTIC_DEFINITION:
        raise PublicV2Error("result semantic definition mismatch")
    if semantic.get("definition_sha256") != digest_bytes(canonical(SEMANTIC_DEFINITION)):
        raise PublicV2Error("result semantic definition hash mismatch")
    if semantic.get("semantic_sha256") != semantic_sha256(result):
        raise PublicV2Error("result semantic hash mismatch")


def validate_publication(publication: dict[str, Any]) -> None:
    if publication.get("schema") != "a23_public_projected_v2_publication_v2":
        raise PublicV2Error("publication schema mismatch")
    require_classification(publication, "publication")
    if publication.get("evidence_class") != "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL":
        raise PublicV2Error("publication evidence class mismatch")
    if publication.get("reset_scope") != "CLEAN_DRAIN_ONLY":
        raise PublicV2Error("publication reset scope mismatch")
    if publication.get("commit_provenance", {}).get("p6_evidence_used") is not False:
        raise PublicV2Error("publication commit provenance P6 must be false")
    for key in ("result_sha256", "export_bundle_sha256", "export_manifest_sha256", "semantic_sha256", "reproduction_result_sha256"):
        require_hash(publication.get(key), f"publication/{key}")
    for key in ("integration_commit", "execution_source_commit", "execution_commit", "publication_commit"):
        require_commit(publication.get("commit_provenance", {}).get(key), f"publication/{key}")
    if publication.get("publication_commit_meaning") != "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS":
        raise PublicV2Error("publication commit meaning mismatch")
    if publication.get("self_referential_commit_claim") is not False:
        raise PublicV2Error("publication must not claim its own containing commit")
    if publication.get("semantic_reproduction", {}).get("matched") is not True:
        raise PublicV2Error("publication lacks semantic reproduction match")
    if publication.get("export_entry_count_excluding_manifest") != 80:
        raise PublicV2Error("publication inventory count mismatch")
    size = publication.get("export_bundle_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise PublicV2Error("publication export byte size mismatch")


def validate_published_archive(payload: bytes, publication: dict[str, Any]) -> dict[str, Any]:
    """Require exact published raw bytes before parsing gzip/tar semantics."""
    validate_publication(publication)
    if len(payload) != publication["export_bundle_size_bytes"]:
        raise PublicV2Error("published archive raw byte size mismatch")
    if digest_bytes(payload) != publication["export_bundle_sha256"]:
        raise PublicV2Error("published archive raw SHA-256 mismatch")
    manifest = validate_archive_bytes(payload)
    if digest_bytes(pretty(manifest)) != publication["export_manifest_sha256"]:
        raise PublicV2Error("published archive manifest SHA-256 mismatch")
    return manifest


def _write_sequence(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(canonical(row) + b"\n" for row in rows)
    path.write_bytes(payload)
    return digest_bytes(stable_read(path, f"sequence/{path.parent.name}/{path.stem}"))


def produce(args: argparse.Namespace) -> int:
    pins = load_pins()
    execution_commit, execution_source = verify_sources(pins, allow_dirty=args.allow_dirty)
    if args.verilator != DEFAULT_VERILATOR:
        raise PublicV2Error("v2 requires the exact pinned Verilator path")
    base_document = base.load_document()
    sources, actual_files, observed_tools, rtl_git, _ = base.validate_integration(
        base_document, args.verilator, allow_dirty=args.allow_dirty,
    )
    projection_payloads, traces, receipt = verify_projection_package(args.projection_dir, pins)
    targets = (args.work_dir, args.output, args.export_bundle)
    if any(path.exists() for path in targets):
        raise PublicV2Error("work/result/export paths must not exist")
    work = args.work_dir
    work.mkdir(parents=True)
    instrumented_tb, instrumentation = instrument_testbench(work)
    prepared = prepare_inputs(work, projection_payloads, pins, traces)
    owners: dict[str, Any] = {}
    mutations: list[dict[str, Any]] = []
    for owner in OWNERS:
        print(f"A23_PUBLIC_V2_OWNER_START owner={owner}", flush=True)
        simulator, build_log = compile_simulator(
            work, args.verilator, base_document, owner, sources[owner], instrumented_tb,
        )
        scenario_results: dict[str, Any] = {}
        for scenario in SCENARIOS:
            trace_name = f"public_projected_v2_{scenario}"
            _, artifact, sequence_rows, simulation_log = execute_case_v2(
                work, simulator, owner, trace_name, "full", prepared[scenario]["path"],
            )
            assert artifact is not None
            if artifact["generated"] != 1100 or artifact["accepted"] != artifact["retired"]:
                raise PublicV2Error(f"scenario accounting failed: {owner}/{scenario}")
            sequence_path = work / f"sequences/{owner}/{scenario}.jsonl"
            sequence_sha = _write_sequence(sequence_path, sequence_rows)
            scenario_pin = next(row for row in pins["scenarios"] if row["id"] == scenario)
            scenario_results[scenario] = {
                **artifact,
                "source_trace_sha256": scenario_pin["trace_sha256"],
                "prepared_trace_sha256": prepared[scenario]["sha256"],
                "prepared_manifest_sha256": prepared[scenario]["manifest_sha256"],
                "sequence_artifact_sha256": sequence_sha,
                "simulation_log_sha256": digest_bytes(stable_read(simulation_log, "scenario simulation log")),
            }
        _, reset, _, reset_log = execute_case_v2(
            work, simulator, owner, "reset_drain_epochs", "reset", None,
        )
        assert reset is not None
        _, activation, _, activation_log = execute_case_v2(
            work, simulator, owner, "public_projected_v2_mutation_activation", "pair", None,
        )
        assert activation is not None
        if activation["count2_commits"] < 1:
            raise PublicV2Error(f"mutation activation failed: {owner}")
        owners[owner] = {
            "baseline_build_log_sha256": digest_bytes(stable_read(build_log, "baseline build log")),
            "scenarios": scenario_results,
            "reset": {
                **reset, "scope": "CLEAN_DRAIN_ONLY",
                "simulation_log_sha256": digest_bytes(stable_read(reset_log, "reset simulation log")),
            },
            "mutation_activation": {
                **activation,
                "simulation_log_sha256": digest_bytes(stable_read(activation_log, "activation simulation log")),
            },
        }
        for mutation in MUTATIONS:
            changed, identity = base.mutated_sources(
                work, base_document, owner, mutation, sources[owner],
            )
            mutant, mutant_build_log = compile_simulator(
                work, args.verilator, base_document, owner, changed, instrumented_tb, mutation,
            )
            mode = "reset" if mutation == "reset_escape" else "pair"
            trace_name = "reset_drain_epochs" if mode == "reset" else "public_projected_v2_mutation_activation"
            process, _, _, mutation_log = execute_case_v2(
                work, mutant, owner, trace_name, mode, None,
                mutation=mutation, expect_success=False,
            )
            first = base.first_diagnostic(process.stdout)
            if process.returncode == 0 or first != EXPECTED_DIAGNOSTIC[mutation] or "A23_SE_ACTUAL_RTL_PASS" in process.stdout:
                raise PublicV2Error(f"source mutant survived: {owner}/{mutation}")
            mutations.append({
                "owner": owner, "mutation": mutation,
                "compiled_successfully": True, "executed": True, "killed": True,
                "exit_code": process.returncode, "first_diagnostic": first,
                "actual_endpoint_RTL_source_rewrite": True,
                "source_identity": identity,
                "build_log_sha256": digest_bytes(stable_read(mutant_build_log, "mutant build log")),
                "simulation_log_sha256": digest_bytes(stable_read(mutation_log, "mutant simulation log")),
            })
            print(f"A23_PUBLIC_V2_MUTATION_KILLED owner={owner} mutation={mutation}", flush=True)
    prepared_hashes = {scenario: prepared[scenario]["sha256"] for scenario in SCENARIOS}
    for scenario in SCENARIOS:
        if {owners[owner]["scenarios"][scenario]["prepared_trace_sha256"] for owner in OWNERS} != {prepared_hashes[scenario]}:
            raise PublicV2Error(f"A2/A3 prepared inputs differ: {scenario}")
    rtl_provenance = base_document["rtl_provenance"]
    result: dict[str, Any] = {
        "schema": "a23_public_projected_v2_result_v2",
        **CLASSIFICATION,
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "reset_scope": "CLEAN_DRAIN_ONLY",
        "identity_accounting": {
            "unique_projected_window_events": 1100,
            "scenario_retimings": list(SCENARIOS),
            "pooled_3300_unique_events": False,
        },
        "execution_accounting": {
            "owners": 2, "projected_actual_RTL_executions": 6,
            "clean_drain_reset_actual_RTL_executions": 2,
            "mutation_activation_actual_RTL_executions": 2,
            "mutation_actual_RTL_executions": 8, "receipt_only_executions": 0,
        },
        "projection": {
            "receipt_sha256": pins["projection_receipt_sha256"],
            "completion_sha256": pins["projection_completion_sha256"],
            "specification_sha256": receipt["specification"]["sha256"],
            "prepared_once_shared_by_A2_A3": prepared_hashes,
            "p6_evidence_used": False,
        },
        "owners": owners,
        "mutations": mutations,
        "observer_instrumentation": instrumentation,
        "provenance": {
            "integration_commit": pins["commit_provenance"]["integration_commit"],
            "execution_source_commit": execution_source,
            "execution_commit": execution_commit,
            "publication_commit_binding_layer": "PUBLICATION_RECORD_ONLY",
            "p6_evidence_used": False,
            "v2_pins_sha256": digest_bytes(stable_read(PINS, "v2 pins")),
            "hardened_replay_pins_sha256": digest_bytes(stable_read(BASE_PINS, "base pins")),
            "hardened_actual_RTL_source_commit": rtl_provenance["source_commit"],
            "hardened_actual_RTL_integration_commit": rtl_provenance["integration_commit"],
            "actual_RTL_file_sha256": actual_files,
            "actual_RTL_git": rtl_git,
        },
        "toolchain": {
            "claim_scope": "OBSERVED_EXECUTABLE_IDENTITY_AND_REPORTED_VERSION_MATCH_ONLY",
            "environment_reproducibility_claimed": False,
            "tools": observed_tools,
        },
        "semantic_reproducibility": {
            "definition": SEMANTIC_DEFINITION,
            "definition_sha256": digest_bytes(canonical(SEMANTIC_DEFINITION)),
            "semantic_sha256": "0" * 64,
        },
    }
    result["semantic_reproducibility"]["semantic_sha256"] = semantic_sha256(result)
    validate_result(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pretty(result))
    entries = export_sources(work, projection_payloads, args.output)
    manifest = build_manifest(entries, result)
    bundle_sha, manifest_sha = write_and_reopen_archive(entries, manifest, args.export_bundle)
    print(
        "A23_PUBLIC_PROJECTED_V2_PASS status=PUBLIC_PROJECTED_EXTENSION "
        "release=HOLD selection=HOLD projected_actual=6 reset_clean_drain=2 "
        f"mutations=8 semantic={result['semantic_reproducibility']['semantic_sha256']} "
        f"result_sha256={digest_bytes(stable_read(args.output, 'final result'))} "
        f"export_sha256={bundle_sha} manifest_sha256={manifest_sha}",
        flush=True,
    )
    return 0


def seal(args: argparse.Namespace) -> int:
    pins = load_pins()
    result_payload = stable_read(args.output, "publication result")
    bundle_payload = stable_read(args.export_bundle, "publication export")
    reproduction_payload = stable_read(args.reproduction_result, "reproduction result")
    result = load_json_bytes(result_payload, "publication result")
    reproduction = load_json_bytes(reproduction_payload, "reproduction result")
    validate_result(result)
    validate_result(reproduction)
    semantic = result["semantic_reproducibility"]["semantic_sha256"]
    if reproduction["semantic_reproducibility"]["semantic_sha256"] != semantic:
        raise PublicV2Error("semantic reproduction digest differs")
    manifest = validate_archive_bytes(bundle_payload)
    publication_commit = require_commit(args.publication_commit, "publication_commit")
    result_relative = str(args.committed_result_path)
    export_relative = str(args.committed_export_path)
    if digest_bytes(git_bytes(publication_commit, result_relative)) != digest_bytes(result_payload):
        raise PublicV2Error("publication commit does not contain exact result payload")
    if digest_bytes(git_bytes(publication_commit, export_relative)) != digest_bytes(bundle_payload):
        raise PublicV2Error("publication commit does not contain exact export payload")
    publication = {
        "schema": "a23_public_projected_v2_publication_v2",
        **CLASSIFICATION,
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "reset_scope": "CLEAN_DRAIN_ONLY",
        "identity_accounting": result["identity_accounting"],
        "commit_provenance": {
            "integration_commit": pins["commit_provenance"]["integration_commit"],
            "execution_source_commit": result["provenance"]["execution_source_commit"],
            "execution_commit": result["provenance"]["execution_commit"],
            "publication_commit": publication_commit,
            "p6_evidence_used": False,
        },
        "publication_commit_meaning": "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS",
        "self_referential_commit_claim": False,
        "result_sha256": digest_bytes(result_payload),
        "export_bundle_sha256": digest_bytes(bundle_payload),
        "export_bundle_size_bytes": len(bundle_payload),
        "export_manifest_sha256": digest_bytes(pretty(manifest)),
        "export_entry_count_excluding_manifest": 80,
        "semantic_sha256": semantic,
        "reproduction_result_sha256": digest_bytes(reproduction_payload),
        "semantic_reproduction": {
            "definition_sha256": result["semantic_reproducibility"]["definition_sha256"],
            "primary_semantic_sha256": semantic,
            "reproduction_semantic_sha256": semantic,
            "matched": True,
        },
    }
    validate_publication(publication)
    validate_published_archive(bundle_payload, publication)
    if args.publication.exists():
        raise PublicV2Error("publication output must not exist")
    args.publication.write_bytes(pretty(publication))
    print(
        f"A23_PUBLIC_PROJECTED_V2_PUBLICATION_PASS publication_commit={publication_commit} "
        f"publication_sha256={digest_bytes(stable_read(args.publication, 'publication'))}",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    producer = subparsers.add_parser("produce")
    producer.add_argument("--projection-dir", type=Path, default=DEFAULT_PROJECTION)
    producer.add_argument("--work-dir", type=Path, required=True)
    producer.add_argument("--output", type=Path, required=True)
    producer.add_argument("--export-bundle", type=Path, required=True)
    producer.add_argument("--verilator", type=Path, default=DEFAULT_VERILATOR)
    producer.add_argument("--allow-dirty", action="store_true", help=argparse.SUPPRESS)
    publisher = subparsers.add_parser("seal")
    publisher.add_argument("--output", type=Path, required=True)
    publisher.add_argument("--export-bundle", type=Path, required=True)
    publisher.add_argument("--reproduction-result", type=Path, required=True)
    publisher.add_argument("--publication", type=Path, required=True)
    publisher.add_argument("--publication-commit", required=True)
    publisher.add_argument("--committed-result-path", type=Path, required=True)
    publisher.add_argument("--committed-export-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        return produce(args) if args.command == "produce" else seal(args)
    except (PublicV2Error, base.ReplayError, base.ReplayUnavailable,
            OSError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"A23_PUBLIC_PROJECTED_V2_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
