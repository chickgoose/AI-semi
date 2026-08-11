#!/usr/bin/env python3
"""Pinned same-top synthesis of production A7 W5 parallel4 and DDR2 endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.w5_a7_equal_flow_synth import helper as base  # noqa: E402


A7_FINAL_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
TOP = "a3_w5_r1_endpoint_top"
LOCAL_WRAPPER = Path(__file__).with_name("a3_w5_r1_endpoint_top.sv")
LOCAL_HELPER = Path(__file__).with_name("helper.py")
LOCAL_RUNNER = Path(__file__).resolve()
LOCAL_WRAPPER_SHA256 = "0a830a60665801b457483bf77c7c4b2dfc79a52bb577e857ffeb2c5826b1c562"
EXPECTED_VERILATOR_SHA256 = "672a1ccf3468902f66387049f001b04f254bbcece7d5e816e3861715889bf252"
EXPECTED_VERILATOR_VERSION = "Verilator 5.032 2025-01-01 rev (Debian 5.032-1)"
VERILATOR_ALLOWED_WARNING_CODES = ("DECLFILENAME",)
ABC_ALLOWED_WARNING_LINES = (
    'ABC: Warning: The network is combinational (run "fraig" or "fraig_sweep").',
)
RTL_PATHS = (
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv",
)
UNIT_TB = "tb/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint_tb.sv"
UNIT_FILELIST = "tb/filelists/a7_r1_candidate_endpoint_unit.f"
STRUCTURAL_COMPARE = "tests/a7_r1_candidate_endpoint/structural_compare.py"
PRODUCTION_CONTRACT = "docs/research/a7_r1_candidate_endpoint_contract.md"
PHYSICAL_SDC = "constraints/a7_event_triggered_ddr_burst_link_w4.sdc"
PINS = {
    RTL_PATHS[0]: "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    RTL_PATHS[1]: "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    RTL_PATHS[2]: "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    RTL_PATHS[3]: "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    RTL_PATHS[4]: "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    RTL_PATHS[5]: "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    RTL_PATHS[6]: "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
    UNIT_TB: "b3af920caa6e7242820f1428bf655045fcf2bd8911e4e41c5ae92d4b0c87e950",
    UNIT_FILELIST: "8cdb53fe75c9e1f07c68d5547e85f96c1c6bdbf4ee7ea4acbc451c1a1eedb850",
    STRUCTURAL_COMPARE: "2c0de42e77db3640d306c21405693a66cf5ab12cacf3e25cb0ed5fc2d9521d88",
    PRODUCTION_CONTRACT: "9fb0dbdfb66df6f8306525b5703399b38d38403fb0d9314fb9a1c116a3a6294a",
    PHYSICAL_SDC: "b45f0b07b790aad7f198bf1a5dffb246ced7da7c29f43185ccb5e18c54446a95",
}
DESIGNS = {
    "complete_parallel4_tx_rx": {
        "style": 0,
        "link_data_pins": 4,
        "link_clock_pins": 1,
        "link_signal_pins": 5,
        "expected_state_bits": 18,
        "expected_functional_cells": 27,
    },
    "a7_ddr2_tx_icg_rx_r1": {
        "style": 1,
        "link_data_pins": 2,
        "link_clock_pins": 1,
        "link_signal_pins": 3,
        "expected_state_bits": 20,
        "expected_functional_cells": 29,
    },
}
DIGITAL_PASS_MARKERS = (
    "A7_R1_NOMINAL_PASS",
    "A7_R1_SAME_CYCLE_ADMISSION_RESET_BLOCK_PASS",
    "A7_R1_OUTPUT_AVAILABLE_CYCLE1_PASS",
    "A7_R1_PENDING_OUTPUT_RESET_BLOCK_PASS",
    "A7_R1_CONSUMER_RETIRE_CYCLE2_PASS",
    "A7_R1_CONTINUOUS_VALID_CHANGING_ADDRESS_PASS events=16",
    "A7_R1_BACK_TO_BACK_PASS events=16",
    "A7_R1_GAPPED_PASS events=3",
    "A7_R1_RESET_RELEASE_ARMING_PASS",
    "A7_R1_STALLED_HELD_VALID_PASS events=1",
    "A7_R1_DRAIN_RESET_PASS",
    "A7_R1_INVALID_MIDFRAME_RESET_OBSERVED_PASS",
    "A7_R1_EXACT_ONCE_ORDER_ADDRESS_PASS",
    "A7_R1_ENDPOINT_REGRESSION_PASS",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def execution_identity() -> dict[str, Any]:
    python_executable = Path(sys.executable).resolve()
    return {
        "runner": {
            "path": str(LOCAL_RUNNER.relative_to(REPO_ROOT)),
            "sha256": base.sha256_file(LOCAL_RUNNER),
        },
        "vendored_helper": {
            "path": str(LOCAL_HELPER.relative_to(REPO_ROOT)),
            "sha256": base.sha256_file(LOCAL_HELPER),
            "external_w4_import": False,
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": sys.implementation.cache_tag,
            "executable": str(python_executable),
            "executable_sha256": base.sha256_file(python_executable),
        },
    }


def audit_verilator_warnings(log_text: str) -> dict[str, Any]:
    observed: dict[str, int] = {}
    unexpected = []
    for line in log_text.splitlines():
        match = re.match(r"^%Warning-([A-Z0-9_]+):", line)
        if match:
            code = match.group(1)
            if code not in VERILATOR_ALLOWED_WARNING_CODES:
                unexpected.append(line)
            observed[code] = observed.get(code, 0) + 1
        elif re.search(r"(?i)^(?:%Error|warning:)|unresolved|implicitly declared", line):
            unexpected.append(line)
    if unexpected:
        raise base.AuditError(
            "unexpected Verilator warning/error/unresolved diagnostic: "
            + " | ".join(unexpected[:8])
        )
    return {
        "policy": "only explicitly allowlisted emitted warning codes are accepted",
        "allowed_codes": list(VERILATOR_ALLOWED_WARNING_CODES),
        "observed_allowed_counts": dict(sorted(observed.items())),
        "unexpected_count": 0,
    }


def audit_yosys_abc_warnings(log_text: str) -> dict[str, Any]:
    observed: dict[str, int] = {}
    unexpected = []
    for line in log_text.splitlines():
        stripped = line.strip()
        warning_like = re.search(
            r"(?i)(?:^|:\s*)warning:|unresolved|implicitly declared", stripped
        )
        if not warning_like:
            continue
        if stripped in ABC_ALLOWED_WARNING_LINES:
            observed[stripped] = observed.get(stripped, 0) + 1
        else:
            unexpected.append(stripped)
    if unexpected:
        raise base.AuditError(
            "unexpected Yosys/ABC warning or unresolved diagnostic: "
            + " | ".join(unexpected[:8])
        )
    return {
        "policy": "only exact allowlisted emitted warning lines are accepted",
        "allowed_lines": list(ABC_ALLOWED_WARNING_LINES),
        "observed_allowed_counts": dict(sorted(observed.items())),
        "unexpected_count": 0,
    }


def pinned_inputs(repo: Path) -> dict[str, bytes]:
    actual = base.run_command(
        ["git", "-C", str(repo), "rev-parse", "42377ca^{commit}"]
    ).stdout.strip()
    if actual != A7_FINAL_COMMIT:
        raise base.AuditError(f"A7 final commit mismatch: 42377ca -> {actual}")

    objects: dict[str, bytes] = {}
    for path in PINS:
        objects[path] = base.git_object(repo, A7_FINAL_COMMIT, path)
    for path, content in objects.items():
        if digest(content) != PINS[path]:
            raise base.AuditError(f"pinned SHA mismatch: {path}")

    filelist = objects[UNIT_FILELIST].decode().splitlines()
    expected_filelist = [*RTL_PATHS, UNIT_TB]
    if filelist != expected_filelist:
        raise base.AuditError("production unit filelist/order mismatch")
    contract = objects[PRODUCTION_CONTRACT].decode()
    required_contract = (
        "One launch\noccurs on every rising edge satisfying:",
        "charged one-bit arming register",
        "This is a phase-related synchronous half-cycle path, not a 2FF CDC",
        "The parallel reference uses the identical ready-valid launch qualifier",
        "a same-cycle `launch_fire`",
        "registered `retire_valid_o`",
        "architectural consumer retirement is two cycles after",
        "physical **HOLD**",
    )
    missing = [phrase for phrase in required_contract if phrase not in contract]
    if missing:
        raise base.AuditError(f"production endpoint contract changed: {missing}")
    return objects


def r1_handshakes(valid: list[int], ready: list[int]) -> list[int]:
    if len(valid) != len(ready):
        raise ValueError("valid/ready length mismatch")
    return [cycle for cycle, (v, r) in enumerate(zip(valid, ready)) if v and r]


def rising_edge_suppressed_launches(valid: list[int], ready: list[int]) -> list[int]:
    """Rejected alternative: launches only on a valid rising edge."""
    previous = 0
    launches = []
    for cycle, (v, r) in enumerate(zip(valid, ready)):
        if v and not previous and r:
            launches.append(cycle)
        previous = v
    return launches


def observer_pulses(sampled_toggles: list[int]) -> list[int]:
    """Model the charged ref-rise seen-toggle detector after reset."""
    seen = 0
    pulses = []
    for toggle in sampled_toggles:
        pulses.append(toggle ^ seen)
        seen = toggle
    return pulses


def verify_phase_contract(objects: dict[str, bytes]) -> dict[str, Any]:
    sdc = objects[PHYSICAL_SDC].decode()
    required = (
        "set A7_W4_PERIOD_NS          16.000",
        "set A7_W4_PHASE_NS            4.000",
        "-waveform {0.000 8.000} [get_ports ref_clk_i]",
        "-waveform {4.000 12.000} [get_ports sample_clk_i]",
    )
    missing = [line for line in required if line not in sdc]
    if missing:
        raise base.AuditError(f"frozen phase constraint changed: {missing}")
    observer = objects[RTL_PATHS[4]].decode()
    if "raw_toggle_i ^ seen_toggle_o" not in observer:
        raise base.AuditError("production seen-toggle observer changed")
    toggles = [1, 0, 1, 0, 1, 0]
    pulses = observer_pulses(toggles)
    if pulses != [1] * len(toggles):
        raise base.AuditError("observer cannot preserve one retirement per cycle")
    return {
        "clock_relationship": "strict_phase_related_synchronous",
        "reference_period_ns": 16.0,
        "sample_phase_ns": 4.0,
        "rx_commit_edge": "burst/sample falling edge at 12 ns modulo period",
        "endpoint_output_register_edge": "following ref rising edge at 16 ns modulo period",
        "commit_to_observation_setup_ns": 4.0,
        "endpoint_output_available_ref_cycles_after_launch": 1,
        "real_synchronous_sink_consumes_ref_cycles_after_launch": 2,
        "latency_distinction": (
            "retire_valid/address become endpoint outputs just after the first ref "
            "rise; a separate posedge sink sees their prior values at that edge and "
            "therefore consumes them on the second ref rise"
        ),
        "observer": "one-bit seen-toggle detector plus registered valid/address",
        "observer_state_bits_charged_each_style": 6,
        "continuous_retire_toggle_witness": toggles,
        "continuous_consumer_pulse_witness": pulses,
        "always_ready_sink": True,
        "two_ff_cdc_claim": False,
        "unrelated_clocks_supported": False,
        "backpressure_supported_at_consumer": False,
    }


def verify_r1_contract(objects: dict[str, bytes]) -> dict[str, Any]:
    qualifier = objects[RTL_PATHS[0]].decode()
    tx = objects[RTL_PATHS[2]].decode()
    parallel = objects[RTL_PATHS[6]].decode()
    if "assign event_ready_o = rst_n & reset_release_armed_q;" not in qualifier:
        raise base.AuditError("production reset-arming ready contract changed")
    if "assign launch_fire_o = event_valid_i & event_ready_o;" not in qualifier:
        raise base.AuditError("production R1 launch equation changed")
    if "if (launch_fire_i)\n        event_addr_q <= event_addr_i;" not in tx:
        raise base.AuditError("DDR per-handshake address load changed")
    if "if (launch_fire)\n        link_data_o <= event_addr_i;" not in parallel:
        raise base.AuditError("parallel per-handshake address load changed")
    valid = [1, 1, 1, 0, 1]
    ready = [1, 1, 1, 1, 1]
    legal = r1_handshakes(valid, ready)
    rejected = rising_edge_suppressed_launches(valid, ready)
    if legal != [0, 1, 2, 4] or rejected != [0, 4]:
        raise base.AuditError("R1 directed contract witness changed")
    return {
        "contract": "one frame per event_valid_i && event_ready_o ref_clk_i posedge",
        "reset_release_arming_state_bits_charged_each_style": 1,
        "first_ref_edge_after_reset_release_accepts": False,
        "continuous_valid_new_address_each_accepted_cycle": "LEGAL",
        "stable_requirement": "transaction held stable only while ready is low",
        "directed_valid": valid,
        "directed_ready": ready,
        "legal_handshake_cycles": legal,
        "edge_suppressed_cycles": rejected,
        "edge_suppression_loses_legal_frames": len(legal) - len(rejected),
        "one_shot_qualifier_required": False,
        "independent_qualifier_rtl_implemented": False,
        "valid_edge_one_shot_state_bits_charged": 0,
        "production_reset_arming_qualifier_state_bits_charged": 1,
        "reason": (
            "both production TX endpoints share the charged reset arming qualifier "
            "and load one frame on every subsequent valid/ready posedge; a valid-edge "
            "one-shot would violate R1"
        ),
    }


def verify_drain_contract(objects: dict[str, bytes]) -> dict[str, Any]:
    ddr = objects[RTL_PATHS[5]].decode()
    parallel = objects[RTL_PATHS[6]].decode()
    common_terms = (
        "~launch_fire", "~(raw_retire_toggle ^ seen_retire_toggle)",
        "~retire_valid_o",
    )
    for name, source, active, clock in (
        ("DDR", ddr, "~frame_active", "~burst_clk_o"),
        ("parallel", parallel, "~frame_active_q", "~link_strobe_o"),
    ):
        missing = [term for term in (*common_terms, active, clock) if term not in source]
        if missing:
            raise base.AuditError(f"{name} fail-closed drain terms missing: {missing}")
    return {
        "same_cycle_launch_guarded": True,
        "active_frame_and_link_clock_guarded": True,
        "unobserved_raw_toggle_guarded": True,
        "registered_pending_valid_guarded_until_sink_sample": True,
        "drain_guard_cells_each_style": 4,
        "drain_guard_cell_attribution": "inherited_owner_accounting",
        "independently_derived_from_pinned_base_blobs": False,
        "note": (
            "W5 independently synthesizes final charged totals 29/27; the common "
            "four-cell decomposition is copied from the pinned owner contract because "
            "this audit does not pin and subtract a pre-guard base blob"
        ),
    }


def percentile95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def analyze(netlist: dict[str, Any]) -> dict[str, Any]:
    module = netlist["modules"][TOP]
    cells = module["cells"]
    ports = module["ports"]
    seq: set[str] = set()
    comb: set[str] = set()
    inputs: dict[str, list[int]] = {}
    outputs: dict[str, list[int]] = {}
    types: dict[str, int] = {}
    seq_bits = 0
    latch_bits = 0
    for name, cell in cells.items():
        cell_type = cell["type"]
        directions = cell.get("port_directions")
        if not directions:
            raise base.AuditError(f"cell lacks directions: {name} {cell_type}")
        types[cell_type] = types.get(cell_type, 0) + 1
        inputs[name] = [
            bit for port, direction in directions.items() if direction == "input"
            for bit in base.numeric_bits(cell["connections"][port])
        ]
        outputs[name] = [
            bit for port, direction in directions.items() if direction == "output"
            for bit in base.numeric_bits(cell["connections"][port])
        ]
        sequential = "DFF" in cell_type or "LATCH" in cell_type
        if sequential:
            seq.add(name)
            q_bits = [
                bit for port, direction in directions.items()
                if direction == "output" and port.upper().startswith("Q")
                for bit in base.numeric_bits(cell["connections"][port])
            ]
            seq_bits += len(q_bits)
            if "LATCH" in cell_type:
                latch_bits += len(q_bits)
        else:
            comb.add(name)
    nongeneric = sorted(cell_type for cell_type in types if not cell_type.startswith("$_"))
    if nongeneric:
        raise base.AuditError(f"non-generic residual cell types: {nongeneric}")
    if latch_bits != 1:
        raise base.AuditError(f"expected exactly one charged ICG latch bit, got {latch_bits}")

    drivers: dict[int, str] = {}
    for name in comb:
        for bit in outputs[name]:
            if bit in drivers:
                raise base.AuditError(f"multiple comb drivers for bit {bit}")
            drivers[bit] = name
    dependencies = {
        name: {drivers[bit] for bit in inputs[name] if bit in drivers}
        for name in comb
    }
    depth: dict[str, int] = {}
    pending = set(comb)
    while pending:
        ready = sorted(name for name in pending if dependencies[name] <= depth.keys())
        if not ready:
            raise base.AuditError("combinational cycle in mapped netlist")
        for name in ready:
            depth[name] = 1 + max(
                (depth[parent] for parent in dependencies[name]), default=0
            )
            pending.remove(name)

    fanout: dict[int, int] = {}
    for bits in inputs.values():
        for bit in bits:
            fanout[bit] = fanout.get(bit, 0) + 1
    for port in ports.values():
        if port["direction"] == "output":
            for bit in base.numeric_bits(port["bits"]):
                fanout[bit] = fanout.get(bit, 0) + 1
    clock_reset = {
        bit for port_name in ("ref_clk_i", "sample_clk_i", "rst_n")
        for bit in base.numeric_bits(ports[port_name]["bits"])
    }
    data_fanouts = [count for bit, count in fanout.items() if bit not in clock_reset]
    clock_reset_loads = sum(fanout.get(bit, 0) for bit in clock_reset)
    sink_pins = sum(len(bits) for bits in inputs.values())
    return {
        "total_cells": len(cells),
        "sequential_bits": seq_bits,
        "latch_bits": latch_bits,
        "dff_bits": seq_bits - latch_bits,
        "comb_cells": len(comb),
        "comb_depth_cells": max(depth.values(), default=0),
        "max_fanout_all": max(fanout.values(), default=0),
        "max_fanout_data": max(data_fanouts, default=0),
        "p95_fanout_data": percentile95(data_fanouts),
        "data_nets_fanout_ge4": sum(value >= 4 for value in data_fanouts),
        "wire_sink_pin_proxy": sink_pins,
        "wire_data_sink_pin_proxy": sink_pins - clock_reset_loads,
        "cell_types": dict(sorted(types.items())),
    }


def recipe(source_paths: list[Path], work: Path, abc: Path, style: int) -> str:
    commands = [
        "read_verilog -sv -DSYNTHESIS " + " ".join(map(str, source_paths)),
        f"chparam -set STYLE {style} {TOP}",
        f"hierarchy -check -top {TOP}",
        "proc",
        "flatten",
        "opt",
        "delete t:$scopeinfo",
        "clean -purge",
        "check -assert",
        f"tee -o {work / 'functional_stat.json'} stat -json -top {TOP}",
        f"synth -top {TOP} -flatten -noabc",
        "delete t:$scopeinfo",
        f"abc -exe {abc} -g simple",
        "clean -purge",
        "check -assert",
        f"tee -o {work / 'stat.json'} stat -json -top {TOP}",
        f"tee -o {work / 'ltp.txt'} ltp -noff",
        f"write_json {work / 'netlist.json'}",
    ]
    return "; ".join(commands)


def run_digital_regression(
    objects: dict[str, bytes], work: Path, verilator: Path, verilator_root: Path
) -> dict[str, Any]:
    if base.sha256_file(verilator) != EXPECTED_VERILATOR_SHA256:
        raise base.AuditError("Verilator SHA mismatch")
    env = os.environ.copy()
    env["VERILATOR_ROOT"] = str(verilator_root)
    version = base.run_command([str(verilator), "--version"], env=env).stdout.strip()
    if version != EXPECTED_VERILATOR_VERSION:
        raise base.AuditError("Verilator version mismatch")
    work.mkdir(parents=True)
    source_paths = []
    for index, path in enumerate((*RTL_PATHS, UNIT_TB)):
        local = work / f"{index}_{Path(path).name}"
        local.write_bytes(objects[path])
        source_paths.append(local)
    executable = work / "obj" / "a7_r1_unit"
    compile_result = base.run_command(
        [
            str(verilator), "--binary", "--timing", "-Wall", "-Wno-fatal",
            "-Wno-BLKSEQ", "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL",
            "--top-module", "a7_r1_candidate_endpoint_tb",
            "--Mdir", str(work / "obj"), "-o", executable.name,
            *map(str, source_paths),
        ],
        cwd=work,
        env=env,
    )
    (work / "compile.log").write_text(compile_result.stdout, encoding="utf-8")
    warning_audit = audit_verilator_warnings(compile_result.stdout)
    simulation = base.run_command([str(executable)], cwd=work, env=env)
    (work / "simulation.log").write_text(simulation.stdout, encoding="utf-8")
    simulation_diagnostic_audit = audit_verilator_warnings(simulation.stdout)
    lines = simulation.stdout.splitlines()
    for marker in DIGITAL_PASS_MARKERS:
        if lines.count(marker) != 1:
            raise base.AuditError(f"digital regression exact PASS missing/duplicate: {marker}")
    if any("%Error" in line or "$fatal" in line for line in lines):
        raise base.AuditError("digital regression error/fatal diagnostic observed")
    return {
        "status": "PASS",
        "pinned_tb_sha256": PINS[UNIT_TB],
        "exact_pass_markers": list(DIGITAL_PASS_MARKERS),
        "verilator_version": version,
        "verilator_sha256": EXPECTED_VERILATOR_SHA256,
        "warning_audit": warning_audit,
        "simulation_diagnostic_audit": simulation_diagnostic_audit,
        "covers": [
            "same-cycle admission drain guard",
            "output availability at cycle 1",
            "pending-valid drain guard",
            "synchronous consumption at cycle 2",
            "continuous 16-event exact-once/order/address",
        ],
    }


def synthesize(
    *, name: str, config: dict[str, int], sources: dict[str, bytes],
    work: Path, yosys: Path, abc: Path, lib_dir: Path,
) -> dict[str, Any]:
    work.mkdir(parents=True)
    source_paths = []
    for index, path in enumerate(RTL_PATHS):
        local = work / f"{index}_{Path(path).name}"
        local.write_bytes(sources[path])
        source_paths.append(local)
    wrapper = work / f"{len(source_paths)}_{LOCAL_WRAPPER.name}"
    wrapper_bytes = LOCAL_WRAPPER.read_bytes()
    if digest(wrapper_bytes) != LOCAL_WRAPPER_SHA256:
        raise base.AuditError("local audit wrapper SHA mismatch")
    wrapper.write_bytes(wrapper_bytes)
    source_paths.append(wrapper)
    script = recipe(source_paths, work, abc, config["style"])
    log = work / "yosys.log"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(lib_dir)
    env["TMPDIR"] = str(work)
    result = base.run_command(
        [str(yosys), "-l", str(log), "-p", script], cwd=work, env=env
    )
    log_text = log.read_text()
    warning_audit = audit_yosys_abc_warnings(log_text)
    if "Found and reported 0 problems." not in log_text:
        raise base.AuditError(f"Yosys check marker absent in {name}")
    netlist = json.loads((work / "netlist.json").read_text())
    metrics = analyze(netlist)
    stat = json.loads((work / "stat.json").read_text())["modules"][f"\\{TOP}"]
    functional_stat = json.loads(
        (work / "functional_stat.json").read_text()
    )["modules"][f"\\{TOP}"]
    if stat["num_cells"] != metrics["total_cells"]:
        raise base.AuditError(f"stat/netlist cell mismatch in {name}")
    if stat["num_processes"] or stat["num_memories"]:
        raise base.AuditError(f"residual process/memory in {name}")
    if metrics["sequential_bits"] != config["expected_state_bits"]:
        raise base.AuditError(
            f"state mismatch in {name}: {metrics['sequential_bits']}"
        )
    if functional_stat["num_cells"] != config["expected_functional_cells"]:
        raise base.AuditError(
            f"scopeinfo-free functional-cell mismatch in {name}: "
            f"{functional_stat['num_cells']}"
        )
    canonical_recipe = recipe(
        [Path(f"PINNED_SOURCE_{index}") for index in range(8)],
        Path("WORK"), Path("YOSYS_ABC"), config["style"]
    )
    return {
        "design": name,
        **metrics,
        "functional_cells_scopeinfo_removed": functional_stat["num_cells"],
        "net_count": stat["num_wires"],
        "net_bit_count": stat["num_wire_bits"],
        "canonical_input_bits_including_clocks_reset": 8,
        "canonical_output_bits_including_observation_padding": 12,
        "functional_input_bits_excluding_clocks_reset": 5,
        "functional_output_bits_excluding_link_clock_unpadded": (
            1 + config["link_data_pins"] + 4 + 1 + 1
        ),
        "link_data_pins": config["link_data_pins"],
        "link_clock_pins": config["link_clock_pins"],
        "charged_link_signal_pins": config["link_signal_pins"],
        "logical_unpadded_link_signals": config["link_signal_pins"],
        "link_signal_count_is_physical_pad_count": False,
        "shared_consumer_observer_state_bits": 6,
        "shared_reset_arming_state_bits": 1,
        "qualifier_state_bits": 0,
        "recipe_sha256": digest((canonical_recipe + "\n").encode()),
        "warning_audit": warning_audit,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a7-repo", type=Path, default=Path("/home/chickgoose/projects/a7"))
    parser.add_argument("--yosys", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys"))
    parser.add_argument("--abc", type=Path, default=Path("/tmp/a9-phase4-yosys/usr/bin/yosys-abc"))
    parser.add_argument(
        "--tool-lib-dir", type=Path,
        default=Path("/tmp/a9-phase4-yosys/usr/lib/x86_64-linux-gnu")
    )
    parser.add_argument(
        "--verilator", type=Path,
        default=Path("/tmp/a7-verilator/usr/bin/verilator"),
    )
    parser.add_argument(
        "--verilator-root", type=Path,
        default=Path("/tmp/a7-verilator/usr/share/verilator"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--markdown-output", type=Path,
        help="deterministic Markdown receipt (default: output path with .md suffix)",
    )
    parser.add_argument("--keep-work", type=Path)
    return parser.parse_args(argv)


def render_markdown(report: dict[str, Any]) -> str:
    rows = {row["design"]: row for row in report["runs"]}
    parallel = rows["complete_parallel4_tx_rx"]
    ddr = rows["a7_ddr2_tx_icg_rx_r1"]
    delta = report["delta_ddr_minus_parallel"]
    phase = report["phase_related_consumer_contract"]
    return f"""# W5 A3 equal-flow A7 endpoint synthesis

