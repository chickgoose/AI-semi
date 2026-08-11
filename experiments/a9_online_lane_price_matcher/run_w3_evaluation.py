#!/usr/bin/env python3
"""Run the W3 model gate against the canonical address-only cap22 suite."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from lane_price_model import Event, ExactKGrant, FlatRoundRobin, LanePriceMatcher


CANONICAL = {
    "full": (
        "benchmarks/clean_slate_aer/manifest.neutrality-n16.json",
        "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    ),
    "capacity": (
        "benchmarks/clean_slate_aer/manifest.multilane-n16.json",
        "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
    ),
    "generator": (
        "benchmarks/clean_slate_aer/generate_trace.py",
        "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
    ),
}
EXPECTED_CAPACITY_COUNT = 22
MOVING_NAMES = {
    "moving_hotspot_single_s3301",
    "moving_hotspot_single_s3302",
    "moving_hotspot_multi_disperse_s3301",
    "moving_hotspot_multi_row_s3301",
    "moving_hotspot_multi_column_s3301",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, (relative, expected) in CANONICAL.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"canonical {name} SHA mismatch: {path}")
        paths[name] = path
    capacity = json.loads(paths["capacity"].read_text(encoding="utf-8"))
    full = json.loads(paths["full"].read_text(encoding="utf-8"))
    if len(capacity.get("runs", [])) != EXPECTED_CAPACITY_COUNT:
        raise SystemExit("canonical capacity manifest is not cap22")
    if len(full.get("runs", [])) != 50:
        raise SystemExit("canonical full manifest is not full50")
    full_by_name = {run["name"]: run for run in full["runs"]}
    for run in capacity["runs"]:
        if full_by_name.get(run["name"]) != run:
            raise SystemExit(f"capacity run differs from full50: {run['name']}")
    if not MOVING_NAMES.issubset(full_by_name):
        raise SystemExit("full50 lacks required moving-hotspot controls")
    return paths


def generate(generator: Path, manifest: Path, output: Path) -> None:
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
    result = subprocess.run(
        [sys.executable, "-I", str(generator), "--manifest", str(manifest),
         "--output-dir", str(output)],
        cwd=generator.parent,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode:
        raise SystemExit(f"trace generation failed:\n{result.stdout}")


def load_trace(path: Path) -> dict[int, list[Event]]:
    result: dict[int, list[Event]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["logical_source"] != row["y"] * 4 + row["x"]:
                raise SystemExit(f"address-only invariant failed in {path}")
            event = Event(
                occurrence=int(row["occurrence_cycle"]),
                event_id=int(row["tb_only_event_id"]),
                source=int(row["logical_source"]),
            )
            result.setdefault(event.occurrence, []).append(event)
    return result


def row_for(suite: str, name: str, model_name: str, fabric, stim_cycles: int) -> dict[str, object]:
    metrics = fabric.metrics
    delivered = max(1, metrics.delivered)
    return {
        "suite": suite,
        "trace": name,
        "model": model_name,
        "generated": metrics.generated,
        "overrun": metrics.overrun,
        "accepted": metrics.accepted,
        "delivered": metrics.delivered,
        "measured_event_per_cycle": f"{metrics.measured_delivered / stim_cycles:.6f}",
        "p95": metrics.percentile(metrics.occurrence_latencies, 95),
        "p99": metrics.percentile(metrics.occurrence_latencies, 99),
        "jain_service_ratio": f"{metrics.jain_service_ratio(fabric.generated_by_source):.6f}",
        "price_updates": metrics.price_updates,
        "price_bit_toggles_per_event": f"{metrics.price_bit_toggles / delivered:.6f}",
        "proposal_rejects": metrics.proposal_rejects,
        "escape_entries": metrics.escape_entries,
        "max_escape_wait": metrics.max_escape_wait,
        "control_state_bits": fabric.control_state_bits(),
        "comparator_depth_proxy": fabric.comparator_depth_proxy(),
    }


def evaluate_trace(suite: str, run: dict, trace_dir: Path) -> list[dict[str, object]]:
    events = load_trace(trace_dir / f"{run['name']}.events.jsonl")
    rows = []
    for model_name, model in (
        ("lane_price", LanePriceMatcher(16, 4)),
        ("price_off", LanePriceMatcher(16, 4, price_enabled=False)),
        ("exact_k", ExactKGrant(16, 4)),
        ("flat_rr", FlatRoundRobin(16, 4)),
    ):
        model.run(events, int(run["stim_cycles"]))
        rows.append(row_for(suite, run["name"], model_name, model, int(run["stim_cycles"])))
    return rows


def directed_rows() -> list[dict[str, object]]:
    scenarios: list[tuple[str, dict[int, list[Event]], int, object]] = []
    alternating = {
        cycle: [
            Event(cycle, cycle * 4 + index, source)
            for index, source in enumerate(
                range(0, 8) if (cycle // 8) & 1 == 0 else range(8, 16)
            )
            if index < 4
        ]
        for cycle in range(512)
    }
    scenarios.append(("alternating_symmetry", alternating, 512, None))
    adversarial = {
        cycle: [Event(cycle, cycle * 16 + source, source) for source in range(16)]
        for cycle in range(256)
    }
    scenarios.append(("adversarial_all_sources", adversarial, 256, None))
    stall_events = {
        cycle: [Event(cycle, cycle * 4 + source, source) for source in range(4)]
        for cycle in range(256)
    }
    scenarios.append((
        "alternating_lane_stall", stall_events, 256,
        lambda cycle: [((cycle + lane) & 1) == 0 for lane in range(4)],
    ))

    rows: list[dict[str, object]] = []
    for name, events, cycles, ready_fn in scenarios:
        for model_name, model in (
            ("lane_price", LanePriceMatcher(16, 4)),
            ("price_off", LanePriceMatcher(16, 4, price_enabled=False)),
            ("exact_k", ExactKGrant(16, 4)),
            ("flat_rr", FlatRoundRobin(16, 4)),
        ):
            model.run(events, cycles, ready_fn=ready_fn)
            rows.append(row_for("directed", name, model_name, model, cycles))

    # Force every eligible source to observe lane zero as the unique cheapest
    # price before the run.  This is state-directed, not a special proposal rule.
    model = LanePriceMatcher(16, 4, reject_bits=2)
    model.prices = [0, model.price_max, model.price_max, model.price_max]
    eligible = list(model.incoming[0])
    events = {0: [Event(0, index, source) for index, source in enumerate(eligible)]}
    model.run(events, 1)
    rows.append(row_for("directed", "all_same_cheapest", "lane_price", model, 1))
    return rows


def aggregate(rows: list[dict[str, object]], suite: str, model: str) -> dict[str, float]:
    chosen = [row for row in rows if row["suite"] == suite and row["model"] == model]
    return {
        "generated": sum(int(row["generated"]) for row in chosen),
        "overrun": sum(int(row["overrun"]) for row in chosen),
        "delivered": sum(int(row["delivered"]) for row in chosen),
        "throughput_sum": sum(float(row["measured_event_per_cycle"]) for row in chosen),
        "worst_p99": max((int(row["p99"]) for row in chosen), default=0),
        "worst_fairness": min((float(row["jain_service_ratio"]) for row in chosen), default=1.0),
        "toggles_per_event": (
            sum(float(row["price_bit_toggles_per_event"]) * int(row["delivered"]) for row in chosen)
            / max(1, sum(int(row["delivered"]) for row in chosen))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    paths = verify_inputs(args.suite_root)
    full = json.loads(paths["full"].read_text(encoding="utf-8"))
    capacity = json.loads(paths["capacity"].read_text(encoding="utf-8"))
    moving_runs = [run for run in full["runs"] if run["name"] in MOVING_NAMES]

    with tempfile.TemporaryDirectory(prefix="a9-w3-") as temporary:
        temp = Path(temporary)
        cap_dir = temp / "cap22"
        moving_dir = temp / "moving"
        moving_manifest = temp / "moving.json"
        moving_manifest.write_text(
            json.dumps({"schema_version": 1, "runs": moving_runs}) + "\n",
            encoding="utf-8",
        )
        generate(paths["generator"], paths["capacity"], cap_dir)
        generate(paths["generator"], moving_manifest, moving_dir)
        rows: list[dict[str, object]] = []
        for run in capacity["runs"]:
            rows.extend(evaluate_trace("cap22", run, cap_dir))
        for run in moving_runs:
            rows.extend(evaluate_trace("moving", run, moving_dir))
        rows.extend(directed_rows())

    fieldnames = list(rows[0])
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    summaries = {
        suite: {model: aggregate(rows, suite, model) for model in ("lane_price", "price_off", "exact_k", "flat_rr")}
        for suite in ("cap22", "moving", "directed")
    }
    candidate = summaries["cap22"]["lane_price"]
    exact = summaries["cap22"]["exact_k"]
    stall_price = next(
        row for row in rows
        if row["suite"] == "directed" and row["trace"] == "alternating_lane_stall"
        and row["model"] == "lane_price"
    )
    stall_off = next(
        row for row in rows
        if row["suite"] == "directed" and row["trace"] == "alternating_lane_stall"
        and row["model"] == "price_off"
    )
    gates = {
        "cap22_complete": sum(row["suite"] == "cap22" and row["model"] == "lane_price" for row in rows) == 22,
        "no_missing_after_drain": candidate["delivered"] + candidate["overrun"] == candidate["generated"],
        "beats_flat_rr_throughput": candidate["throughput_sum"] > summaries["cap22"]["flat_rr"]["throughput_sum"],
        "at_least_90pct_exact_k": candidate["throughput_sum"] >= 0.90 * exact["throughput_sum"],
        "fairness_floor": candidate["worst_fairness"] >= 0.90,
        "price_active_when_stalled": int(stall_price["price_updates"]) > 0,
        "price_delivers_stall_gain": (
            int(stall_price["delivered"]) > int(stall_off["delivered"])
            or int(stall_price["p99"]) < int(stall_off["p99"])
        ),
        "bounded_escape_directed": max(
            int(row["max_escape_wait"])
            for row in rows if row["suite"] == "directed" and row["model"] == "lane_price"
        ) <= max(map(len, LanePriceMatcher(16, 4).incoming)),
        "n64_local_depth": LanePriceMatcher(64, 8).comparator_depth_proxy() < FlatRoundRobin(64, 8).comparator_depth_proxy(),
    }
    result = {
        "canonical": {
            "suite_head": subprocess.run(
                ["/usr/bin/git", "rev-parse", "HEAD"], cwd=args.suite_root,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip(),
            "full50_sha256": CANONICAL["full"][1],
            "cap22_sha256": CANONICAL["capacity"][1],
            "generator_v4_sha256": CANONICAL["generator"][1],
        },
        "summaries": summaries,
        "proxies": {
            "n16_control_state_bits": LanePriceMatcher(16, 4).control_state_bits(),
            "n64_control_state_bits": LanePriceMatcher(64, 8).control_state_bits(),
            "n16_comparator_depth": LanePriceMatcher(16, 4).comparator_depth_proxy(),
            "n64_comparator_depth": LanePriceMatcher(64, 8).comparator_depth_proxy(),
            "n16_max_lane_adjacency": max(map(len, LanePriceMatcher(16, 4).incoming)),
            "n64_max_lane_adjacency": max(map(len, LanePriceMatcher(64, 8).incoming)),
            "n16_exact_k_state_bits": ExactKGrant(16, 4).control_state_bits(),
            "n64_exact_k_state_bits": ExactKGrant(64, 8).control_state_bits(),
            "n16_exact_k_depth": ExactKGrant(16, 4).comparator_depth_proxy(),
            "n64_exact_k_depth": ExactKGrant(64, 8).comparator_depth_proxy(),
            "n16_flat_rr_state_bits": FlatRoundRobin(16, 4).control_state_bits(),
            "n64_flat_rr_state_bits": FlatRoundRobin(64, 8).control_state_bits(),
            "n16_flat_rr_depth": FlatRoundRobin(16, 4).comparator_depth_proxy(),
            "n64_flat_rr_depth": FlatRoundRobin(64, 8).comparator_depth_proxy(),
        },
        "gates": gates,
        "decision": "GO" if all(gates.values()) else "HOLD",
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
