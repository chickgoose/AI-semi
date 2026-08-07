#!/usr/bin/env python3
"""Generate candidate-owned N=16/32/64 A8 scaling manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def runs_for(source_count: int) -> list[dict[str, object]]:
    width = 4 if source_count == 16 else 8
    height = source_count // width
    geometry = {"width": width, "height": height}
    common = {"fixed_polarity": 1, "fixed_event_type": "spike"}

    def run(name: str, workload: str, seed: int, load: float, cycles: int,
            parameters: dict[str, object]) -> dict[str, object]:
        return {
            "name": f"n{source_count}_{name}",
            "workload": workload,
            "seed": seed,
            "geometry": geometry,
            "load": load,
            "stim_cycles": cycles,
            "parameters": {**common, **parameters},
        }

    return [
        run("sparse", "basic_sparse", 8101, 0.031, 1024,
            {"event_count": source_count}),
        run("simultaneous", "basic_simultaneous", 8102, 0.063, 512,
            {"simultaneous_count": source_count, "occurrence_cycle": 64}),
        run("uniform_l0p90_s1", "uniform", 8201, 0.9, 2048, {}),
        run("uniform_l0p90_s2", "uniform", 8202, 0.9, 2048, {}),
        run("uniform_l1p25_s1", "uniform", 8201, 1.25, 2048, {}),
        run("uniform_l1p25_s2", "uniform", 8202, 1.25, 2048, {}),
        run("rotating_victim", "rotating_victim", 8301, 1.025, 4096,
            {"epoch_cycles": 256, "victim_period": 8, "background_load": 0.9}),
        run("timing_pair", "timing_pair", 8401, 0.625, 2048,
            {"pair_count": 128, "pair_gap": 2,
             "pair_deadline_slack": 16, "background_load": 0.5}),
        run("elephant_mouse", "elephant_mouse", 8501, 0.9, 2048,
            {"elephant_source": 0, "elephant_share": 0.8}),
        run("moving_hotspot", "moving_hotspot", 8601, 0.9, 2048,
            {"dwell_cycles": 128, "hotspot_count": 1, "hot_share": 0.8}),
        run("retrigger", "retrigger", 8701, 0.25, 2048,
            {"trigger_count": 128, "repeats": 4, "retrigger_interval": 1}),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source_count in (16, 32, 64):
        payload = {
            "schema_version": 1,
            "description": (
                f"Candidate-owned A8 N={source_count} scaling subset; "
                "not a replacement for the frozen common N=16 suite"
            ),
            "runs": runs_for(source_count),
        }
        path = args.output_dir / f"manifest.a8-scaling-n{source_count}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
