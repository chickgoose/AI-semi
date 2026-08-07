#!/usr/bin/env python3
"""Read-only static extractor for the A2--A9 link/pin normalization audit.

The program prints JSON to stdout and never writes any worktree.  It extracts
parameter defaults from the current native RTL, records binding/profile
availability, and computes the whole-native-boundary functional pin count at
N=16.  It intentionally does not invent a physical link for parallel candidates.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Candidate:
    track: str
    name: str
    rtl: str
    binding: str | None
    profile: str | None
    lanes: int
    output_ready: bool
    ingress_event: bool = True
    event_parameter: str = "ADDR_WIDTH"
    serialization: str = "none_parallel_native_seam"
    codec: str = "none"
    observation_pins: int = 0
    physical_link_pins: int | None = None


CANDIDATES = (
    Candidate("a2", "a2-adaptive-dual-path",
              "rtl/candidates/a2_adaptive_dual_path/a2_adaptive_dual_path_core.sv",
              "rtl/candidates/a2_adaptive_dual_path/a2_adaptive_dual_path_binding.sv",
              "rtl/candidates/a2_adaptive_dual_path/capability_profile.json", 1, True),
    Candidate("a3", "a3-homeostatic-inhibition",
              "rtl/candidates/a3_homeostatic_inhibition/a3_homeostatic_inhibition.sv",
              "rtl/candidates/a3_homeostatic_inhibition/a3_clean_binding.sv",
              "rtl/candidates/a3_homeostatic_inhibition/candidate-profile.json", 1, True),
    Candidate("a4", "a4-quadtree",
              "rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.sv",
              "tests/a4/aer_a4_clean_binding.sv",
              "tests/a4/capability_profile.json", 1, True,
              serialization="parallel_tree_links_internal_only"),
    Candidate("a5", "a5-speculative-pregrant",
              "rtl/candidates/a5_speculative_pregrant/a5_speculative_pregrant_ppa_top.sv",
              "tests/a5_speculative_pregrant/aer_a5_speculative_pregrant_binding.sv",
              "tests/a5_speculative_pregrant/capability_profile.json", 1, True),
    Candidate("a6", "a6-v2-lossless-codec-rejected",
              "rtl/candidates/a6_lossless_aer_codec/a6_v2_lossless_codec_top.sv",
              "rtl/candidates/a6_lossless_aer_codec/a6_v2_candidate_replacement.sv",
              "rtl/candidates/a6_lossless_aer_codec/capability_profile_v2.json", 1, True,
              ingress_event=False, event_parameter="EVENT_WIDTH",
              serialization="two_data_pin_counted_framed_internal_link",
              codec="exact_encoder_plus_decoder", observation_pins=6,
              physical_link_pins=5),
    Candidate("a7", "a7-parallel-event-compactor-k4",
              "rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv",
              "tb/clean/native/a7_parallel_event_compactor_binding.sv",
              None, 4, True, serialization="four_parallel_retire_lanes"),
    Candidate("a8", "a8-age-calendar-wheel",
              "rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel.sv",
              "tests/a8_age_calendar_wheel/a8_clean_binding.sv",
              "rtl/candidates/a8_age_calendar_wheel/candidate_profile.json", 1, False),
    Candidate("a9", "a9-distributed-token-fabric-l4",
              "rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv",
              "rtl/candidates/a9_distributed_token_fabric/a9_clean_binding.sv",
              None, 4, True, serialization="parallel_payload_per_internal_hop"),
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.rstrip("\n")


def parameter(text: str, name: str, fallback: int) -> int:
    match = re.search(rf"parameter\s+(?:int|integer)\s+{name}\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else fallback


def candidate_dirty(status_lines: list[str], candidate: Candidate) -> bool:
    rtl_root = str(Path(candidate.rtl).parent)
    exact_files = {path for path in (candidate.binding, candidate.profile) if path}
    for line in status_lines:
        path = line[3:]
        if (path == rtl_root or path.startswith(rtl_root + "/") or
                path in exact_files):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-root", type=Path,
                        default=Path("/home/chickgoose/projects"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = []
    for candidate in CANDIDATES:
        root = args.projects_root / candidate.track
        rtl_path = root / candidate.rtl
        text = rtl_path.read_text(encoding="utf-8")
        num_sources = parameter(text, "NUM_SOURCES", 16)
        event_width = parameter(text, candidate.event_parameter, 16)
        source_width = max(1, math.ceil(math.log2(num_sources)))
        lanes = candidate.lanes
        ingress_pins = 2 * num_sources
        if candidate.ingress_event:
            ingress_pins += num_sources * event_width
        output_pins = lanes * (1 + event_width + source_width)
        if candidate.output_ready:
            output_pins += lanes
        status_lines = git(root, "status", "--porcelain").splitlines()
        binding_exists = bool(candidate.binding and (root / candidate.binding).is_file())
        profile_exists = bool(candidate.profile and (root / candidate.profile).is_file())
        rows.append({
            "track": candidate.track.upper(), "candidate": candidate.name,
            "sha": git(root, "rev-parse", "HEAD"),
            "worktree_dirty": bool(status_lines),
            "candidate_paths_dirty": candidate_dirty(status_lines, candidate),
            "rtl": candidate.rtl, "binding": candidate.binding,
            "binding_exists": binding_exists, "profile": candidate.profile,
            "profile_exists": profile_exists,
            "num_sources": num_sources, "normalized_retire_lanes": lanes,
            "event_width": event_width, "source_width": source_width,
            "ingress_event_payload_present": candidate.ingress_event,
            "native_output_ready": candidate.output_ready,
            "native_serialization": candidate.serialization,
            "codec_boundary": candidate.codec,
            "whole_native_functional_pins_excluding_clk_reset":
                ingress_pins + output_pins + candidate.observation_pins,
            "semantic_seam_pins_excluding_measurement_observation":
                ingress_pins + output_pins,
            "measurement_observation_pins": candidate.observation_pins,
            "declared_physical_link_pins": candidate.physical_link_pins,
            "common_functional_event_recount": binding_exists,
            "whole_boundary_pin_cycle_recount": binding_exists,
            "link_only_pin_cycle_recount": candidate.physical_link_pins is not None,
        })
    result = {
        "schema_version": 1,
        "formula": "events_per_pin_cycle=completed_events/(measurement_cycles*functional_pin_bits)",
        "rows": rows,
    }
    if args.check:
        expected = {"A2": 310, "A3": 310, "A4": 310, "A5": 310,
                    "A6": 50, "A7": 376, "A8": 309, "A9": 376}
        actual = {row["track"]: row["whole_native_functional_pins_excluding_clk_reset"]
                  for row in rows}
        if actual != expected:
            raise SystemExit(f"pin extraction changed: expected={expected} actual={actual}")
        if len(rows) != 8:
            raise SystemExit("expected eight tracks")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
