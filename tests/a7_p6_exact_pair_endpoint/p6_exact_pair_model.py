#!/usr/bin/env python3
"""Independent P6 codec, frozen-trace projector, and replay checker."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def encode(count: int, addr0: int, addr1: int = 0) -> int:
    if count not in (1, 2):
        raise ValueError("P6 count must be one or two")
    if not 0 <= addr0 < 16 or not 0 <= addr1 < 16:
        raise ValueError("P6 address must be four bits")
    return ((count == 2) << 9) | (addr0 << 4) | (addr1 if count == 2 else 0)


def decode(word: int) -> tuple[int, int, int]:
    if not 0 <= word < 1024:
        raise ValueError("P6 word must be ten bits")
    if word & 0x100:
        raise ValueError("P6 reserved bit is nonzero")
    pair = bool(word & 0x200)
    addr0 = (word >> 4) & 0xF
    addr1 = word & 0xF
    if not pair and addr1:
        raise ValueError("P6 singleton has a nonzero second-address field")
    return (2 if pair else 1), addr0, addr1


def project_k2(events: list[dict[str, Any]], stim_cycles: int) -> tuple[list[dict[str, Any]], int]:
    """Deterministic rotating K2 source-latch projection; not a ranked scheduler."""
    arrivals: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        arrivals[int(event["occurrence_cycle"])].append(event)
    pending: list[dict[str, Any] | None] = [None] * 16
    base = 0
    cycle = 0
    overrun = 0
    records: list[dict[str, Any]] = []
    while cycle < stim_cycles or any(item is not None for item in pending):
        for event in arrivals.get(cycle, []):
            source = int(event["logical_source"])
            if pending[source] is not None:
                overrun += 1
            else:
                pending[source] = event
        winners: list[int] = []
        for offset in range(16):
            source = (base + offset) % 16
            if pending[source] is not None:
                winners.append(source)
                if len(winners) == 2:
                    break
        if winners:
            selected = [pending[source] for source in winners]
            assert all(item is not None for item in selected)
            records.append({"cycle": cycle, "events": selected})
            for source in winners:
                pending[source] = None
            base = (winners[-1] + 1) % 16
        cycle += 1
        if cycle > stim_cycles + 65536:
            raise RuntimeError("K2 projection failed to drain")
    return records, overrun


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def prepare(manifest_path: Path, trace_dir: Path, bundle_path: Path,
            expected_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_records: list[dict[str, Any]] = []
    base_cycle = 0
    total_events = 0
    total_overrun = 0
    pair_records = 0
    timing_relations: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)

    for run in manifest["runs"]:
        name = run["name"]
        events = read_jsonl(trace_dir / f"{name}.events.jsonl")
        records, overrun = project_k2(events, int(run["stim_cycles"]))
        total_overrun += overrun
        last_cycle = 0
        for record in records:
            selected = record["events"]
            global_record = {
                "sequence": len(all_records),
                "cycle": base_cycle + int(record["cycle"]),
                "count": len(selected),
                "addr0": int(selected[0]["logical_source"]),
                "addr1": int(selected[1]["logical_source"]) if len(selected) == 2 else 0,
                "events": [],
            }
            pair_records += int(len(selected) == 2)
            for lane, event in enumerate(selected):
                metadata = {
                    "run": name,
                    "event_id": int(event["tb_only_event_id"]),
                    "source": int(event["logical_source"]),
                    "occurrence_cycle": int(event["occurrence_cycle"]),
                    "lane": lane,
                    "relation_id": event.get("relation_id"),
                    "relation_role": event.get("relation_role"),
                }
                global_record["events"].append(metadata)
                if metadata["relation_id"] is not None:
                    key = (name, int(metadata["relation_id"]))
                    timing_relations[key][str(metadata["relation_role"])] = metadata
            all_records.append(global_record)
            total_events += len(selected)
            last_cycle = max(last_cycle, int(record["cycle"]))
        base_cycle += max(int(run["stim_cycles"]), last_cycle + 1) + 8

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with bundle_path.open("w", encoding="ascii", newline="\n") as output:
        for record in all_records:
            output.write(
                f"{record['cycle']} {record['count']} "
                f"{record['addr0']:x} {record['addr1']:x}\n"
            )

    summary = {
        "schema_version": 1,
        "records": all_records,
        "record_count": len(all_records),
        "event_count": total_events,
        "pair_records": pair_records,
        "events_per_link_cell": total_events / len(all_records),
        "k2_source_overrun": total_overrun,
        "timing_relation_count": sum(
            set(relation) >= {"a", "b"} for relation in timing_relations.values()
        ),
    }
    expected_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def check(expected_path: Path, observed_path: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    observed: list[dict[str, int]] = []
    for number, line in enumerate(observed_path.read_text(encoding="ascii").splitlines(), 1):
        fields = line.split(",")
        if len(fields) != 5:
            raise ValueError(f"observed line {number} has {len(fields)} fields")
        observed.append({
            "sequence": int(fields[0]), "cycle": int(fields[1]),
            "count": int(fields[2]), "addr0": int(fields[3], 16),
            "addr1": int(fields[4], 16),
        })
    records = expected["records"]
    if len(observed) != len(records):
        raise ValueError(f"record count mismatch observed={len(observed)} expected={len(records)}")

    event_output_cycle: dict[tuple[str, int], int] = {}
    latency: int | None = None
    for actual, wanted in zip(observed, records, strict=True):
        for field in ("sequence", "count", "addr0", "addr1"):
            if actual[field] != wanted[field]:
                raise ValueError(
                    f"record {wanted['sequence']} {field} mismatch "
                    f"actual={actual[field]} expected={wanted[field]}"
                )
        this_latency = actual["cycle"] - int(wanted["cycle"])
        if latency is None:
            latency = this_latency
        elif this_latency != latency:
            raise ValueError(
                f"variable endpoint latency sequence={wanted['sequence']} "
                f"actual={this_latency} expected={latency}"
            )
        for event in wanted["events"]:
            event_output_cycle[(event["run"], int(event["event_id"]))] = actual["cycle"]

    relations: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        for event in record["events"]:
            if event["relation_id"] is not None:
                relations[(event["run"], int(event["relation_id"]))][event["relation_role"]] = event
    checked = 0
    exact = 0
    for relation in relations.values():
        if not {"a", "b"} <= set(relation):
            continue
        a, b = relation["a"], relation["b"]
        occurrence_gap = int(b["occurrence_cycle"]) - int(a["occurrence_cycle"])
        output_gap = event_output_cycle[(b["run"], int(b["event_id"]))] - \
                     event_output_cycle[(a["run"], int(a["event_id"]))]
        checked += 1
        exact += int(output_gap == occurrence_gap)
    if checked and exact != checked:
        raise ValueError(f"timing-gap mismatch exact={exact} checked={checked}")
    return {
        "records": len(records), "events": expected["event_count"],
        "pairs": expected["pair_records"], "fixed_latency_cycles": latency,
        "timing_relations": checked, "timing_exact": exact,
        "timing_exact_ratio": 1.0 if not checked else exact / checked,
    }


def self_test() -> dict[str, int]:
    words: set[int] = set()
    transactions = 0
    for addr0 in range(16):
        word = encode(1, addr0)
        assert decode(word) == (1, addr0, 0)
        words.add(word)
        transactions += 1
    for addr0 in range(16):
        for addr1 in range(16):
            word = encode(2, addr0, addr1)
            assert decode(word) == (2, addr0, addr1)
            words.add(word)
            transactions += 1
    assert len(words) == transactions == 272
    for bad in (0x001, 0x100, 0x2FF | 0x100):
        try:
            decode(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"malformed P6 word accepted: {bad:03x}")
    return {"transactions": transactions, "unique_words": len(words)}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    prep = sub.add_parser("prepare")
    prep.add_argument("--manifest", type=Path, required=True)
    prep.add_argument("--trace-dir", type=Path, required=True)
    prep.add_argument("--bundle", type=Path, required=True)
    prep.add_argument("--expected", type=Path, required=True)
    verify = sub.add_parser("check")
    verify.add_argument("--expected", type=Path, required=True)
    verify.add_argument("--observed", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "self-test":
        result = self_test()
        print(f"A7_P6_MODEL_PASS transactions={result['transactions']} unique_words={result['unique_words']}")
    elif args.command == "prepare":
        result = prepare(args.manifest, args.trace_dir, args.bundle, args.expected)
        print(
            "A7_P6_PREPARE_PASS "
            f"records={result['record_count']} events={result['event_count']} "
            f"pairs={result['pair_records']} event_per_cell={result['events_per_link_cell']:.6f} "
            f"k2_overrun={result['k2_source_overrun']}"
        )
    else:
        result = check(args.expected, args.observed)
        print(
            "A7_P6_REPLAY_MODEL_PASS "
            f"records={result['records']} events={result['events']} pairs={result['pairs']} "
            f"latency={result['fixed_latency_cycles']} timing={result['timing_exact']}/"
            f"{result['timing_relations']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
