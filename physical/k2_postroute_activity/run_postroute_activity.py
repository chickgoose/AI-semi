#!/usr/bin/env python3
"""Produce hash-bound gate-level activity from one frozen common trace.

This launches only Xcelium.  It consumes an already completed Innovus
post-route netlist/SDF pair; it never launches or modifies a physical run.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Sequence


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = HERE / "candidates.json"
TB = ROOT / "tb/clean/aer_clean_tb.sv"
BENCH_IF = ROOT / "tb/clean/aer_bench_if.sv"
ASSERTIONS = ROOT / "tb/clean/aer_clean_assertions.sv"
BINDING = HERE / "postroute_binding.sv"
PREPARE = ROOT / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
REBASE = ROOT / "physical/k2_w3_common_activity/rebase_vcd.py"
VCD_TO_SAIF = ROOT / "physical/k2_w3_common_activity/vcd_to_saif.py"
FROZEN_TB_SHA256 = "27d9437a5179b0cb909d02edee1ac2f82ea6d20aeab9cfb64997b458192102a2"
SHA256 = re.compile(r"[0-9a-f]{64}")
SIMPLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
BAD_TOOL_LOG = re.compile(
    r"(?mi)^\s*(?:ERROR|FATAL)\s*[:\[]|\*E,|\*F,|"
    r"seg(?:mentation)?\s+fault|simulation\s+failed"
)
BAD_SDF_LOG = re.compile(
    r"(?mi)\*E,SDF|SDF[^\n]*(?:error|failed|pathdelays[^\n]*0\s+annotated)"
)


class ActivityError(RuntimeError):
    pass


def canonical(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_read(path: Path, *, allow_executable_symlink: bool = False) -> tuple[Path, bytes]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if allow_executable_symlink:
        absolute = absolute.resolve(strict=True)
    elif absolute.is_symlink():
        raise ActivityError(f"symlink input is forbidden: {absolute}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError as error:
        raise ActivityError(f"cannot open regular input {absolute}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ActivityError(f"input is not a regular file: {absolute}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after):
            raise ActivityError(f"input changed while read: {absolute}")
        payload = b"".join(chunks)
        if not payload:
            raise ActivityError(f"input is empty: {absolute}")
        return absolute, payload
    finally:
        os.close(descriptor)


def identity(path: Path, *, executable: bool = False) -> dict[str, Any]:
    resolved, payload = stable_read(path, allow_executable_symlink=executable)
    return {"path": str(resolved), "sha256": digest(payload), "size_bytes": len(payload)}


def load_registry() -> tuple[dict[str, Any], dict[str, Any]]:
    registry_payload = REGISTRY.read_bytes()
    registry = json.loads(registry_payload)
    expected = {"schema", "activity_scope", "frozen_trace", "candidates", "periods"}
    if set(registry) != expected or registry["schema"] != \
            "k2_postroute_activity_candidates_v1":
        raise ActivityError("candidate registry schema/field mismatch")
    if registry["activity_scope"] != "aer_clean_tb.candidate.dut" or \
            list(registry["candidates"]) != [
                "fovea", "cluster2", "fovea_a7", "a2_p6", "a3_p6"]:
        raise ActivityError("candidate registry scope/order mismatch")
    frozen = registry["frozen_trace"]
    if set(frozen) != {"name", "trace_sha256", "run_manifest_sha256"} or \
            frozen["name"] != "mixed_phase_always_ready_identity" or \
            not SHA256.fullmatch(str(frozen["trace_sha256"])) or \
            not SHA256.fullmatch(str(frozen["run_manifest_sha256"])):
        raise ActivityError("frozen trace registry row malformed")
    expected_periods = {
        "5.0": {"period_ps": 5000, "ref_half_period_ps": 2500,
                "sample_first_rise_ps": 3750},
        "5.7": {"period_ps": 5700, "ref_half_period_ps": 2850,
                "sample_first_rise_ps": 4275},
        "6.5": {"period_ps": 6500, "ref_half_period_ps": 3250,
                "sample_first_rise_ps": 4875},
    }
    if registry["periods"] != expected_periods:
        raise ActivityError("physical period registry mismatch")
    expected_lanes = {"fovea": 1, "cluster2": 8, "fovea_a7": 2,
                      "a2_p6": 2, "a3_p6": 2}
    for name, lanes in expected_lanes.items():
        row = registry["candidates"][name]
        if set(row) != {"top", "kind_define", "retire_lanes"} or \
                not SIMPLE_NAME.fullmatch(str(row["top"])) or \
                not SIMPLE_NAME.fullmatch(str(row["kind_define"])) or \
                row["retire_lanes"] != lanes:
            raise ActivityError(f"candidate registry row malformed: {name}")
    return registry, {"path": str(REGISTRY), "sha256": digest(registry_payload)}


def validate_frozen_input(path: Path, expected: str, label: str) -> dict[str, Any]:
    result = identity(path)
    if not SHA256.fullmatch(expected) or result["sha256"] != expected:
        raise ActivityError(f"frozen {label} SHA-256 mismatch")
    return result


def validate_netlist_sdf(netlist: Path, sdf: Path, top: str) -> tuple[
        dict[str, Any], dict[str, Any]]:
    netlist_path, netlist_payload = stable_read(netlist)
    sdf_path, sdf_payload = stable_read(sdf)
    netlist_id = {"path": str(netlist_path), "sha256": digest(netlist_payload),
                  "size_bytes": len(netlist_payload)}
    sdf_id = {"path": str(sdf_path), "sha256": digest(sdf_payload),
              "size_bytes": len(sdf_payload)}
    try:
        netlist_text = netlist_payload.decode("utf-8", errors="strict")
        sdf_text = sdf_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ActivityError("netlist/SDF is not UTF-8 text") from error
    modules = re.findall(rf"(?m)^\s*module\s+{re.escape(top)}\b", netlist_text)
    if len(modules) != 1:
        raise ActivityError("post-route netlist does not define the exact top once")
    designs = re.findall(r"\(DESIGN\s+\"([^\"]+)\"\)", sdf_text)
    if designs != [top] or sdf_text.count("(DELAYFILE") != 1:
        raise ActivityError("post-route SDF is not uniquely bound to the exact top")
    if not re.search(r"\(TIMESCALE\s+[^)]+\)", sdf_text):
        raise ActivityError("post-route SDF has no timescale")
    return netlist_id, sdf_id


def materialize_period_tb(frozen: bytes, period_ps: int,
                          half_period_ps: int) -> bytes:
    """Change only the frozen TB time unit and its sole clock delay."""
    try:
        text = frozen.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ActivityError("frozen common TB is not UTF-8") from error
    timescale = "`timescale 1ns/1ps"
    clock = "  always #5 clk = ~clk;"
    if text.count(timescale) != 1 or text.count(clock) != 1 or \
            period_ps != half_period_ps * 2:
        raise ActivityError("frozen TB period transform precondition failed")
    transformed = text.replace(timescale, "`timescale 1ps/1ps", 1).replace(
        clock, f"  always #{half_period_ps} clk = ~clk;", 1)
    if transformed.count("always #") != 1:
        raise ActivityError("frozen TB contains an unaccounted delay clock")
    return transformed.encode()


def run(command: list[str], cwd: Path, log: Path) -> None:
    with log.open("ab") as stream:
        stream.write(("COMMAND " + json.dumps(command) + "\n").encode())
        result = subprocess.run(command, cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise ActivityError(f"command exited {result.returncode}: {command[0]}")


def read_one_csv(path: Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1 or not rows[0]:
        raise ActivityError(f"CSV must contain exactly one data row: {path.name}")
    return rows[0]


def validate_functional_evidence(summary: Path, events: Path,
                                 ledger: Path) -> dict[str, int]:
    row = read_one_csv(summary)
    required = {"generated", "source_overrun", "accepted", "delivered", "errors",
                "measurement_delivered", "measurement_cycles"}
    if not required.issubset(row):
        raise ActivityError("common summary lacks required accounting fields")
    try:
        values = {key: int(row[key]) for key in required}
    except ValueError as error:
        raise ActivityError("common summary accounting is not integral") from error
    if (values["errors"] != 0 or values["accepted"] != values["delivered"] or
            values["generated"] != values["source_overrun"] + values["accepted"] or
            values["measurement_cycles"] <= 0):
        raise ActivityError("common summary conservation/error gate failed")
    with events.open(newline="") as stream:
        event_rows = list(csv.DictReader(stream))
    if len(event_rows) != values["generated"] or \
            sum(item.get("event_state") == "source_overrun" for item in event_rows) != \
            values["source_overrun"] or \
            sum(item.get("event_state") == "delivered" for item in event_rows) != \
            values["delivered"]:
        raise ActivityError("event CSV does not reproduce common summary accounting")
    with ledger.open(newline="") as stream:
        ledger_rows = list(csv.DictReader(stream, delimiter="\t"))
    expected_fields = ["ordinal", "sim_tick_1ps", "lane", "logical_source",
                       "logical_event"]
    if not ledger_rows or list(ledger_rows[0]) != expected_fields:
        raise ActivityError("retirement ledger field set is malformed or empty")
    if len(ledger_rows) != values["measurement_delivered"]:
        raise ActivityError("retirement ledger count differs from measured deliveries")
    previous_tick = -1
    for ordinal, item in enumerate(ledger_rows):
        try:
            observed = int(item["ordinal"])
            tick = int(item["sim_tick_1ps"])
            lane = int(item["lane"])
            source = int(item["logical_source"])
            event = int(item["logical_event"], 16)
        except ValueError as error:
            raise ActivityError("retirement ledger contains a noninteger field") from error
        if (observed != ordinal or tick < previous_tick or lane < 0 or
                not (0 <= source < 16) or event != source):
            raise ActivityError("retirement ledger ordering/address invariant failed")
        previous_tick = tick
    return values


def validate_vcd_and_saif(vcd: Path, saif: Path, scope: str) -> tuple[int, int]:
    text = vcd.read_text(errors="strict")
    scopes: list[str] = []
    exact = 0
    for line in text.splitlines():
        words = line.split()
        if words[:1] == ["$scope"] and len(words) >= 4:
            scopes.append(words[2])
            if ".".join(scopes) == scope:
                exact += 1
        elif words[:1] == ["$upscope"]:
            if not scopes:
                raise ActivityError("VCD scope stack underflow")
            scopes.pop()
        elif "$enddefinitions" in line:
            break
    if exact != 1:
        raise ActivityError("VCD does not contain exactly one declared activity scope")
    timestamps = [int(value) for value in re.findall(r"(?m)^#([0-9]+)$", text)]
    if not timestamps or min(timestamps) != 0 or max(timestamps) <= 0:
        raise ActivityError("VCD does not span a zero-based positive window")
    saif_text = saif.read_text(errors="strict")
    duration = re.findall(r"\(DURATION\s+([0-9]+)\)", saif_text)
    if duration != [str(max(timestamps))] or re.search(r"\(TX\s+[1-9][0-9]*\)", saif_text):
        raise ActivityError("SAIF duration mismatch or unknown-state residence is nonzero")
    return min(timestamps), max(timestamps)


def write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o444)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def snapshot(origin: dict[str, Any], destination: Path) -> dict[str, Any]:
    source_path, payload = stable_read(Path(origin["path"]))
    if digest(payload) != origin["sha256"] or len(payload) != origin["size_bytes"]:
        raise ActivityError(f"input changed before immutable snapshot: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_exclusive(destination, payload)
    copied = identity(destination)
    if copied["sha256"] != origin["sha256"]:
        raise ActivityError(f"immutable snapshot differs from input: {source_path}")
    return {"origin": origin, "execution_copy": copied}


def produce(args: argparse.Namespace) -> Path:
    registry, registry_id = load_registry()
    row = registry["candidates"][args.candidate]
    period = registry["periods"].get(args.period_ns)
    if period is None:
        raise ActivityError("period is not one of the exact physical profiles")
    producer_paths = (TB, BENCH_IF, ASSERTIONS, BINDING, PREPARE, REBASE,
                      VCD_TO_SAIF, Path(__file__))
    producer_origins = {str(path.relative_to(ROOT)): identity(path)
                        for path in producer_paths}
    if producer_origins[str(TB.relative_to(ROOT))]["sha256"] != FROZEN_TB_SHA256:
        raise ActivityError("frozen common TB SHA-256 mismatch")
    trace_id = validate_frozen_input(
        args.trace, registry["frozen_trace"]["trace_sha256"], "trace")
    manifest_id = validate_frozen_input(
        args.run_manifest, registry["frozen_trace"]["run_manifest_sha256"],
        "run manifest")
    netlist_id, sdf_id = validate_netlist_sdf(args.netlist, args.sdf, row["top"])
    model_ids = [identity(path) for path in args.model]
    if len({item["sha256"] for item in model_ids}) != len(model_ids):
        raise ActivityError("duplicate vendor timing-model bytes")
    xrun_id = identity(args.xrun, executable=True)
    if not os.access(xrun_id["path"], os.X_OK):
        raise ActivityError("resolved Xcelium tool is not executable")
    output = Path(os.path.abspath(os.fspath(args.output)))
    if output.exists() or output.is_symlink():
        raise ActivityError(f"refusing existing output: {output}")
    output.mkdir(parents=True)
    log = output / "tool.log"
    log.write_bytes(b"")
    inputs = output / "inputs"
    trace_snapshot = snapshot(trace_id, inputs / Path(trace_id["path"]).name)
    manifest_snapshot = snapshot(manifest_id, inputs / Path(manifest_id["path"]).name)
    netlist_snapshot = snapshot(netlist_id, inputs / f"{row['top']}.postroute.v")
    sdf_snapshot = snapshot(sdf_id, inputs / f"{row['top']}.postroute.sdf")
    model_snapshots = [snapshot(model, inputs / "models" /
                                f"{index:02d}-{Path(model['path']).name}")
                       for index, model in enumerate(model_ids)]
    source_snapshots = {}
    for relative, origin in producer_origins.items():
        destination = inputs / "producer" / relative
        source_snapshots[relative] = snapshot(origin, destination)
    frozen_tb_source = source_snapshots[str(TB.relative_to(ROOT))]
    frozen_tb_payload = stable_read(Path(
        frozen_tb_source["execution_copy"]["path"]))[1]
    period_tb = inputs / "producer" / "period_aer_clean_tb.sv"
    write_exclusive(period_tb, materialize_period_tb(
        frozen_tb_payload, period["period_ps"], period["ref_half_period_ps"]))
    period_tb_id = identity(period_tb)
    bench_if = Path(source_snapshots[str(BENCH_IF.relative_to(ROOT))][
        "execution_copy"]["path"])
    assertions = Path(source_snapshots[str(ASSERTIONS.relative_to(ROOT))][
        "execution_copy"]["path"])
    binding = Path(source_snapshots[str(BINDING.relative_to(ROOT))][
        "execution_copy"]["path"])
    prepare = Path(source_snapshots[str(PREPARE.relative_to(ROOT))][
        "execution_copy"]["path"])
    rebase = Path(source_snapshots[str(REBASE.relative_to(ROOT))][
        "execution_copy"]["path"])
    vcd_to_saif = Path(source_snapshots[str(VCD_TO_SAIF.relative_to(ROOT))][
        "execution_copy"]["path"])
    # The registry was parsed before output creation; retain its exact bytes in
    # the execution evidence just like the other authorities.
    registry_snapshot = snapshot(
        {"path": str(REGISTRY.resolve()), "sha256": registry_id["sha256"],
         "size_bytes": REGISTRY.stat().st_size},
        inputs / "producer" / "physical/k2_postroute_activity/candidates.json")

    version = subprocess.run([xrun_id["path"], "-version"], cwd=output,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             check=False)
    (output / "xrun.version.txt").write_bytes(version.stdout)
    if version.returncode or not version.stdout.strip():
        raise ActivityError("Xcelium version query failed")

    prepared = output / "input.svtrace"
    run([sys.executable, str(prepare), "--trace",
         trace_snapshot["execution_copy"]["path"],
         "--run-manifest", manifest_snapshot["execution_copy"]["path"],
         "--output", str(prepared),
         "--addr-width", "16"], output, log)

    xrun_snapshot = f"k2_postroute_{args.candidate}"
    library = output / "xcelium.d"
    common = [xrun_id["path"], "-64bit", "-sv", "-timescale", "1ps/1ps",
              "-top", "aer_clean_tb", "-snapshot", xrun_snapshot,
              "-define", f"K2_POSTROUTE_DUT={row['top']}",
              "-define", row["kind_define"], "-define",
              f'K2_POSTROUTE_SDF="{sdf_snapshot["execution_copy"]["path"]}"',
              "-define", f"K2_POSTROUTE_PERIOD_PS={period['period_ps']}",
              "-define", f"K2_POSTROUTE_REF_HALF_PS={period['ref_half_period_ps']}",
              "-define", "K2_POSTROUTE_SAMPLE_FIRST_RISE_PS=" +
              str(period["sample_first_rise_ps"]),
              "-defparam", "aer_clean_tb.NUM_SOURCES=16",
              "-defparam", "aer_clean_tb.ADDR_WIDTH=16",
              "-defparam", f"aer_clean_tb.RETIRE_LANES={row['retire_lanes']}",
              "-defparam", "aer_clean_tb.FIFO_DEPTH=0"]
    run([*common, "-elaborate", "-access", "+r",
         "-xmlibdirname", str(library),
         *[item["execution_copy"]["path"] for item in model_snapshots],
         netlist_snapshot["execution_copy"]["path"], str(bench_if), str(binding),
         str(assertions), str(period_tb)], output, log)
    raw_vcd = output / "raw.vcd"
    window = output / "window.txt"
    summary = output / "summary.csv"
    events = output / "events.csv"
    ledger = output / "retire-ledger.tsv"
    run([xrun_id["path"], "-64bit", "-R", "-snapshot", xrun_snapshot,
         "-xmlibdirname", str(library), "+CLEAN_TEST=trace",
         "+TRACE_NAME=" + registry["frozen_trace"]["name"],
         "+TRACE_FILE=" + str(prepared), "+CANDIDATE=" + args.candidate,
         "+METRICS=" + str(summary), "+EVENT_METRICS=" + str(events),
         "+ACTIVITY_VCD=" + str(raw_vcd), "+ACTIVITY_WINDOW=" + str(window),
         "+RETIRE_LEDGER=" + str(ledger)], output, log)
    tool_text = log.read_text(errors="replace")
    sdf_warnings = re.findall(r"(?m)^xmelab: \*W,(SDF[A-Z0-9]+):", tool_text)
    annotation = re.findall(
        r"Annotation completed with (\d+) Errors and (\d+) Warnings", tool_text)
    pathdelays = re.findall(
        r"No\. of Pathdelays\s*=\s*(\d+).*?Annotated\s*=\s*100\.00%\s*\((\d+)/(\d+)\)",
        tool_text)
    tchecks = re.findall(
        r"No\. of Tchecks\s*=\s*(\d+).*?Annotated\s*=\s*0\.00%\s*\((\d+)/(\d+)\)",
        tool_text)
    if tool_text.count("K2_POSTROUTE_SDF_REQUESTED scope=aer_clean_tb.candidate.dut") != 1 or \
            tool_text.count("AER_CLEAN_TEST_PASS") != 1 or \
            BAD_TOOL_LOG.search(tool_text) or BAD_SDF_LOG.search(tool_text) or \
            len(annotation) != 1 or annotation[0][0] != "0" or \
            set(sdf_warnings) - {"SDFNET"} or \
            len(pathdelays) != 1 or int(pathdelays[0][0]) <= 0 or \
            pathdelays[0][0] != pathdelays[0][1] or \
            pathdelays[0][1] != pathdelays[0][2] or len(tchecks) != 1:
        raise ActivityError("Xcelium/SDF/common-TB completion log gate failed")
    counts = validate_functional_evidence(summary, events, ledger)

    rebased = output / f"postroute-{args.period_ns.replace('.', 'p')}ns.vcd"
    validation = output / "postroute.validation.txt"
    run([sys.executable, str(rebase), "--input", str(raw_vcd),
         "--window", str(window), "--summary", str(summary),
         "--output", str(rebased), "--sha-output", str(validation)], output, log)
    saif = output / f"postroute-{args.period_ns.replace('.', 'p')}ns.saif"
    run([sys.executable, str(vcd_to_saif), "--vcd", str(rebased),
         "--output", str(saif)], output, log)
    first, last = validate_vcd_and_saif(
        rebased, saif, registry["activity_scope"])
    if first != 0 or last != (counts["measurement_cycles"] + 1) * period["period_ps"]:
        raise ActivityError("target-period activity window differs from common cycle ledger")
    final_xrun_id = identity(Path(xrun_id["path"]), executable=True)
    if final_xrun_id != xrun_id:
        raise ActivityError("Xcelium executable changed during activity production")

    artifacts = {path.name: identity(path) for path in
                 (prepared, summary, events, ledger, window, raw_vcd, rebased,
                  validation, saif, log,
                  output / "xrun.version.txt")}
    receipt = {
        "schema": "k2_postroute_gate_activity_receipt_v1",
        "status": "PASS",
        "candidate": args.candidate,
        "top": row["top"],
        "boundary": "exact_innovus_postroute_netlist_with_sdf",
        "activity_scope": registry["activity_scope"],
        "target_period_ns": args.period_ns,
        "target_period_ps": period["period_ps"],
        "clock_materialization": {
            "kind": "frozen_tb_exact_timescale_and_single_clock_delay_transform",
            "source": frozen_tb_source,
            "materialized": period_tb_id,
            "ref_half_period_ps": period["ref_half_period_ps"],
            "sample_first_rise_ps": period["sample_first_rise_ps"]},
        "window": {"start_tick_1ps": first, "end_tick_1ps": last,
                   "start_ns": "0", "end_ns": str(Decimal(last) / 1000),
                   "measurement_cycles": counts["measurement_cycles"],
                   "activity_cycles": counts["measurement_cycles"] + 1},
        "accounting": counts,
        "retirement_ledger": {"records": counts["measurement_delivered"],
                              **artifacts[ledger.name]},
        "frozen_trace": {"name": registry["frozen_trace"]["name"],
                         "trace": trace_snapshot,
                         "run_manifest": manifest_snapshot},
        "postroute": {"netlist": netlist_snapshot, "sdf": sdf_snapshot,
                      "models": model_snapshots,
                      "sdf_annotation": {
                          "status": "PASS_PATHDELAYS_100_PERCENT",
                          "pathdelays": int(pathdelays[0][0]),
                          "errors": 0,
                          "warnings": int(annotation[0][1]),
                          "warning_codes": sorted(set(sdf_warnings)),
                          "timing_checks_total": int(tchecks[0][0]),
                          "timing_checks_annotated": int(tchecks[0][1]),
                          "timing_checks_authority":
                              "INNOVUS_POSTROUTE_STA_NOT_GLS_VENDOR_MODEL"}},
        "tool": {"xrun": xrun_id,
                 "version_output": artifacts["xrun.version.txt"]},
        "producer_sources": source_snapshots,
        "registry": registry_snapshot,
        "artifacts": artifacts,
        "innovus_power_input": {
            # This block intentionally matches run_k2_physical_innovus_plan.py's
            # exact descriptor schema and can be copied without reshaping.
            "file": {"path": artifacts[rebased.name]["path"],
                     "sha256": artifacts[rebased.name]["sha256"]},
            "format": "VCD",
            "scope": registry["activity_scope"], "window_start_ns": "0",
            "window_end_ns": str(Decimal(last) / 1000)},
        "derived_saif": {"file": artifacts[saif.name], "format": "SAIF",
                         "root_instance": "dut"},
    }
    receipt["document_sha256"] = digest(canonical(receipt))
    receipt_path = output / "activity-receipt.json"
    write_exclusive(receipt_path, canonical(receipt))
    print(f"K2_POSTROUTE_ACTIVITY_PASS candidate={args.candidate} receipt={receipt_path}")
    return receipt_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--candidate", choices=(
        "fovea", "cluster2", "fovea_a7", "a2_p6", "a3_p6"), required=True)
    result.add_argument("--netlist", type=Path, required=True)
    result.add_argument("--sdf", type=Path, required=True)
    result.add_argument("--model", type=Path, action="append", required=True)
    result.add_argument("--trace", type=Path, required=True)
    result.add_argument("--run-manifest", type=Path, required=True)
    result.add_argument("--period-ns", choices=("5.0", "5.7", "6.5"), required=True)
    result.add_argument("--xrun", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        produce(parser().parse_args(argv))
    except (ActivityError, OSError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"K2_POSTROUTE_ACTIVITY_FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