Decision: **PHYSICAL_HOLD**. The generic mapping is a structural economics
screen, not timing, route, CDC, or power evidence.

## Frozen boundary

- Production W5 endpoint commit: `{A7_FINAL_COMMIT}`
- Same synthesis top: `{TOP}`, STYLE=0 parallel and STYLE=1 DDR
- R1 load: one frame at every `valid && ready` ref-clock rising edge; continuous
  valid with a new address each accepted cycle is supported.
- Both production tops charge the same one-bit reset-release arming qualifier;
  the first safe ref edge arms ready and does not accept a transaction.
- Valid-edge one-shot suppression: rejected (0 additional state bits). The witness has
  four legal handshakes but an edge detector would launch only two.
- Both production styles include the identical six-bit consumer observer: one
  seen-toggle bit, one valid register, and four address registers.
- Frozen clocks are phase-related: {phase['reference_period_ns']:.0f} ns period,
  {phase['sample_phase_ns']:.0f} ns sample phase. RX commits at burst fall and
  endpoint retire output becomes available {phase['commit_to_observation_setup_ns']:.0f}
  ns later at the next ref rise (1 ref cycle after launch). A distinct synchronous
  sink consumes that registered output at the following ref rise (2 cycles after
  launch). This is **not a 2FF CDC claim**.
