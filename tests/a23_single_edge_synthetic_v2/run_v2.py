#!/usr/bin/env python3
"""Run, seal, and independently validate hardened synthetic replay v2 evidence."""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Iterable


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
BASE_PACKAGE = PROJECT / "tests/a23_full_single_edge_replay"
BASE_RUNNER = BASE_PACKAGE / "run_replay.py"
BASE_PINS = BASE_PACKAGE / "pins.json"
EXPORT_HELPER_DIR = PROJECT / "tests/a23_single_edge_synthetic_export"
EXPECTED_SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
EXPECTED_SOURCE_TREE = "e6030c7990f602a7fc1c73ac529b008b8e2c4133"
EXPECTED_INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
EXPECTED_INTEGRATION_TREE = "d0fda8da2c10693b5d7093e0e2d505590722c1ea"
EXPECTED_PINS_SHA256 = "0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0"
RESULT_SCHEMA = "a23_full_single_edge_replay_result_v1"
V2_RESULT_SCHEMA = "a23_single_edge_synthetic_v2_result_v1"
EXPORT_SCHEMA = "a23_single_edge_synthetic_v2_export_manifest_v1"
PUBLICATION_SCHEMA = "a23_single_edge_synthetic_v2_publication_v1"
STATUS = "PASS_HARDENED_SYNTHETIC_V2"
ARCHIVE_PREFIX = "a23-single-edge-synthetic-v2"
IMPLEMENTATION_FILES = (
    "tests/a23_single_edge_synthetic_v2/README.md",
    "tests/a23_single_edge_synthetic_v2/run_all.sh",
    "tests/a23_single_edge_synthetic_v2/run_v2.py",
    "tests/a23_single_edge_synthetic_v2/test_synthetic_v2.py",
    "tests/a23_single_edge_synthetic_export/export_preserved.py",
)

sys.path.insert(0, str(EXPORT_HELPER_DIR))
import export_preserved as retained  # noqa: E402


class V2Error(RuntimeError):
    pass


def pretty(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def semantic_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    value = retained.load_json_bytes(data, label)
    return retained.require_object(value, label)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json_bytes(path.read_bytes(), label)
    except OSError as error:
        raise V2Error(f"cannot read {label}: {error}") from error


def ephemeral_log_pointers() -> list[str]:
    pointers: list[str] = []
    for owner in ("a2", "a3"):
        pointers.extend((
            f"/owners/{owner}/baseline_build_log_sha256",
            f"/owners/{owner}/mutation_activation/simulation_log_sha256",
            f"/owners/{owner}/reset/simulation_log_sha256",
        ))
    for index in range(8):
        pointers.extend((
            f"/mutations/{index}/build_log_sha256",
            f"/mutations/{index}/simulation_log_sha256",
        ))
    return sorted(pointers)


EPHEMERAL_LOG_POINTERS = tuple(ephemeral_log_pointers())


def decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise V2Error(f"JSON pointer is not absolute: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~")
            for part in pointer[1:].split("/")]


def remove_pointer(document: Any, pointer: str) -> None:
    parts = decode_pointer(pointer)
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as error:
                raise V2Error(f"missing semantic exclusion pointer: {pointer}") from error
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise V2Error(f"missing semantic exclusion pointer: {pointer}")
    final = parts[-1]
    if isinstance(current, list):
        try:
            del current[int(final)]
        except (ValueError, IndexError) as error:
            raise V2Error(f"missing semantic exclusion pointer: {pointer}") from error
    elif isinstance(current, dict) and final in current:
        del current[final]
    else:
        raise V2Error(f"missing semantic exclusion pointer: {pointer}")


