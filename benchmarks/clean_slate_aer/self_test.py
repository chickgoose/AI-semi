#!/usr/bin/env python3
"""Self-test for the deterministic clean-slate AER trace generator."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLE_MANIFEST = ROOT / "manifest.example.json"
sys.path.insert(0, str(ROOT))

import generate_trace  # noqa: E402  (import sibling after fixing isolated-mode path)


def directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        file.relative_to(path).as_posix(): file.read_bytes()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check_trace(output: Path, metadata: dict[str, object]) -> None:
    run = metadata["run"]
    geometry = run["geometry"]
    assert run["sink"]["mode"] in {"always", "periodic", "shock"}
    events = read_events(output / metadata["trace_file"])
    assert len(events) == metadata["event_count"]
    assert metadata["event_schema"] == list(generate_trace.EVENT_FIELDS)
    assert "tb_only_event_id" not in metadata["dut_payload_fields"]
    assert "tb_only_event_id" in metadata["tb_only_fields"]
    assert "ready" not in metadata["event_schema"]
    assert [event["tb_only_event_id"] for event in events] == list(range(len(events)))
    assert [event["occurrence_cycle"] for event in events] == sorted(
        event["occurrence_cycle"] for event in events
    )
    for event in events:
        assert tuple(event) == generate_trace.EVENT_FIELDS
        assert 0 <= event["occurrence_cycle"] < run["stim_cycles"]
        assert 0 <= event["logical_source"] < geometry["width"] * geometry["height"]
        assert 0 <= event["x"] < geometry["width"]
        assert 0 <= event["y"] < geometry["height"]
        assert event["logical_source"] == event["y"] * geometry["width"] + event["x"]
        assert event["polarity"] in (-1, 1)
        assert isinstance(event["event_type"], str) and event["event_type"]
        assert event["deadline"] >= event["occurrence_cycle"]


def check_workload_signatures(output: Path) -> None:
    simultaneous = read_events(output / "basic_simultaneous.events.jsonl")
    assert len({event["occurrence_cycle"] for event in simultaneous}) == 1

    fanin = read_events(output / "global_fanin.events.jsonl")
    first_cycle = min(event["occurrence_cycle"] for event in fanin)
    first_burst = [event for event in fanin if event["occurrence_cycle"] == first_cycle]
    assert len(first_burst) == 64
    assert len({event["logical_source"] for event in first_burst}) == 64

    cluster = read_events(output / "local_cluster.events.jsonl")
    assert all(abs(event["x"] - 4) <= 1 and abs(event["y"] - 4) <= 1 for event in cluster)

    retrigger = read_events(output / "retrigger.events.jsonl")
    per_source_cycles: dict[int, list[int]] = defaultdict(list)
    for event in retrigger:
        per_source_cycles[event["logical_source"]].append(event["occurrence_cycle"])
    assert any(
        any(right - left == 1 for left, right in zip(cycles, cycles[1:]))
        for cycles in per_source_cycles.values()
    )

    pairs = read_events(output / "timing_pair.events.jsonl")
    type_counts = Counter(event["event_type"] for event in pairs)
    assert type_counts["timing_a"] == type_counts["timing_b"] == 24

    shock = read_events(output / "backpressure_shock.events.jsonl")
    shock_count = sum(192 <= event["occurrence_cycle"] < 288 for event in shock)
    background_count = len(shock) - shock_count
    assert shock_count > background_count


def check_seed_sensitivity(root: Path) -> None:
    raw = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    uniform = next(run for run in raw["runs"] if run["workload"] == "uniform")
    manifest_a = {"schema_version": 1, "runs": [dict(uniform, name="seed_a", seed=123)]}
    manifest_b = {"schema_version": 1, "runs": [dict(uniform, name="seed_b", seed=124)]}
    path_a = root / "seed-a.json"
    path_b = root / "seed-b.json"
    path_a.write_text(json.dumps(manifest_a), encoding="utf-8")
    path_b.write_text(json.dumps(manifest_b), encoding="utf-8")
    out_a = root / "seed-a"
    out_b = root / "seed-b"
    result_a = generate_trace.generate_manifest(path_a, out_a)[0]
    result_b = generate_trace.generate_manifest(path_b, out_b)[0]
    assert result_a["trace_sha256"] != result_b["trace_sha256"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="clean-slate-aer-selftest-") as temporary:
        root = Path(temporary)
        first = root / "first"
        second = root / "second"
        first_results = generate_trace.generate_manifest(EXAMPLE_MANIFEST, first)
        generate_trace.generate_manifest(EXAMPLE_MANIFEST, second)
        assert directory_bytes(first) == directory_bytes(second), "generation is not byte deterministic"
        assert {result["run"]["workload"] for result in first_results} == set(
            generate_trace.WORKLOADS
        )
        for metadata in first_results:
            check_trace(first, metadata)
        check_workload_signatures(first)
        check_seed_sensitivity(root)
    print(f"SELF_TEST_PASS workloads={len(generate_trace.WORKLOADS)} deterministic=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
