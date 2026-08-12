#!/usr/bin/env python3
"""Fail-closed W7 replay and policy/parallelism decomposition for Ganghee Cluster2."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROVENANCE_PATH = HERE / "provenance.json"
RTL_DIR = HERE / "rtl"
TB_PATH = HERE / "cluster2_direct_tb.sv"
RESULT_SCHEMA = "a3-w7-cluster2-result-v1"
PASS_RE = re.compile(
    r"^W7_CLUSTER2_RTL_PASS generated=(\d+) accepted=(\d+) delivered=(\d+) "
    r"overrun=(\d+) center=(\d+) peripheral=(\d+) both_lane_cycles=(\d+) "
    r"max_events_cycle=(\d+) latency_sum=(\d+) max_latency=(\d+) drain_cycles=(\d+)$",
    re.MULTILINE,
)


class GateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read JSON {path}: {exc}") from exc


def strip_verilog_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def check_provenance(root: Path = HERE) -> dict:
    provenance = load_json(root / "provenance.json")
    if provenance.get("schema") != "a3-w7-cluster2-provenance-v1":
        raise GateError("unexpected provenance schema")
    if provenance.get("top") != "aer_tx16_trad_rowcol_fovea_cluster2":
        raise GateError("unexpected Cluster2 top")
    expected_paths = ["rtl/arbiter2.v", "rtl/arbiter4_tree.v",
                      "rtl/aer_tx16_trad_rowcol_fovea_cluster2.v"]
    closure = provenance.get("closure")
    if not isinstance(closure, list) or [item.get("path") for item in closure] != expected_paths:
        raise GateError("file-list closure is not exact or ordered")
    filelist = (root / "rtl/cluster2.f").read_text(encoding="utf-8").splitlines()
    if filelist != [Path(path).name for path in expected_paths]:
        raise GateError("cluster2.f is not the exact three-file closure")
    for item in closure:
        path = root / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise GateError(f"RTL SHA mismatch: {item['path']}")

    scalar = provenance.get("scalar_fovea_reference", {})
    scalar_path = root / scalar.get("path", "missing")
    if not scalar_path.is_file() or sha256(scalar_path) != scalar.get("sha256"):
        raise GateError("scalar Fovea reference SHA mismatch")
    scalar_code = strip_verilog_comments(scalar_path.read_text(encoding="utf-8"))
    scalar_compact = re.sub(r"\s+", "", scalar_code)
    for token in ("parameterWEIGHT=5", "reg[RW-1:0]round;",
                  "wireprefer_center=(round!=WEIGHT[RW-1:0]);"):
        if token not in scalar_compact:
            raise GateError(f"scalar Fovea 1:5:5:1 token missing: {token}")

    cluster = strip_verilog_comments(
        (root / "rtl/aer_tx16_trad_rowcol_fovea_cluster2.v").read_text(encoding="utf-8")
    )
    compact = re.sub(r"\s+", "", cluster)
    required = (
        "moduleaer_tx16_trad_rowcol_fovea_cluster2(",
        "inputclk,inputrst,input[15:0]req,",
        "outputregvalid0,outputreg[1:0]row0,outputreg[3:0]col_mask0,",
        "outputregvalid1,outputreg[1:0]row1,outputreg[3:0]col_mask1",
        "localparam[3:0]CENTER_MASK=4'b0110;",
        "localparam[3:0]PERIPH_MASK=4'b1001;",
        "wire[3:0]center_req_in=row_req&CENTER_MASK;",
        "wire[3:0]periph_req_in=row_req&PERIPH_MASK;",
        "arbiter4_treecenter_arb(.clk(clk),.rst(rst),.req(center_req_in),.gnt(center_gnt));",
        "arbiter4_treeperiph_arb(.clk(clk),.rst(rst),.req(periph_req_in),.gnt(periph_gnt));",
        "valid0<=|center_gnt;",
        "valid1<=|periph_gnt;",
        "col_mask0<=sel_center_cols;",
        "col_mask1<=sel_periph_cols;",
    )
    for token in required:
        if token not in compact:
            raise GateError(f"Cluster2 semantic token missing: {token}")
    for forbidden in ("WEIGHT", "prefer_center", "round"):
        if forbidden in cluster:
            raise GateError(f"Cluster2 unexpectedly contains scalar weighting: {forbidden}")
    return provenance


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def check_benchmark(provenance: dict, repo: Path = REPO) -> dict[str, list[str]]:
    bench = provenance["benchmark"]
    generator = repo / "benchmarks/clean_slate_aer/generate_trace.py"
    full_path = repo / bench["full50_manifest"]
    cap_path = repo / bench["capacity22_manifest"]
    for path, expected in (
        (generator, bench["generator_sha256"]),
        (full_path, bench["full50_manifest_sha256"]),
        (cap_path, bench["capacity22_manifest_sha256"]),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise GateError(f"benchmark provenance SHA mismatch: {path}")
    generator_text = generator.read_text(encoding="utf-8")
    if f'GENERATOR_VERSION = "{bench["generator_version"]}"' not in generator_text:
        raise GateError("generator version marker mismatch")
    full = load_json(full_path)
    cap = load_json(cap_path)
    full_runs = full.get("runs")
    cap_runs = cap.get("runs")
    if len(full_runs) != bench["full50_run_count"] or len(cap_runs) != bench["capacity22_run_count"]:
        raise GateError("official run count mismatch")
    full_by_name = {run["name"]: run for run in full_runs}
    if len(full_by_name) != len(full_runs):
        raise GateError("duplicate full50 run name")
    for run in cap_runs:
        name = run["name"]
        if name not in full_by_name or canonical_json(run) != canonical_json(full_by_name[name]):
            raise GateError(f"capacity22 run is not byte-equivalent config subset: {name}")
    return {"full50": [run["name"] for run in full_runs],
            "capacity22": [run["name"] for run in cap_runs]}


class Arbiter2:
    def __init__(self) -> None:
        self.last = 1

    def comb(self, req: int) -> int:
        req0, req1 = req & 1, (req >> 1) & 1
        grant1 = req1 & (int(self.last == 0) | (1 - req0))
        grant0 = req0 & (1 - grant1)
        return grant0 | (grant1 << 1)

    def update(self, req: int, grant: int) -> None:
        if req:
            self.last = (grant >> 1) & 1


class Arbiter4:
    def __init__(self) -> None:
        self.lo, self.hi, self.top = Arbiter2(), Arbiter2(), Arbiter2()

    def step(self, req: int) -> int:
        lo_req, hi_req = req & 3, (req >> 2) & 3
        lo_gnt, hi_gnt = self.lo.comb(lo_req), self.hi.comb(hi_req)
        group_req = int(bool(lo_req)) | (int(bool(hi_req)) << 1)
        group_gnt = self.top.comb(group_req)
        grant = lo_gnt if group_gnt & 1 else ((hi_gnt << 2) if group_gnt & 2 else 0)
        self.lo.update(lo_req, lo_gnt)
        self.hi.update(hi_req, hi_gnt)
        self.top.update(group_req, group_gnt)
        return grant


def onehot_index(bits: int) -> int:
    for index in range(4):
        if bits & (1 << index):
            return index
    return 3


def row_requests(req: int) -> int:
    return sum(int(bool(req & (0xF << (row * 4)))) << row for row in range(4))


def row_cols(req: int, row: int) -> int:
    return (req >> (row * 4)) & 0xF


class Cluster2:
    name = "cluster2_dual_bitmap"

    def __init__(self, drop_peripheral: bool = False) -> None:
        self.center, self.peripheral = Arbiter4(), Arbiter4()
        self.drop_peripheral = drop_peripheral

    def step(self, req: int) -> list[tuple[int, int]]:
        rows = row_requests(req)
        center_grant = self.center.step(rows & 0x6)
        peripheral_grant = self.peripheral.step(rows & 0x9)
        outputs = []
        if center_grant:
            row = onehot_index(center_grant)
            outputs.append((row, row_cols(req, row)))
        if peripheral_grant and not self.drop_peripheral:
            row = onehot_index(peripheral_grant)
            outputs.append((row, row_cols(req, row)))
        return outputs


class WeightedBitmap:
    name = "weighted_5_to_1_bitmap"

    def __init__(self, scalar: bool = False) -> None:
        self.center, self.peripheral = Arbiter4(), Arbiter4()
        self.column = Arbiter4()
        self.round = 0
        self.scalar = scalar
        self.name = "canonical_weighted_scalar" if scalar else self.name

    def step(self, req: int) -> list[tuple[int, int]]:
        rows = row_requests(req)
        center_avail, peripheral_avail = bool(rows & 0x6), bool(rows & 0x9)
        prefer_center = self.round != 5
        use_center = (prefer_center and center_avail) or (
            (not prefer_center) and not peripheral_avail and center_avail)
        use_peripheral = ((not prefer_center) and peripheral_avail) or (
            prefer_center and not center_avail and peripheral_avail)
        center_req = rows & 0x6 if use_center else 0
        peripheral_req = rows & 0x9 if use_peripheral else 0
        center_grant = self.center.step(center_req)
        peripheral_grant = self.peripheral.step(peripheral_req)
        row_grant = center_grant if use_center else peripheral_grant if use_peripheral else 0
        if not row_grant:
            self.column.step(0)
            return []
        row = onehot_index(row_grant)
        cols = row_cols(req, row)
        if self.round == 5:
            self.round = 0
        else:
            self.round += 1
        if self.scalar:
            col_grant = self.column.step(cols)
            cols = col_grant
        else:
            self.column.step(0)
        return [(row, cols)]


class EqualSplitBitmap:
    name = "equal_split_bitmap"

    def __init__(self) -> None:
        self.center, self.peripheral = Arbiter4(), Arbiter4()
        self.team = Arbiter2()

    def step(self, req: int) -> list[tuple[int, int]]:
        rows = row_requests(req)
        team_req = int(bool(rows & 0x6)) | (int(bool(rows & 0x9)) << 1)
        team_grant = self.team.comb(team_req)
        self.team.update(team_req, team_grant)
        center_grant = self.center.step((rows & 0x6) if team_grant & 1 else 0)
        peripheral_grant = self.peripheral.step((rows & 0x9) if team_grant & 2 else 0)
        grant = center_grant or peripheral_grant
        if not grant:
            return []
        row = onehot_index(grant)
        return [(row, row_cols(req, row))]


@dataclass(frozen=True)
class Event:
    cycle: int
    event_id: int
    source: int


def read_events(path: Path) -> list[Event]:
    events = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                raw = json.loads(line)
                events.append(Event(int(raw["occurrence_cycle"]),
                                    int(raw["tb_only_event_id"]),
                                    int(raw["logical_source"])))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise GateError(f"invalid trace {path}:{line_number}: {exc}") from exc
    return events


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def simulate(events: list[Event], stim_cycles: int, policy_factory) -> dict:
    by_cycle: dict[int, list[Event]] = defaultdict(list)
    for event in events:
        if not 0 <= event.source < 16 or not 0 <= event.cycle < stim_cycles:
            raise GateError("trace event outside N16/stimulus bounds")
        by_cycle[event.cycle].append(event)
    policy = policy_factory()
    pending: dict[int, Event] = {}
    outputs: list[tuple[int, int]] = []
    accepted = overrun = delivered = fixed_window_delivered = 0
    center_delivered = peripheral_delivered = 0
    both_lane_cycles = max_events_cycle = 0
    latency_values: list[int] = []
    result_on_last_stim_cycle = False
    group_generated = [0, 0]
    group_accepted = [0, 0]
    group_overrun = [0, 0]

    def observe(cycle: int) -> int:
        nonlocal delivered, fixed_window_delivered, center_delivered
        nonlocal peripheral_delivered, both_lane_cycles, max_events_cycle
        result_sources = []
        if len(outputs) > 1:
            both_lane_cycles += 1
        for row, columns in outputs:
            if columns == 0:
                raise GateError("model emitted empty bitmap")
            for col in range(4):
                if columns & (1 << col):
                    result_sources.append(row * 4 + col)
        if len(result_sources) != len(set(result_sources)):
            raise GateError("model emitted duplicate source in one cycle")
        max_events_cycle = max(max_events_cycle, len(result_sources))
        for source in result_sources:
            event = pending.pop(source, None)
            if event is None:
                raise GateError(f"duplicate/phantom model result source={source}")
            delivered += 1
            fixed_window_delivered += int(cycle < stim_cycles)
            # Match the frozen common event CSV convention: occurrence and
            # observed retirement boundaries are counted inclusively.
            latency_values.append(cycle - event.cycle + 1)
            if source // 4 in (1, 2):
                center_delivered += 1
            else:
                peripheral_delivered += 1
        return sum(1 << source for source in result_sources)

    for cycle in range(stim_cycles):
        if cycle == stim_cycles - 1:
            result_on_last_stim_cycle = bool(outputs)
        result_mask = observe(cycle)
        for event in by_cycle.get(cycle, []):
            group = int(event.source // 4 not in (1, 2))
            group_generated[group] += 1
            if event.source in pending:
                overrun += 1
                group_overrun[group] += 1
            else:
                pending[event.source] = event
                accepted += 1
                group_accepted[group] += 1
        req = sum(1 << source for source in pending) & ~result_mask
        outputs = policy.step(req)

    drain_cycles = 0
    cycle = stim_cycles
    while (pending or outputs) and drain_cycles < 256:
        result_mask = observe(cycle)
        req = sum(1 << source for source in pending) & ~result_mask
        outputs = policy.step(req)
        cycle += 1
        drain_cycles += 1
    if pending or outputs:
        raise GateError(f"model drain timeout pending={sorted(pending)}")
    if accepted != delivered or accepted + overrun != len(events):
        raise GateError("model conservation invariant failed")
    mean_latency = statistics.fmean(latency_values) if latency_values else 0.0
    return {
        "generated": len(events), "accepted": accepted, "delivered": delivered,
        "overrun": overrun, "fixed_window_delivered": fixed_window_delivered,
        "center_delivered": center_delivered,
        "peripheral_delivered": peripheral_delivered,
        "both_lane_cycles": both_lane_cycles, "max_events_cycle": max_events_cycle,
        "latency_sum": sum(latency_values), "mean_latency": round(mean_latency, 9),
        "p99_latency": percentile(latency_values, 0.99),
        # The direct TB charges one final observed quiet edge after any drain
        # activity, matching the common reset/drain release convention.
        "max_latency": max(latency_values, default=0),
        "drain_cycles": drain_cycles + int(drain_cycles != 0 or result_on_last_stim_cycle),
        "center_generated": group_generated[0], "peripheral_generated": group_generated[1],
        "center_accepted": group_accepted[0], "peripheral_accepted": group_accepted[1],
        "center_overrun": group_overrun[0], "peripheral_overrun": group_overrun[1],
        "_latencies": latency_values,
    }


def public_metrics(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if not key.startswith("_")}


def aggregate(results: Iterable[dict], stim_cycles: Iterable[int]) -> dict:
    rows = list(results)
    cycles = list(stim_cycles)
    sums = {key: sum(row[key] for row in rows) for key in (
        "generated", "accepted", "delivered", "overrun", "fixed_window_delivered",
        "center_delivered", "peripheral_delivered", "both_lane_cycles",
        "latency_sum", "center_generated", "peripheral_generated",
        "center_accepted", "peripheral_accepted", "center_overrun", "peripheral_overrun",
    )}
    latencies = [value for row in rows for value in row["_latencies"]]
    total_cycles = sum(cycles)
    sums.update({
        "measurement_cycles": total_cycles,
        "throughput": round(sums["fixed_window_delivered"] / total_cycles, 9),
        "overrun_ratio": round(sums["overrun"] / sums["generated"], 9) if sums["generated"] else 0,
        "mean_latency": round(statistics.fmean(latencies), 9) if latencies else 0,
        "p99_latency": percentile(latencies, 0.99),
        "max_latency": max(latencies, default=0),
        "max_events_cycle": max((row["max_events_cycle"] for row in rows), default=0),
    })
    return sums


def find_tool(env_name: str, names: tuple[str, ...], fallbacks: tuple[Path, ...]) -> Path:
    if os.environ.get(env_name):
        path = Path(os.environ[env_name]).resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        raise GateError(f"{env_name} is not executable: {path}")
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for path in fallbacks:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise GateError(f"missing executable for {env_name}")


def run_command(command: list[str], *, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, check=False)
    if expect_success and process.returncode != 0:
        raise GateError(f"command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}")
    return process


def compile_rtl(iverilog: Path, build_dir: Path, cluster_path: Path | None = None) -> Path:
    image = build_dir / "cluster2.vvp"
    command = [str(iverilog), "-g2012", "-Wall", "-s", "a3_w7_cluster2_direct_tb",
               "-o", str(image), str(RTL_DIR / "arbiter2.v"),
               str(RTL_DIR / "arbiter4_tree.v"),
               str(cluster_path or RTL_DIR / "aer_tx16_trad_rowcol_fovea_cluster2.v"),
               str(TB_PATH)]
    process = run_command(command)
    benign_warning = "warning: Some design elements have no explicit time unit and/or"
    unexpected = [line for line in process.stdout.splitlines()
                  if ("warning" in line.lower() or "error" in line.lower())
                  and benign_warning not in line]
    if unexpected:
        raise GateError("unexpected compile diagnostic:\n" + "\n".join(unexpected))
    return image


def write_stimulus(path: Path, events: list[Event], stim_cycles: int) -> None:
    lines = [f"{stim_cycles} {len(events)}"]
    lines.extend(f"{event.cycle} {event.event_id} {event.source}" for event in events)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def run_rtl_trace(vvp: Path, image: Path, stim: Path) -> dict:
    process = run_command([str(vvp), str(image), "+MODE=trace", f"+STIM={stim}"])
    matches = PASS_RE.findall(process.stdout)
    if len(matches) != 1:
        raise GateError(f"expected exactly one RTL PASS marker, got {len(matches)}\n{process.stdout}")
    keys = ("generated", "accepted", "delivered", "overrun", "center_delivered",
            "peripheral_delivered", "both_lane_cycles", "max_events_cycle",
            "latency_sum", "max_latency", "drain_cycles")
    return dict(zip(keys, map(int, matches[0])))


def check_rtl_match(model: dict, rtl: dict, run_name: str) -> None:
    for key in rtl:
        if model[key] != rtl[key]:
            raise GateError(f"RTL/model mismatch {run_name} {key}: {rtl[key]} != {model[key]}")


def run_negative_gates(iverilog: Path, vvp: Path, build_dir: Path, baseline: Path) -> dict:
    reset = run_command([str(vvp), str(baseline), "+MODE=reset"])
    if reset.stdout.count("W7_CLUSTER2_RESET_PASS") != 1:
        raise GateError("reset gate did not emit exact PASS marker")
    source = (RTL_DIR / "aer_tx16_trad_rowcol_fovea_cluster2.v").read_text(encoding="utf-8")
    mutants = {
        "stale_valid_on_reset": ("valid1 <= 1'b0; row1", "valid1 <= 1'b1; row1",
                                 "reset did not clear both valids"),
        "drop_peripheral_lane": ("valid1 <= |periph_gnt;", "valid1 <= 1'b0;",
                                 "first dual-lane result mismatch"),
    }
    result = {"baseline_reset": "PASS", "mutants": {}}
    for name, (before, after, diagnostic) in mutants.items():
        if source.count(before) != 1:
            raise GateError(f"negative mutation anchor is not unique: {name}")
        mutant_path = build_dir / f"{name}.v"
        mutant_path.write_text(source.replace(before, after), encoding="utf-8")
        mutant_dir = build_dir / name
        mutant_dir.mkdir()
        image = compile_rtl(iverilog, mutant_dir, mutant_path)
        process = run_command([str(vvp), str(image), "+MODE=reset"], expect_success=False)
        if process.returncode == 0 or diagnostic not in process.stdout or "W7_CLUSTER2_RESET_PASS" in process.stdout:
            raise GateError(f"negative mutant escaped gate: {name}\n{process.stdout}")
        result["mutants"][name] = {"status": "EXPECTED_FAIL_CAUGHT",
                                     "diagnostic": diagnostic}
    return result


def generate_suite(generator: Path, manifest: Path, output_dir: Path) -> list[dict]:
    process = run_command([sys.executable, str(generator), "--manifest", str(manifest),
                           "--output-dir", str(output_dir)])
    index = load_json(output_dir / "generation-index.json")
    if not isinstance(index.get("runs"), list):
        raise GateError("generation index lacks runs")
    if "Traceback" in process.stdout:
        raise GateError("generator traceback")
    return index["runs"]


def delta(new: dict, old: dict) -> dict:
    return {key: round(new[key] - old[key], 9) for key in (
        "accepted", "overrun", "fixed_window_delivered", "throughput",
        "mean_latency", "p99_latency", "max_latency")}


def persistent_policy_probe(factory, cycles: int = 120) -> dict:
    policy = factory()
    row_opportunities = [0, 0, 0, 0]
    bitmap_events = 0
    dual_lane_cycles = 0
    for _ in range(cycles):
        outputs = policy.step(0xFFFF)
        dual_lane_cycles += int(len(outputs) == 2)
        for row, columns in outputs:
            row_opportunities[row] += 1
            bitmap_events += columns.bit_count()
    return {
        "cycles": cycles,
        "row_opportunities_0_1_2_3": row_opportunities,
        "center_opportunities": row_opportunities[1] + row_opportunities[2],
        "peripheral_opportunities": row_opportunities[0] + row_opportunities[3],
        "bitmap_events": bitmap_events,
        "dual_lane_cycles": dual_lane_cycles,
    }


def execute(output: Path | None) -> dict:
    provenance = check_provenance()
    expected_names = check_benchmark(provenance)
    iverilog = find_tool("A3_W7_IVERILOG", ("iverilog",),
                         (Path("/tmp/a7-toolchain/usr/bin/iverilog"),))
    vvp = find_tool("A3_W7_VVP", ("vvp",), (Path("/tmp/a7-toolchain/usr/bin/vvp"),))
    tool_id = run_command([str(iverilog), "-V"]).stdout.splitlines()[0]
    generator = REPO / "benchmarks/clean_slate_aer/generate_trace.py"
    policies = (Cluster2, EqualSplitBitmap, WeightedBitmap,
                lambda: WeightedBitmap(scalar=True))
    policy_names = ("cluster2_dual_bitmap", "equal_split_bitmap",
                    "weighted_5_to_1_bitmap", "canonical_weighted_scalar")

    with tempfile.TemporaryDirectory(prefix="a3-w7-cluster2-") as temporary:
        work = Path(temporary)
        image = compile_rtl(iverilog, work)
        negative = run_negative_gates(iverilog, vvp, work, image)
        suites = {}
        per_run = {}
        for suite_name, manifest_key in (("full50", "full50_manifest"),
                                         ("capacity22", "capacity22_manifest")):
            suite_dir = work / suite_name
            generated_runs = generate_suite(generator, REPO / provenance["benchmark"][manifest_key], suite_dir)
            names = [item["run"]["name"] for item in generated_runs]
            if names != expected_names[suite_name]:
                raise GateError(f"generated run order mismatch: {suite_name}")
            model_rows = {name: [] for name in policy_names}
            cycles = []
            per_run[suite_name] = []
            for item in generated_runs:
                trace = suite_dir / item["trace_file"]
                if sha256(trace) != item["trace_sha256"]:
                    raise GateError(f"generated trace SHA mismatch: {item['run']['name']}")
                events = read_events(trace)
                stim_cycles = int(item["run"]["stim_cycles"])
                cycles.append(stim_cycles)
                metrics_by_policy = {}
                for name, factory in zip(policy_names, policies):
                    metrics = simulate(events, stim_cycles, factory)
                    model_rows[name].append(metrics)
                    metrics_by_policy[name] = public_metrics(metrics)
                stim = work / f"{suite_name}-{item['run']['name']}.stim"
                write_stimulus(stim, events, stim_cycles)
                rtl = run_rtl_trace(vvp, image, stim)
                check_rtl_match(model_rows["cluster2_dual_bitmap"][-1], rtl, item["run"]["name"])
                per_run[suite_name].append({
                    "name": item["run"]["name"], "trace_sha256": item["trace_sha256"],
                    "stim_cycles": stim_cycles,
                    "cluster2_dual_bitmap": metrics_by_policy["cluster2_dual_bitmap"],
                    "rtl_lockstep": "PASS",
                })
            aggregate_models = {name: aggregate(model_rows[name], cycles) for name in policy_names}
            cluster = aggregate_models["cluster2_dual_bitmap"]
            # The one-lane counterfactual keeps both teams live and selects
            # them fairly; it removes only simultaneous team retirement.
            serialized_mutant = aggregate_models["equal_split_bitmap"]
            if serialized_mutant["delivered"] >= cluster["delivered"] or cluster["both_lane_cycles"] == 0:
                raise GateError(f"parallel-lane negative gate did not distinguish {suite_name}")
            suites[suite_name] = {
                "run_count": len(generated_runs), "rtl_lockstep_runs": len(generated_runs),
                "models": aggregate_models,
                "decomposition": {
                    "within_row_bitmap_vs_scalar": delta(
                        aggregate_models["weighted_5_to_1_bitmap"],
                        aggregate_models["canonical_weighted_scalar"]),
                    "remove_5_to_1_weight_at_one_bitmap_lane": delta(
                        aggregate_models["equal_split_bitmap"],
                        aggregate_models["weighted_5_to_1_bitmap"]),
                    "add_second_independent_team_lane": delta(
                        aggregate_models["cluster2_dual_bitmap"],
                        aggregate_models["equal_split_bitmap"]),
                },
                "drop_peripheral_lane_negative": {
                    "status": "EXPECTED_DEGRADATION_CAUGHT",
                    "delivered_delta": serialized_mutant["delivered"] - cluster["delivered"],
                    "overrun_delta": serialized_mutant["overrun"] - cluster["overrun"],
                },
            }

    receipt = {
        "schema": RESULT_SCHEMA,
        "status": "PASS",
        "decision": "DIGITAL_LOCAL_GO__PHYSICAL_AND_BACKPRESSURE_HOLD",
        "provenance": provenance,
        "execution": {
            "python": sys.version.split()[0], "python_executable": sys.executable,
            "iverilog": str(iverilog), "iverilog_identity": tool_id,
            "vvp": str(vvp), "runner_sha256": sha256(Path(__file__)),
            "tb_sha256": sha256(TB_PATH),
            "semantics": "one exact pending slot/source; occurrence while occupied is source_overrun; raw bitmap retirement; sink always ready",
        },
        "negative_gate": negative,
        "persistent_full_req_policy_probe": {
            name: persistent_policy_probe(factory)
            for name, factory in zip(policy_names, policies)
        },
        "suites": suites,
        "per_run": per_run,
        "claim_boundary": {
            "rtl": "exact canonical three-blob Cluster2 closure locksteps the executable model on all 50+22 runs",
            "weight": "Cluster2 contains no WEIGHT/round/prefer_center; 1:5:5:1 is removed, not preserved",
            "parallelism": "two independent center/peripheral bitmap lanes account separately from weight removal and within-row bitmap expansion",
            "holds": ["independent lane backpressure", "physical PPA", "server Xcelium reproduction"],
        },
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_text(encoded, encoding="utf-8")
        os.replace(temporary_output, output)
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = execute(args.output)
    except GateError as exc:
        print(f"W7_CLUSTER2_FAIL: {exc}", file=sys.stderr)
        return 2
    print("W7_CLUSTER2_PASS "
          f"full={receipt['suites']['full50']['run_count']} "
          f"capacity={receipt['suites']['capacity22']['run_count']} "
          f"decision={receipt['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
