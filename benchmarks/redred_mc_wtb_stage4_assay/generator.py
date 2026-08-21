"""Generate deterministic, score-free Stage-4 UZH inputs and manifests."""

from __future__ import annotations

import argparse
import bisect
import hashlib
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_existing_registry,
)

from .source import (
    OFFICIAL_SOURCE_PINS,
    Calibration,
    EventSample,
    PoseSample,
    SourceInputError,
    SourcePins,
    canonicalize_quaternion,
    iter_event_samples,
    load_calibration,
    load_pose_samples,
    sensor_ray,
    shortest_arc_slerp,
    validate_sources,
)


class AssayInputError(ValueError):
    """The score-free Stage-4 input contract failed."""


EVENTS_FILE = "stage4_events.jsonl"
OCCURRENCE_BATCHES_FILE = "stage4_occurrence_batches.jsonl"
POSE_SNAPSHOTS_FILE = "stage4_occurrence_pose_snapshots.jsonl"
DATASET_POSES_FILE = "stage4_dataset_pose_packets.jsonl"
ORACLE_POSES_FILE = "oracle_resampled_groundtruth_1khz.jsonl"
ORACLE_SCHEDULE_FILE = "stage4_oracle_window_schedule.jsonl"
MANIFEST_FILE = "stage4_input_manifest.json"
OCCURRENCE_INGRESS_LANES = 6
PRESENTATION_LANES = 2
STAGING_SERIALIZER_ENTRIES = 6
EVENT_PAYLOAD_BITS = 102
POSE_INDEX_BITS = 14
DEV_MAX_EXACT_TIMESTAMP_BURST = 5


