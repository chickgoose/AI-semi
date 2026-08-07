#!/usr/bin/env python3
"""Regression gate for the frozen N=16 architecture-neutral trace suite."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.neutrality-n16.json"
GOLDEN = ROOT / "fixtures" / "neutrality_n16_golden.json"
sys.path.insert(0, str(ROOT))

import generate_trace  # noqa: E402


def read_events(directory: Path, name: str) -> list[dict[str, object]]:
    path = directory / f"{name}.events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def strip_address(event: dict[str, object]) -> tuple[object, ...]:
    return (
        event["occurrence_cycle"],
        event["tb_only_event_id"],
        event["polarity"],
        event["event_type"],
        event["relation_id"],
        event["relation_role"],
        event["deadline"],
    )


def assert_source_boundary(events: list[dict[str, object]]) -> None:
    seen: set[tuple[object, object]] = set()
    for event in events:
        key = (event["occurrence_cycle"], event["logical_source"])
        assert key not in seen, f"duplicate source occurrence at {key}"
        seen.add(key)
        assert event["polarity"] == 1
        assert event["event_type"] == "spike"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aer-neutrality-n16-") as temporary:
        first = Path(temporary) / "first"
        second = Path(temporary) / "second"
        first_runs = generate_trace.generate_manifest(MANIFEST, first)
        second_runs = generate_trace.generate_manifest(MANIFEST, second)
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert len(first_runs) == 46
        assert golden["generator_version"] == generate_trace.GENERATOR_VERSION
        assert golden["suite"] == MANIFEST.name
        assert golden["runs"] == [
            {
                "name": metadata["run"]["name"],
                "event_count": metadata["event_count"],
                "trace_sha256": metadata["trace_sha256"],
                "actual_mean_load": metadata["actual_mean_load"],
                "peak_events_per_cycle": metadata["peak_events_per_cycle"],
                "report_group": metadata["report_group"],
            }
            for metadata in first_runs
        ]
        assert [run["trace_sha256"] for run in first_runs] == [
            run["trace_sha256"] for run in second_runs
        ]
        for metadata in first_runs:
            run = metadata["run"]
            assert run["geometry"] == {"width": 4, "height": 4}
            assert run["sink"]["mode"] == "always"
            events = read_events(first, run["name"])
            assert_source_boundary(events)
            assert metadata["actual_mean_load"] == str(
                generate_trace.Decimal(len(events))
                / generate_trace.Decimal(run["stim_cycles"])
            )
            assert metadata["peak_events_per_cycle"] == max(
                Counter(event["occurrence_cycle"] for event in events).values(),
                default=0,
            )

        shapes = [read_events(first, f"shape_b{size}") for size in (1, 4, 16)]
        source_histograms = [
            Counter(event["logical_source"] for event in events) for events in shapes
        ]
        assert source_histograms[0] == source_histograms[1] == source_histograms[2]
        assert [event["logical_source"] for event in shapes[0]] == [
            event["logical_source"] for event in shapes[1]
        ] == [event["logical_source"] for event in shapes[2]]
        assert [len(events) for events in shapes] == [2048, 2048, 2048]
        assert [
            max(Counter(event["occurrence_cycle"] for event in events).values())
            for events in shapes
        ] == [1, 4, 16]

        local = read_events(first, "spatial_local")
        dispersed = read_events(first, "spatial_dispersed")
        assert [strip_address(event) for event in local] == [
            strip_address(event) for event in dispersed
        ]
        assert [event["logical_source"] for event in local] != [
            event["logical_source"] for event in dispersed
        ]
        assert max(Counter(event["occurrence_cycle"] for event in local).values()) == 4
        local_mirror = read_events(first, "spatial_local_mirror")
        assert [strip_address(event) for event in local] == [
            strip_address(event) for event in local_mirror
        ]
        assert [event["logical_source"] for event in local_mirror] == [
            (int(event["logical_source"]) // 4) * 4
            + (3 - (int(event["logical_source"]) % 4))
            for event in local
        ]

        victim_identity = read_events(first, "rotating_victim_identity")
        victim_affine = read_events(first, "rotating_victim_affine")
        assert [strip_address(event) for event in victim_identity] == [
            strip_address(event) for event in victim_affine
        ]
        assert [event["logical_source"] for event in victim_affine] == [
            (5 * int(event["logical_source"]) + 3) % 16
            for event in victim_identity
        ]

        for base_name in ("elephant_mouse", "retrigger"):
            identity = read_events(first, f"{base_name}_identity")
            affine = read_events(first, f"{base_name}_affine")
            assert [strip_address(event) for event in identity] == [
                strip_address(event) for event in affine
            ]
            assert [event["logical_source"] for event in affine] == [
                (5 * int(event["logical_source"]) + 3) % 16 for event in identity
            ]

        sparse_identity = read_events(first, "core_sparse_identity")
        sparse_rotated = read_events(first, "core_sparse_rotate180")
        assert [strip_address(event) for event in sparse_identity] == [
            strip_address(event) for event in sparse_rotated
        ]
        assert [event["logical_source"] for event in sparse_rotated] == [
            15 - int(event["logical_source"]) for event in sparse_identity
        ]

        moving_names = (
            "moving_hotspot_single_s3301",
            "moving_hotspot_multi_disperse_s3301",
            "moving_hotspot_multi_row_s3301",
            "moving_hotspot_multi_column_s3301",
        )
        moving = [read_events(first, name) for name in moving_names]
        assert all(
            [strip_address(event) for event in events]
            == [strip_address(event) for event in moving[0]]
            for events in moving[1:]
        )
        for epoch in range(16):
            anchor = (3301 + epoch * 15) % 16
            anchor_x, anchor_y = anchor % 4, anchor // 4
            dispersed_hot = {
                ((anchor_y + index) % 4) * 4 + ((anchor_x + index) % 4)
                for index in range(4)
            }
            assert len({source % 4 for source in dispersed_hot}) == 4
            assert len({source // 4 for source in dispersed_hot}) == 4

        for seed in (3501, 3502):
            phases = read_events(first, f"phase_transition_s{seed}")
            assert not any(int(event["occurrence_cycle"]) >= 3584 for event in phases)
            counts = Counter((int(event["occurrence_cycle"]) * 8) // 4096 for event in phases)
            assert sum(counts[index] for index in (4, 5)) > sum(
                counts[index] for index in (2, 3)
            ) > sum(counts[index] for index in (0, 1))
            assert counts[6] > counts[7]

        uniform_names = [
            metadata["run"]["name"]
            for metadata in first_runs
            if metadata["run"]["workload"] == "uniform"
        ]
        assert len(uniform_names) == 21
        assert len({name.rsplit("_s", 1)[1] for name in uniform_names}) == 3
        uniform_groups = {
            metadata["report_group"]
            for metadata in first_runs
            if metadata["run"]["workload"] == "uniform"
        }
        assert uniform_groups == {"uniform"}

    print("NEUTRALITY_SELF_TEST_PASS runs=46 n=16 deterministic=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