- Consumer backpressure, unrelated clocks, and handshake/FIFO variants are out
  of scope.

The production endpoint already contains reset arming, the phase-related
seen-toggle observer, the complete parallel reference, and its digital
regression. Its fail-closed drain guard covers same-cycle launch, active frame,
unobserved raw commit, and registered valid pending synchronous consumption.
The local A3 wrapper only selects one production top and pads the
two-bit DDR link observation port; it adds no state or functional behavior.
The runner compiles and executes the pinned production TB and requires every
named PASS marker exactly once before synthesis results can be published.

## Equal-flow generic results

| Metric | Complete parallel4 | A7 DDR2 | DDR - parallel |
|---|---:|---:|---:|
| Charged functional cells, scopeinfo removed | {parallel['functional_cells_scopeinfo_removed']} | {ddr['functional_cells_scopeinfo_removed']} | {delta['functional_cells_scopeinfo_removed']:+d} |
| ABC generic mapped cells | {parallel['total_cells']} | {ddr['total_cells']} | {delta['total_cells']:+d} |
| Combinational cells | {parallel['comb_cells']} | {ddr['comb_cells']} | {delta['comb_cells']:+d} |
| Sequential bits | {parallel['sequential_bits']} | {ddr['sequential_bits']} | {delta['sequential_bits']:+d} |
| DFF bits | {parallel['dff_bits']} | {ddr['dff_bits']} | {delta['dff_bits']:+d} |
| ICG latch bits | {parallel['latch_bits']} | {ddr['latch_bits']} | {delta['latch_bits']:+d} |
| Comb depth proxy | {parallel['comb_depth_cells']} | {ddr['comb_depth_cells']} | {delta['comb_depth_cells']:+d} |
| Nets / net bits | {parallel['net_count']} / {parallel['net_bit_count']} | {ddr['net_count']} / {ddr['net_bit_count']} | {delta['net_count']:+d} / {delta['net_bit_count']:+d} |
| Max data fanout | {parallel['max_fanout_data']} | {ddr['max_fanout_data']} | {delta['max_fanout_data']:+d} |
| Data sink-pin proxy | {parallel['wire_data_sink_pin_proxy']} | {ddr['wire_data_sink_pin_proxy']} | {delta['wire_data_sink_pin_proxy']:+d} |
| Logical unpadded link signals | {parallel['charged_link_signal_pins']} | {ddr['charged_link_signal_pins']} | {delta['charged_link_signal_pins']:+d} |

