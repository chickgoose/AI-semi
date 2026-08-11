#!/usr/bin/env python3
"""Prepare exact common streams and evaluate a pinned A7 W5 R1 endpoint bundle."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable


COMMON_COMMIT = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
GENERATOR_SHA256 = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
POLICY_SHA256 = "7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33"
MANIFEST_SHA256 = {
    "full50": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
OFFICIAL_NAMES_SHA256 = {
    "full50": "c0b830a99e7be01a908091a45e594df5dc187923bc887a6a3924a4b20700dc6c",
    "capacity22": "f51147b6533c78928afb7d69683acd54170f757a83fb131db163d56c8bd2f15a",
}
GENERATOR_PATH = "benchmarks/clean_slate_aer/generate_trace.py"
POLICY_PATH = "scripts/common_suite_official.py"
MANIFEST_PATH = {
    "full50": "benchmarks/clean_slate_aer/manifest.neutrality-n16.json",
    "capacity22": "benchmarks/clean_slate_aer/manifest.multilane-n16.json",
}
ENDPOINT_CONTRACT = "a5_w5_r1_full_endpoint_v1"
ENDPOINT_NAMES = ("parallel_r1_full", "ddr_r1_full")
A7_W5_ENDPOINT_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_W5_SOURCE_SHA256 = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv": "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv": "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv": "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv": "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv": "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv": "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv": "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
}
PRIMARY_CLOCK_CONTRACT = "phase_related_synchronous_frozen_source_v1"
HANDSHAKE_CONTRACT = "ready_valid_posedge_each_handshake_v1"
RETIRE_CONTRACT = "consumer_observation_next_ref_rise_v1"
TICKS_PER_CORE_CYCLE = 4
RESET_CYCLES_PER_RUN = 2
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class ContractError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def names_sha256(names: Iterable[str]) -> str:
    return sha256_bytes(("\n".join(names) + "\n").encode("utf-8"))


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def git_text(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], text=True,
        capture_output=True, check=False,
    )
    if result.returncode:
        raise ContractError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def resolve_commit(repository: Path, commit: str) -> str:
    if not COMMIT_RE.fullmatch(commit):
        raise ContractError("endpoint/common commit must be an exact 40-hex object ID")
    resolved = git_text(repository, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ContractError("commit object did not resolve exactly")
    return resolved


def git_blob(repository: Path, commit: str, relative_path: str) -> bytes:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ContractError(f"unsafe git path: {relative_path}")
    result = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "blob",
         f"{commit}:{relative_path}"], capture_output=True, check=False,
    )
    if result.returncode:
        raise ContractError(
            f"missing pinned blob {commit}:{relative_path}: "
            + result.stderr.decode("utf-8", "replace").strip()
        )
    return result.stdout


def exclusive_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def materialize(repository: Path, commit: str, paths: Iterable[str], root: Path) -> None:
    resolve_commit(repository, commit)
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for relative_path in paths:
        exclusive_write(root / relative_path, git_blob(repository, commit, relative_path))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSONL {path}: {exc}") from exc


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


def write_json(path: Path, document: Any) -> None:
    exclusive_write(
        path, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def generate_common_snapshot(common_repo: Path, temporary: Path) -> tuple[Path, dict[str, Any]]:
    snapshot = temporary / "common"
    materialize(
        common_repo, COMMON_COMMIT,
        (GENERATOR_PATH, POLICY_PATH, *MANIFEST_PATH.values()), snapshot,
    )
    generator = snapshot / GENERATOR_PATH
    policy_path = snapshot / POLICY_PATH
    if sha256(generator) != GENERATOR_SHA256 or sha256(policy_path) != POLICY_SHA256:
        raise ContractError("pinned generator/policy SHA mismatch")
    policy = runpy.run_path(str(policy_path))
    if policy.get("GENERATOR_VERSION") != "4.0":
        raise ContractError("pinned policy is not generator-v4")
    generated = temporary / "generated"
    generated.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for suite, manifest_relative in MANIFEST_PATH.items():
        manifest = snapshot / manifest_relative
        if sha256(manifest) != MANIFEST_SHA256[suite]:
            raise ContractError(f"{suite}: official manifest SHA mismatch")
        result = subprocess.run(
            [sys.executable, "-B", str(generator), "--manifest", str(manifest),
             "--output-dir", str(generated / suite)],
            cwd=snapshot, env=environment, text=True, capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ContractError(f"{suite}: generator failed: {result.stdout}{result.stderr}")
    return generated, policy


def boundary_rows(trace_rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    if [row.get("tb_only_event_id") for row in trace_rows] != list(range(len(trace_rows))):
        raise ContractError("trace IDs must be exact contiguous TB-only IDs")
    rows = []
    next_launch = 0
    for presentation_index, trace in enumerate(trace_rows):
        occurrence = trace.get("occurrence_cycle")
        source = trace.get("logical_source")
        if not isinstance(occurrence, int) or occurrence < 0:
            raise ContractError("invalid trace occurrence")
        if not isinstance(source, int) or not 0 <= source < 16:
            raise ContractError("invalid address-only logical source")
        launch = max(occurrence, next_launch)
        rows.append({
            "presentation_index": presentation_index,
            "launch_cycle": launch,
            "occurrence_cycle": occurrence,
            "tb_only_event_id": int(trace["tb_only_event_id"]),
            "address": source,
        })
        next_launch = launch + 1
    return rows


def prepare_boundary(common_repo: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ContractError(f"boundary output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=output.parent))
    staging.chmod(0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="a5-w5-common.") as temporary_name:
            generated, policy = generate_common_snapshot(common_repo, Path(temporary_name))
            index: dict[str, Any] = {
                "schema_version": 1,
                "contract": "a5_w5_trace_to_single_lane_v1",
                "status": "BOUNDARY_PREPARED_ENDPOINT_NOT_QUALIFIED",
                "provenance": {
                    "common_commit": COMMON_COMMIT,
                    "generator_sha256": GENERATOR_SHA256,
                    "official_policy_sha256": POLICY_SHA256,
                    "manifest_sha256": MANIFEST_SHA256,
                },
                "boundary": {
                    "dut_visible_fields": ["address"],
                    "tb_only_fields": ["presentation_index", "occurrence_cycle",
                                       "tb_only_event_id", "launch_cycle"],
                    "serialization": "stable trace order; launch=max(occurrence, prior_launch+1)",
                    "acceptance_policy": (
                        "one frame per core/ref posedge with valid&&ready; valid may stay high "
                        "while successive accepted addresses change; address is stable only "
                        "while valid&&(!ready)"
                    ),
                    "handshake_contract": HANDSHAKE_CONTRACT,
                    "clock_contract": PRIMARY_CLOCK_CONTRACT,
                    "retire_contract": RETIRE_CONTRACT,
                    "single_lane_max_events_per_core_cycle": 1,
                    "timebase_ticks_per_core_cycle": TICKS_PER_CORE_CYCLE,
                    "fixed_window": "0 <= retire_tick < stim_cycles*4",
                    "initial_reset_cycles_per_run": RESET_CYCLES_PER_RUN,
                    "tb_serializer_is_not_dut_hardware": True,
                    "ids_enter_dut": False,
                },
                "suites": {},
            }
            total_events = 0
            for suite in ("full50", "capacity22"):
                generated_index = read_json(generated / suite / "generation-index.json")
                names = tuple(item["run"]["name"] for item in generated_index["runs"])
                if names != tuple(policy["SUITES"][suite]["names"]):
                    raise ContractError(f"{suite}: exact name/order mismatch")
                suite_rows = []
                for metadata in generated_index["runs"]:
                    name = metadata["run"]["name"]
                    trace = generated / suite / metadata["trace_file"]
                    if (sha256(trace) != metadata["trace_sha256"]
                            or metadata["trace_sha256"] != policy["TRACE_SHA256"][name]):
                        raise ContractError(f"{suite}/{name}: exact trace SHA mismatch")
                    rows = boundary_rows(read_jsonl(trace))
                    relative = Path(suite) / f"{name}.single-lane.jsonl"
                    payload = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                                      for row in rows).encode("utf-8")
                    exclusive_write(staging / relative, payload)
                    total_events += len(rows)
                    suite_rows.append({
                        "name": name,
                        "workload": metadata["run"]["workload"],
                        "stim_cycles": metadata["run"]["stim_cycles"],
                        "event_count": len(rows),
                        "trace_sha256": metadata["trace_sha256"],
                        "boundary_file": str(relative),
                        "boundary_sha256": sha256_bytes(payload),
                        "last_launch_cycle": rows[-1]["launch_cycle"] if rows else None,
                    })
                index["suites"][suite] = {
                    "manifest_sha256": MANIFEST_SHA256[suite],
                    "official_names_sha256": names_sha256(names),
                    "run_count": len(suite_rows),
                    "runs": suite_rows,
                }
            index["total_boundary_events_including_suite_overlap"] = total_events
            write_json(staging / "boundary-index.json", index)
            directory_fd = os.open(staging, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.rename(staging, output)
        fsync_directory(output.parent)
        staging = Path()
        return index
    finally:
        if staging != Path() and staging.exists():
            shutil.rmtree(staging)


def validate_boundary(root: Path) -> dict[str, Any]:
    index_path = root / "boundary-index.json"
    index = read_json(index_path)
    if (index.get("schema_version") != 1
            or index.get("contract") != "a5_w5_trace_to_single_lane_v1"):
        raise ContractError("boundary index schema/contract mismatch")
    if index.get("provenance", {}).get("common_commit") != COMMON_COMMIT:
        raise ContractError("boundary common commit mismatch")
    if index.get("boundary", {}).get("ids_enter_dut") is not False:
        raise ContractError("boundary must keep IDs outside DUT")
    if (index.get("boundary", {}).get("handshake_contract") != HANDSHAKE_CONTRACT
            or index.get("boundary", {}).get("clock_contract") != PRIMARY_CLOCK_CONTRACT
            or index.get("boundary", {}).get("retire_contract") != RETIRE_CONTRACT):
        raise ContractError("boundary handshake/clock/retire contract mismatch")
    for suite, expected_count in (("full50", 50), ("capacity22", 22)):
        suite_doc = index["suites"].get(suite, {})
        if suite_doc.get("run_count") != expected_count:
            raise ContractError(f"{suite}: boundary cardinality mismatch")
        names: set[str] = set()
        for run in suite_doc.get("runs", []):
            if run["name"] in names:
                raise ContractError(f"{suite}: duplicate run name")
            names.add(run["name"])
            path = root / run["boundary_file"]
            if sha256(path) != run["boundary_sha256"]:
                raise ContractError(f"{suite}/{run['name']}: boundary SHA mismatch")
            rows = read_jsonl(path)
            if len(rows) != run["event_count"]:
                raise ContractError(f"{suite}/{run['name']}: boundary count mismatch")
            if [row["presentation_index"] for row in rows] != list(range(len(rows))):
                raise ContractError(f"{suite}/{run['name']}: presentation index mismatch")
            launches = [row["launch_cycle"] for row in rows]
            if any(right <= left for left, right in zip(launches, launches[1:])):
                raise ContractError(f"{suite}/{run['name']}: non-single-lane launch")
            if any(row["launch_cycle"] < row["occurrence_cycle"] for row in rows):
                raise ContractError(f"{suite}/{run['name']}: launch precedes occurrence")
            if any(set(row) != {"presentation_index", "launch_cycle", "occurrence_cycle",
                                "tb_only_event_id", "address"} for row in rows):
                raise ContractError(f"{suite}/{run['name']}: boundary field drift")
        if (suite_doc.get("official_names_sha256") != OFFICIAL_NAMES_SHA256[suite]
                or names_sha256(run["name"] for run in suite_doc.get("runs", []))
                != OFFICIAL_NAMES_SHA256[suite]):
            raise ContractError(f"{suite}: official exact name/order digest mismatch")
    return index


def load_endpoint_bundle(repository: Path, commit: str, manifest_path: str,
                         temporary: Path) -> tuple[dict[str, Any], Path, str]:
    resolve_commit(repository, commit)
    if commit != A7_W5_ENDPOINT_COMMIT:
        raise ContractError(f"endpoint commit must be pinned A7 W5 {A7_W5_ENDPOINT_COMMIT}")
    if manifest_path != "A5_BUILTIN_PINNED_A7_W5":
        raise ContractError("production endpoint uses the A5 built-in pinned A7 W5 bundle contract")
    driver_source = Path(__file__).resolve().parent / "production_endpoint_driver.py"
    tb_source = Path(__file__).resolve().parent / "a5_w5_production_tb.sv"
    if not driver_source.is_file() or not tb_source.is_file():
        raise ContractError("A5 production driver/TB is missing")
    manifest = {
        "schema_version": 1, "contract": ENDPOINT_CONTRACT,
        "timebase_ticks_per_core_cycle": TICKS_PER_CORE_CYCLE,
        "dut_visible_fields": ["address"],
        "tb_only_observer_fields": ["presentation_index"],
        "handshake_contract": HANDSHAKE_CONTRACT,
        "clock_contract": PRIMARY_CLOCK_CONTRACT,
        "retire_contract": RETIRE_CONTRACT,
        "sink_ready_policy": "always_ready",
        "toggle_groups": ["data", "control", "clock"],
        "endpoints": [
            {"name": "parallel_r1_full",
             "observation_boundary": "next_ref_rise_after_transmit_commit"},
            {"name": "ddr_r1_full", "transmit_commit": "burst_fall",
             "observation_boundary": "next_ref_rise_after_burst_fall",
             "retire_detector": "charged_seen_toggle"},
        ],
        "driver": {"kind": "python3", "path": "a5/production_endpoint_driver.py"},
        "bundle": ([{"path": path, "sha256": digest}
                    for path, digest in A7_W5_SOURCE_SHA256.items()] + [
            {"path": "a5/production_endpoint_driver.py", "sha256": sha256(driver_source)},
            {"path": "a5/a5_w5_production_tb.sv", "sha256": sha256(tb_source)},
        ]),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                      + "\n").encode()
    manifest_sha = sha256_bytes(manifest_bytes)
    if (manifest.get("schema_version") != 1
            or manifest.get("contract") != ENDPOINT_CONTRACT
            or manifest.get("timebase_ticks_per_core_cycle") != TICKS_PER_CORE_CYCLE
            or tuple(item.get("name") for item in manifest.get("endpoints", ())) != ENDPOINT_NAMES
            or manifest.get("dut_visible_fields") != ["address"]
            or manifest.get("tb_only_observer_fields") != ["presentation_index"]
            or manifest.get("handshake_contract") != HANDSHAKE_CONTRACT
            or manifest.get("clock_contract") != PRIMARY_CLOCK_CONTRACT
            or manifest.get("retire_contract") != RETIRE_CONTRACT
            or manifest.get("sink_ready_policy") != "always_ready"
            or manifest.get("toggle_groups") != ["data", "control", "clock"]):
        raise ContractError("endpoint manifest contract mismatch")
    endpoint_docs = {item["name"]: item for item in manifest["endpoints"]}
    if (endpoint_docs["parallel_r1_full"].get("observation_boundary")
            != "next_ref_rise_after_transmit_commit"
            or endpoint_docs["ddr_r1_full"].get("transmit_commit") != "burst_fall"
            or endpoint_docs["ddr_r1_full"].get("observation_boundary")
            != "next_ref_rise_after_burst_fall"
            or endpoint_docs["ddr_r1_full"].get("retire_detector")
            != "charged_seen_toggle"):
        raise ContractError("endpoint observation-boundary contract mismatch")
    driver = manifest.get("driver", {})
    if driver.get("kind") != "python3" or not isinstance(driver.get("path"), str):
        raise ContractError("endpoint driver contract mismatch")
    bundle = manifest.get("bundle")
    if not isinstance(bundle, list) or not bundle:
        raise ContractError("endpoint bundle is empty")
    by_path = {item.get("path"): item for item in bundle if isinstance(item, dict)}
    if driver["path"] not in by_path or len(by_path) != len(bundle):
        raise ContractError("endpoint bundle paths are duplicate or omit driver")
    root = temporary / "endpoint"
    root.mkdir(mode=0o700)
    for relative, item in by_path.items():
        if not isinstance(relative, str) or not isinstance(item.get("sha256"), str):
            raise ContractError("endpoint bundle entry malformed")
        if relative == "a5/production_endpoint_driver.py":
            content = driver_source.read_bytes()
        elif relative == "a5/a5_w5_production_tb.sv":
            content = tb_source.read_bytes()
        else:
            content = git_blob(repository, commit, relative)
        if sha256_bytes(content) != item["sha256"]:
            raise ContractError(f"stale endpoint bundle SHA: {relative}")
        exclusive_write(root / relative, content)
    exclusive_write(root / "endpoint-manifest.json", manifest_bytes)
    return manifest, root, manifest_sha


def validate_run_result(boundary_rows_: list[dict[str, Any]], result: dict[str, Any],
                        run: dict[str, Any], endpoint: str, suite: str) -> dict[str, Any]:
    if (result.get("schema_version") != 1
            or result.get("endpoint") != endpoint
            or result.get("suite") != suite
            or result.get("name") != run["name"]
            or result.get("trace_sha256") != run["trace_sha256"]
            or result.get("boundary_sha256") != run["boundary_sha256"]
            or result.get("timebase_ticks_per_core_cycle") != TICKS_PER_CORE_CYCLE
            or result.get("dut_visible_fields") != ["address"]
            or result.get("tb_only_observer_fields") != ["presentation_index"]
            or result.get("handshake_contract") != HANDSHAKE_CONTRACT
            or result.get("clock_contract") != PRIMARY_CLOCK_CONTRACT
            or result.get("retire_contract") != RETIRE_CONTRACT
            or result.get("sink_ready_policy") != "always_ready"):
        raise ContractError("endpoint run result contract mismatch")
    accepted = result.get("accepted")
    retired = result.get("retired")
    if not isinstance(accepted, list) or not isinstance(retired, list):
        raise ContractError("endpoint accepted/retired rows missing")
    expected_indices = list(range(len(boundary_rows_)))
    if [row.get("presentation_index") for row in accepted] != expected_indices:
        raise ContractError("endpoint did not accept the exact required cohort")
    if [row.get("presentation_index") for row in retired] != expected_indices:
        raise ContractError("endpoint loss/duplicate/reorder or incomplete drain")
    latencies = []
    fixed_window = 0
    previous_accept_tick = None
    for boundary, accept, retire in zip(boundary_rows_, accepted, retired):
        if accept.get("address") != boundary["address"] or retire.get("address") != boundary["address"]:
            raise ContractError("endpoint address mismatch")
        launch_tick = boundary["launch_cycle"] * TICKS_PER_CORE_CYCLE
        occurrence_tick = boundary["occurrence_cycle"] * TICKS_PER_CORE_CYCLE
        accept_tick = accept.get("accept_tick")
        retire_tick = retire.get("retire_tick")
        if (not isinstance(accept_tick, int) or accept_tick != launch_tick
                or not isinstance(retire_tick, int) or retire_tick < accept_tick):
            raise ContractError("endpoint timing is invalid")
        if accept_tick % TICKS_PER_CORE_CYCLE != 0:
            raise ContractError("accept must be a ready-valid ref/core posedge handshake")
        if (retire_tick % TICKS_PER_CORE_CYCLE != 0
                or retire_tick < accept_tick + 2 * TICKS_PER_CORE_CYCLE):
            raise ContractError(
                "retire must be sampled by the registered consumer, not post-NBA producer output"
            )
        if (previous_accept_tick is not None
                and accept_tick < previous_accept_tick + TICKS_PER_CORE_CYCLE):
            raise ContractError("more than one ready-valid handshake in a core cycle")
        previous_accept_tick = accept_tick
        latencies.append(retire_tick - occurrence_tick)
        fixed_window += int(retire_tick < run["stim_cycles"] * TICKS_PER_CORE_CYCLE)
    reset = result.get("reset", {})
    if (reset.get("initial_reset_cycles") != RESET_CYCLES_PER_RUN
            or reset.get("retired_during_reset") != 0
            or reset.get("phantom_after_reset") != 0
            or reset.get("state_clear_observed") is not True):
        raise ContractError("endpoint reset evidence failed")
    handshake = result.get("handshake", {})
    if (handshake.get("accepted_on_valid_and_ready_posedge") is not True
            or handshake.get("continuous_valid_back_to_back_supported") is not True
            or handshake.get("held_address_stable_while_not_ready") is not True
            or handshake.get("edge_suppression_used") is not False):
        raise ContractError("ready-valid handshake evidence failed")
    observation = result.get("observation", {})
    if (observation.get("consumer_boundary") != "next_ref_rise"
            or observation.get("phase_related_synchronous") is not True
            or observation.get("unrelated_cdc_claimed") is not False):
        raise ContractError("consumer observation evidence failed")
    if endpoint == "ddr_r1_full" and (
            observation.get("transmit_commit") != "burst_fall"
            or observation.get("retire_detector") != "charged_seen_toggle"
            or observation.get("seen_toggle_charged_before_traffic") is not True):
        raise ContractError("DDR commit/toggle observation evidence failed")
    if endpoint == "parallel_r1_full" and observation.get("fair_boundary") \
            != "next_ref_rise_after_transmit_commit":
        raise ContractError("parallel endpoint does not use the fair consumer boundary")
    toggles = result.get("toggles", {})
    if set(toggles) != {"data", "control", "clock"}:
        raise ContractError("endpoint toggle split must be data/control/clock")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in toggles.values()):
        raise ContractError("endpoint toggle counters must be nonnegative integers")
    delivered = len(retired)
    return {
        "accepted": len(accepted),
        "delivered": delivered,
        "fixed_window_delivered": fixed_window,
        "latency_ticks": latencies,
        "toggles": toggles,
        "reset_cycles": reset["initial_reset_cycles"],
    }


def evaluate_endpoint(boundary_root: Path, endpoint_repo: Path, endpoint_commit: str,
                      endpoint_manifest_path: str, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ContractError(f"evaluation output already exists: {output}")
    boundary = validate_boundary(boundary_root)
    boundary_index_sha = sha256(boundary_root / "boundary-index.json")
    with tempfile.TemporaryDirectory(prefix="a5-w5-endpoint.") as temporary_name:
        temporary = Path(temporary_name)
        manifest, bundle_root, manifest_sha = load_endpoint_bundle(
            endpoint_repo, endpoint_commit, endpoint_manifest_path, temporary
        )
        driver = bundle_root / manifest["driver"]["path"]
        driver_output = temporary / "driver-output"
        command = [
            sys.executable, "-B", str(driver),
            "--bundle-root", str(bundle_root),
            "--boundary-root", str(boundary_root.resolve()),
            "--boundary-index-sha256", boundary_index_sha,
            "--endpoint-commit", endpoint_commit,
            "--endpoint-manifest-sha256", manifest_sha,
            "--output-dir", str(driver_output),
        ]
        result = subprocess.run(command, cwd=bundle_root, text=True,
                                capture_output=True, check=False)
        if result.returncode:
            raise ContractError(f"endpoint driver failed: {result.stdout}{result.stderr}")
        result_index_path = driver_output / "endpoint-result-index.json"
        result_index = read_json(result_index_path)
        provenance = result_index.get("provenance", {})
        if (result_index.get("schema_version") != 1
                or provenance.get("endpoint_commit") != endpoint_commit
                or provenance.get("endpoint_manifest_sha256") != manifest_sha
                or provenance.get("boundary_index_sha256") != boundary_index_sha
                or provenance.get("driver_sha256") != sha256(driver)
                or not isinstance(provenance.get("simulator"), dict)
                or not provenance["simulator"].get("identity")
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(provenance["simulator"].get("executable_sha256")))
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(provenance.get("compile_log_sha256")))
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(provenance.get("binary_sha256")))):
            raise ContractError("endpoint result provenance mismatch")
        entries = result_index.get("runs")
        expected_entries = 2 * (50 + 22)
        if not isinstance(entries, list) or len(entries) != expected_entries:
            raise ContractError("endpoint result cardinality mismatch")
        entry_by_key = {}
        for entry in entries:
            key = (entry.get("endpoint"), entry.get("suite"), entry.get("name"))
            if key in entry_by_key:
                raise ContractError("duplicate endpoint result entry")
            artifact_relative = Path(str(entry.get("artifact")))
            if artifact_relative.is_absolute() or ".." in artifact_relative.parts:
                raise ContractError("unsafe endpoint result artifact path")
            artifact = driver_output / artifact_relative
            if sha256(artifact) != entry.get("artifact_sha256"):
                raise ContractError("endpoint result artifact SHA mismatch")
            entry_by_key[key] = read_json(artifact)

        aggregates: dict[str, Any] = {}
        per_run = []
        for endpoint in ENDPOINT_NAMES:
            for suite in ("full50", "capacity22"):
                latency_ticks: list[int] = []
                totals = Counter()
                for run in boundary["suites"][suite]["runs"]:
                    key = (endpoint, suite, run["name"])
                    if key not in entry_by_key:
                        raise ContractError(f"missing endpoint result {key}")
                    boundary_rows_ = read_jsonl(boundary_root / run["boundary_file"])
                    metrics = validate_run_result(
                        boundary_rows_, entry_by_key[key], run, endpoint, suite
                    )
                    latency_ticks.extend(metrics.pop("latency_ticks"))
                    toggles = metrics.pop("toggles")
                    totals.update(metrics)
                    totals.update({f"toggle_{name}": value for name, value in toggles.items()})
                    per_run.append({"endpoint": endpoint, "suite": suite,
                                    "name": run["name"], **metrics, **{
                                        f"toggle_{name}": value for name, value in toggles.items()
                                    }})
                delivered = totals["delivered"]
                aggregates[f"{endpoint}:{suite}"] = {
                    **dict(totals),
                    "mean_occurrence_to_retire_ticks": (
                        sum(latency_ticks) / len(latency_ticks) if latency_ticks else None
                    ),
                    "p50_occurrence_to_retire_ticks": percentile(latency_ticks, 0.50),
                    "p95_occurrence_to_retire_ticks": percentile(latency_ticks, 0.95),
                    "p99_occurrence_to_retire_ticks": percentile(latency_ticks, 0.99),
                    "max_occurrence_to_retire_ticks": max(latency_ticks) if latency_ticks else None,
                    "toggle_per_delivered": {
                        name: totals[f"toggle_{name}"] / delivered if delivered else None
                        for name in ("data", "control", "clock")
                    },
                }
        for suite in ("full50", "capacity22"):
            parallel = aggregates[f"parallel_r1_full:{suite}"]
            ddr = aggregates[f"ddr_r1_full:{suite}"]
            if parallel["accepted"] != ddr["accepted"]:
                raise ContractError(f"{suite}: endpoints accepted different cohorts")
        document = {
            "schema_version": 1,
            "status": "PASS",
            "contract": ENDPOINT_CONTRACT,
            "qualification": "COMMON_WORKLOAD_ENDPOINT_EVALUATION",
            "provenance": {
                "endpoint_commit": endpoint_commit,
                "endpoint_manifest_path": endpoint_manifest_path,
                "endpoint_manifest_sha256": manifest_sha,
                "boundary_index_sha256": boundary_index_sha,
                "driver_sha256": sha256(driver),
                "compile_log_sha256": provenance["compile_log_sha256"],
                "binary_sha256": provenance["binary_sha256"],
                "simulator": provenance["simulator"],
            },
            "invariants": {
                "same_accepted_cohort": True,
                "standard_ready_valid_each_posedge": True,
                "continuous_valid_back_to_back": True,
                "phase_related_synchronous_only": True,
                "same_consumer_observation_boundary": True,
                "address_only_dut": True,
                "ids_reconstructed_in_dut": False,
                "loss_duplicate_phantom_order": "PASS",
                "reset": "PASS",
            },
            "aggregates": aggregates,
            "runs": per_run,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.parent / f".{output.name}.{os.getpid()}.tmp"
    if temporary_output.exists():
        raise ContractError("evaluation temporary output collision")
    write_json(temporary_output, document)
    os.replace(temporary_output, output)
    fsync_directory(output.parent)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--common-repo", type=Path,
                         default=Path("/home/chickgoose/projects/a1"))
    prepare.add_argument("--output", type=Path, required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--boundary-root", type=Path, required=True)
    evaluate.add_argument("--endpoint-repo", type=Path,
                          default=Path("/home/chickgoose/projects/a7"))
    evaluate.add_argument("--endpoint-commit", required=True)
    evaluate.add_argument("--endpoint-manifest-path", default="A5_BUILTIN_PINNED_A7_W5")
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            document = prepare_boundary(args.common_repo, args.output)
            print("A5_W5_BOUNDARY_READY_NOT_ENDPOINT_PASS "
                  f"full50={document['suites']['full50']['run_count']} "
                  f"capacity22={document['suites']['capacity22']['run_count']} "
                  f"output={args.output}")
            return 0
        evaluate_endpoint(
            args.boundary_root, args.endpoint_repo, args.endpoint_commit,
            args.endpoint_manifest_path, args.output,
        )
        print(f"A5_W5_ENDPOINT_EVALUATION_PASS output={args.output}")
        return 0
    except ContractError as exc:
        print(f"A5_W5_FAIL_CLOSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
