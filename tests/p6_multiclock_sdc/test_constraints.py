#!/usr/bin/env python3
"""Fail-closed static gates for the P6 multi-clock SDC/MMMC templates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SDC = ROOT / "constraints" / "p6_multiclock.sdc"
MMMC = ROOT / "scripts" / "ppa" / "p6_multiclock_mmmc.tcl"
BUFFERED_REGISTRY = ROOT / "constraints" / "p6_ganghee_golden_registry.json"
RAW_REGISTRY = ROOT / "constraints" / "p6_ganghee_raw_golden_registry.json"
COMMON_REGISTRY = ROOT / "constraints" / "p6_ganghee_common_constraints.json"


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_once(text: str, token: str) -> None:
    count = text.count(token)
    require(count == 1, f"expected one occurrence of {token!r}, found {count}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(data: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    require(actual == expected, f"{label} SHA mismatch: {actual} != {expected}")


def lint_sdc(text: str) -> None:
    required_once = (
        "create_clock -name p6_ref_clk",
        "create_clock -name p6_sample_clk",
        "create_generated_clock -name p6_link_clk",
        "create_clock -name p6_reset_release_clk",
        "set_clock_gating_check -setup",
        "set p6_async_reset_pins",
        "set p6_ref_reset_pins",
        "set p6_link_reset_pins",
        "p6_require_nonempty ref_clock_registers",
        "p6_require_nonempty link_clock_registers",
        "P6_MULTICLOCK_SDC_READY",
    )
    for token in required_once:
        require_once(text, token)

    required = (
        "p6_period_ns / 2.0",
        "p6_period_ns / 4.0",
        "3.0 * $p6_period_ns / 4.0",
        "13.0 * $p6_period_ns / 16.0",
        "15.0 * $p6_period_ns / 16.0",
        "-waveform [list 0.0 $p6_half_cycle_ns]",
        "-waveform [list $p6_quarter_cycle_ns $p6_three_quarter_cycle_ns]",
        "-source $p6_sample_port",
        "-divide_by 1 $p6_clock_port",
        "set_min_pulse_width -high",
        "set_min_pulse_width -low",
        "[get_pins -hierarchical *endpoint/tx/frame_active_o]",
        "set_input_delay -min",
        "set_input_delay -max",
        "-clock p6_reset_release_clk",
        "set_output_delay -min",
        "set_output_delay -max",
        "-clock_fall -add_delay",
        "[all_registers -async_pins]",
        "[all_registers -clock p6_ref_clk -async_pins]",
        "[all_registers -clock p6_link_clk -async_pins]",
        "[all_registers -clock p6_ref_clk]",
        "[all_registers -clock p6_link_clk]",
        "if {$p6_uncertainty_ns >= $p6_quarter_cycle_ns}",
        "if {$p6_gate_setup_ns + $p6_gate_hold_ns >= $p6_half_cycle_ns}",
        "P6 expected five forwarded data ports",
    )
    for token in required:
        require(token in text, f"SDC missing {token!r}")

    require(text.count("-clock_fall -add_delay") == 2,
            "both min and max falling-edge DDR output delays are required")
    require(text.count("set_min_pulse_width") == 2,
            "one high and one low min-pulse command are required")
    require("set_false_path" not in text,
            "P6 SDC must not false-path reset or phase-related crossings")
    require("set_clock_groups" not in text,
            "P6 ref/sample/generated clocks must remain related")
    require("all_outputs" in text and "set_load" in text,
            "every output must retain a physical load")

    for port, label in (
        ("ref_clk_i", "ref_clock_port"),
        ("sample_clk_i", "sample_clock_port"),
        ("rst_n", "reset_port"),
        ("p6_clk_o", "forwarded_clock_port"),
    ):
        pattern = rf"p6_require_singleton\s+{label}\s+.*?\[get_ports\s+{port}\]"
        require(re.search(pattern, text, re.DOTALL) is not None,
                f"{port} lacks a singleton collection assertion")

    required_env = {
        "P6_REF_PERIOD_NS",
        "P6_CLOCK_UNCERTAINTY_NS",
        "P6_INPUT_DELAY_MIN_NS",
        "P6_INPUT_DELAY_MAX_NS",
        "P6_OUTPUT_DELAY_MIN_NS",
        "P6_OUTPUT_DELAY_MAX_NS",
        "P6_RESET_DELAY_MIN_NS",
        "P6_RESET_DELAY_MAX_NS",
        "P6_INPUT_TRANSITION_NS",
        "P6_OUTPUT_LOAD_PF",
        "P6_CLOCK_GATING_SETUP_NS",
        "P6_CLOCK_GATING_HOLD_NS",
        "P6_MIN_PULSE_HIGH_NS",
        "P6_MIN_PULSE_LOW_NS",
    }
    observed = set(re.findall(r"p6_require_env\s+(P6_[A-Z0-9_]+)", text))
    require(observed == required_env,
            f"SDC environment contract mismatch: {sorted(observed ^ required_env)}")


def lint_mmmc(text: str) -> None:
    required_once = (
        "create_library_set -name p6_setup_libset",
        "create_library_set -name p6_hold_libset",
        "create_rc_corner -name p6_setup_rc",
        "create_rc_corner -name p6_hold_rc",
        "create_delay_corner -name p6_setup_corner",
        "create_delay_corner -name p6_hold_corner",
        "create_constraint_mode -name p6_functional",
        "create_analysis_view -name p6_setup_view",
        "create_analysis_view -name p6_hold_view",
        "set_analysis_view -setup [list p6_setup_view] -hold [list p6_hold_view]",
        "P6_MULTICLOCK_MMMC_READY",
    )
    for token in required_once:
        require_once(text, token)

    required = (
        "![file exists $path]",
        "![file isfile $path]",
        "[file size $path] == 0",
        "setup and hold Liberty files must be distinct",
        "setup and hold RC conditions are identical",
        "-library_set p6_setup_libset -rc_corner p6_setup_rc",
        "-library_set p6_hold_libset -rc_corner p6_hold_rc",
        "-constraint_mode p6_functional -delay_corner p6_setup_corner",
        "-constraint_mode p6_functional -delay_corner p6_hold_corner",
        "-qrc_tech $p6_setup_qrc",
        "-qrc_tech $p6_hold_qrc",
    )
    for token in required:
        require(token in text, f"MMMC missing {token!r}")

    required_env = {
        "P6_SETUP_LIBERTY",
        "P6_HOLD_LIBERTY",
        "P6_SETUP_QRC_TECH",
        "P6_HOLD_QRC_TECH",
        "P6_MULTICLOCK_SDC",
        "P6_SETUP_RC_TEMPERATURE_C",
        "P6_HOLD_RC_TEMPERATURE_C",
    }
    observed = set(re.findall(r"p6_mmmc_require_env\s+(P6_[A-Z0-9_]+)", text))
    require(observed == required_env,
            f"MMMC environment contract mismatch: {sorted(observed ^ required_env)}")


def verify_cohort_archive(registry: dict, root_env: str,
                          archive_env: str) -> Path:
    golden_root = Path(os.environ.get(root_env, registry["extracted_root"]))
    golden_archive = Path(os.environ.get(
        archive_env, registry["archive"]["canonical_path"]))
    require(golden_root.is_dir(), f"golden root missing: {golden_root}")
    require(golden_archive.is_file(), f"golden archive missing: {golden_archive}")
    require(sha256(golden_archive) == registry["archive"]["sha256"],
            f"{registry['cohort']} archive SHA mismatch")
    for relative, expected in registry["pinned_files"].items():
        path = golden_root / relative
        require(path.is_file(), f"{registry['cohort']} member missing: {relative}")
        verify_digest(path.read_bytes(), expected,
                      f"{registry['cohort']}:{relative}")

    # Exercise each cohort's digest gate without modifying preserved evidence.
    first_relative, first_digest = next(iter(registry["pinned_files"].items()))
    mutated = (golden_root / first_relative).read_bytes() + b"\n"
    try:
        verify_digest(mutated, first_digest,
                      f"in-memory {registry['cohort']} mutation")
    except ContractError:
        pass
    else:
        raise AssertionError(
            f"{registry['cohort']} in-memory mutation escaped SHA gate")
    return golden_root


def verify_sweeps(golden_root: Path, registry: dict, cohort_suffix: str,
                  prefixes: dict[str, str]) -> None:
    expected_sdc_tail = (
        "set_clock_uncertainty 0.100 [get_clocks clk]\n"
        "set_input_delay  -clock clk 0.250 [remove_from_collection [all_inputs] [get_ports clk]]\n"
        "set_output_delay -clock clk 0.250 [all_outputs]\n"
        "set_load 0.010 [all_outputs]\n"
    )
    for candidate, periods in registry["sweeps_ns"].items():
        run_root = golden_root / f"synth/pnr/resynth_{candidate}_{cohort_suffix}"
        prefix = prefixes[candidate]
        observed_periods: list[float] = []
        for path in run_root.glob(f"{prefix}*.sdc"):
            if path.name.endswith("_out.sdc"):
                continue
            match = re.fullmatch(rf"{re.escape(prefix)}(.+)\.sdc", path.name)
            require(match is not None, f"unexpected golden SDC name: {path.name}")
            period_text = match.group(1)
            expected = (f"create_clock -name clk -period {period_text} [get_ports clk]\n" +
                        expected_sdc_tail)
            require(path.read_text() == expected,
                    f"{registry['cohort']} {candidate} {period_text} SDC changed")
            observed_periods.append(float(period_text))
        require(sorted(observed_periods) == sorted(periods),
                f"{registry['cohort']} {candidate} sweep set mismatch")


def verify_flow_markers(golden_root: Path, cohort_suffix: str,
                        periods: dict[str, str]) -> None:
    for candidate, period in periods.items():
        run_root = golden_root / f"synth/pnr/resynth_{candidate}_{cohort_suffix}"
        mmmc = (run_root / f"mmmc_{period}.tcl").read_text()
        require("slow_vdd1v0_basicCells.lib" in mmmc,
                f"{cohort_suffix} {candidate} Liberty changed")
        require("create_rc_corner -name rc_typical -qrc_tech" in mmmc,
                f"{cohort_suffix} {candidate} QRC command changed")
        require("set_analysis_view -setup {view_slow} -hold {view_slow}" in mmmc,
                f"{cohort_suffix} {candidate} single-view MMMC changed")
        run = (run_root / f"run_{period}.tcl").read_text()
        for token in (
            "floorPlan -r 1.0 0.5 10 10 10 10",
            "addRing -nets {VDD VSS}", "place_opt_design",
            "clock_opt_design", "routeDesign", "extractRC",
        ):
            require(token in run,
                    f"{cohort_suffix} {candidate} flow lost {token}")


def verify_tool_markers(golden_root: Path, cohort_suffix: str,
                        fovea_period: str) -> None:
    run_root = golden_root / f"synth/pnr/resynth_fovea_{cohort_suffix}"
    genus = (run_root / f"genus_{fovea_period}.log").read_text()
    innovus = (run_root / f"innovus_{fovea_period}.log").read_text()
    require("Version: 23.14-s090_1" in genus, "Genus version changed")
    require("lp_insert_clock_gating true" in genus,
            "Genus clock-gating setting changed")
    require("(1.000000, 0.900000, 125.000000)" in genus,
            "Liberty PVT changed")
    require("Version:\tv23.14-s088_1" in innovus,
            "Innovus version changed")
    require("RC-Corner Temperature : 25 Celsius" in innovus,
            "RC temperature changed")
    require("Analysis Mode: MMMC Non-OCV" in innovus,
            "analysis mode changed")


def test_ganghee_golden() -> None:
    raw = json.loads(RAW_REGISTRY.read_text())
    buffered = json.loads(BUFFERED_REGISTRY.read_text())
    common = json.loads(COMMON_REGISTRY.read_text())
    require(raw["schema"] == "p6-ganghee-raw-pnr-golden-v1",
            "unexpected raw-core registry schema")
    require(buffered["schema"] == "p6-ganghee-buffered-pnr-golden-v1",
            "unexpected buffered registry schema")
    require(raw["cohort"] == "raw-core" and
            buffered["cohort"] == "buffered-extension",
            "Ganghee cohort labels changed")
    require(raw["archive"]["sha256"] != buffered["archive"]["sha256"],
            "raw and buffered evidence must remain distinct")

    raw_root = verify_cohort_archive(
        raw, "P6_GANGHEE_RAW_GOLDEN_ROOT", "P6_GANGHEE_RAW_GOLDEN_ARCHIVE")
    buffered_root = verify_cohort_archive(
        buffered, "P6_GANGHEE_BUFFERED_GOLDEN_ROOT",
        "P6_GANGHEE_BUFFERED_GOLDEN_ARCHIVE")
    require(raw_root.resolve() != buffered_root.resolve(),
            "raw and buffered extracted roots must remain distinct")

    verify_sweeps(raw_root, raw, "raw", {
        "fovea": "aer_tx16_trad_rowcol_fovea_",
        "cluster2": "aer_tx16_trad_rowcol_fovea_cluster2_",
    })
    verify_sweeps(buffered_root, buffered, "buffered", {
        "fovea": "aer_fovea_buffered_",
        "cluster2": "aer_cluster2_buffered_",
    })
    verify_flow_markers(raw_root, "raw", {"fovea": "1.2", "cluster2": "1.0"})
    verify_flow_markers(buffered_root, "buffered",
                        {"fovea": "1.0", "cluster2": "1.0"})
    verify_tool_markers(raw_root, "raw", "1.2")
    verify_tool_markers(buffered_root, "buffered", "1.0")

    # The source SDC body is proven common only where period and candidate
    # coincide.  Do not promote sweep coverage or PPA results across cohorts.
    for raw_relative, buffered_relative in (
        ("synth/pnr/resynth_fovea_raw/aer_tx16_trad_rowcol_fovea_1.2.sdc",
         "synth/pnr/resynth_fovea_buffered/aer_fovea_buffered_1.2.sdc"),
        ("synth/pnr/resynth_cluster2_raw/aer_tx16_trad_rowcol_fovea_cluster2_1.0.sdc",
         "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0.sdc"),
    ):
        require((raw_root / raw_relative).read_bytes() ==
                (buffered_root / buffered_relative).read_bytes(),
                f"same-period raw/buffered SDC differs: {raw_relative}")

    for candidate, raw_period, buffered_period in (
        ("fovea", "1.2", "1.2"),
        ("cluster2", "1.0", "1.0"),
    ):
        raw_mmmc = (raw_root /
            f"synth/pnr/resynth_{candidate}_raw/mmmc_{raw_period}.tcl").read_text()
        buffered_mmmc = (buffered_root /
            f"synth/pnr/resynth_{candidate}_buffered/mmmc_{buffered_period}.tcl").read_text()
        normalize_sdc_path = lambda text: re.sub(
            r"-sdc_files \{[^}]+\}", "-sdc_files {COHORT_SDC}", text)
        require(normalize_sdc_path(raw_mmmc) == normalize_sdc_path(buffered_mmmc),
                f"raw/buffered {candidate} MMMC differs beyond SDC path")

    require(raw["common_sdc"] == buffered["common_sdc"],
            "raw/buffered source-SDC intersection changed")
    for field in ("technology", "mmmc", "tools"):
        require(raw[field] == buffered[field],
                f"raw/buffered {field} intersection changed")
    require(common["schema"] == "p6-ganghee-proven-common-v1",
            "unexpected common-constraint registry schema")
    require(common["cohort_archives"] == {
        "raw_core_sha256": raw["archive"]["sha256"],
        "buffered_extension_sha256": buffered["archive"]["sha256"],
    }, "common registry lost exact cohort provenance")
    require(common["sdc"] == {
        "single_source_clock_port": "clk",
        "clock_waveform_fraction": [0.0, 0.5],
        "clock_uncertainty_ns": raw["common_sdc"]["clock_uncertainty_ns"],
        "all_nonclock_input_delay_ns": raw["common_sdc"]["input_delay_ns"],
        "all_output_delay_ns": raw["common_sdc"]["output_delay_ns"],
        "all_output_load_pf": raw["common_sdc"]["output_load_pf"],
        "input_driver_or_transition": None,
        "generated_clock": None,
        "min_pulse_width": None,
        "reset_exception_or_release_clock": None,
    }, "common SDC contains an unproven or changed constraint")
    require(common["technology"] == {
        "liberty": raw["technology"]["liberty"],
        "liberty_pvt": [
            raw["technology"]["liberty_pvt"]["process"],
            raw["technology"]["liberty_pvt"]["voltage_v"],
            raw["technology"]["liberty_pvt"]["temperature_c"],
        ],
        "qrc_tech": raw["technology"]["qrc_tech"],
        "qrc_temperature_c": raw["technology"]["qrc_temperature_c"],
        "tech_lef": raw["technology"]["tech_lef"],
        "macro_lef": raw["technology"]["macro_lef"],
    }, "common technology contains an unproven or changed input")
    require(common["mmmc"] == {
        "analysis_mode": raw["mmmc"]["analysis_mode"],
        "setup_view": raw["mmmc"]["setup_view"],
        "hold_view": raw["mmmc"]["hold_view"],
        "same_single_liberty_and_rc_for_setup_and_hold": True,
    }, "common MMMC contains an unproven or changed assumption")
    require(common["tools"] == {
        "genus": raw["tools"]["genus"],
        "innovus": raw["tools"]["innovus"],
        "genus_clock_gating_insertion": True,
    }, "common tool contract contains an unproven or changed assumption")
    require(common["physical_flow_skeleton"] == [
        "floorPlan -r 1.0 0.5 10 10 10 10",
        "M6/M7 VDD/VSS core ring and sroute",
        "place_opt_design", "clock_opt_design", "routeDesign", "extractRC",
    ], "common physical-flow skeleton changed")
    require(set(common["not_common"]) == {
        "top module and interface boundary",
        "ready/backpressure and queue storage",
        "reset implementation semantics",
        "period sweep set",
        "reported area, timing, power, DRC, connectivity, or qualification status",
    }, "cohort exclusions changed")

    raw_fovea = (raw_root /
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea.v").read_text()
    raw_cluster2 = (raw_root /
        "rtl/ganghee_cluster2/aer_tx16_trad_rowcol_fovea_cluster2.v").read_text()
    buffered_fovea = (buffered_root / "rtl/aer_fovea_buffered.sv").read_text()
    lane_buffer = (buffered_root / "rtl/lane_buffer2.sv").read_text()
    require("input         rst" in raw_fovea and
            "always @(posedge clk)" in raw_fovea and
            "negedge rst" not in raw_fovea,
            "raw Fovea synchronous reset boundary changed")
    require("ready" not in raw_fovea and "ready" not in raw_cluster2 and
            "lane_buffer2" not in raw_fovea and "lane_buffer2" not in raw_cluster2,
            "raw cohort acquired buffered/backpressure logic")
    require("input  logic         ready" in buffered_fovea and
            "lane_buffer2" in buffered_fovea and
            "posedge clk or negedge rst_n" in lane_buffer,
            "buffered cohort boundary changed")


def expect_sdc_reject(mutated: str, label: str) -> None:
    try:
        lint_sdc(mutated)
    except ContractError:
        return
    raise AssertionError(f"SDC mutation escaped: {label}")


def expect_mmmc_reject(mutated: str, label: str) -> None:
    try:
        lint_mmmc(mutated)
    except ContractError:
        return
    raise AssertionError(f"MMMC mutation escaped: {label}")


def test_source_boundary() -> None:
    for relative in (
        "rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6_top.sv",
        "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6_top.sv",
        "rtl/candidates/a4_paired_cortical_column_k2_p6/a4_paired_cortical_column_k2_p6_top.sv",
    ):
        text = (ROOT / relative).read_text()
        for port in ("ref_clk_i", "sample_clk_i", "rst_n", "p6_clk_o", "p6_data_o"):
            require(port in text, f"{relative} lost P6 port {port}")
        require("a7_p6_atomic_bundle_adapter" in text, f"{relative} lost P6 adapter")

    tx = (ROOT / "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv").read_text()
    require("assign p6_clk_o = sample_clk_i & frame_active_o & rst_n;" in tx,
            "P6 gate hierarchy/semantics changed")
    endpoint = (ROOT /
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv").read_text()
    require("a7_p6_pair_tx tx" in endpoint, "P6 tx instance name changed")
    require("a7_p6_pair_rx rx" in endpoint, "P6 rx instance name changed")
    adapter = (ROOT /
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_adapter.sv").read_text()
    require("a7_p6_exact_pair_endpoint endpoint" in adapter,
            "P6 endpoint instance name changed")


def test_sdc_mutation_gate() -> None:
    text = SDC.read_text()
    lint_sdc(text)
    removals = (
        "create_generated_clock -name p6_link_clk",
        "set_clock_gating_check -setup",
        "set_min_pulse_width -high",
        "set_min_pulse_width -low",
        "-clock_fall -add_delay",
        "[all_registers -async_pins]",
        "[all_registers -clock p6_ref_clk -async_pins]",
        "[all_registers -clock p6_link_clk -async_pins]",
        "p6_require_nonempty ref_clock_registers",
        "p6_require_nonempty link_clock_registers",
        "if {$p6_uncertainty_ns >= $p6_quarter_cycle_ns}",
        "P6 expected five forwarded data ports",
    )
    for token in removals:
        expect_sdc_reject(text.replace(token, "MUTATED", 1), f"remove {token}")
    expect_sdc_reject(text + "\nset_false_path -from [get_ports rst_n]\n",
                      "false-path reset")
    expect_sdc_reject(text + "\nset_clock_groups -asynchronous -group p6_ref_clk\n",
                      "async clock group")


def test_mmmc_mutation_gate() -> None:
    text = MMMC.read_text()
    lint_mmmc(text)
    removals = (
        "create_library_set -name p6_hold_libset",
        "create_rc_corner -name p6_hold_rc",
        "create_delay_corner -name p6_hold_corner",
        "create_analysis_view -name p6_hold_view",
        "set_analysis_view -setup [list p6_setup_view] -hold [list p6_hold_view]",
        "setup and hold Liberty files must be distinct",
        "setup and hold RC conditions are identical",
        "[file size $path] == 0",
    )
    for token in removals:
        expect_mmmc_reject(text.replace(token, "MUTATED", 1), f"remove {token}")


def main() -> None:
    test_ganghee_golden()
    test_source_boundary()
    test_sdc_mutation_gate()
    test_mmmc_mutation_gate()
    print("P6_MULTICLOCK_SDC_TESTS_PASS raw_sha=PASS buffered_sha=PASS "
          "cohort_separation=PASS static_mutations=22 tops=3")


if __name__ == "__main__":
    main()
