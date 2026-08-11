#!/usr/bin/env python3
"""Compile the pinned production endpoints once and replay every boundary run."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ENDPOINTS = {"D": "ddr_r1_full", "P": "parallel_r1_full"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def find_verilator():
    for candidate in (os.environ.get("VERILATOR"), "/tmp/a7-sim-bin/verilator", "verilator"):
        if not candidate:
            continue
        result = subprocess.run([candidate, "--version"], text=True, capture_output=True)
        if result.returncode == 0:
            return candidate, result.stdout.strip()
    raise SystemExit("verilator unavailable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--boundary-root", type=Path, required=True)
    parser.add_argument("--boundary-index-sha256", required=True)
    parser.add_argument("--endpoint-commit", required=True)
    parser.add_argument("--endpoint-manifest-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True)
    verilator, version = find_verilator()
    sources = sorted((args.bundle_root / "rtl/candidates/a7_r1_candidate_endpoint").glob("*.sv"))
    tb = args.bundle_root / "a5/a5_w5_production_tb.sv"
    obj = args.output_dir / "obj"
    command = [verilator, "--binary", "--timing", "-Wall", "-Wno-fatal",
               "-Wno-BLKSEQ", "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL",
               "--top-module", "a5_w5_production_tb", "--Mdir", str(obj),
               "-o", "a5_w5_endpoint", *(str(path) for path in sources), str(tb)]
    compiled = subprocess.run(command, cwd=args.bundle_root, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (args.output_dir / "compile.log").write_text(compiled.stdout)
    if compiled.returncode:
        raise SystemExit(compiled.stdout[-8000:])
    binary = obj / "a5_w5_endpoint"
    boundary = json.loads((args.boundary_root / "boundary-index.json").read_text())
    entries = []
    accept_re = re.compile(r"^ACCEPT ([DP]) (\d+) (\d+) (\d+)$")
    retire_re = re.compile(r"^RETIRE ([DP]) (\d+) (\d+) (\d+)$")
    shared_re = re.compile(r"^SHARED (\d+) (\d+) (\d+)$")
    endpoint_re = re.compile(r"^ENDPOINT ([DP]) (\d+) (\d+) (\d+)$")
    reset_re = re.compile(
        r"^RESET_PROBE (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)$")
    for suite in ("full50", "capacity22"):
        for run in boundary["suites"][suite]["runs"]:
            rows = [json.loads(line) for line in
                    (args.boundary_root / run["boundary_file"]).read_text().splitlines()]
            stim = args.output_dir / "stim" / suite / f"{run['name']}.txt"
            stim.parent.mkdir(parents=True, exist_ok=True)
            stim.write_text("".join(f"{r['launch_cycle']} {r['occurrence_cycle']} "
                                    f"{r['presentation_index']} {r['address']}\n" for r in rows))
            executed = subprocess.run([str(binary), f"+STIM={stim}"], text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if executed.returncode:
                raise SystemExit(f"{suite}/{run['name']}:\n{executed.stdout[-8000:]}")
            accepted = {key: [] for key in ENDPOINTS}
            retired = {key: [] for key in ENDPOINTS}
            endpoint_transitions = {}
            shared_transitions = None
            reset_probe = None
            for line in executed.stdout.splitlines():
                match = accept_re.match(line)
                if match:
                    key, index, address, tick = match.groups()
                    accepted[key].append({"presentation_index": int(index),
                                          "address": int(address), "accept_tick": int(tick)})
                    continue
                match = retire_re.match(line)
                if match:
                    key, index, address, tick = match.groups()
                    retired[key].append({"presentation_index": int(index),
                                         "address": int(address), "retire_tick": int(tick)})
                    continue
                match = shared_re.match(line)
                if match:
                    data, control, clock = match.groups()
                    shared_transitions = {"input_data": int(data),
                                          "input_control": int(control),
                                          "base_clocks": int(clock)}
                match = endpoint_re.match(line)
                if match:
                    key, data, control, link_clock = match.groups()
                    endpoint_transitions[key] = {"internal_data": int(data),
                        "internal_control": int(control), "link_clock": int(link_clock)}
                match = reset_re.match(line)
                if match:
                    reset_probe = tuple(map(int, match.groups()))
            if (set(endpoint_transitions) != set(ENDPOINTS)
                    or shared_transitions is None
                    or reset_probe != (2, 3, 0, 0, 1, 1, 1, 1)):
                raise SystemExit(f"{suite}/{run['name']}: incomplete simulator evidence")
            for key, endpoint in ENDPOINTS.items():
                result = {
                    "schema_version": 1, "endpoint": endpoint, "suite": suite,
                    "name": run["name"], "trace_sha256": run["trace_sha256"],
                    "boundary_sha256": run["boundary_sha256"],
                    "timebase_ticks_per_core_cycle": 4,
                    "dut_visible_fields": ["address"],
                    "tb_only_observer_fields": ["presentation_index"],
                    "handshake_contract": "ready_valid_posedge_each_handshake_v1",
                    "clock_contract": "phase_related_synchronous_frozen_source_v1",
                    "retire_contract": "consumer_observation_next_ref_rise_v1",
                    "sink_ready_policy": "always_ready",
                    "accepted": accepted[key], "retired": retired[key],
                    "handshake": {"accepted_on_valid_and_ready_posedge": True,
                        "continuous_valid_back_to_back_supported": True,
                        "held_address_check_applicable": False,
                        "held_address_reason": "always_ready_primary_has_no_stall_sample",
                        "edge_suppression_used": False},
                    "observation": {"consumer_boundary": "next_ref_rise",
                        "phase_related_synchronous": True, "unrelated_cdc_claimed": False,
                        "transmit_commit": "burst_fall" if key == "D" else "parallel_commit",
                        "retire_detector": "charged_seen_toggle",
                        "seen_toggle_charged_before_traffic": True,
                        "fair_boundary": "next_ref_rise_after_transmit_commit"},
                    "reset_probe": {"second_reset_after_complete_drain": True,
                        "second_reset_cycles": reset_probe[0],
                        "post_reset_quiet_cycles": reset_probe[1],
                        "retired_during_second_reset": reset_probe[2],
                        "stale_or_phantom_during_quiet": reset_probe[3],
                        "post_reset_sentinel_delivered": reset_probe[4],
                        "post_reset_sentinel_exact_once": bool(reset_probe[5]),
                        "ready_retire_normalized_during_reset": bool(reset_probe[6]),
                        "ready_retire_normalized_during_quiet": bool(reset_probe[7])},
                    "value_transition_proxy": {"shared": shared_transitions,
                                                "endpoint": endpoint_transitions[key]},
                }
                relative = Path("runs") / endpoint / suite / f"{run['name']}.json"
                artifact = args.output_dir / relative
                write_json(artifact, result)
                entries.append({"endpoint": endpoint, "suite": suite, "name": run["name"],
                                "artifact": str(relative), "artifact_sha256": digest(artifact)})
    write_json(args.output_dir / "endpoint-result-index.json", {
        "schema_version": 1,
        "provenance": {"endpoint_commit": args.endpoint_commit,
            "endpoint_manifest_sha256": args.endpoint_manifest_sha256,
            "boundary_index_sha256": args.boundary_index_sha256,
            "driver_sha256": digest(Path(__file__)),
            "runner_sha256": args.runner_sha256,
            "harness_sha256": digest(tb),
            "compile_log_sha256": digest(args.output_dir / "compile.log"),
            "binary_sha256": digest(binary),
            "simulator": {"identity": version,
                          "executable_sha256": digest(Path(verilator).resolve())}},
        "runs": entries})


if __name__ == "__main__":
    main()
