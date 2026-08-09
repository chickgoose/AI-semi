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


def phase_events(
    events: list[dict[str, object]], start: int, length: int
) -> list[dict[str, object]]:
    return [
        event for event in events
        if start <= int(event["occurrence_cycle"]) < start + length
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aer-neutrality-n16-") as temporary:
        first = Path(temporary) / "first"
        second = Path(temporary) / "second"
        first_runs = generate_trace.generate_manifest(MANIFEST, first)
        second_runs = generate_trace.generate_manifest(MANIFEST, second)
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert len(first_runs) == 50
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
            assert metadata["event_identity_mode"] == "address_only"
            assert metadata["dut_address_fields"] == ["logical_source"]
            assert metadata["dut_payload_fields"] == []
            assert {"polarity", "event_type"} <= set(metadata["trace_metadata_fields"])
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

        pairwise_identity = read_events(first, "pairwise_contention_identity")
        pairwise_affine = read_events(first, "pairwise_contention_affine")
        expected_pairs = 16 * 15 // 2
        for events in (pairwise_identity, pairwise_affine):
            relation_counts = Counter(event["relation_id"] for event in events)
            assert len(relation_counts) == expected_pairs * 2
            assert set(relation_counts.values()) == {2}
            assert max(Counter(event["occurrence_cycle"] for event in events).values()) == 2
        assert [strip_address(event) for event in pairwise_identity] == [
            strip_address(event) for event in pairwise_affine
        ]

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

        mixed_identity = read_events(first, "mixed_phase_always_ready_identity")
        mixed_reversed = read_events(first, "mixed_phase_always_ready_bit_reverse")
        assert [strip_address(event) for event in mixed_identity] == [
            strip_address(event) for event in mixed_reversed
        ]
        assert [int(event["logical_source"]) for event in mixed_reversed] == [
            int(f"{int(event['logical_source']):04b}"[::-1], 2)
            for event in mixed_identity
        ]

        u_bernoulli = phase_events(mixed_identity, 0, 640)
        u_smooth = phase_events(mixed_identity, 640, 640)
        assert Counter(event["logical_source"] for event in u_bernoulli) == Counter(
            event["logical_source"] for event in u_smooth
        )

        s_persistent = phase_events(mixed_identity, 1280, 256)
        s_rotating = phase_events(mixed_identity, 1536, 256)
        for events, start in ((s_persistent, 1280), (s_rotating, 1536)):
            assert Counter(event["logical_source"] for event in events) == Counter(
                {source: 64 for source in range(16)}
            )
            assert Counter(event["occurrence_cycle"] for event in events) == Counter(
                {cycle: 4 for cycle in range(start, start + 256)}
            )
        for block in range(4):
            persistent_sources = {
                int(event["logical_source"])
                for event in s_persistent
                if 1280 + block * 64 <= int(event["occurrence_cycle"])
                < 1280 + (block + 1) * 64
            }
            assert persistent_sources == {block + 4 * row for row in range(4)}

        h_a = phase_events(mixed_identity, 1792, 768)
        h_b = phase_events(mixed_identity, 2560, 768)
        h_a_replay = phase_events(mixed_identity, 3328, 768)
        for events, start in ((h_a, 1792), (h_b, 2560), (h_a_replay, 3328)):
            assert Counter(event["occurrence_cycle"] for event in events) == Counter(
                {cycle: 2 for cycle in range(start, start + 768)}
            )
        assert [
            (int(event["occurrence_cycle"]) - 1792, event["logical_source"])
            for event in h_a
        ] == [
            (int(event["occurrence_cycle"]) - 3328, event["logical_source"])
            for event in h_a_replay
        ]
        map_a = [5, 6, 9, 10] + [
            source for source in range(16) if source not in {5, 6, 9, 10}
        ]
        map_b = [0, 5, 10, 15] + [
            source for source in range(16) if source not in {0, 5, 10, 15}
        ]
        inverse_a = {source: rank for rank, source in enumerate(map_a)}
        inverse_b = {source: rank for rank, source in enumerate(map_b)}
        assert [inverse_a[int(event["logical_source"])] for event in h_a] == [
            inverse_b[int(event["logical_source"])] for event in h_b
        ]

    print("NEUTRALITY_SELF_TEST_PASS runs=50 capacity=22 n=16 address_only=1 deterministic=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