def timestamp_to_cycle(timestamp_ns: int, window_start_ns: int, clock_period_ps: int) -> int:
    """Apply ceil((timestamp_ns-window_start_ns)*1000/clock_period_ps)."""

    for name, value in (
        ("timestamp_ns", timestamp_ns),
        ("window_start_ns", window_start_ns),
        ("clock_period_ps", clock_period_ps),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise AssayInputError("%s must be an integer" % name)
    if timestamp_ns < 0 or window_start_ns < 0 or clock_period_ps <= 0:
        raise AssayInputError("cycle conversion inputs are out of range")
    numerator = (timestamp_ns - window_start_ns) * 1000
    return -(-numerator // clock_period_ps)


def _in_forbidden(timestamp_ns: int, forbidden: Tuple[int, int]) -> bool:
    return forbidden[0] <= timestamp_ns < forbidden[1]


def _validate_dataset_pose_packet_stream(
    packets: Sequence[Mapping[str, Any]], expected_sha256: str
) -> None:
    if hashlib.sha256(_jsonl_bytes(packets)).hexdigest() != expected_sha256:
        raise AssayInputError("dataset pose packet stream hash differs from its authority")
    for packet in packets:
        packet_body = dict(packet)
        packet_hash = packet_body.pop("packet_sha256", None)
        if packet_hash != canonical_sha256(packet_body):
            raise AssayInputError("dataset pose packet hash differs from its canonical record")
        if int(packet["visible_cycle"]) != int(packet["commit_cycle"]) + 1:
            raise AssayInputError("dataset pose packet visibility is not one cycle after commit")


def _extract_events(
    events_path: Path,
    rows: Sequence[Mapping[str, Any]],
    calibration: Calibration,
    dataset_pose_packets: Sequence[Mapping[str, Any]],
    dataset_pose_stream_sha256: str,
    expected_line_count: int,
    forbidden: Tuple[int, int],
    clock_period_ps: int,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    int,
    Mapping[str, int],
]:
    _validate_dataset_pose_packet_stream(
        dataset_pose_packets, dataset_pose_stream_sha256
    )

    selected = {str(row["window_id"]): [] for row in rows}  # type: Dict[str, List[EventSample]]
    cursor = 0
    line_count = 0
    try:
        with events_path.open("rb") as stream:
            for event in iter_event_samples(stream):
                line_count += 1
                while cursor < len(rows) and event.timestamp_ns >= rows[cursor]["query_end_ns_exclusive"]:
                    cursor += 1
                if cursor >= len(rows):
                    continue
                row = rows[cursor]
                if row["warmup_start_ns_inclusive"] <= event.timestamp_ns < row["query_end_ns_exclusive"]:
                    if _in_forbidden(event.timestamp_ns, forbidden):
                        raise AssayInputError("forbidden event reached selected inputs")
                    selected[str(row["window_id"])].append(event)
    except (OSError, SourceInputError) as exc:
        if isinstance(exc, AssayInputError):
            raise
        raise AssayInputError("event extraction failed") from exc
    if line_count != expected_line_count:
        raise AssayInputError("events.txt line count differs from its source pin")

    output = []  # type: List[Dict[str, Any]]
    occurrence_batches = []  # type: List[Dict[str, Any]]
    occurrence_snapshots = []  # type: List[Dict[str, Any]]
    summaries = []  # type: List[Dict[str, Any]]
    total_query = 0
    total_entry_cycles = 0
    aggregate_peak_occupancy = 0
    for row in rows:
        window_id = str(row["window_id"])
        window_start = int(row["warmup_start_ns_inclusive"])
        query_start = int(row["query_start_ns_inclusive"])
        events = selected[window_id]
        query_ids = []  # type: List[int]
        window_records = []  # type: List[Dict[str, Any]]
        exact_clusters = {}  # type: Dict[int, Tuple[int, int]]
        index = 0
        while index < len(events):
            cluster_end = index + 1
            while (
                cluster_end < len(events)
                and events[cluster_end].timestamp_ns == events[index].timestamp_ns
            ):
                cluster_end += 1
            cluster_id = events[index].event_id
            cluster_size = cluster_end - index
            if cluster_size > DEV_MAX_EXACT_TIMESTAMP_BURST:
                raise AssayInputError("exact-timestamp burst exceeds the frozen development bound")
            exact_clusters[events[index].timestamp_ns] = (cluster_id, cluster_size)
            for event in events[index:cluster_end]:
                is_query = event.timestamp_ns >= query_start
                if is_query:
                    query_ids.append(event.event_id)
                cluster = exact_clusters[event.timestamp_ns]
                window_records.append(
                    {
                        "window_id": window_id,
                        "event_id": event.event_id,
                        "timestamp_ns": event.timestamp_ns,
                        "x": event.x,
                        "y": event.y,
                        "polarity": event.polarity,
                        "sensor_ray": list(sensor_ray(event, calibration)),
                        "is_query": is_query,
                        "window_event_ordinal": len(window_records),
                        "occurrence_cycle": timestamp_to_cycle(
                            event.timestamp_ns, window_start, clock_period_ps
                        ),
                        "equal_timestamp_cluster_id": cluster[0],
                        "equal_timestamp_cluster_size": cluster[1],
                    }
                )
            index = cluster_end

        window_pose_packets = [
            packet
            for packet in dataset_pose_packets
            if packet["window_id"] == window_id
        ]
        pose_commit_cycles = [int(packet["commit_cycle"]) for packet in window_pose_packets]
        if any(
            right < left for left, right in zip(pose_commit_cycles, pose_commit_cycles[1:])
        ):
            raise AssayInputError("dataset pose packet commits are not ordered")
        pose_ids = [int(packet["source_pose_id"]) for packet in window_pose_packets]
        pose_timestamps = [int(packet["timestamp_ns"]) for packet in window_pose_packets]
        if any(right <= left for left, right in zip(pose_ids, pose_ids[1:])) or any(
            right <= left for left, right in zip(pose_timestamps, pose_timestamps[1:])
        ):
            raise AssayInputError("dataset pose packet identities are not strictly ordered")
        batch_start = 0
        window_batches = []  # type: List[Dict[str, Any]]
        while batch_start < len(window_records):
            occurrence_cycle = window_records[batch_start]["occurrence_cycle"]
            batch_end = batch_start + 1
            while (
                batch_end < len(window_records)
                and window_records[batch_end]["occurrence_cycle"] == occurrence_cycle
            ):
                batch_end += 1
            members = window_records[batch_start:batch_end]
            if len(members) > OCCURRENCE_INGRESS_LANES:
                raise AssayInputError("occurrence batch exceeds six atomic ingress lanes")
            eligible_end = bisect.bisect_left(pose_commit_cycles, occurrence_cycle)
            if eligible_end < 2:
                raise AssayInputError("occurrence batch lacks two pre-edge pose packets")
            visible = window_pose_packets[eligible_end - 2:eligible_end]
            if any(
                int(packet["commit_cycle"]) >= occurrence_cycle
                or int(packet["visible_cycle"]) > occurrence_cycle
                for packet in visible
            ):
                raise AssayInputError("occurrence snapshot is not strictly pre-edge")
            if any(_in_forbidden(int(packet["timestamp_ns"]), forbidden) for packet in visible):
                raise AssayInputError("forbidden source pose reached occurrence snapshot")
            latest = visible[-1]
            snapshot = {
                "schema": "redred.mc_wtb.stage4_occurrence_pose_snapshot/v1",
                "window_id": window_id,
                "occurrence_batch_id": len(window_batches),
                "occurrence_cycle": occurrence_cycle,
                "event_timestamp_range_ns": [
                    min(int(record["timestamp_ns"]) for record in members),
                    max(int(record["timestamp_ns"]) for record in members),
                ],
                "dataset_pose_packet_stream_sha256": dataset_pose_stream_sha256,
                "selection_rule": "two_latest_packets_with_commit_cycle_strictly_before_occurrence_cycle",
                "pose_packets": [
                    {
                        "source_pose_id": packet["source_pose_id"],
                        "timestamp_ns": packet["timestamp_ns"],
                        "quaternion_xyzw": packet["quaternion_xyzw"],
                        "pose_value_sha256": packet["pose_value_sha256"],
                        "packet_sha256": packet["packet_sha256"],
                        "commit_cycle": packet["commit_cycle"],
                        "visible_cycle": packet["visible_cycle"],
                    }
                    for packet in visible
                ],
            }
            snapshot_sha256 = canonical_sha256(snapshot)
            batch_id = len(window_batches)
            authoritative_snapshot = dict(snapshot)
            authoritative_snapshot["pose_snapshot_sha256"] = snapshot_sha256
            occurrence_snapshots.append(authoritative_snapshot)
            for lane, record in enumerate(members):
                record["occurrence_batch_id"] = batch_id
                record["occurrence_lane"] = lane
                record["occurrence_batch_size"] = len(members)
                record["occurrence_pose_snapshot_sha256"] = snapshot_sha256
                record["causal_pose_source_index"] = latest["source_pose_id"]
                record["payload_hex"] = _pack_event_payload(record)
            batch = {
                "window_id": window_id,
                "occurrence_batch_id": batch_id,
                "occurrence_cycle": occurrence_cycle,
                "event_count": len(members),
                "event_ids": [record["event_id"] for record in members],
                "payload_hex": [record["payload_hex"] for record in members],
                "pose_snapshot": authoritative_snapshot,
                "pose_snapshot_sha256": snapshot_sha256,
            }
            window_batches.append(batch)
            occurrence_batches.append(batch)
            batch_start = batch_end

        serializer = _schedule_staging_serializer(window_records, window_batches)
        total_entry_cycles += serializer["staging_entry_cycles"]
        aggregate_peak_occupancy = max(
            aggregate_peak_occupancy, serializer["peak_staging_occupancy"]
        )
        output.extend(window_records)
        cluster_snapshot_hashes = {}  # type: Dict[int, str]
        for record in window_records:
            timestamp_ns = int(record["timestamp_ns"])
            snapshot_hash = str(record["occurrence_pose_snapshot_sha256"])
            prior_hash = cluster_snapshot_hashes.setdefault(timestamp_ns, snapshot_hash)
            if prior_hash != snapshot_hash:
                raise AssayInputError("equal-timestamp cluster has multiple pose snapshots")
        total_query += len(query_ids)
        summaries.append(
            {
                "window_id": window_id,
                "selected_event_count": len(events),
                "query_event_count": len(query_ids),
                "ordered_query_event_ids_sha256": canonical_sha256(query_ids),
                "occurrence_batch_count": len(window_batches),
                "maximum_occurrence_batch_size": max(
                    (batch["event_count"] for batch in window_batches), default=0
                ),
                "maximum_exact_timestamp_burst": max(
                    (size for _, size in exact_clusters.values()), default=0
                ),
                "maximum_serializer_queue_cycles": max(
                    (
                        record["serializer_queue_cycles"]
                        for record in window_records
                    ),
                    default=0,
                ),
                "peak_staging_occupancy": serializer["peak_staging_occupancy"],
                "staging_entry_cycles": serializer["staging_entry_cycles"],
            }
        )
    return (
        output,
        occurrence_batches,
        occurrence_snapshots,
        summaries,
        total_query,
        {
            "peak_staging_occupancy": aggregate_peak_occupancy,
            "staging_entry_cycles": total_entry_cycles,
        },
    )


def _pack_event_payload(record: Mapping[str, Any]) -> str:
    """Pack the corrected 102-bit occurrence payload, least-significant field first."""

    fields = (
        (record["event_id"], 24, "dataset_event_index"),
        (record["window_event_ordinal"], 11, "join_sequence_index"),
        (record["timestamp_ns"], 36, "timestamp_ns"),
        (record["x"], 8, "x"),
        (record["y"], 8, "y"),
        (record["polarity"], 1, "polarity"),
        (record["causal_pose_source_index"], POSE_INDEX_BITS, "pose_index"),
    )
    packed = 0
    shift = 0
    for value, width, name in fields:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value >= 1 << width
        ):
            raise AssayInputError("%s does not fit the corrected payload" % name)
        packed |= value << shift
        shift += width
    if shift != EVENT_PAYLOAD_BITS:
        raise AssayInputError("event payload layout does not total 102 bits")
    return ("%026x" % packed)


def _schedule_staging_serializer(
    records: Sequence[Dict[str, Any]], batches: Sequence[Mapping[str, Any]]
) -> Mapping[str, int]:
    """Drain two old entries, then atomically admit one up-to-six occurrence batch."""

    by_id = {record["event_id"]: record for record in records}
    queue = []  # type: List[int]
    current_cycle = None  # type: Optional[int]
    peak = 0
    entry_cycles = 0

    def service_cycle(cycle: int, arriving: Sequence[int]) -> None:
        nonlocal peak, entry_cycles
        retiring = queue[:PRESENTATION_LANES]
        del queue[:len(retiring)]
        for lane, event_id in enumerate(retiring):
            record = by_id[event_id]
            record["presentation_cycle"] = cycle
            record["presentation_lane"] = lane
            record["serializer_queue_cycles"] = cycle - record["occurrence_cycle"]
        if len(queue) + len(arriving) > STAGING_SERIALIZER_ENTRIES:
            raise AssayInputError("six-entry staging serializer overflow")
        queue.extend(arriving)
        peak = max(peak, len(queue))
        entry_cycles += len(queue)

    for batch in batches:
        occurrence_cycle = int(batch["occurrence_cycle"])
        if current_cycle is None:
            current_cycle = occurrence_cycle
        while current_cycle < occurrence_cycle:
            if queue:
                service_cycle(current_cycle, ())
                current_cycle += 1
            else:
                current_cycle = occurrence_cycle
        service_cycle(current_cycle, batch["event_ids"])
        current_cycle += 1
    while queue:
        if current_cycle is None:
            raise AssayInputError("serializer state is inconsistent")
        service_cycle(current_cycle, ())
        current_cycle += 1
    if any("presentation_cycle" not in record for record in records):
        raise AssayInputError("serializer failed to present every event")
    return {
        "peak_staging_occupancy": peak,
        "staging_entry_cycles": entry_cycles,
    }


def _pose_value_sha256(pose_id: int, timestamp_ns: int, quaternion: Sequence[float]) -> str:
    return canonical_sha256(
        {
            "pose_id": pose_id,
            "timestamp_ns": timestamp_ns,
            "quaternion_xyzw": list(quaternion),
        }
    )


def _dataset_pose_packets(
    poses: Sequence[PoseSample],
    rows: Sequence[Mapping[str, Any]],
    forbidden: Tuple[int, int],
    clock_period_ps: int,
    delayed_deadline_ns: int,
) -> List[Dict[str, Any]]:
    times = [pose.timestamp_ns for pose in poses]
    packets = []  # type: List[Dict[str, Any]]
    for row in rows:
        window_start = int(row["warmup_start_ns_inclusive"])
        support_end = int(row["query_end_ns_exclusive"]) + delayed_deadline_ns
        history_end = bisect.bisect_right(times, window_start)
        if history_end < 3:
            raise AssayInputError("three source pose history packets are required")
        end = bisect.bisect_right(times, support_end)
        for pose in poses[history_end - 3:end]:
            if _in_forbidden(pose.timestamp_ns, forbidden):
                raise AssayInputError("forbidden source pose reached packet inputs")
            arrival_cycle = timestamp_to_cycle(
                pose.timestamp_ns, window_start, clock_period_ps
            )
            visible_cycle = arrival_cycle + 1
            packet = {
                "window_id": row["window_id"],
                "source_pose_id": pose.pose_id,
                "timestamp_ns": pose.timestamp_ns,
                "quaternion_xyzw": list(pose.quaternion_xyzw),
                "pose_value_sha256": _pose_value_sha256(
                    pose.pose_id, pose.timestamp_ns, pose.quaternion_xyzw
                ),
                "arrival_cycle": arrival_cycle,
                "commit_cycle": arrival_cycle,
                "visible_cycle": visible_cycle,
                "visible_at_window_start": visible_cycle <= 0,
            }
            packet["packet_sha256"] = canonical_sha256(packet)
            packets.append(packet)
        if sum(
            1
            for packet in packets
            if packet["window_id"] == row["window_id"]
            and packet["visible_at_window_start"]
        ) < 2:
            raise AssayInputError("two visible source poses are required at window start")
    return packets


def _oracle_grid_timestamps(
    rows: Sequence[Mapping[str, Any]], cadence_ns: int, origin_ns: int
) -> Tuple[int, ...]:
    timestamps = set()
    for row in rows:
        window_start = int(row["warmup_start_ns_inclusive"])
        query_end = int(row["query_end_ns_exclusive"])
        first = origin_ns + ((window_start - origin_ns) // cadence_ns) * cadence_ns
        first -= cadence_ns
        last = origin_ns + ((query_end - origin_ns) // cadence_ns) * cadence_ns
        timestamp = first
        while timestamp <= last:
            if timestamp >= 0:
                timestamps.add(timestamp)
            timestamp += cadence_ns
    return tuple(sorted(timestamps))


def _oracle_packets(
    poses: Sequence[PoseSample],
    rows: Sequence[Mapping[str, Any]],
    forbidden: Tuple[int, int],
    cadence_ns: int,
    origin_ns: int,
) -> List[Dict[str, Any]]:
    times = [pose.timestamp_ns for pose in poses]
    packets = []  # type: List[Dict[str, Any]]
    for timestamp_ns in _oracle_grid_timestamps(rows, cadence_ns, origin_ns):
        if (timestamp_ns - origin_ns) % cadence_ns != 0:
            raise AssayInputError("oracle timestamp is not on the frozen global phase")
        if _in_forbidden(timestamp_ns, forbidden):
            raise AssayInputError("forbidden timestamp reached oracle packet inputs")
        after_index = bisect.bisect_right(times, timestamp_ns)
        before_index = after_index - 1
        if before_index < 0 or after_index >= len(poses):
            raise AssayInputError("oracle timestamp lacks two source brackets")
        before, after = poses[before_index], poses[after_index]
        if not before.timestamp_ns <= timestamp_ns < after.timestamp_ns:
            raise AssayInputError("oracle source bracket ordering failed")
        numerator = timestamp_ns - before.timestamp_ns
        denominator = after.timestamp_ns - before.timestamp_ns
        quaternion = shortest_arc_slerp(
            before.quaternion_xyzw,
            after.quaternion_xyzw,
            numerator,
            denominator,
        )
        oracle_id = (timestamp_ns - origin_ns) // cadence_ns
        packets.append(
            {
                "oracle_pose_id": oracle_id,
                "effective_timestamp_ns": timestamp_ns,
                "quaternion_xyzw": list(quaternion),
                "before_source_pose_id": before.pose_id,
                "before_timestamp_ns": before.timestamp_ns,
                "after_source_pose_id": after.pose_id,
                "after_timestamp_ns": after.timestamp_ns,
                "slerp_numerator_ns": numerator,
                "slerp_denominator_ns": denominator,
                "pose_value_sha256": _pose_value_sha256(
                    oracle_id, timestamp_ns, quaternion
                ),
            }
        )
    return packets


def _oracle_schedule(
    packets: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    clock_period_ps: int,
    cadence_ns: int,
    commit_delay_cycles: int,
    visibility_delay_cycles: int,
) -> List[Dict[str, Any]]:
    schedule = []  # type: List[Dict[str, Any]]
    for row in rows:
        window_start = int(row["warmup_start_ns_inclusive"])
        support_start = window_start - cadence_ns
        query_end = int(row["query_end_ns_exclusive"])
        for packet in packets:
            timestamp_ns = int(packet["effective_timestamp_ns"])
            if support_start <= timestamp_ns <= query_end:
                effective_cycle = timestamp_to_cycle(
                    timestamp_ns, window_start, clock_period_ps
                )
                commit_cycle = effective_cycle + commit_delay_cycles
                schedule.append(
                    {
                        "window_id": row["window_id"],
                        "oracle_pose_id": packet["oracle_pose_id"],
                        "effective_timestamp_ns": timestamp_ns,
                        "pose_value_sha256": packet["pose_value_sha256"],
                        "effective_cycle": effective_cycle,
                        "commit_cycle": commit_cycle,
                        "visible_cycle": commit_cycle + visibility_delay_cycles,
                    }
                )
    return schedule


def _jsonl_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _artifact(name: str, payload: bytes, records: int) -> Dict[str, Any]:
    return {
        "path": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": records,
        "size_bytes": len(payload),
    }


def _module_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ordered_payload_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (str(record["payload_hex"]) + "\n").encode("ascii") for record in records
    )


def _runtime_binding() -> Mapping[str, Any]:
    executable = Path(sys.executable).resolve()
    identity = {
        "implementation": sys.implementation.name,
        "version": list(sys.version_info[:3]),
        "byteorder": sys.byteorder,
        "executable_path_name": executable.name,
    }
    return {
        "identity": identity,
        "identity_sha256": canonical_sha256(identity),
        "python_executable_sha256": _module_sha256(executable),
    }


def generate_score_free_inputs(
    dataset_dir: Path,
    output_dir: Path,
    *,
    source_pins: SourcePins = OFFICIAL_SOURCE_PINS,
    fixture_label: Optional[str] = None,
) -> Mapping[str, Any]:
    """Generate inputs/manifests only; no arm transformation or quality computation."""

    if source_pins != OFFICIAL_SOURCE_PINS and (
        type(fixture_label) is not str or not fixture_label
    ):
        raise AssayInputError("non-official source pins require a fixture label")
    if source_pins == OFFICIAL_SOURCE_PINS and fixture_label is not None:
        raise AssayInputError("official source generation must not carry a fixture label")
    try:
        contract = load_comparison_contract()
        registry_validation = validate_existing_registry(contract)
        sources = validate_sources(Path(dataset_dir), source_pins)
        calibration = load_calibration(sources.calibration_path)
        poses = load_pose_samples(sources.groundtruth_path)
    except (SourceInputError, ValueError) as exc:
        if isinstance(exc, AssayInputError):
            raise
        raise AssayInputError("input provenance validation failed") from exc

    document = contract.as_dict()
    timing = document["timing"]
    arms = document["arms"]
    if (
        timing.get("raw_ingress_lanes") != OCCURRENCE_INGRESS_LANES
        or timing.get("raw_ingress_capture_entries") != STAGING_SERIALIZER_ENTRIES
        or timing.get("downstream_event_lanes") != PRESENTATION_LANES
        or timing.get("event_record_bits") != EVENT_PAYLOAD_BITS
        or timing.get("event_record_includes_causal_pose_index_bits")
        != POSE_INDEX_BITS
    ):
        raise AssayInputError("assay ingress constants differ from frozen contract")
    rows = tuple(window_registry())
    forbidden_values = document["registry"]["forbidden_interval_ns"]
    forbidden = (int(forbidden_values[0]), int(forbidden_values[1]))
    dataset_packets = _dataset_pose_packets(
        poses,
        rows,
        forbidden,
        int(timing["clock_period_ps"]),
        int(arms["delayed_exact"]["deadline_ns"]),
    )
    dataset_pose_payload = _jsonl_bytes(dataset_packets)
    dataset_pose_stream_sha256 = hashlib.sha256(dataset_pose_payload).hexdigest()
    try:
        (
            event_records,
            occurrence_batches,
            occurrence_snapshots,
            window_summaries,
            query_count,
            serializer_accounting,
        ) = _extract_events(
            sources.events_path,
            rows,
            calibration,
            dataset_packets,
            dataset_pose_stream_sha256,
            source_pins.events_line_count,
            forbidden,
            int(timing["clock_period_ps"]),
        )
    except (SourceInputError, OSError) as exc:
        raise AssayInputError("event input generation failed") from exc
    if query_count != document["registry"]["query_event_count"]:
        raise AssayInputError("query event count differs from frozen contract")

    oracle_contract = arms["oracle_resampled_groundtruth_1khz"]
    oracle_packets = _oracle_packets(
        poses,
        rows,
        forbidden,
        int(oracle_contract["cadence_ns"]),
        int(oracle_contract["cadence_origin_ns"]),
    )
    oracle_schedule = _oracle_schedule(
        oracle_packets,
        rows,
        int(timing["clock_period_ps"]),
        int(oracle_contract["cadence_ns"]),
        int(oracle_contract["commit_delay_cycles"]),
        int(oracle_contract["visibility_delay_after_commit_cycles"]),
    )

    payloads = {
        EVENTS_FILE: _jsonl_bytes(event_records),
        OCCURRENCE_BATCHES_FILE: _jsonl_bytes(occurrence_batches),
        POSE_SNAPSHOTS_FILE: _jsonl_bytes(occurrence_snapshots),
        DATASET_POSES_FILE: dataset_pose_payload,
        ORACLE_POSES_FILE: _jsonl_bytes(oracle_packets),
        ORACLE_SCHEDULE_FILE: _jsonl_bytes(oracle_schedule),
    }
    artifacts = {
        name: _artifact(name, payload, len(records))
        for name, payload, records in (
            (EVENTS_FILE, payloads[EVENTS_FILE], event_records),
            (
                OCCURRENCE_BATCHES_FILE,
                payloads[OCCURRENCE_BATCHES_FILE],
                occurrence_batches,
            ),
            (
                POSE_SNAPSHOTS_FILE,
                payloads[POSE_SNAPSHOTS_FILE],
                occurrence_snapshots,
            ),
            (DATASET_POSES_FILE, payloads[DATASET_POSES_FILE], dataset_packets),
            (ORACLE_POSES_FILE, payloads[ORACLE_POSES_FILE], oracle_packets),
            (ORACLE_SCHEDULE_FILE, payloads[ORACLE_SCHEDULE_FILE], oracle_schedule),
        )
    }
    benchmarks_root = Path(__file__).resolve().parents[1]
    generator_code_hashes = {
        "generator.py": _module_sha256(Path(__file__)),
        "source.py": _module_sha256(Path(__file__).with_name("source.py")),
        "redred_mc_wtb_stage4_contract/contract.py": _module_sha256(
            benchmarks_root / "redred_mc_wtb_stage4_contract" / "contract.py"
        ),
        "redred_mc_wtb_causal_reference/development.py": _module_sha256(
            benchmarks_root / "redred_mc_wtb_causal_reference" / "development.py"
        ),
    }
    runtime_binding = _runtime_binding()
    ordered_payload = _ordered_payload_bytes(event_records)
    authoritative_binding = {
        "schema": "redred.mc_wtb.stage4_authoritative_input_binding/v1",
        "ordered_102bit_occurrence_records": {
            "serialization": "lowercase_26_hex_digits_plus_lf_in_source_event_order",
            "record_count": len(event_records),
            "sha256": hashlib.sha256(ordered_payload).hexdigest(),
            "ordered_event_ids_sha256": canonical_sha256(
                [record["event_id"] for record in event_records]
            ),
        },
        "raw_source_streams": {
            "events.txt_sha256": source_pins.events_sha256,
            "groundtruth.txt_sha256": source_pins.groundtruth_sha256,
            "calib.txt_sha256": source_pins.calibration_sha256,
        },
        "dataset_pose_packet_stream": {
            "path": DATASET_POSES_FILE,
            "sha256": artifacts[DATASET_POSES_FILE]["sha256"],
            "record_count": len(dataset_packets),
        },
        "occurrence_pose_snapshot_stream": {
            "path": POSE_SNAPSHOTS_FILE,
            "sha256": artifacts[POSE_SNAPSHOTS_FILE]["sha256"],
            "record_count": len(occurrence_snapshots),
        },
        "oracle_pose_stream": {
            "path": ORACLE_POSES_FILE,
            "sha256": artifacts[ORACLE_POSES_FILE]["sha256"],
            "record_count": len(oracle_packets),
        },
        "oracle_window_schedule_stream": {
            "path": ORACLE_SCHEDULE_FILE,
            "sha256": artifacts[ORACLE_SCHEDULE_FILE]["sha256"],
            "record_count": len(oracle_schedule),
        },
        "generator_code_sha256": generator_code_hashes,
        "runtime": runtime_binding,
    }
    authoritative_binding["binding_sha256"] = canonical_sha256(authoritative_binding)
    manifest = {
        "schema": "redred.mc_wtb.stage4_score_free_inputs/v2",
        "content_class": "DECISION_INPUTS_ONLY_NO_ARM_TRANSFORMS",
        "provenance_scope": (
            "OFFICIAL_HASH_PINNED_DEVELOPMENT_INPUT"
            if source_pins == OFFICIAL_SOURCE_PINS
            else "SYNTHETIC_FIXTURE_ONLY"
        ),
        "fixture_label": fixture_label,
        "comparison_contract_sha256": contract.canonical_sha256,
        "generator_runtime": {
            "generator_code_sha256": generator_code_hashes,
            "runtime": runtime_binding,
        },
        "authoritative_input_binding": authoritative_binding,
        "registry": {
            "window_count": registry_validation.window_count,
            "sha256": registry_validation.canonical_sha256,
            "query_event_count": query_count,
            "forbidden_interval_ns": list(forbidden),
            "forbidden_interval_selected_records": 0,
        },
        "source": {
            "sequence": "UZH_DAVIS_shapes_rotation",
            "events_sha256": source_pins.events_sha256,
            "groundtruth_sha256": source_pins.groundtruth_sha256,
            "calibration_sha256": source_pins.calibration_sha256,
            "events_size_bytes": source_pins.events_size_bytes,
            "events_line_count": source_pins.events_line_count,
            "validation": "whole_file_hashes_before_parsing",
        },
        "event_inputs": {
            "ray_model": "radtan_inverse_newton_then_normalized_sensor_ray",
            "selected_event_count": len(event_records),
            "occurrence_batch_count": len(occurrence_batches),
            "ordered_selected_event_ids_sha256": canonical_sha256(
                [record["event_id"] for record in event_records]
            ),
            "ordered_query_event_ids_sha256": canonical_sha256(
                [record["event_id"] for record in event_records if record["is_query"]]
            ),
            "ordered_102bit_records_sha256": hashlib.sha256(ordered_payload).hexdigest(),
        },
        "occurrence_pose_snapshots": {
            "authority": "canonical_dataset_pose_packet_stream",
            "selection_rule": "two_latest_packets_with_commit_cycle_strictly_before_occurrence_cycle",
            "snapshot_count": len(occurrence_snapshots),
            "equal_timestamp_cluster_policy": "one_identical_snapshot_hash_per_cluster",
            "dataset_pose_packet_stream_sha256": dataset_pose_stream_sha256,
        },
        "timing": {
            "clock_period_ps": timing["clock_period_ps"],
            "timestamp_to_cycle_rule": timing["timestamp_to_cycle_rule"],
            "prescore_input_boundary_correction": (
                "raw_uzh_occurrence_baseline_6_lane_to_charged_2_lane_serializer_v1"
            ),
            "occurrence_ingress_lanes": timing["raw_ingress_lanes"],
            "ingress_capture_entries": timing["raw_ingress_capture_entries"],
            "presentation_lanes": timing["downstream_event_lanes"],
            "development_max_exact_timestamp_burst": DEV_MAX_EXACT_TIMESTAMP_BURST,
            "event_record_bits": EVENT_PAYLOAD_BITS,
            "event_payload_pose_index_bits": POSE_INDEX_BITS,
            "pose_packet_bits": timing["pose_packet_bits"],
            "same_edge_pose_visible_to_event": timing[
                "same_edge_pose_visible_to_event"
            ],
            "dataset_pose_arrival_assumption": timing[
                "dataset_pose_arrival_assumption"
            ],
        },
        "staging_serializer": {
            "entries": STAGING_SERIALIZER_ENTRIES,
            "payload_bits_per_entry": EVENT_PAYLOAD_BITS,
            "payload_state_bits": STAGING_SERIALIZER_ENTRIES * EVENT_PAYLOAD_BITS,
            "drain_records_per_cycle": PRESENTATION_LANES,
            "cycle_order": "present_up_to_two_old_then_atomically_admit_occurrence_batch",
            "overflow_action": "protocol_failure_no_external_buffer",
            "peak_occupancy": serializer_accounting["peak_staging_occupancy"],
            "entry_cycles": serializer_accounting["staging_entry_cycles"],
            "payload_bit_cycles": (
                serializer_accounting["staging_entry_cycles"] * EVENT_PAYLOAD_BITS
            ),
        },
        "event_payload_layout_lsb_first": [
            {"field": "dataset_event_index", "source": "event_id", "bits": 24},
            {
                "field": "join_sequence_index",
                "source": "window_event_ordinal",
                "bits": 11,
            },
            {"field": "timestamp_ns", "bits": 36},
            {"field": "x", "bits": 8},
            {"field": "y", "bits": 8},
            {"field": "polarity", "bits": 1},
            {"field": "causal_pose_source_index", "bits": POSE_INDEX_BITS},
        ],
        "oracle_resampled_groundtruth_1khz": {
            "cadence_ns": oracle_contract["cadence_ns"],
            "cadence_origin_ns": oracle_contract["cadence_origin_ns"],
            "generator": oracle_contract["generator"],
            "interface_width_bits": oracle_contract["interface_width_bits"],
            "commit_delay_cycles": oracle_contract["commit_delay_cycles"],
            "visibility_delay_after_commit_cycles": oracle_contract[
                "visibility_delay_after_commit_cycles"
            ],
            "ordered_pose_ids_sha256": canonical_sha256(
                [packet["oracle_pose_id"] for packet in oracle_packets]
            ),
        },
        "windows": window_summaries,
        "artifacts": artifacts,
    }
    output = Path(output_dir)
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise AssayInputError("output directory must not exist or must be empty")
    else:
        output.mkdir(parents=True)
    for name, payload in payloads.items():
        (output / name).write_bytes(payload)
    (output / MANIFEST_FILE).write_bytes(canonical_json_bytes(manifest))
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    generate_score_free_inputs(args.dataset_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
