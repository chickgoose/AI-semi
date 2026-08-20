#!/usr/bin/env python3
"""Build the source-bound, fixed-cohort RTL stimulus without inventing events."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.redred_uzh_shapes_pose_join import inspect as inspect_pose_join


START_NS = 41_321_000_000
RECORDS = 1_100
PAYLOAD_W = 102
EXPECTED_A23_SHA256 = "7eb025d9ba6de3dcd538311e75b11b55c51439ba9fc8fbf747213af1577053e0"
EXPECTED_POSE_JOIN_SHA256 = {
    "events_pose_join.jsonl": "a49b7d813fde313bfbcc27526e337c7268ab11803a19898feee8f27afc576796",
    "poses.jsonl": "4461d867e8adc8daaeb089fc739613ee7c89ac2f32c825de561ba88ff83ca0c1",
    "calibration.json": "bf718266f210e0bf7d64ff31b1fb4d125f905b0f67d6070976bdaf25ec450cdb",
    "receipt.json": "85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87",
    "COMPLETE.json": "c7692b20dc7d1f305a723cff695b9b794421fdfd39d6a021a17876c56d155756",
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def pack(event: dict, source: int) -> int:
    fields = (
        (event["dataset_event_index"], 24),
        (event["join_sequence_index"], 11),
        (event["timestamp_ns"], 36),
        (event["x"], 8),
        (event["y"], 8),
        (event["polarity_01"], 1),
        (event["causal_pose"]["source_pose_index"], 14),
    )
    value = 0
    shift = 0
    for field, width in fields:
        if not isinstance(field, int) or field < 0 or field >= 1 << width:
            raise ValueError(f"field {field!r} does not fit {width} bits")
        value |= field << shift
        shift += width
    if shift != PAYLOAD_W or source not in range(16):
        raise ValueError("payload/source contract differs")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pose-join", type=Path, required=True)
    parser.add_argument("--join-spec", type=Path, required=True)
    parser.add_argument("--a23-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    join_check = inspect_pose_join(args.pose_join, args.join_spec)
    if (
        join_check.get("status") != "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED"
        or join_check.get("official_uzh_source") is not True
    ):
        raise SystemExit("official pose-join package inspection did not pass")
    for name, expected in EXPECTED_POSE_JOIN_SHA256.items():
        if sha((args.pose_join / name).read_bytes()) != expected:
            raise SystemExit(f"pose-join authority differs: {name}")
    pose_rows = read_jsonl(args.pose_join / "events_pose_join.jsonl")
    if len(pose_rows) != RECORDS + 1 or pose_rows[0].get("record_type") != "header":
        raise SystemExit("pose-join cohort differs")
    events = pose_rows[1:]
    archive_raw = args.a23_archive.read_bytes()
    if sha(archive_raw) != EXPECTED_A23_SHA256:
        raise SystemExit("A23 archive authority differs")
    with tarfile.open(fileobj=io.BytesIO(archive_raw), mode="r:gz") as archive:
        names = [member.name for member in archive.getmembers()]
        if len(names) != len(set(names)):
            raise SystemExit("A23 archive contains duplicate members")
        member = archive.extractfile("inputs/trace_1x.jsonl")
        projected_member = archive.extractfile("inputs/projected_events.jsonl")
        if member is None:
            raise SystemExit("trace member is absent")
        if projected_member is None:
            raise SystemExit("projected event member is absent")
        trace_raw = member.read()
        projected_raw = projected_member.read()
    trace = [json.loads(line) for line in io.BytesIO(trace_raw)]
    projected = [json.loads(line) for line in io.BytesIO(projected_raw)]
    if len(trace) != RECORDS or len(projected) != RECORDS:
        raise SystemExit("projected input count differs")

    groups: dict[int, list[tuple[int, int]]] = defaultdict(list)
    source_cycle = Counter()
    source_rows = []
    for ordinal, (event, row, projected_row) in enumerate(zip(events, trace, projected)):
        delta_ns = event["timestamp_ns"] - START_NS
        projected_floor_cycle = (delta_ns * 2) // 13
        causal_admission_cycle = (delta_ns * 2 + 12) // 13
        expected_polarity = 1 if row["polarity"] > 0 else 0
        source = row["logical_source"]
        if (
            event["join_sequence_index"] != ordinal
            or event["dataset_event_index"] != 13_856_250 + ordinal
            or row["tb_only_event_id"] != ordinal
            or row["occurrence_cycle"] != projected_floor_cycle
            or row["polarity"] not in (-1, 1)
            or event["polarity_01"] != expected_polarity
            or not (0 <= event["x"] < 240 and 0 <= event["y"] < 180)
            or source != (event["y"] * 4 // 180) * 4 + event["x"] * 4 // 240
            or projected_row != {
                "bx": event["x"] * 4 // 240,
                "by": event["y"] * 4 // 180,
                "dataset_event_index": event["dataset_event_index"],
                "logical_source": source,
                "polarity": event["polarity_01"],
                "timestamp_seconds": event["timestamp_seconds_lexeme"],
                "x": event["x"],
                "y": event["y"],
            }
        ):
            raise SystemExit(f"source binding differs at ordinal {ordinal}")
        payload = pack(event, source)
        groups[causal_admission_cycle].append((source, payload))
        source_cycle[(causal_admission_cycle, source)] += 1
        source_rows.append(
            {
                "dataset_event_index": event["dataset_event_index"],
                "join_sequence_index": ordinal,
                "occurrence_timestamp_ns": event["timestamp_ns"],
                "occurrence_cycle": causal_admission_cycle,
                "projection_floor_cycle": projected_floor_cycle,
                "logical_source": source,
                "x": event["x"],
                "y": event["y"],
                "polarity_01": event["polarity_01"],
                "causal_pose_source_index": event["causal_pose"]["source_pose_index"],
                "payload_hex": f"{payload:026x}",
            }
        )
    if max(map(len, groups.values())) != 6 or max(source_cycle.values()) != 3:
        raise SystemExit("qualified multiplicity bounds differ")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    stimulus_lines = []
    for cycle in sorted(groups):
        records = groups[cycle]
        mask = (1 << len(records)) - 1
        columns = [str(cycle), f"{mask:02x}"]
        for lane in range(6):
            source, payload = records[lane] if lane < len(records) else (0, 0)
            columns.extend((f"{source:x}", f"{payload:026x}"))
        stimulus_lines.append(" ".join(columns))
    stimulus_raw = ("\n".join(stimulus_lines) + "\n").encode()
    source_raw = b"".join(canonical(row) for row in source_rows)
    manifest = {
        "schema": "redred.mc_wtb_occurrence_baseline.stimulus/v1",
        "record_count": RECORDS,
        "group_count": len(groups),
        "first_cycle": min(groups),
        "last_cycle": max(groups),
        "max_events_per_cycle": max(map(len, groups.values())),
        "max_same_source_per_cycle": max(source_cycle.values()),
        "payload_width": PAYLOAD_W,
        "clock_period_ps": 6500,
        "source_epoch_start_ns": START_NS,
        "admission_cycle_mapping": "ceil((occurrence_timestamp_ns-source_epoch_start_ns)*2/13)",
        "never_admit_before_occurrence": True,
        "stimulus_sha256": sha(stimulus_raw),
        "source_records_sha256": sha(source_raw),
        "pose_join_events_sha256": sha((args.pose_join / "events_pose_join.jsonl").read_bytes()),
        "a23_archive_sha256": sha(archive_raw),
        "join_spec_sha256": sha(args.join_spec.read_bytes()),
    }
    (args.output_dir / "stimulus.txt").write_bytes(stimulus_raw)
    (args.output_dir / "source_records.jsonl").write_bytes(source_raw)
    (args.output_dir / "stimulus_manifest.json").write_bytes(canonical(manifest))
    print("MC_WTB_OCCURRENCE_STIMULUS_PASS", json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