def semantic_projection(result: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(result)
    for pointer in EPHEMERAL_LOG_POINTERS:
        remove_pointer(projected, pointer)
    return projected


def semantic_digest(result: dict[str, Any]) -> str:
    return digest(semantic_bytes(semantic_projection(result)))


def difference_pointers(left: Any, right: Any, prefix: str = "") -> set[str]:
    if type(left) is not type(right):
        return {prefix or "/"}
    if isinstance(left, dict):
        differences: set[str] = set()
        for key in set(left) | set(right):
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in left or key not in right:
                differences.add(child)
            else:
                differences.update(difference_pointers(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        if len(left) != len(right):
            return {prefix or "/"}
        differences: set[str] = set()
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            differences.update(
                difference_pointers(left_item, right_item, f"{prefix}/{index}")
            )
        return differences
    return set() if left == right else {prefix or "/"}


def git_output(arguments: list[str], *, binary: bool = False) -> bytes | str:
    process = subprocess.run(
        ["git", *arguments], cwd=PROJECT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary, check=False,
    )
    if process.returncode:
        error = process.stderr if not binary else process.stderr.decode(errors="replace")
        raise V2Error(f"Git command failed: {' '.join(arguments)}: {error.strip()}")
    return process.stdout


def git_bytes(commit: str, relative: str) -> bytes:
    return git_output(["show", f"{commit}:{relative}"], binary=True)  # type: ignore[return-value]


def current_commit() -> str:
    return str(git_output(["rev-parse", "HEAD"])).strip()


def verify_result_identity(result: dict[str, Any], package_commit: str | None = None) -> list[str]:
    names = retained.validate_result_contract(result)
    provenance = retained.require_object(result.get("provenance"), "result provenance")
    rtl = retained.require_object(provenance.get("actual_rtl_git"), "actual RTL Git")
    expected_rtl = {
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_tree": EXPECTED_SOURCE_TREE,
        "integration_commit": EXPECTED_INTEGRATION_COMMIT,
        "integration_tree": EXPECTED_INTEGRATION_TREE,
    }
    for key, expected in expected_rtl.items():
        if rtl.get(key) != expected:
            raise V2Error(f"actual RTL identity differs for {key}")
    if provenance.get("pins_sha256") != EXPECTED_PINS_SHA256:
        raise V2Error("hardened replay pins identity differs")
    if package_commit is not None and provenance.get("package_commit") != package_commit:
        raise V2Error("replay package commit differs from v2 implementation commit")
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "PASS":
        raise V2Error("base replay result is not PASS")
    return names


def verify_tools(result: dict[str, Any]) -> str:
    tools = result["provenance"]["verified_tools"]
    if set(tools) != {"python", "verilator", "verilator_bin", "make", "cxx"}:
        raise V2Error("tool role roster differs")
    for role, identity in tools.items():
        path = Path(identity["path"])
        if path.is_symlink() or not path.is_file():
            raise V2Error(f"tool is absent or symlinked: {role}")
        if file_digest(path) != identity["sha256"]:
            raise V2Error(f"tool bytes differ: {role}")
    return digest(semantic_bytes(tools))


def verify_package_inputs(
    result: dict[str, Any], package_commit: str,
) -> tuple[dict[str, bytes], str]:
    verified = result["provenance"]["verified_files"]
    if not isinstance(verified, dict) or not verified:
        raise V2Error("producer verified-file inventory is absent")
    contents: dict[str, bytes] = {}
    identities: dict[str, str] = {}
    for relative, expected_sha in sorted(verified.items()):
        relative = retained.safe_relative(relative, "producer input path")
        data = git_bytes(package_commit, relative)
        if digest(data) != expected_sha:
            raise V2Error(f"producer input differs in package commit: {relative}")
        contents[f"inputs/repository/{relative}"] = data
        identities[relative] = expected_sha
    for relative in IMPLEMENTATION_FILES:
        data = git_bytes(package_commit, relative)
        sha = digest(data)
        contents[f"inputs/repository/{relative}"] = data
        identities[relative] = sha
    return contents, digest(semantic_bytes(identities))


def verify_rtl_git_blobs(result: dict[str, Any]) -> None:
    rtl = result["provenance"]["actual_rtl_git"]
    verified = result["provenance"]["verified_files"]
    for label in ("source", "integration"):
        commit = rtl[f"{label}_commit"]
        tree = str(git_output(["rev-parse", f"{commit}^{{tree}}"])).strip()
        if tree != rtl[f"{label}_tree"]:
            raise V2Error(f"{label} RTL tree differs")
        for relative in rtl["verified_rtl_paths"]:
            if digest(git_bytes(commit, relative)) != verified[relative]:
                raise V2Error(f"{label} RTL blob differs: {relative}")


def trace_identity(result: dict[str, Any], names: list[str]) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    for name in names:
        a2 = result["owners"]["a2"]["full50"]["runs"][name]
        a3 = result["owners"]["a3"]["full50"]["runs"][name]
        if (a2["trace_sha256"] != a3["trace_sha256"] or
                a2["prepared_trace_sha256"] != a3["prepared_trace_sha256"]):
            raise V2Error(f"A2/A3 input identity differs: {name}")
        rows.append({
            "name": name,
            "trace_sha256": a2["trace_sha256"],
            "prepared_trace_sha256": a2["prepared_trace_sha256"],
        })
    return rows, digest(semantic_bytes(rows))


def sequence_evidence(root: Path, names: list[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    fields = ["tb_event_id", "logical_source", "occurrence_cycle", "accept_cycle",
              "retire_cycle", "event_state"]
    for owner in ("a2", "a3"):
        for name in names:
            path = root / f"work/artifacts/{owner}/none/{name}/events.csv"
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            sequence = [[int(row[key]) if key != "event_state" else row[key]
                         for key in fields] for row in rows]
            if [row[0] for row in sequence] != list(range(len(sequence))):
                raise V2Error(f"event row sequence is not contiguous: {owner}/{name}")
            retired = [row for row in sequence if row[-1] == "retired"]
            evidence.append({
                "owner": owner,
                "trace": name,
                "event_row_count": len(sequence),
                "retired_row_count": len(retired),
                "event_row_sequence_sha256": digest(semantic_bytes(sequence)),
                "retired_timing_rows_sha256": digest(semantic_bytes(retired)),
            })
    return evidence


def run_campaign(retained_root: Path) -> int:
    retained_root = retained_root.absolute()
    if retained_root.exists() or retained_root.is_symlink():
        raise V2Error(f"retained root must not exist: {retained_root}")
    retained_root.mkdir(parents=True)
    command = [
        sys.executable, str(BASE_RUNNER),
        "--work-dir", str(retained_root / "work"),
        "--output", str(retained_root / "result.json"),
    ]
    process = subprocess.run(
        command, cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    (retained_root / "campaign.log").write_text(process.stdout, encoding="utf-8")
    sys.stdout.write(process.stdout)
    if process.returncode:
        raise V2Error(f"base replay failed with exit {process.returncode}")
    result = load_json(retained_root / "result.json", "fresh replay result")
    verify_result_identity(result, current_commit())
    if "A23_FULL_SINGLE_EDGE_REPLAY_PASS" not in process.stdout:
        raise V2Error("fresh replay lacks campaign PASS sentinel")
    return 0


def retained_payload(
    root: Path, result: dict[str, Any], names: list[str], prefix: str,
    *, full: bool,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    files, directories = retained.scan_regular_tree(root)
    if full:
        required = retained.expected_evidence_paths(names) | {"campaign.log"}
        missing, scratch = retained.closed_inventory(files, directories, required)
        if missing:
            raise V2Error(f"retained evidence is missing: {sorted(missing)}")
        retained.validate_claims(root, files, result, names)
        selected = required
    else:
        selected = {"result.json", "campaign.log"}
        missing = selected - set(files)
        if missing:
            raise V2Error(f"reproduction identity bytes are missing: {sorted(missing)}")
        unexpected = set(files) - {path for path in files if path.startswith("work/")} - selected
        if unexpected:
            raise V2Error(f"unexpected reproduction root files: {sorted(unexpected)}")
        scratch = {path for path in files if path.startswith("work/")}
    payload: dict[str, bytes] = {}
    for relative in sorted(selected):
        payload[f"{prefix}/{relative}"] = retained.read_regular(root, relative, files[relative])
    scratch_rows = []
    for relative in sorted(scratch):
        data = retained.read_regular(root, relative, files[relative])
        scratch_rows.append([relative, len(data), digest(data)])
    summary = {
        "root_basename": root.name,
        "scanned_regular_file_count": len(files),
        "scanned_size_bytes": sum(info.st_size for info in files.values()),
        "retained_payload_file_count": len(selected),
        "retained_payload_size_bytes": sum(len(value) for value in payload.values()),
        "excluded_scratch_file_count": len(scratch_rows),
        "excluded_scratch_inventory_sha256": digest(semantic_bytes(scratch_rows)),
    }
    return payload, summary


def semantic_definition() -> dict[str, Any]:
    return {
        "algorithm": "SHA-256",
        "input": "parsed a23_full_single_edge_replay_result_v1",
        "exclusions": "remove exactly these JSON pointers before serialization",
        "excluded_json_pointers": list(EPHEMERAL_LOG_POINTERS),
        "serialization": (
            "UTF-8 JSON; sort_keys=true; separators=(',',':'); ensure_ascii=true; "
            "no trailing newline"
        ),
        "all_other_fields_including_package_source_integration_tool_and_trace_identities":
            "included",
    }


def inventory(payload: dict[str, bytes], roles: dict[str, str]) -> list[dict[str, Any]]:
    return [{
        "path": path,
        "role": roles.get(path, "retained_campaign_evidence"),
        "size_bytes": len(data),
        "sha256": digest(data),
    } for path, data in sorted(payload.items())]


def write_archive(
    path: Path, manifest: dict[str, Any], payload: dict[str, bytes],
) -> None:
    if path.exists() or path.is_symlink():
        raise V2Error(f"archive output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = {f"{ARCHIVE_PREFIX}/MANIFEST.json": pretty(manifest)}
    entries.update({f"{ARCHIVE_PREFIX}/{name}": data for name, data in payload.items()})
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                           compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted(entries.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o444
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))


def read_archive(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    seen: set[str] = set()
    contents: dict[str, bytes] = {}
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = retained.safe_relative(member.name, "archive member")
                if name in seen:
                    raise V2Error(f"duplicate archive member: {name}")
                seen.add(name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise V2Error(f"archive member is not a regular file: {name}")
                if (member.mode != 0o444 or member.uid != 0 or member.gid != 0 or
                        member.uname not in ("", None) or member.gname not in ("", None) or
                        member.mtime != 0):
                    raise V2Error(f"unsafe or nondeterministic archive metadata: {name}")
                if set(member.pax_headers) - {"path"}:
                    raise V2Error(f"unexpected PAX metadata: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise V2Error(f"archive member cannot be read: {name}")
                data = extracted.read()
                if len(data) != member.size:
                    raise V2Error(f"archive member size differs: {name}")
                contents[name] = data
    except (tarfile.TarError, OSError) as error:
        raise V2Error(f"cannot reopen export: {error}") from error
    manifest_name = f"{ARCHIVE_PREFIX}/MANIFEST.json"
    if manifest_name not in contents:
        raise V2Error("sealed export manifest is absent")
    manifest = load_json_bytes(contents.pop(manifest_name), "sealed export manifest")
    if manifest.get("schema") != EXPORT_SCHEMA or manifest.get("status") != STATUS:
        raise V2Error("sealed export manifest schema/status differs")
    expected: dict[str, dict[str, Any]] = {}
    rows = manifest.get("inventory")
    if not isinstance(rows, list):
        raise V2Error("sealed export inventory is not a list")
    for row in rows:
        row = retained.require_object(row, "sealed export inventory row")
        if set(row) != {"path", "role", "size_bytes", "sha256"}:
            raise V2Error("sealed export inventory row fields differ")
        name = f"{ARCHIVE_PREFIX}/{retained.safe_relative(row['path'], 'inventory path')}"
        if name in expected:
            raise V2Error(f"duplicate inventory path: {name}")
        expected[name] = row
    if set(contents) != set(expected):
        raise V2Error(
            f"sealed export is not closed: missing={sorted(set(expected)-set(contents))} "
            f"extra={sorted(set(contents)-set(expected))}"
        )
    for name, row in expected.items():
        data = contents[name]
        if len(data) != row["size_bytes"] or digest(data) != row["sha256"]:
            raise V2Error(f"sealed export hash/size differs: {name}")
    payload = {name.removeprefix(f"{ARCHIVE_PREFIX}/"): data
               for name, data in contents.items()}
    return manifest, payload


def materialize_primary(payload: dict[str, bytes], root: Path) -> None:
    prefix = "primary/"
    for name, data in payload.items():
        if not name.startswith(prefix):
            continue
        relative = retained.safe_relative(name.removeprefix(prefix), "primary payload path")
        destination = root.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def validate_reopened(
    archive_path: Path, result_path: Path, publication_path: Path,
) -> dict[str, Any]:
    manifest, payload = read_archive(archive_path)
    v2_result = load_json(result_path, "published v2 result")
    publication = load_json(publication_path, "v2 publication")
    if v2_result.get("schema") != V2_RESULT_SCHEMA or v2_result.get("status") != STATUS:
        raise V2Error("published v2 result schema/status differs")
    if publication.get("schema") != PUBLICATION_SCHEMA or publication.get("status") != STATUS:
        raise V2Error("publication schema/status differs")
    checks = {
        "v2_result_sha256": file_digest(result_path),
        "export_sha256": file_digest(archive_path),
        "export_manifest_sha256": digest(pretty(manifest)),
    }
    for key, actual in checks.items():
        if publication.get(key) != actual:
            raise V2Error(f"publication tuple differs for {key}")
    if publication.get("v2_result_size_bytes") != result_path.stat().st_size:
        raise V2Error("publication v2 result size differs")
    if publication.get("export_size_bytes") != archive_path.stat().st_size:
        raise V2Error("publication export size differs")
    embedded_result = payload.get("result/synthetic_v2_result.json")
    if embedded_result != result_path.read_bytes():
        raise V2Error("embedded and published v2 results differ")
    primary_result = load_json_bytes(payload["primary/result.json"], "embedded primary result")
    reproduction_result = load_json_bytes(
        payload["reproduction/result.json"], "embedded reproduction result"
    )
    primary_semantic = semantic_digest(primary_result)
    reproduction_semantic = semantic_digest(reproduction_result)
    if primary_semantic != reproduction_semantic:
        raise V2Error("reopened semantic reproduction differs")
    if v2_result["semantic_reproduction"]["semantic_digest_sha256"] != primary_semantic:
        raise V2Error("v2 semantic digest differs after reopen")
    differences = sorted(difference_pointers(primary_result, reproduction_result))
    if differences != v2_result["semantic_reproduction"]["observed_difference_json_pointers"]:
        raise V2Error("v2 observed difference pointers differ after reopen")
    if not set(differences) <= set(EPHEMERAL_LOG_POINTERS):
        raise V2Error("non-ephemeral result field differs across reproduction")
    with tempfile.TemporaryDirectory(prefix="a23-v2-reopen-") as temporary:
        extracted = Path(temporary)
        materialize_primary(payload, extracted)
        files, directories = retained.scan_regular_tree(extracted)
        names = verify_result_identity(primary_result, publication["package_commit"])
        required = retained.expected_evidence_paths(names) | {"campaign.log"}
        missing, scratch = retained.closed_inventory(files, directories, required)
        if missing or scratch:
            raise V2Error("reopened primary payload is missing evidence or contains scratch")
        retained.validate_claims(extracted, files, primary_result, names)
        sequences = sequence_evidence(extracted, names)
        if sequences != v2_result["sequence_evidence"]["full50_runs"]:
            raise V2Error("reopened sequence evidence differs")
    identity = v2_result["identities"]
    for key in ("package_commit", "source_commit", "source_tree",
                "integration_commit", "integration_tree", "tool_identity_sha256",
                "trace_identity_sha256", "package_input_identity_sha256"):
        if publication.get(key) != identity.get(key):
            raise V2Error(f"publication identity tuple differs for {key}")
    return {
        "status": STATUS,
        "archive_sha256": checks["export_sha256"],
        "archive_size_bytes": archive_path.stat().st_size,
        "inventory_entry_count": len(manifest["inventory"]),
        "semantic_digest_sha256": primary_semantic,
    }


def seal(
    primary_root: Path, reproduction_root: Path, result_path: Path,
    archive_path: Path, publication_path: Path,
) -> dict[str, Any]:
    outputs = (result_path, archive_path, publication_path)
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise V2Error("v2 publication outputs must not exist")
    package_commit = current_commit()
    primary = load_json(primary_root / "result.json", "primary result")
    reproduction = load_json(reproduction_root / "result.json", "reproduction result")
    names = verify_result_identity(primary, package_commit)
    if verify_result_identity(reproduction, package_commit) != names:
        raise V2Error("reproduction trace roster differs")
    differences = sorted(difference_pointers(primary, reproduction))
    if not set(differences) <= set(EPHEMERAL_LOG_POINTERS):
        raise V2Error(f"non-ephemeral reproduction differences: {differences}")
    primary_semantic = semantic_digest(primary)
    reproduction_semantic = semantic_digest(reproduction)
    if primary_semantic != reproduction_semantic:
        raise V2Error("semantic reproduction digest differs")
    verify_rtl_git_blobs(primary)
    tool_digest = verify_tools(primary)
    package_payload, package_digest = verify_package_inputs(primary, package_commit)
    traces, trace_digest = trace_identity(primary, names)
    primary_payload, primary_summary = retained_payload(
        primary_root, primary, names, "primary", full=True,
    )
    reproduction_payload, reproduction_summary = retained_payload(
        reproduction_root, reproduction, names, "reproduction", full=False,
    )
    sequences = sequence_evidence(primary_root, names)
    identities = {
        "package_commit": package_commit,
        "package_input_identity_sha256": package_digest,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_tree": EXPECTED_SOURCE_TREE,
        "integration_commit": EXPECTED_INTEGRATION_COMMIT,
        "integration_tree": EXPECTED_INTEGRATION_TREE,
        "tool_identity_sha256": tool_digest,
        "trace_identity_sha256": trace_digest,
        "pins_sha256": EXPECTED_PINS_SHA256,
    }
    v2_result = {
        "schema": V2_RESULT_SCHEMA,
        "status": STATUS,
        "evidence_class": "TEAM_DEFINED_SYNTHETIC_FULL50_ACTUAL_SINGLE_EDGE_RTL_V2",
        "dataset": {
            "id": "full50",
            "source_class": "TEAM_DEFINED_SYNTHETIC",
            "organizer_official": False,
            "trace_count": 50,
            "actual_full50_executions": 100,
            "shared_prepared_trace_count": 50,
            "trace_identities": traces,
        },
        "execution_accounting": primary["execution_accounting"],
        "identities": identities,
        "primary": {
            "legacy_result_sha256": file_digest(primary_root / "result.json"),
            "legacy_result_size_bytes": (primary_root / "result.json").stat().st_size,
            "retention": primary_summary,
        },
        "semantic_reproduction": {
            "definition": semantic_definition(),
            "semantic_digest_sha256": primary_semantic,
            "primary_legacy_result_sha256": file_digest(primary_root / "result.json"),
            "reproduction_legacy_result_sha256": file_digest(
                reproduction_root / "result.json"
            ),
            "reproduction_legacy_result_size_bytes": (
                reproduction_root / "result.json"
            ).stat().st_size,
            "observed_difference_json_pointers": differences,
            "retention": reproduction_summary,
        },
        "sequence_evidence": {
            "full50_runs": sequences,
            "event_row_order": "trace/TB event-id row order retained and hashed",
            "execution_time_global_retire_order": (
                "checked by the pinned TB accepted FIFO and bound by each retained PASS log"
            ),
            "within_same_cycle_lane_order_reconstructable_from_event_csv": False,
            "nonclaim": (
                "row/timing digests do not independently reconstruct within-cycle lane order"
            ),
        },
        "qualification": {
            "hardened_synthetic_single_edge_RTL": "PASS",
            "canonical_campaign": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
            "physical": "HOLD",
            "power": "HOLD",
            "CDC_RDC": "HOLD",
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(pretty(v2_result))
    payload = {}
    payload.update(primary_payload)
    payload.update(reproduction_payload)
    payload.update(package_payload)
    payload["result/synthetic_v2_result.json"] = result_path.read_bytes()
    roles = {path: "verified_repository_input" for path in package_payload}
    roles["primary/result.json"] = "primary_legacy_result"
    roles["primary/campaign.log"] = "primary_campaign_driver_log"
    roles["reproduction/result.json"] = "semantic_reproduction_legacy_result"
    roles["reproduction/campaign.log"] = "semantic_reproduction_campaign_driver_log"
    roles["result/synthetic_v2_result.json"] = "published_v2_result"
    rows = inventory(payload, roles)
    manifest = {
        "schema": EXPORT_SCHEMA,
        "status": STATUS,
        "archive_prefix": ARCHIVE_PREFIX,
        "safe_metadata": {
            "regular_files_only": True,
            "mode": "0444",
            "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
            "gzip_mtime": 0,
        },
        "closure": {
            "manifest_is_the_only_non_inventory_member": True,
            "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
            "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
            "missing_or_extra_entries": "FORBIDDEN",
        },
        "inventory_entry_count": len(rows),
        "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        "inventory": rows,
    }
    write_archive(archive_path, manifest, payload)
    publication = {
        "schema": PUBLICATION_SCHEMA,
        "status": STATUS,
        **identities,
        "semantic_digest_sha256": primary_semantic,
        "primary_legacy_result_sha256": file_digest(primary_root / "result.json"),
        "primary_legacy_result_size_bytes": (primary_root / "result.json").stat().st_size,
        "reproduction_legacy_result_sha256": file_digest(
            reproduction_root / "result.json"
        ),
        "reproduction_legacy_result_size_bytes": (
            reproduction_root / "result.json"
        ).stat().st_size,
        "v2_result_sha256": file_digest(result_path),
        "v2_result_size_bytes": result_path.stat().st_size,
        "export_sha256": file_digest(archive_path),
        "export_size_bytes": archive_path.stat().st_size,
        "export_manifest_sha256": digest(pretty(manifest)),
        "export_inventory_entry_count": len(rows),
        "physical_status": "HOLD",
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    }
    publication_path.write_bytes(pretty(publication))
    return validate_reopened(archive_path, result_path, publication_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    campaign = subparsers.add_parser("campaign")
    campaign.add_argument("--retained-root", required=True, type=Path)
    sealer = subparsers.add_parser("seal")
    sealer.add_argument("--primary-root", required=True, type=Path)
    sealer.add_argument("--reproduction-root", required=True, type=Path)
    sealer.add_argument("--result", required=True, type=Path)
    sealer.add_argument("--archive", required=True, type=Path)
    sealer.add_argument("--publication", required=True, type=Path)
    validator = subparsers.add_parser("validate")
    validator.add_argument("--result", required=True, type=Path)
    validator.add_argument("--archive", required=True, type=Path)
    validator.add_argument("--publication", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "campaign":
            run_campaign(args.retained_root)
            print(f"A23_SYNTHETIC_V2_CAMPAIGN_PASS retained_root={args.retained_root}")
        elif args.command == "seal":
            report = seal(
                args.primary_root, args.reproduction_root,
                args.result, args.archive, args.publication,
            )
            print("A23_SYNTHETIC_V2_SEAL_PASS " + json.dumps(report, sort_keys=True))
        else:
            report = validate_reopened(args.archive, args.result, args.publication)
            print("A23_SYNTHETIC_V2_VALIDATE_PASS " + json.dumps(report, sort_keys=True))
        return 0
    except (V2Error, retained.RejectError, retained.HoldError, OSError,
            subprocess.SubprocessError, ValueError, KeyError) as error:
        print(f"A23_SYNTHETIC_V2_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
