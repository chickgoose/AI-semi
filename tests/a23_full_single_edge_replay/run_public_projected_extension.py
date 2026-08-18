#!/usr/bin/env python3
"""Actual-RTL replay of the noncanonical public UZH projected extension."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from typing import Any


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
PINS = PACKAGE / "public_projected_pins.json"
BASE_PINS = PACKAGE / "pins.json"
DEFAULT_PROJECTION = Path("/tmp/redred-uzh-shapes-projection-f59c10e")
SCENARIOS = ("1x", "64x", "256x")
TRACE_FIELDS = {
    "occurrence_cycle", "tb_only_event_id", "logical_source", "x", "y",
    "polarity", "event_type", "relation_id", "relation_role", "deadline",
}
EXPECTED_DIAGNOSTIC = {
    "drop": "A23_SE_DROP_FAIL",
    "duplicate": "A23_SE_DUPLICATE_FAIL",
    "reorder": "A23_SE_REORDER_FAIL",
    "reset_escape": "A23_SE_RESET_ESCAPE_FAIL",
}

sys.path.insert(0, str(PACKAGE))
import run_replay as base  # noqa: E402

sys.path.insert(0, str(PROJECT))
from benchmarks.redred_uzh_shapes_projection import project as projection  # noqa: E402


class ExtensionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def pretty(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode("ascii")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExtensionError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExtensionError(f"JSON root is not an object: {path}")
    return value


def load_pins() -> dict[str, Any]:
    pins = load_json(PINS)
    if pins.get("schema") != "a23_public_projected_extension_pins_v1":
        raise ExtensionError("public projected pin schema mismatch")
    if pins.get("status") != "PUBLIC_PROJECTED_EXTENSION":
        raise ExtensionError("extension status must remain PUBLIC_PROJECTED_EXTENSION")
    for field in ("release_status", "selection_status"):
        if pins.get(field) != "HOLD":
            raise ExtensionError(f"{field} must remain HOLD")
    if pins.get("canonical_redred_traffic") is not False:
        raise ExtensionError("projected extension cannot be canonical REDRED")
    if pins.get("official_redred_traffic") is not False:
        raise ExtensionError("projected extension cannot be official REDRED")
    if pins.get("p6_evidence_used") is not False:
        raise ExtensionError("P6 provenance is forbidden")
    scenarios = pins.get("scenarios")
    if not isinstance(scenarios, list) or tuple(row.get("id") for row in scenarios) != SCENARIOS:
        raise ExtensionError("projected scenario order must be exactly 1x,64x,256x")
    if any(row.get("event_count") != 1100 for row in scenarios):
        raise ExtensionError("every projected scenario must contain exactly 1100 rows")
    if pins.get("identity_accounting") != {
        "unique_projected_window_events": 1100,
        "scenario_retimings": 3,
        "pooled_3300_unique_events": False,
    }:
        raise ExtensionError("projected identity accounting forbids pooled 3300")
    return pins


def git_bytes(commit: str, path: str) -> bytes:
    process = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=PROJECT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise ExtensionError(f"Git object lacks projected source path: {commit}:{path}")
    return process.stdout


def verify_extension_sources(pins: dict[str, Any], *, allow_dirty: bool) -> str:
    base_sha = base.sha256(BASE_PINS)
    if base_sha != pins.get("hardened_replay_pins_sha256"):
        raise ExtensionError("hardened actual-RTL replay pins changed")
    files = pins.get("files")
    if not isinstance(files, dict):
        raise ExtensionError("extension file pins are absent")
    for relative, expected in files.items():
        path = PROJECT / relative
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ExtensionError(f"extension file pin is invalid: {relative}")
        if not path.is_file() or base.sha256(path) != expected:
            raise ExtensionError(f"extension file SHA mismatch: {relative}")
    provenance = pins.get("projection_source_provenance", {})
    source_commit = provenance.get("source_commit")
    integrated_commit = provenance.get("integrated_commit")
    source_tree = provenance.get("source_tree")
    integrated_tree = provenance.get("integrated_tree")
    for label, value in (
        ("source_commit", source_commit), ("integrated_commit", integrated_commit),
        ("source_tree", source_tree), ("integrated_tree", integrated_tree),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ExtensionError(f"invalid projected Git provenance: {label}")
    for commit, expected_tree in (
        (source_commit, source_tree), (integrated_commit, integrated_tree),
    ):
        actual_tree = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=PROJECT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
        if actual_tree != expected_tree:
            raise ExtensionError(f"projected Git tree mismatch: {commit}")
    for relative in provenance.get("verified_paths", []):
        expected = files.get(relative)
        if expected is None:
            raise ExtensionError(f"projected Git path is not file-pinned: {relative}")
        for commit in (source_commit, integrated_commit):
            if digest_bytes(git_bytes(commit, relative)) != expected:
                raise ExtensionError(f"projected source bytes differ in {commit}: {relative}")
    selected = sorted(set(files) | {str(PINS.relative_to(PROJECT)), str(BASE_PINS.relative_to(PROJECT))})
    if not allow_dirty:
        for relative in selected:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", relative], cwd=PROJECT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if tracked.returncode:
                raise ExtensionError(f"extension input is not tracked: {relative}")
        changed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *selected],
            cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        if changed:
            raise ExtensionError("extension package is not clean against HEAD")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def parse_trace(path: Path, scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    data = path.read_bytes()
    actual_sha = digest_bytes(data)
    if actual_sha != scenario["trace_sha256"]:
        raise ExtensionError(f"wrong projected trace hash: {scenario['id']}")
    rows: list[dict[str, Any]] = []
    previous = (-1, -1)
    for line_number, raw in enumerate(data.splitlines(keepends=True), start=1):
        if not raw.endswith(b"\n"):
            raise ExtensionError(f"projected trace lacks LF: {scenario['id']}:{line_number}")
        try:
            row = json.loads(raw[:-1].decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ExtensionError(f"invalid projected JSONL: {scenario['id']}:{line_number}") from error
        if not isinstance(row, dict) or set(row) != TRACE_FIELDS:
            raise ExtensionError(f"projected trace field mismatch: {scenario['id']}:{line_number}")
        event_id = len(rows)
        cycle = row["occurrence_cycle"]
        source = row["logical_source"]
        if row["tb_only_event_id"] != event_id:
            raise ExtensionError(f"projected event order mismatch: {scenario['id']}:{line_number}")
        if (isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 0 or
                isinstance(source, bool) or not isinstance(source, int) or not 0 <= source < 16):
            raise ExtensionError(f"projected event scalar mismatch: {scenario['id']}:{line_number}")
        if (row["x"] + 4 * row["y"] != source or row["polarity"] not in (-1, 1) or
                row["event_type"] != "public_projected_event" or
                row["relation_id"] is not None or row["relation_role"] is not None or
                not isinstance(row["deadline"], int) or row["deadline"] < cycle):
            raise ExtensionError(f"projected event semantics mismatch: {scenario['id']}:{line_number}")
        if (cycle, event_id) < previous:
            raise ExtensionError(f"projected occurrence order mismatch: {scenario['id']}:{line_number}")
        previous = (cycle, event_id)
        rows.append(row)
    if len(rows) != 1100 or len(rows) != scenario["event_count"]:
        raise ExtensionError(f"wrong projected row count: {scenario['id']}")
    if rows[0]["occurrence_cycle"] != scenario["first_cycle"]:
        raise ExtensionError(f"wrong projected first cycle: {scenario['id']}")
    if rows[-1]["occurrence_cycle"] != scenario["last_cycle"]:
        raise ExtensionError(f"wrong projected last cycle: {scenario['id']}")
    return rows, actual_sha


def verify_projection_package(
    projection_dir: Path, pins: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    receipt_path = projection_dir / "receipt.json"
    completion_path = projection_dir / "COMPLETE.json"
    if base.sha256(receipt_path) != pins["projection_receipt_sha256"]:
        raise ExtensionError("wrong projection receipt hash")
    if base.sha256(completion_path) != pins["projection_completion_sha256"]:
        raise ExtensionError("wrong projection completion hash")
    inspected = projection.inspect(projection_dir)
    if inspected != {
        "status": "HOLD_PUBLIC_PROJECTED_EXTENSION_UNREPLAYED",
        "release_status": "HOLD",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "actual_replay_bound": False,
        "receipt_sha256": pins["projection_receipt_sha256"],
    }:
        raise ExtensionError("projection receipt was relabeled or already replay-bound")
    receipt = load_json(receipt_path)
    if [row.get("id") for row in receipt.get("scenarios", [])] != list(SCENARIOS):
        raise ExtensionError("wrong projection receipt scenario order")
    if receipt.get("conservation", {}).get("projected_events") != 1100:
        raise ExtensionError("wrong projection receipt event count")
    if receipt.get("lineage", {}).get("p6_evidence_used") is not False:
        raise ExtensionError("projection receipt contains P6 provenance")
    traces: dict[str, list[dict[str, Any]]] = {}
    identity_fingerprint: bytes | None = None
    for scenario in pins["scenarios"]:
        rows, _ = parse_trace(projection_dir / scenario["trace_file"], scenario)
        fingerprint = canonical([
            [row["tb_only_event_id"], row["logical_source"], row["x"], row["y"], row["polarity"]]
            for row in rows
        ])
        if identity_fingerprint is None:
            identity_fingerprint = fingerprint
        elif fingerprint != identity_fingerprint:
            raise ExtensionError("projected scenario inputs do not preserve identical event identity/order")
        traces[scenario["id"]] = rows
    return traces, receipt


def prepare_inputs(
    work: Path, projection_dir: Path, pins: dict[str, Any], traces: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    prepared: dict[str, dict[str, Any]] = {}
    for scenario in pins["scenarios"]:
        scenario_id = scenario["id"]
        trace_path = projection_dir / scenario["trace_file"]
        manifest_path = work / "prepared" / f"{scenario_id}.manifest.json"
        output_path = work / "prepared" / f"{scenario_id}.trace"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "trace_file": trace_path.name,
            "trace_sha256": scenario["trace_sha256"],
            "event_count": 1100,
            "event_identity_mode": "address_only",
            "report_group": f"public_projected_{scenario_id}",
            "run": {
                "name": f"public_projected_{scenario_id}",
                "geometry": {"width": 4, "height": 4},
                "stim_cycles": scenario["last_cycle"] + 1,
                "load": "0.0",
                "sink": {"mode": "always"},
                "seed": f"uzh_shapes_{scenario_id}",
            },
        }
        manifest_path.write_bytes(pretty(manifest))
        base.run(
            [sys.executable, str(base.PREPARER), "--trace", str(trace_path),
             "--run-manifest", str(manifest_path), "--output", str(output_path),
             "--addr-width", "4"],
            cwd=PROJECT, log=work / f"logs/prepare-public-{scenario_id}.log",
        )
        lines = output_path.read_text(encoding="ascii").splitlines()
        if len(lines) != 1101:
            raise ExtensionError(f"prepared projected row count mismatch: {scenario_id}")
        encoded = [tuple(map(int, line.split())) for line in lines[1:]]
        expected = [
            (row["occurrence_cycle"], row["tb_only_event_id"], row["logical_source"],
             row["logical_source"], row["deadline"])
            for row in traces[scenario_id]
        ]
        if encoded != expected:
            raise ExtensionError(f"prepared projected bytes changed identity/order: {scenario_id}")
        prepared[scenario_id] = {
            "path": output_path,
            "sha256": base.sha256(output_path),
            "manifest_path": manifest_path,
            "manifest_sha256": base.sha256(manifest_path),
            "stim_cycles": scenario["last_cycle"] + 1,
        }
    return prepared


def export_bundle(
    work: Path, projection_dir: Path, result_path: Path, bundle_path: Path,
) -> tuple[str, str, int]:
    entries: list[tuple[str, Path]] = []
    for name in (
        "COMPLETE.json", "LICENSE.txt", "projected_events.jsonl", "receipt.json",
        "trace_1x.jsonl", "trace_64x.jsonl", "trace_256x.jsonl",
    ):
        entries.append((f"inputs/{name}", projection_dir / name))
    for root_name in ("prepared", "artifacts", "mutated-rtl", "logs"):
        root = work / root_name
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            entries.append((f"run/{root_name}/{path.relative_to(root)}", path))
    entries.append(("result/public_projected_result.json", result_path))
    inventory = {
        arcname: {"size_bytes": path.stat().st_size, "sha256": base.sha256(path)}
        for arcname, path in entries
    }
    manifest = {
        "schema": "a23_public_projected_export_manifest_v1",
        "status": "PUBLIC_PROJECTED_EXTENSION",
        "release_status": "HOLD",
        "selection_status": "HOLD",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "unique_projected_window_events": 1100,
        "scenario_retimings": list(SCENARIOS),
        "pooled_3300_unique_events": False,
        "entries": inventory,
    }
    manifest_bytes = pretty(manifest)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with bundle_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                all_entries = [("MANIFEST.json", manifest_bytes)] + [
                    (arcname, path.read_bytes()) for arcname, path in entries
                ]
                for arcname, payload in all_entries:
                    info = tarfile.TarInfo(arcname)
                    info.size = len(payload)
                    info.mode = 0o444
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(payload))
    return base.sha256(bundle_path), digest_bytes(manifest_bytes), len(entries)


def run_campaign(args: argparse.Namespace) -> int:
    pins = load_pins()
    package_commit = verify_extension_sources(pins, allow_dirty=args.allow_dirty)
    base_document = base.load_document()
    sources, verified_files, verified_tools, rtl_git, _ = base.validate_integration(
        base_document, args.verilator, allow_dirty=args.allow_dirty,
    )
    traces, projection_receipt = verify_projection_package(args.projection_dir, pins)
    work = args.work_dir.resolve()
    output = args.output.resolve()
    bundle = args.export_bundle.resolve()
    publication = args.publication.resolve()
    if any(path.exists() for path in (work, output, bundle, publication)):
        raise ExtensionError("work/output/export/publication paths must not exist")
    work.mkdir(parents=True)
    prepared = prepare_inputs(work, args.projection_dir, pins, traces)
    owners: dict[str, Any] = {}
    mutation_results: list[dict[str, Any]] = []
    for owner in ("a2", "a3"):
        print(f"A23_PUBLIC_PROJECTED_OWNER_START owner={owner}", flush=True)
        simulator, build_log = base.compile_simulator(
            work, args.verilator, base_document, owner, sources[owner],
        )
        scenario_results: dict[str, Any] = {}
        for scenario_id in SCENARIOS:
            name = f"public_projected_{scenario_id}"
            _, artifact, simulation_log = base.execute_case(
                work, simulator, owner, name, "full", prepared[scenario_id]["path"],
            )
            assert artifact is not None
            if artifact["generated"] != 1100:
                raise ExtensionError(f"actual RTL generated count mismatch: {owner}/{scenario_id}")
            if artifact["accepted"] != artifact["retired"]:
                raise ExtensionError(f"actual RTL exact-once mismatch: {owner}/{scenario_id}")
            scenario_results[scenario_id] = {
                **base.public(artifact),
                "source_trace_sha256": next(
                    row["trace_sha256"] for row in pins["scenarios"] if row["id"] == scenario_id
                ),
                "prepared_trace_sha256": prepared[scenario_id]["sha256"],
                "prepared_manifest_sha256": prepared[scenario_id]["manifest_sha256"],
                "simulation_log_sha256": base.sha256(simulation_log),
            }
        _, reset_artifact, reset_log = base.execute_case(
            work, simulator, owner, "reset_drain_epochs", "reset", None,
        )
        assert reset_artifact is not None
        if reset_artifact["pre_reset_clean_drain"] != 1:
            raise ExtensionError(f"extension reset lacked clean pre-drain: {owner}")
        _, activation, activation_log = base.execute_case(
            work, simulator, owner, "public_projected_mutation_activation", "pair", None,
        )
        assert activation is not None
        if activation["count2_commits"] < 1:
            raise ExtensionError(f"extension mutation activation failed: {owner}")
        owners[owner] = {
            "baseline_build_log_sha256": base.sha256(build_log),
            "scenarios": scenario_results,
            "reset": {**base.public(reset_artifact), "simulation_log_sha256": base.sha256(reset_log)},
            "mutation_activation": {
                **base.public(activation), "simulation_log_sha256": base.sha256(activation_log),
            },
        }
        for mutation in base.MUTATION_NAMES:
            changed, identity = base.mutated_sources(
                work, base_document, owner, mutation, sources[owner],
            )
            mutant, mutation_build_log = base.compile_simulator(
                work, args.verilator, base_document, owner, changed, mutation,
            )
            mode = "reset" if mutation == "reset_escape" else "pair"
            name = "reset_drain_epochs" if mode == "reset" else "public_projected_mutation_activation"
            process, _, simulation_log = base.execute_case(
                work, mutant, owner, name, mode, None,
                mutation=mutation, expect_success=False,
            )
            first = base.first_diagnostic(process.stdout)
            if (process.returncode == 0 or first != EXPECTED_DIAGNOSTIC[mutation] or
                    "A23_SE_ACTUAL_RTL_PASS" in process.stdout):
                raise ExtensionError(f"extension source mutation survived: {owner}/{mutation}")
            mutation_results.append({
                "owner": owner, "mutation": mutation,
                "compiled_successfully": True, "executed": True, "killed": True,
                "exit_code": process.returncode, "first_diagnostic": first,
                "actual_endpoint_RTL_source_rewrite": True,
                "source_identity": identity,
                "build_log_sha256": base.sha256(mutation_build_log),
                "simulation_log_sha256": base.sha256(simulation_log),
            })
            print(f"A23_PUBLIC_PROJECTED_MUTATION_KILLED owner={owner} mutation={mutation}", flush=True)
    prepared_hashes = {name: value["sha256"] for name, value in prepared.items()}
    for scenario_id in SCENARIOS:
        hashes = {owners[owner]["scenarios"][scenario_id]["prepared_trace_sha256"] for owner in owners}
        if hashes != {prepared_hashes[scenario_id]}:
            raise ExtensionError(f"A2/A3 prepared input differs: {scenario_id}")
    result = {
        "schema": "a23_public_projected_extension_result_v1",
        "status": "PUBLIC_PROJECTED_EXTENSION",
        "release_status": "HOLD",
        "selection_status": "HOLD",
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "p6_evidence_used": False,
        "identity_accounting": {
            "unique_projected_window_events": 1100,
            "scenario_retimings": list(SCENARIOS),
            "pooled_3300_unique_events": False,
        },
        "execution_accounting": {
            "owners": 2, "projected_actual_RTL_executions": 6,
            "reset_actual_RTL_executions": 2,
            "mutation_activation_actual_RTL_executions": 2,
            "mutation_actual_RTL_executions": 8,
            "receipt_only_executions": 0,
        },
        "projection": {
            "receipt_sha256": pins["projection_receipt_sha256"],
            "completion_sha256": pins["projection_completion_sha256"],
            "specification_sha256": projection_receipt["specification"]["sha256"],
            "prepared_once_shared_by_A2_A3": prepared_hashes,
        },
        "conservation_contract": [
            "per scenario: generated = source_overrun + accepted",
            "per scenario after clean drain: accepted = retired",
            "source_overrun is not an accepted-event correctness failure",
        ],
        "owners": owners,
        "mutations": mutation_results,
        "provenance": {
            "package_commit": package_commit,
            "extension_pins_sha256": base.sha256(PINS),
            "hardened_replay_pins_sha256": base.sha256(BASE_PINS),
            "verified_actual_RTL_files": verified_files,
            "verified_tools": verified_tools,
            "actual_RTL_git": rtl_git,
            "projection_source_git": pins["projection_source_provenance"],
        },
        "export_contract": {
            "bundle_contains_source_projection_prepared_inputs_run_artifacts_and_mutants": True,
            "publication_seals_result_and_bundle": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pretty(result))
    bundle_sha, manifest_sha, entry_count = export_bundle(
        work, args.projection_dir, output, bundle,
    )
    publication_document = {
        "schema": "a23_public_projected_extension_publication_v1",
        "status": "PUBLIC_PROJECTED_EXTENSION",
        "release_status": "HOLD", "selection_status": "HOLD",
        "canonical_redred_traffic": False, "official_redred_traffic": False,
        "p6_evidence_used": False, "pooled_3300_unique_events": False,
        "package_commit": package_commit,
        "result_sha256": base.sha256(output),
        "export_bundle_sha256": bundle_sha,
        "export_manifest_sha256": manifest_sha,
        "export_entry_count": entry_count,
    }
    publication.write_bytes(pretty(publication_document))
    print(
        "A23_PUBLIC_PROJECTED_EXTENSION_PASS status=PUBLIC_PROJECTED_EXTENSION "
        "release=HOLD selection=HOLD projected_actual=6 reset=2 activation=2 "
        f"mutations=8 result={output} export={bundle}", flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection-dir", type=Path, default=DEFAULT_PROJECTION)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--export-bundle", required=True, type=Path)
    parser.add_argument("--publication", required=True, type=Path)
    parser.add_argument("--verilator", type=Path, default=base.DEFAULT_VERILATOR)
    parser.add_argument("--allow-dirty", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return run_campaign(args)
    except (ExtensionError, base.ReplayError, base.ReplayUnavailable,
            projection.ProjectionFailure, OSError, subprocess.SubprocessError) as error:
        print(f"A23_PUBLIC_PROJECTED_EXTENSION_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
