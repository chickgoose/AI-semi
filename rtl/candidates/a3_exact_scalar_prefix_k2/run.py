#!/usr/bin/env python3
"""Fail-closed local qualification for A3 Exact-Scalar-Prefix-K2."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
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

from oracle import AtomicK2Model, persistent_probe


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RTL = HERE / "rtl/a3_exact_scalar_prefix_k2.sv"
LOCKSTEP_TB = HERE / "tb/lockstep_tb.sv"
DIRECT_TB = HERE / "tb/direct_tb.sv"
FROZEN = REPO / "benchmarks/clean_slate_aer"
GENERATOR = FROZEN / "generate_trace.py"
FULL_MANIFEST = FROZEN / "manifest.neutrality-n16.json"
CAP_MANIFEST = FROZEN / "manifest.multilane-n16.json"
COMMON_TB = REPO / "tb/clean/aer_clean_tb.sv"
COMMON_FILELIST = HERE / "files.f"
COMMON_TB_RELATIVE = "tb/clean/aer_clean_tb.sv"
COMMON_TB_SOURCE_COMMIT = "32c2ec5ab1d5805e895ca83d3bc66ee02e8d6777"
COMMON_TB_BLOB_SHA1 = "3cdd4d45ccbcf70fcb79bf17188b4021b95d73e0"
EXPECTED = {
    GENERATOR: "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    FULL_MANIFEST: "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    CAP_MANIFEST: "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
    COMMON_TB: "27d9437a5179b0cb909d02edee1ac2f82ea6d20aeab9cfb64997b458192102a2",
}
EXPECTED_RUNS = {"full50": 50, "capacity22": 22}
COMMON_RETIRE_LATENCY = 1
ALLOW_WARNING = (
    "warning: System task ($fatal) cannot be synthesized in an always_ff process.",
    "warning: System task ($fatal) cannot be synthesized in an always process.",
)


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Event:
    cycle: int
    event_id: int
    source: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot load JSON {path}: {exc}") from exc


def command(command: list[str], *, env: dict[str, str] | None = None,
            success: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, check=False, env=env)
    if success and process.returncode != 0:
        raise GateError(f"command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}")
    return process


def find_tool(env_name: str, names: tuple[str, ...], fallbacks: tuple[Path, ...]) -> Path:
    override = os.environ.get(env_name)
    if override:
        path = Path(override).resolve()
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise GateError(f"{env_name} is not executable: {path}")
        return path
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for path in fallbacks:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise GateError(f"required tool missing: {env_name}")


def verify_frozen() -> dict:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise GateError(f"frozen common input SHA mismatch: {path}")
    if 'GENERATOR_VERSION = "4.0"' not in GENERATOR.read_text(encoding="utf-8"):
        raise GateError("frozen generator is not version 4.0")
    if git_blob_sha1(COMMON_TB) != COMMON_TB_BLOB_SHA1:
        raise GateError("frozen common TB Git blob SHA mismatch")
    pinned_blob = command([
        "git", "-C", str(REPO), "rev-parse",
        f"{COMMON_TB_SOURCE_COMMIT}:{COMMON_TB_RELATIVE}",
    ]).stdout.strip()
    if pinned_blob != COMMON_TB_BLOB_SHA1:
        raise GateError("pinned A1 common TB commit/blob mismatch")
    filelist_entries = [
        line.strip()
        for line in COMMON_FILELIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if filelist_entries.count(COMMON_TB_RELATIVE) != 1:
        raise GateError(
            "A3 common filelist does not bind the pinned common TB exactly once"
        )
    full = load_json(FULL_MANIFEST)
    cap = load_json(CAP_MANIFEST)
    full_runs = full.get("runs", [])
    cap_runs = cap.get("runs", [])
    if len(full_runs) != 50 or len(cap_runs) != 22:
        raise GateError("frozen manifest run count mismatch")
    full_by_name = {item["name"]: item for item in full_runs}
    for item in cap_runs:
        if item["name"] not in full_by_name or item != full_by_name[item["name"]]:
            raise GateError(f"capacity run is not exact full50 subset: {item.get('name')}")
    return {"generator_sha256": EXPECTED[GENERATOR],
            "full_manifest_sha256": EXPECTED[FULL_MANIFEST],
            "capacity_manifest_sha256": EXPECTED[CAP_MANIFEST],
            "common_tb_sha256": EXPECTED[COMMON_TB],
            "common_tb_git_blob_sha1": COMMON_TB_BLOB_SHA1,
            "common_tb_source_commit": COMMON_TB_SOURCE_COMMIT,
            "common_tb_filelist": str(COMMON_FILELIST.relative_to(REPO)),
            "common_tb_occurrence_order":
                "negedge occurrence classification before following posedge accept/fire",
            "generator_version": "4.0"}


def compile_iverilog(iverilog: Path, output: Path, tb: Path,
                     defines: tuple[str, ...] = ()) -> None:
    invocation = [str(iverilog), "-g2012", "-Wall"]
    invocation.extend(f"-D{name}" for name in defines)
    invocation.extend(["-s", tb.stem.replace("direct_tb", "a3_exact_scalar_prefix_k2_direct_tb")
                       if tb == DIRECT_TB else "a3_exact_scalar_prefix_k2_lockstep_tb",
                       "-o", str(output), str(RTL), str(tb)])
    process = command(invocation)
    diagnostics = []
    for line in process.stdout.splitlines():
        lower = line.lower()
        if "warning" in lower or "error" in lower or "sorry" in lower:
            if not any(allowed in line for allowed in ALLOW_WARNING):
                diagnostics.append(line)
    if diagnostics:
        raise GateError("unexpected Icarus diagnostic:\n" + "\n".join(diagnostics))


def read_events(path: Path) -> list[Event]:
    events = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                raw = json.loads(line)
                events.append(Event(int(raw["occurrence_cycle"]),
                                    int(raw["tb_only_event_id"]),
                                    int(raw["logical_source"])))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise GateError(f"bad trace {path}:{number}: {exc}") from exc
    return events


def write_vector(stream, rst: bool, ready: bool, pending: int,
                 model: AtomicK2Model) -> None:
    snap = model.snapshot()
    stream.write(
        f"{int(rst)} {int(ready)} {pending:04x} "
        f"{snap['grant_count']} {snap['addr0']} {snap['addr1']} "
        f"{snap['round']} {snap['center']} {snap['peripheral']} {snap['column']}\n"
    )


def replay_trace(events: list[Event], stim_cycles: int, vector_stream, *,
                 _mutate_fire_before_occurrence: bool = False) -> dict:
    by_cycle: dict[int, list[Event]] = defaultdict(list)
    for event in events:
        if not 0 <= event.source < 16 or not 0 <= event.cycle < stim_cycles:
            raise GateError("trace event outside N16/stimulus bounds")
        by_cycle[event.cycle].append(event)

    model = AtomicK2Model()
    pending: dict[int, Event] = {}
    accepted = delivered = overrun = fixed_delivered = 0
    latencies: list[int] = []
    cycles_written = 0

    model.step(rst=True, ready=False, pending=0)
    write_vector(vector_stream, True, False, 0, model)
    cycles_written += 1

    def one_edge(cycle: int, inject: bool) -> None:
        nonlocal accepted, delivered, overrun, fixed_delivered, cycles_written
        fired = model.grants

        def classify_occurrences() -> None:
            nonlocal accepted, overrun
            if inject:
                for event in by_cycle.get(cycle, []):
                    if event.source in pending:
                        overrun += 1
                    else:
                        pending[event.source] = event
                        accepted += 1

        def commit_fired() -> None:
            nonlocal delivered, fixed_delivered
            for source in fired:
                event = pending.pop(source, None)
                if event is None:
                    raise GateError(f"oracle duplicate/phantom source {source}")
                delivery_cycle = cycle + COMMON_RETIRE_LATENCY
                delivered += 1
                fixed_delivered += int(delivery_cycle < stim_cycles)
                # The common TB stamps an occurrence at negedge with the
                # preceding cycle_count, then increments cycle_count at the
                # posedge that observes retirement.
                latencies.append(delivery_cycle - event.cycle + 1)

        # The common TB offers occurrences at negedge.  The registered owner
        # bundle does not accept/fire until the following posedge, whose NBA
        # clears pending only after the occurrence has already seen the old
        # high level.  A same-source occurrence on that indexed cycle is
        # therefore overrun, not a replacement accepted after an early pop.
        if _mutate_fire_before_occurrence:
            commit_fired()
            classify_occurrences()
            pending_mask = sum(1 << source for source in pending)
        else:
            classify_occurrences()
            # This is the level sampled by the owner at the following posedge.
            # The TB's nonblocking pending clear is not visible until after
            # that sample, even though the fired identity is removed from the
            # event scoreboard at the edge.
            pending_mask = sum(1 << source for source in pending)
        observed_fired = model.step(rst=False, ready=True, pending=pending_mask)
        if observed_fired != fired:
            raise GateError("oracle atomic fire mismatch")
        if not _mutate_fire_before_occurrence:
            commit_fired()
        write_vector(vector_stream, False, True, pending_mask, model)
        cycles_written += 1

    for cycle in range(stim_cycles):
        one_edge(cycle, True)
    drain = 0
    while (pending or model.grants) and drain < 256:
        one_edge(stim_cycles + drain, False)
        drain += 1
    if pending or model.grants:
        raise GateError("oracle drain timeout")
    if accepted != delivered or accepted + overrun != len(events):
        raise GateError("oracle conservation mismatch")
    return {
        "generated": len(events), "accepted": accepted, "delivered": delivered,
        "overrun": overrun, "fixed_window_delivered": fixed_delivered,
        "latency_sum": sum(latencies), "latencies": latencies,
        "max_latency": max(latencies, default=0), "drain_cycles": drain,
        "vector_cycles": cycles_written,
    }


COMMON_SEMANTIC_EXPECTED = {
    "generated": 2,
    "accepted": 1,
    "delivered": 1,
    "overrun": 1,
    "fixed_window_delivered": 0,
    "latencies": [3],
    "owner_drain_cycles": 0,
    "common_link_tail_cycles": 1,
    "vector_pending_masks": ["0000", "0001", "0001"],
}


def common_semantic_probe(*, mutate_fire_before_occurrence: bool = False) -> dict:
    """Exercise a retrigger while its prior occurrence fires at the next edge."""

    vectors = io.StringIO()
    result = replay_trace(
        [Event(cycle=0, event_id=0, source=0),
         Event(cycle=1, event_id=1, source=0)],
        stim_cycles=2,
        vector_stream=vectors,
        _mutate_fire_before_occurrence=mutate_fire_before_occurrence,
    )
    selected = {
        key: result[key] for key in COMMON_SEMANTIC_EXPECTED
        if key not in {
            "vector_pending_masks", "owner_drain_cycles",
            "common_link_tail_cycles",
        }
    }
    selected["owner_drain_cycles"] = result["drain_cycles"]
    selected["common_link_tail_cycles"] = COMMON_RETIRE_LATENCY
    selected["vector_pending_masks"] = [
        line.split()[2] for line in vectors.getvalue().splitlines()
    ]
    return selected


def qualify_common_semantic_probe(*, mutate_fire_before_occurrence: bool = False) -> dict:
    result = common_semantic_probe(
        mutate_fire_before_occurrence=mutate_fire_before_occurrence
    )
    if result != COMMON_SEMANTIC_EXPECTED:
        raise GateError(
            "COMMON_SEMANTIC_MISMATCH "
            f"expected={COMMON_SEMANTIC_EXPECTED} actual={result}"
        )
    return result


def generate_suite(manifest: Path, output: Path) -> list[dict]:
    process = command([sys.executable, str(GENERATOR), "--manifest", str(manifest),
                       "--output-dir", str(output)])
    if "Traceback" in process.stdout:
        raise GateError("frozen generator traceback")
    index = load_json(output / "generation-index.json")
    if not isinstance(index.get("runs"), list):
        raise GateError("frozen generator index lacks runs")
    return index["runs"]


def percentile(values: list[int], numerator: int = 99) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, (numerator * len(ordered) + 99) // 100 - 1)]


def aggregate(rows: list[dict], measurement_cycles: int) -> dict:
    latencies = [value for row in rows for value in row["latencies"]]
    result = {key: sum(row[key] for row in rows) for key in (
        "generated", "accepted", "delivered", "overrun",
        "fixed_window_delivered", "latency_sum", "vector_cycles")}
    result.update({
        "measurement_cycles": measurement_cycles,
        "throughput": round(result["fixed_window_delivered"] / measurement_cycles, 9),
        "mean_latency": round(statistics.fmean(latencies), 9) if latencies else 0,
        "p99_latency": percentile(latencies),
        "max_latency": max(latencies, default=0),
    })
    return result


def write_vectors(path: Path, lines_path: Path, count: int) -> None:
    with path.open("w", encoding="ascii") as output:
        output.write(f"{count}\n")
        with lines_path.open(encoding="ascii") as source:
            shutil.copyfileobj(source, output)


def run_lockstep(vvp: Path, image: Path, vectors: Path,
                 expected_marker: str = "A3_EXACT_SCALAR_PREFIX_K2_LOCKSTEP_PASS",
                 success: bool = True) -> subprocess.CompletedProcess[str]:
    process = command([str(vvp), str(image), f"+VECTORS={vectors}"], success=success)
    if success and process.stdout.count(expected_marker) != 1:
        raise GateError(f"lockstep marker mismatch:\n{process.stdout}")
    return process


def directed_vectors(path: Path) -> int:
    model = AtomicK2Model()
    vectors: list[tuple[bool, bool, int]] = []
    vectors.append((True, False, 0xFFFF))
    vectors.extend([
        (False, True, 0xFFFF), (False, True, 0xFFFF),
        (False, False, 0xF11F), (False, False, 0xFFFF),
        (False, False, 0xFFFF), (False, True, 0xFFFF),
        (False, True, 0x0000), (False, True, 0x0000),
        (False, True, 0x1001), (False, True, 0x1001),
        (False, True, 0x0000), (False, True, 0x0020),
        (False, True, 0x0020), (False, True, 0x0000),
        (False, True, 0x0020), (True, False, 0xFFFF),
        (False, True, 0x0000),
    ])
    with path.open("w", encoding="ascii") as stream:
        stream.write(f"{len(vectors)}\n")
        for rst, ready, pending in vectors:
            model.step(rst=rst, ready=ready, pending=pending)
            write_vector(stream, rst, ready, pending, model)
    return len(vectors)


def yosys_metrics(yosys: Path, work: Path) -> dict:
    json_netlist = work / "synth.json"
    script = (
        f"read_verilog -sv -DSYNTHESIS {RTL}; "
        "hierarchy -check -top a3_exact_scalar_prefix_k2; "
        "proc; flatten; opt; memory; opt; "
        f"write_json {json_netlist}; "
        "techmap; opt; abc -g simple; clean; "
        "stat -json; ltp -noff"
    )
    env = os.environ.copy()
    libdir = yosys.parents[1] / "lib/x86_64-linux-gnu"
    if libdir.is_dir():
        old = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = str(libdir) + ((":" + old) if old else "")
    process = command([str(yosys), "-Q", "-p", script], env=env)
    netlist = load_json(json_netlist)
    module = netlist["modules"]["a3_exact_scalar_prefix_k2"]
    state_bits = 0
    for cell in module.get("cells", {}).values():
        if cell["type"] in ("$dff", "$adff", "$sdff", "$dffe", "$sdffe"):
            width = cell.get("parameters", {}).get("WIDTH", "0")
            state_bits += int(width, 2) if isinstance(width, str) else int(width)
    json_matches = re.findall(r'\{\s*"creator".*?\n\}', process.stdout, re.S)
    if not json_matches:
        # Yosys stat JSON is embedded in log text; parse its module fragment.
        cell_match = re.search(r'"num_cells"\s*:\s*(\d+)', process.stdout)
        type_block = re.search(r'"num_cells_by_type"\s*:\s*\{([^}]*)\}', process.stdout, re.S)
        if not cell_match or not type_block:
            raise GateError("cannot parse Yosys stat JSON")
        gate_cells = int(cell_match.group(1))
        cell_types = {name: int(value) for name, value in
                      re.findall(r'"([^"]+)"\s*:\s*(\d+)', type_block.group(1))}
    else:
        raw = json.loads(json_matches[-1])
        stats = raw["modules"]["\\a3_exact_scalar_prefix_k2"]
        gate_cells = stats["num_cells"]
        cell_types = stats["num_cells_by_type"]
    paths = [int(value) for value in re.findall(r'length[= ](\d+)', process.stdout)]
    if not paths:
        raise GateError("cannot parse Yosys topological depth")
    return {
        "yosys_identity": command([str(yosys), "-V"], env=env).stdout.strip(),
        "registered_state_bits": state_bits,
        "mapped_cell_count": gate_cells,
        "mapped_cells_by_type": cell_types,
        "longest_topological_path_cells": max(paths),
        "interpretation": "generic Yosys/ABC proxy; not standard-cell area, routed delay, Fmax, or power",
    }


def execute(output: Path | None) -> dict:
    frozen = verify_frozen()
    common_semantic = qualify_common_semantic_probe()
    iverilog = find_tool("A3_K2_IVERILOG", ("iverilog",),
                         (Path("/tmp/a7-toolchain/usr/bin/iverilog"),))
    vvp = find_tool("A3_K2_VVP", ("vvp",), (Path("/tmp/a7-toolchain/usr/bin/vvp"),))
    yosys = find_tool("A3_K2_YOSYS", ("yosys",), (Path("/tmp/a7-toolchain/usr/bin/yosys"),))

    with tempfile.TemporaryDirectory(prefix="a3-exact-scalar-prefix-k2-") as temporary:
        work = Path(temporary)
        direct_image = work / "direct.vvp"
        lockstep_image = work / "lockstep.vvp"
        compile_iverilog(iverilog, direct_image, DIRECT_TB)
        compile_iverilog(iverilog, lockstep_image, LOCKSTEP_TB)
        direct = command([str(vvp), str(direct_image)])
        directed_markers = (
            "A3_K2_NATIVE_ONEHOT_PASS",
            "A3_K2_PERSISTENT_PASS", "A3_K2_SPARSE_FALLBACK_PASS",
            "A3_K2_STALL_ATOMIC_PASS", "A3_K2_RESET_DRAIN_PASS",
            "A3_K2_RETRIGGER_PASS", "A3_EXACT_SCALAR_PREFIX_K2_DIRECT_PASS",
        )
        for marker in directed_markers:
            if direct.stdout.count(marker) != 1:
                raise GateError(f"directed marker missing/duplicate: {marker}\n{direct.stdout}")

        directed_path = work / "directed.vec"
        directed_count = directed_vectors(directed_path)
        run_lockstep(vvp, lockstep_image, directed_path)

        line_path = work / "frozen.lines"
        suite_rows: dict[str, dict] = {}
        total_vectors = 0
        with line_path.open("w", encoding="ascii") as vector_stream:
            for suite_name, manifest in (("full50", FULL_MANIFEST),
                                         ("capacity22", CAP_MANIFEST)):
                suite_dir = work / suite_name
                generated = generate_suite(manifest, suite_dir)
                if len(generated) != EXPECTED_RUNS[suite_name]:
                    raise GateError(f"generated run count mismatch: {suite_name}")
                metrics = []
                measurement_cycles = 0
                run_names = []
                for item in generated:
                    trace = suite_dir / item["trace_file"]
                    if sha256(trace) != item["trace_sha256"]:
                        raise GateError(f"generated trace SHA mismatch: {item['run']['name']}")
                    stim_cycles = int(item["run"]["stim_cycles"])
                    measurement_cycles += stim_cycles
                    row = replay_trace(read_events(trace), stim_cycles, vector_stream)
                    total_vectors += row["vector_cycles"]
                    metrics.append(row)
                    run_names.append(item["run"]["name"])
                suite_rows[suite_name] = {
                    "run_count": len(generated), "run_names": run_names,
                    **aggregate(metrics, measurement_cycles),
                }
        frozen_vectors = work / "frozen.vec"
        write_vectors(frozen_vectors, line_path, total_vectors)
        run_lockstep(vvp, lockstep_image, frozen_vectors)

        mutation_results = {}
        diagnostics = {
            "A3_K2_MUT_STALE": "LOCKSTEP_MISMATCH",
            "A3_K2_MUT_DUP": "LOCKSTEP_MISMATCH",
            "A3_K2_MUT_STATE_ADV": "LOCKSTEP_MISMATCH",
        }
        for define, diagnostic in diagnostics.items():
            image = work / f"{define}.vvp"
            compile_iverilog(iverilog, image, LOCKSTEP_TB, (define,))
            process = run_lockstep(vvp, image, directed_path, success=False)
            if process.returncode == 0 or diagnostic not in process.stdout or \
                    "A3_EXACT_SCALAR_PREFIX_K2_LOCKSTEP_PASS" in process.stdout:
                raise GateError(f"mutation escaped: {define}\n{process.stdout}")
            mutation_results[define] = {"status": "EXPECTED_FAIL_CAUGHT",
                                        "diagnostic": diagnostic}

        try:
            qualify_common_semantic_probe(mutate_fire_before_occurrence=True)
        except GateError as exc:
            if "COMMON_SEMANTIC_MISMATCH" not in str(exc):
                raise
            mutation_results["A3_K2_MUT_FIRE_BEFORE_OCCURRENCE"] = {
                "status": "EXPECTED_FAIL_CAUGHT",
                "diagnostic": "COMMON_SEMANTIC_MISMATCH",
            }
        else:
            raise GateError("mutation escaped: A3_K2_MUT_FIRE_BEFORE_OCCURRENCE")

        synth = yosys_metrics(yosys, work)

    receipt = {
        "schema": "a3-exact-scalar-prefix-k2-evidence-v2",
        "status": "PASS",
        "candidate": "a3_exact_scalar_prefix_k2",
        "rtl_sha256": sha256(RTL),
        "oracle_sha256": sha256(HERE / "oracle.py"),
        "runner_sha256": sha256(HERE / "run.py"),
        "semantics": {
            "boundary": "N16 level-held pending bitmap; up to two ordered address grants per atomic bundle",
            "commit": "grant_count 0/1/2 plus ordered addresses; all count lanes commit on grant_count!=0 && bundle_ready; policy advances exactly count scalar microsteps; stalled offer and state hold",
            "ordering": "lane1 is one canonical scalar transition after lane0, with lane0 address masked and all Fovea/RR next state applied",
            "architecture": "two replicated canonical scalar selectors in combinational lookahead; no shared population-prefix rank topology",
        },
        "frozen_v4": frozen,
        "execution": {
            "icarus_identity": command([str(iverilog), "-V"]).stdout.splitlines()[0],
            "python": sys.version.split()[0],
            "directed_markers": list(directed_markers),
            "directed_lockstep_vectors": directed_count,
            "frozen_lockstep_vectors": total_vectors,
            "frozen_lockstep_runs": 72,
            "common_semantic_probe": common_semantic,
        },
        "persistent_probe": persistent_probe(120),
        "suites": suite_rows,
        "mutations": mutation_results,
        "synthesis": synth,
        "limits": [
            "local Icarus and independent Python oracle only; no Xcelium/formal qualification",
            "atomic bundle ready only; independent per-lane backpressure is unsupported",
            "independently stalled transport requires a separate buffered link adapter that cannot mutate scheduler policy",
            "one outstanding event per address; same-edge occurrence identity is not carried by the bitmap",
            "Yosys metrics are generic logic proxies, not physical PPA or timing closure",
        ],
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, output)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = execute(args.output)
    except GateError as exc:
        print(f"A3_EXACT_SCALAR_PREFIX_K2_FAIL: {exc}", file=sys.stderr)
        return 2
    print("A3_EXACT_SCALAR_PREFIX_K2_PASS "
          f"full={result['suites']['full50']['run_count']} "
          f"capacity={result['suites']['capacity22']['run_count']} "
          f"vectors={result['execution']['frozen_lockstep_vectors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
