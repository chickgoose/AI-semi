#!/usr/bin/env python3
"""Run the pinned A2/A3/A4 scheduler plus actual-P6 digital replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT / "tests/a23_full_p6_replay"
TB = PACKAGE / "tb/a23_full_p6_replay_tb.sv"
PINS = PACKAGE / "pins.json"
GENERATOR = PROJECT / "benchmarks/clean_slate_aer/generate_trace.py"
PREPARER = PROJECT / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
OFFICIAL = PROJECT / "scripts/common_suite_official.py"
FULL_MANIFEST = PROJECT / "tests/common_suite_receipt/fixtures/manifest.neutrality-n16.json"
CAPACITY_MANIFEST = PROJECT / "tests/common_suite_receipt/fixtures/manifest.multilane-n16.json"
RUN_ALL = PACKAGE / "run_all.sh"
DEFAULT_VERILATOR = Path("/tmp/a7-toolchain/usr/bin/verilator")

sys.path.insert(0, str(PROJECT / "scripts"))
import common_suite_official as official  # noqa: E402


OWNERS = {
    "a2": {
        "define": "A23_OWNER_A2",
        "top": "rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6_top.sv",
        "owner": "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv",
        "filelist": "rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6.f",
        "wrapper": "tests/a23_full_p6_replay/rtl/a23_a2_p6_observer_wrapper.sv",
    },
    "a3": {
        "define": "A23_OWNER_A3",
        "top": "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6_top.sv",
        "owner": "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv",
        "filelist": "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6.f",
        "wrapper": "tests/a23_full_p6_replay/rtl/a23_a3_p6_observer_wrapper.sv",
    },
    "a4": {
        "define": "A23_OWNER_A4",
        "top": "rtl/candidates/a4_paired_cortical_column_k2_p6/a4_paired_cortical_column_k2_p6_top.sv",
        "owner": "rtl/candidates/a4_paired_cortical_column_k2/a4_paired_cortical_column_k2.sv",
        "filelist": "rtl/candidates/a4_paired_cortical_column_k2_p6/a4_paired_cortical_column_k2_p6.f",
        "wrapper": "tests/a23_full_p6_replay/rtl/a23_a4_p6_observer_wrapper.sv",
    },
}

EXPECTED_ACCEPT_RETIRE_CYCLES = {"a2": 3, "a3": 2, "a4": 2}
EXPECTED_FULL50_FIXED_RETIRE = {"a2": 103940, "a3": 93548, "a4": 102099}
EXPECTED_FULL50_COMMON = {
    "a2": {"accepted": 104046, "source_overrun": 2370,
           "max_occurrence_to_accept": 23},
    "a3": {"accepted": 93645, "source_overrun": 12771,
           "max_occurrence_to_accept": 265},
    "a4": {"accepted": 102171, "source_overrun": 4245,
           "max_occurrence_to_accept": 23},
}

MUTATIONS = {
    "drop": {"marker": "A23_REPLAY_DROP_FAIL", "trace": "core_simultaneous_identity"},
    "duplicate": {"marker": "A23_REPLAY_DUP_FAIL", "trace": "core_simultaneous_identity"},
    "swap": {
        "marker": "A23_REPLAY_SWAP_FAIL", "trace": "core_simultaneous_identity",
        "define": "A7_P6_MUTATE_SWAP_PAIR",
    },
    "microstep": {
        "marker": "A23_REPLAY_MICROSTEP_FAIL", "trace": "core_simultaneous_identity",
        "define": "A7_P6_MUTATE_PARTIAL_PAIR_COMMIT",
    },
    "reset": {
        "marker": "A23_REPLAY_RESET_FAIL", "trace": "basic_reset_drain",
        "define": "A7_P6_MUTATE_RESET_PHANTOM", "reset": True,
    },
}


class ReplayError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path, log: Path, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command, cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if expect_success and process.returncode:
        raise ReplayError(
            f"command failed exit={process.returncode}: {' '.join(command)}\n{process.stdout[-4000:]}"
        )
    return process


def load_pins(verilator: Path) -> dict[str, Any]:
    try:
        pins = json.loads(PINS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReplayError(f"cannot load pins: {error}") from error
    if pins.get("schema") != "a23_full_p6_replay_pins_v1":
        raise ReplayError("pin schema mismatch")
    for relative, expected in pins.get("files", {}).items():
        path = PROJECT / relative
        if not path.is_file() or sha256(path) != expected:
            raise ReplayError(f"file pin mismatch: {relative}")
    tool_pins = pins.get("tools", {})
    tool_key = str(verilator.resolve())
    if tool_key not in tool_pins or sha256(verilator.resolve()) != tool_pins[tool_key]:
        raise ReplayError(f"Verilator pin mismatch: {tool_key}")
    companion = verilator.resolve().with_name("verilator_bin")
    companion_key = str(companion)
    if companion_key not in tool_pins or sha256(companion) != tool_pins[companion_key]:
        raise ReplayError(f"Verilator binary pin mismatch: {companion_key}")
    return pins


def filelist_sources(owner: str) -> list[Path]:
    filelist = PROJECT / OWNERS[owner]["filelist"]
    sources = []
    for line in filelist.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(PROJECT / stripped)
    if not sources or any(not path.is_file() for path in sources):
        raise ReplayError(f"invalid actual RTL file list for {owner}")
    return sources


def verify_required_pin_coverage(pins: dict[str, Any]) -> None:
    required = {
        str(path.relative_to(PROJECT))
        for owner in OWNERS
        for path in filelist_sources(owner)
    }
    for config in OWNERS.values():
        required.update((config["owner"], config["top"], config["filelist"], config["wrapper"]))
    required.update({
        str(TB.relative_to(PROJECT)), str(Path(__file__).resolve().relative_to(PROJECT)),
        str(RUN_ALL.relative_to(PROJECT)),
        str(GENERATOR.relative_to(PROJECT)), str(PREPARER.relative_to(PROJECT)),
        str(OFFICIAL.relative_to(PROJECT)), str(FULL_MANIFEST.relative_to(PROJECT)),
        str(CAPACITY_MANIFEST.relative_to(PROJECT)),
    })
    missing = sorted(required - set(pins["files"]))
    if missing:
        raise ReplayError(f"required owner/P6/top/TB/runner pins missing: {missing}")

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(PINS.relative_to(PROJECT))],
        cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if tracked.returncode:
        raise ReplayError("pins.json must belong to the immutable package commit")
    changed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--",
         str(PACKAGE.relative_to(PROJECT))],
        cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if changed:
        raise ReplayError(
            "replay package must be byte-clean against HEAD before evidence generation"
        )


def prepare_traces(work: Path) -> dict[str, Path]:
    traces = work / "generator-v4"
    run(
        [sys.executable, str(GENERATOR), "--manifest", str(FULL_MANIFEST),
         "--output-dir", str(traces)],
        cwd=PROJECT, log=work / "logs/generator-v4.log",
    )
    prepared: dict[str, Path] = {}
    for name in official.FULL50:
        event_path = traces / f"{name}.events.jsonl"
        manifest_path = traces / f"{name}.manifest.json"
        if sha256(event_path) != official.TRACE_SHA256[name]:
            raise ReplayError(f"generator-v4 SHA mismatch: {name}")
        output = work / "prepared" / f"{name}.trace"
        run(
            [sys.executable, str(PREPARER), "--trace", str(event_path),
             "--run-manifest", str(manifest_path), "--output", str(output),
             "--addr-width", "4"],
            cwd=PROJECT, log=work / f"logs/prepare-{name}.log",
        )
        prepared[name] = output
    return prepared


def mutated_sources(work: Path, owner: str, mutation: str) -> tuple[list[Path], dict[str, str]]:
    sources = filelist_sources(owner)
    if mutation not in {"drop", "duplicate"}:
        return sources, {}
    tx = PROJECT / "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv"
    text = tx.read_text(encoding="utf-8")
    if mutation == "drop":
        anchors = {
            "frame_word_q[9]   <= (input_count_i == 2'd2);":
                "frame_word_q[9]   <= 1'b0; // A23 real RTL drop mutation",
            "frame_word_q[3:0] <= (input_count_i == 2'd2) ?\n                             input_addr1_i : 4'd0;":
                "frame_word_q[3:0] <= 4'd0; // A23 real RTL drop mutation",
        }
    else:
        anchors = {
            "frame_word_q[3:0] <= (input_count_i == 2'd2) ?\n                             input_addr1_i : 4'd0;":
                "frame_word_q[3:0] <= (input_count_i == 2'd2) ?\n                             input_addr0_i : 4'd0; // A23 real RTL duplicate mutation",
        }
    for old, new in anchors.items():
        if text.count(old) != 1:
            raise ReplayError(f"{mutation}: actual RTL mutation anchor count is not one")
        text = text.replace(old, new)
    destination = work / "mutated-rtl" / mutation / tx.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    replaced = [destination if path.resolve() == tx.resolve() else path for path in sources]
    return replaced, {
        "base_path": str(tx.relative_to(PROJECT)),
        "base_sha256": sha256(tx),
        "mutated_path": str(destination.relative_to(work)),
        "mutated_sha256": sha256(destination),
    }


def compile_simulator(
    work: Path, verilator: Path, owner: str, mutation: str = "none"
) -> tuple[Path, dict[str, str]]:
    sources, mutation_identity = mutated_sources(work, owner, mutation)
    build = work / "build" / owner / mutation
    build.mkdir(parents=True, exist_ok=False)
    binary = build / "sim"
    command = [
        str(verilator), "--binary", "--timing", "--assert", "-Wall",
        "-Wno-fatal", "-Wno-BLKSEQ", "-Wno-WIDTHEXPAND",
        "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL", "-Wno-SYNCASYNCNET",
        f"-D{OWNERS[owner]['define']}", "--top-module", "a23_full_p6_replay_tb",
        "--Mdir", str(build), "-o", "sim",
    ]
    mutation_define = MUTATIONS.get(mutation, {}).get("define")
    if mutation_define:
        command.append(f"-D{mutation_define}")
    command.extend(str(path) for path in sources)
    command.extend((str(PROJECT / OWNERS[owner]["wrapper"]), str(TB)))
    run(command, cwd=PROJECT, log=work / f"logs/build-{owner}-{mutation}.log")
    if not binary.is_file():
        raise ReplayError(f"Verilator did not create simulator for {owner}/{mutation}")
    return binary, mutation_identity


def parse_single_csv(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        expected = {
            "owner", "trace", "generated", "source_overrun", "accepted",
            "retired", "fixed_window_retired", "fixed_window_cycles",
            "observation_cycles", "reset_test",
        }
        if set(reader.fieldnames or ()) != expected:
            raise ReplayError(f"summary artifact schema mismatch: {path}")
        rows = list(reader)
    if len(rows) != 1:
        raise ReplayError(f"expected one summary row: {path}")
    return rows[0]


def latency_summary(values: Iterable[int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    percentile = lambda fraction: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {
        "count": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(0.50), "p95": percentile(0.95),
        "p99": percentile(0.99), "max": ordered[-1],
    }


def parse_run(
    summary_path: Path, event_path: Path, expected_owner: str,
    expected_trace: str, expected_reset: bool,
) -> dict[str, Any]:
    summary = parse_single_csv(summary_path)
    with event_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required_event_fields = {
            "owner", "trace", "tb_only_event_id", "logical_source",
            "occurrence_cycle", "accept_cycle", "retire_cycle",
            "deadline_cycle", "event_state",
        }
        if set(reader.fieldnames or ()) != required_event_fields:
            raise ReplayError(f"event artifact schema mismatch: {event_path}")
        events = list(reader)
    if summary.get("owner") != expected_owner or summary.get("trace") != expected_trace:
        raise ReplayError(f"summary owner/trace provenance mismatch: {summary_path}")
    occurrence_accept: list[int] = []
    accept_retire: list[int] = []
    retired_states = 0
    overrun_states = 0
    for expected_id, event in enumerate(events):
        if event["owner"] != expected_owner or event["trace"] != expected_trace:
            raise ReplayError(f"event owner/trace provenance mismatch: {event_path}")
        if int(event["tb_only_event_id"]) != expected_id:
            raise ReplayError(f"event IDs are not exact and contiguous: {event_path}")
        source = int(event["logical_source"])
        occurrence = int(event["occurrence_cycle"])
        deadline = int(event["deadline_cycle"])
        if not 0 <= source < 16 or occurrence < 0 or deadline < occurrence:
            raise ReplayError(f"invalid event provenance fields: {event_path}")
        if event["event_state"] == "retired":
            accept = int(event["accept_cycle"])
            retire = int(event["retire_cycle"])
            if not occurrence <= accept <= retire:
                raise ReplayError(f"negative or inverted latency in {event_path}")
            occurrence_accept.append(accept - occurrence)
            accept_retire.append(retire - accept)
            retired_states += 1
        elif event["event_state"] == "source_overrun":
            if int(event["accept_cycle"]) != -1 or int(event["retire_cycle"]) != -1:
                raise ReplayError(f"overrun carries accept/retire timing: {event_path}")
            overrun_states += 1
        else:
            raise ReplayError(f"nonterminal event in passing artifact: {event_path}")
    numeric = {
        key: int(summary[key]) for key in (
            "generated", "source_overrun", "accepted", "retired",
            "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
            "reset_test",
        )
    }
    if numeric["generated"] != len(events):
        raise ReplayError(f"summary/event cardinality mismatch: {summary_path}")
    if numeric["source_overrun"] != overrun_states:
        raise ReplayError(f"summary/event overrun mismatch: {summary_path}")
    if numeric["accepted"] != retired_states or numeric["retired"] != retired_states:
        raise ReplayError(f"summary/event accepted/retired mismatch: {summary_path}")
    if numeric["generated"] != numeric["source_overrun"] + numeric["accepted"]:
        raise ReplayError(f"summary occurrence conservation mismatch: {summary_path}")
    if numeric["fixed_window_retired"] > numeric["retired"]:
        raise ReplayError(f"fixed-window retire exceeds total: {summary_path}")
    if numeric["fixed_window_cycles"] < 0 or numeric["observation_cycles"] <= 0:
        raise ReplayError(f"invalid summary windows: {summary_path}")
    if numeric["reset_test"] != int(expected_reset):
        raise ReplayError(f"summary reset provenance mismatch: {summary_path}")
    return {
        **numeric,
        "occurrence_to_accept": latency_summary(occurrence_accept),
        "accept_to_retire": latency_summary(accept_retire),
        "fixed_window_events_per_cycle": round(
            numeric["fixed_window_retired"] / max(1, numeric["fixed_window_cycles"]), 9
        ),
        "summary_sha256": sha256(summary_path), "events_sha256": sha256(event_path),
        "_occurrence_accept": occurrence_accept, "_accept_retire": accept_retire,
    }


def execute_case(
    work: Path, binary: Path, owner: str, name: str, trace: Path | None,
    mutation: str = "none", reset: bool = False, expect_success: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    case = work / "artifacts" / owner / mutation / name
    case.mkdir(parents=True, exist_ok=True)
    event_path = case / "events.csv"
    summary_path = case / "summary.csv"
    command = [
        str(binary), f"+OWNER={owner}", f"+TRACE_NAME={name}",
        f"+EVENT_OUTPUT={event_path}", f"+SUMMARY_OUTPUT={summary_path}",
        f"+MUTATION={mutation}",
    ]
    if trace is not None:
        command.append(f"+TRACE_FILE={trace}")
    if reset:
        command.append("+RESET_TEST")
    process = run(
        command, cwd=PROJECT, log=case / "simulation.log",
        expect_success=expect_success,
    )
    if not expect_success:
        return process, None
    if "A23_REPLAY_ALL_PASS" not in process.stdout:
        raise ReplayError(f"missing pass sentinel: {owner}/{name}")
    artifact = parse_run(summary_path, event_path, owner, name, reset)
    expected_latency = EXPECTED_ACCEPT_RETIRE_CYCLES[owner]
    if any(value != expected_latency for value in artifact["_accept_retire"]):
        raise ReplayError(
            f"{owner}/{name}: accept-to-retire is not the fixed "
            f"{expected_latency}-cycle actual-P6 window"
        )
    return process, artifact


def aggregate(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = list(runs)
    occurrence = [value for item in selected for value in item["_occurrence_accept"]]
    internal = [value for item in selected for value in item["_accept_retire"]]
    totals = {
        key: sum(item[key] for item in selected)
        for key in ("generated", "source_overrun", "accepted", "retired",
                    "fixed_window_retired", "fixed_window_cycles")
    }
    return {
        "run_count": len(selected), "totals": totals,
        "occurrence_to_accept": latency_summary(occurrence),
        "accept_to_retire": latency_summary(internal),
        "fixed_window_events_per_cycle": round(
            totals["fixed_window_retired"] / max(1, totals["fixed_window_cycles"]), 9
        ),
    }


def assert_aggregate(name: str, aggregate_result: dict[str, Any], expected_runs: int) -> None:
    if aggregate_result["run_count"] != expected_runs:
        raise ReplayError(f"{name}: aggregate run count mismatch")
    totals = aggregate_result["totals"]
    if totals["generated"] != totals["source_overrun"] + totals["accepted"]:
        raise ReplayError(f"{name}: aggregate occurrence conservation mismatch")
    if totals["accepted"] != totals["retired"]:
        raise ReplayError(f"{name}: aggregate accept/retire conservation mismatch")


def verify_capacity_subset(prepared: dict[str, Path]) -> dict[str, str]:
    document = json.loads(CAPACITY_MANIFEST.read_text(encoding="utf-8"))
    names = tuple(run["name"] for run in document.get("runs", []))
    if names != official.CAPACITY22:
        raise ReplayError("capacity22 manifest is not the exact frozen ordered subset")
    if len(names) != len(set(names)) or not set(names).issubset(official.FULL50):
        raise ReplayError("capacity22 contains duplicates or a non-full50 member")
    references = {name: official.TRACE_SHA256[name] for name in names}
    for name, expected_sha in references.items():
        generated = prepared[name].parent.parent / "generator-v4" / f"{name}.events.jsonl"
        if sha256(generated) != expected_sha:
            raise ReplayError(f"capacity22 exact trace SHA mismatch: {name}")
    return references


def strip_private(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verilator", type=Path, default=DEFAULT_VERILATOR)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    output = args.output.resolve()
    if work.exists() or output.exists():
        print("error: work-dir and output must not already exist", file=sys.stderr)
        return 2
    try:
        pins = load_pins(args.verilator)
        verify_required_pin_coverage(pins)
        work.mkdir(parents=True)
        prepared = prepare_traces(work)
        capacity_references = verify_capacity_subset(prepared)
        owner_results: dict[str, Any] = {}
        mutation_results: list[dict[str, Any]] = []

        for owner in OWNERS:
            print(f"A23_REPLAY_OWNER_START owner={owner}", flush=True)
            baseline, _ = compile_simulator(work, args.verilator, owner)
            runs: dict[str, dict[str, Any]] = {}
            for index, name in enumerate(official.FULL50, start=1):
                _, result = execute_case(work, baseline, owner, name, prepared[name])
                assert result is not None
                runs[name] = result
                if (index % 10) == 0:
                    print(f"A23_REPLAY_PROGRESS owner={owner} full50={index}/50", flush=True)
            _, reset_result = execute_case(
                work, baseline, owner, "basic_reset_drain", None, reset=True,
            )
            assert reset_result is not None

            full_aggregate = aggregate(runs.values())
            capacity_runs = [runs[name] for name in official.CAPACITY22]
            capacity_aggregate = aggregate(capacity_runs)
            assert_aggregate(f"{owner}/full50", full_aggregate, 50)
            assert_aggregate(f"{owner}/capacity22", capacity_aggregate, 22)
            if (full_aggregate["totals"]["fixed_window_retired"] !=
                    EXPECTED_FULL50_FIXED_RETIRE[owner]):
                raise ReplayError(
                    f"{owner}/full50: fixed-window retire total differs from "
                    "the independently audited actual-P6 replay"
                )
            common_expected = EXPECTED_FULL50_COMMON[owner]
            if (full_aggregate["totals"]["accepted"] !=
                    common_expected["accepted"] or
                    full_aggregate["totals"]["source_overrun"] !=
                    common_expected["source_overrun"] or
                    full_aggregate["occurrence_to_accept"]["max"] !=
                    common_expected["max_occurrence_to_accept"]):
                raise ReplayError(
                    f"{owner}/full50: occurrence acceptance totals or maximum "
                    "latency differ from owner-boundary common replay"
                )
            owner_results[owner] = {
                "full50": {
                    "execution_count": 50, "aggregate": full_aggregate,
                    "runs": {
                        name: {"trace_sha256": official.TRACE_SHA256[name],
                               "prepared_trace_sha256": sha256(prepared[name]),
                               **strip_private(runs[name])}
                        for name in official.FULL50
                    },
                },
                "capacity22": {
                    "execution_count": 0,
                    "derived_from_full50_execution": True,
                    "independent_additional_sample_count": 0,
                    "run_names": list(official.CAPACITY22),
                    "run_trace_sha256": capacity_references,
                    "aggregate": capacity_aggregate,
                },
                "reset": strip_private(reset_result),
            }

            for mutation, contract in MUTATIONS.items():
                mutant, mutation_identity = compile_simulator(
                    work, args.verilator, owner, mutation,
                )
                trace = None if contract.get("reset") else prepared[contract["trace"]]
                process, _ = execute_case(
                    work, mutant, owner, contract["trace"], trace,
                    mutation=mutation, reset=bool(contract.get("reset")),
                    expect_success=False,
                )
                marker = contract["marker"]
                killed = process.returncode != 0 and marker in process.stdout
                if not killed:
                    raise ReplayError(
                        f"mutation survived or wrong diagnostic: {owner}/{mutation} "
                        f"exit={process.returncode} expected={marker}"
                    )
                mutation_results.append({
                    "owner": owner, "mutation": mutation,
                    "actual_rtl": True, "killed": True,
                    "first_required_diagnostic": marker,
                    "exit_code": process.returncode,
                    "compile_define": contract.get("define"),
                    "source_mutation": mutation_identity or None,
                })
                print(f"A23_REPLAY_MUTATION_KILLED owner={owner} mutation={mutation}", flush=True)

        package_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
        result = {
            "schema": "a23_full_p6_replay_result_v1",
            "status": "PASS",
            "boundary": "actual_scheduler_plus_actual_phase_related_always_ready_P6",
            "ordered_link_adapter": False,
            "observation_wrapper_state_bits": 0,
            "acceptance_observation": "actual_atomic_bundle_commit_count_and_ordered_addresses",
            "retirement_scoreboard": "actual_P6_retire_valid_and_addresses_in_global_accept_order",
            "cycle_semantics": "common_TB_one_entry_occurrence_latch_before_indexed_accept_edge_nonblocking_clear",
            "generator": {
                "version": official.GENERATOR_VERSION,
                "source_commit": official.SOURCE_COMMIT,
                "full50_manifest_sha256": official.SUITES["full50"]["manifest_sha256"],
                "capacity22_manifest_sha256": official.SUITES["capacity22"]["manifest_sha256"],
                "capacity22_is_full50_subset_view": True,
            },
            "execution_accounting": {
                "owners": 3, "full50_actual_executions": 150,
                "capacity22_subset_references": 66,
                "capacity22_additional_executions": 0,
                "reset_actual_executions": 3,
                "mutation_actual_RTL_executions": 15,
            },
            "owners": owner_results,
            "mutations": mutation_results,
            "provenance": {
                "publication_model": "immutable_two_commit_package_then_result",
                "package_commit": package_commit,
                "pins_path": str(PINS.relative_to(PROJECT)),
                "pins_sha256": sha256(PINS),
                "verified_files": pins["files"], "verified_tools": pins["tools"],
            },
            "qualification": {
                "digital_RTL": "GO", "physical": "HOLD", "CDC_RDC": "HOLD",
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"A23_FULL_P6_REPLAY_PASS owners=3 full50=150 capacity22_subset=66 "
            f"capacity22_additional=0 reset=3 mutations=15 output={output}",
            flush=True,
        )
        return 0
    except (ReplayError, OSError, subprocess.SubprocessError) as error:
        print(f"A23_FULL_P6_REPLAY_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