The 5-versus-3 count means logical unpadded data signals plus the forwarded
clock/strobe; it is not a physical pad or package-pin count. DDR saves two such
signals but costs two sequential bits, two charged functional cells, and four
ABC-mapped cells in this exact boundary. The final totals independently reproduce
27/29; their common four-cell drain-guard decomposition is inherited owner
accounting, not an A3 base-blob subtraction. Generic latch/flop logic does not prove a
characterized ICG/ODDR/IDDR implementation, timing closure, routed wire savings,
or energy benefit; all physical claims remain HOLD.

## Reproduction

```sh
python3 scripts/w5_a7_equal_flow_synth/run.py \\
  --output reports/w5_a7_equal_flow_synth.json
python3 -m unittest scripts.w5_a7_equal_flow_synth.test_run
```

The runner receipts its own bytes, the vendored W5 helper, and Python identity,
and SHA-checks every pinned A7 git object, the frozen SDC, the independent
wrapper, Verilator, Yosys, ABC, and its Tcl runtime. It observes and explicitly
allows only Verilator `DECLFILENAME` diagnostics and ABC's exact combinational-
network warning; this is not a warning-free claim. Missing/duplicate digital
PASS markers, unexpected warnings, unresolved objects, residual
processes/memories, scopeinfo-contaminated functional counts, state-count changes,
drain-contract changes, or Yosys check failures fail closed.
Generated RTL copies, simulator objects, and synthesis products use the system
temporary directory; the source checkout is read-only except for the explicitly
requested receipt output paths.
The JSON and this Markdown file are atomically replaced and byte-deterministic.
"""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        objects = pinned_inputs(args.a7_repo)
        r1 = verify_r1_contract(objects)
        phase = verify_phase_contract(objects)
        drain = verify_drain_contract(objects)
        base.verify_tool(args.yosys, base.EXPECTED_YOSYS_SHA256, "Yosys")
        base.verify_tool(args.abc, base.EXPECTED_ABC_SHA256, "ABC")
        tcl = args.tool_lib_dir / "libtcl8.6.so.0"
        if base.sha256_file(tcl) != base.EXPECTED_TCL_SHA256:
            raise base.AuditError("Tcl runtime SHA mismatch")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(args.tool_lib_dir)
        yosys_version = base.run_command([str(args.yosys), "-V"], env=env).stdout.strip()
        abc_version = base.run_command(
            [str(args.abc), "-q", "version; quit"]
        ).stdout.strip()
        if yosys_version != base.EXPECTED_YOSYS_VERSION:
            raise base.AuditError("Yosys version mismatch")
        if abc_version != base.EXPECTED_ABC_VERSION:
            raise base.AuditError("ABC version mismatch")

        # All generated sources, simulator objects, and synthesis products live
        # outside the source tree so the audit runs from a read-only checkout.
        temporary = Path(tempfile.mkdtemp(prefix="a3-w5-a7-"))
        try:
            digital = run_digital_regression(
                objects, temporary / "digital_regression",
                args.verilator, args.verilator_root,
            )
            rows = [
                synthesize(
                    name=name, config=config, sources=objects,
                    work=temporary / name, yosys=args.yosys, abc=args.abc,
                    lib_dir=args.tool_lib_dir,
                )
                for name, config in DESIGNS.items()
            ]
        finally:
            if args.keep_work:
                if args.keep_work.exists():
                    raise base.AuditError("--keep-work destination exists")
                shutil.copytree(temporary, args.keep_work)
            shutil.rmtree(temporary)

        indexed = {row["design"]: row for row in rows}
        parallel = indexed["complete_parallel4_tx_rx"]
        ddr = indexed["a7_ddr2_tx_icg_rx_r1"]
        report = {
            "schema_version": 2,
            "audit": "a3_w5_a7_equal_flow_full_endpoint_synthesis",
            "status": "PASS",
            "decision": "PHYSICAL_HOLD",
            "provenance": {
                "a7_production_w5_commit": A7_FINAL_COMMIT,
                "git_objects": {path: {"sha256": PINS[path]} for path in PINS},
                "independent_a3_wrapper": {
                    "path": str(LOCAL_WRAPPER.relative_to(REPO_ROOT)),
                    "sha256": LOCAL_WRAPPER_SHA256,
                    "role": "stateless same-top selector and observation padding only",
                },
                "same_top": TOP,
                "source_normalization": "none",
                "execution_identity": execution_identity(),
                "workspace_policy": {
                    "source_checkout": "read_only",
                    "generated_work_directory": "system_temporary_directory",
                    "repository_local_temporary_directories": False,
                    "writes": "only caller-provided receipt output paths",
                },
            },
            "r1_handshake_and_qualifier_gate": r1,
            "phase_related_consumer_contract": phase,
            "fail_closed_drain_contract": drain,
            "production_digital_regression": digital,
            "diagnostic_policy": {
                "warning_free_claim": False,
                "unexpected_warning_or_unresolved_policy": "FAIL_CLOSED",
                "verilator": digital["warning_audit"],
                "yosys_abc_by_design": {
                    row["design"]: row["warning_audit"] for row in rows
                },
            },
            "tool": {
                "yosys_version": yosys_version,
                "yosys_sha256": base.sha256_file(args.yosys),
                "abc_version": abc_version,
                "abc_sha256": base.sha256_file(args.abc),
                "tcl_runtime_sha256": base.sha256_file(tcl),
                "verilator_version": digital["verilator_version"],
                "verilator_sha256": digital["verilator_sha256"],
                "mapping": (
                    "same top/style chparam; proc/flatten/opt and delete scopeinfo "
                    "functional stat; synth -flatten -noabc; "
                    "delete scopeinfo; abc -g simple; clean/check/stat/write_json"
                ),
            },
            "accounting_contract": {
                "boundary": (
                    "complete TX + link + ICG + RX + identical ref-domain "
                    "always-ready consumer observation endpoint"
                ),
                "same_functional_ports": True,
                "same_reset": "asynchronous active-low rst_n",
                "same_load": "one address per valid && ready ref-clock posedge",
                "endpoint_vs_sink_latency": (
                    "retire output available after 1 ref cycle; distinct synchronous "
                    "sink consumes after 2 ref cycles"
                ),
                "state": (
                    "all mapped DFF Q bits plus exactly one generic ICG latch bit; "
                    "each style includes six consumer-observer bits"
                ),
                "link_count_semantics": {
                    "reported_values": {"DDR": 3, "parallel": 5},
                    "meaning": (
                        "logical unpadded link signals: data signals plus forwarded "
                        "link clock/strobe"
                    ),
                    "physical_pad_count": False,
                    "pad_cells_or_package_pins_in_scope": False,
                },
                "excluded": [
                    "upstream producer", "consumer backpressure/FIFO",
                    "unrelated-clock CDC synchronizer",
                    "pads and characterized ICG/ODDR/IDDR cells",
                ],
            },
            "architecture_decision": {
                "primary_endpoint": "strict phase-related synchronous R1",
                "production_w5_endpoint_bound": True,
                "reason": (
                    "42377ca provides both complete tops with identical reset arming, "
                    "fail-closed drain, and ref-rise observer under the pinned 4 ns "
                    "phase relationship"
                ),
                "always_ready_primary_only": True,
                "future_variants": [
                    "consumer backpressure handshake/FIFO", "unrelated-clock CDC"
                ],
            },
            "runs": rows,
            "delta_ddr_minus_parallel": {
                key: ddr[key] - parallel[key]
                for key in (
                    "functional_cells_scopeinfo_removed", "total_cells",
                    "comb_cells", "sequential_bits", "dff_bits",
                    "latch_bits", "comb_depth_cells", "net_count", "net_bit_count",
                    "max_fanout_data", "wire_data_sink_pin_proxy",
                    "charged_link_signal_pins", "qualifier_state_bits",
                )
            },
            "gate": {
                "r1_standard_handshake_preserved": True,
                "one_shot_qualifier_rejected": True,
                "valid_edge_one_shot_state_charged": 0,
                "reset_arming_qualifier_state_charged_each_style": 1,
                "phase_related_observer_charged_equally": True,
                "continuous_one_event_per_cycle_preserved": True,
                "cdc_claim": "NONE",
                "exact_state_and_pin_accounting": True,
                "generic_structural_comparison": "PASS",
                "physical_claim": "HOLD",
                "reason": (
                    "generic latch/gate and opposite-edge flops are not characterized "
                    "ICG/ODDR/IDDR cells; no STA/CDC/RDC/route/power evidence"
                ),
            },
        }
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pending = args.output.with_suffix(args.output.suffix + ".tmp")
        pending.write_text(encoded, encoding="utf-8")
        pending.replace(args.output)
        markdown_output = args.markdown_output or args.output.with_suffix(".md")
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_pending = markdown_output.with_suffix(markdown_output.suffix + ".tmp")
        markdown_pending.write_text(render_markdown(report), encoding="utf-8")
        markdown_pending.replace(markdown_output)
        print(json.dumps({"status": "PASS", "runs": rows, "delta": report[
            "delta_ddr_minus_parallel"]}, indent=2, sort_keys=True))
        return 0
    except (base.AuditError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
