"""Deterministic Stage-1 model for motion-compensated world-tile binning.

This is an analysis model, not a transport codec.  It retains an exact event
ledger while comparing a sensor-fixed occupancy projection with the same
events warped into a fixed reference-camera frame using supplied rotations.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
from bisect import bisect_right
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from demos.known_motion_coordinate.model import (
    KNOWN_MOTION_BLOB_API_ID,
    InputBlob,
    InterfaceError,
    Intrinsics,
    Pose,
    open_input_blob,
    parse_intrinsics_blob,
    parse_pose_stream_blob,
    warp_pixel,
)


EVENT_HEADER_SCHEMA = "redred.mc_wtb.event_stream/v1"
EVENT_SCHEMA = "redred.mc_wtb.event/v1"
RESULT_SCHEMA = "redred.mc_wtb.stage1_analysis/v1"
EVIDENCE_CLASS = "SYNTHETIC_DEMO"
MODEL_IMPLEMENTATION_ID = "redred.mc_wtb.stage1.python-reference/hardening2-v1"
RESULT_CONTRACT_REVISION = "hardening2-v1"

# Logical widths are a declared comparison convention, not JSON file size or
# an implemented wire protocol.  Keeping them data-independent prevents an
# input from changing the accounting rule in its favor.
_RAW_SENSOR_PAYLOAD_WIDTHS: Mapping[str, int] = MappingProxyType({
    "x_bits": 16,
    "y_bits": 16,
    "polarity_bits": 1,
    "timestamp_bits": 64,
    "pose_version_bits": 16,
})
_OCCUPANCY_PACKET_WIDTHS: Mapping[str, int] = MappingProxyType({
    "tile_x_bits": 16,
    "tile_y_bits": 16,
    "polarity_bits": 1,
    "time_bin_start_bits": 64,
    "pose_version_bits": 16,
    "multiplicity_count_bits": 16,
})
LOGICAL_BIT_FORMAT: Mapping[str, Any] = MappingProxyType({
    "format_id": "redred.mc_wtb.logical_bits/fixed-v1",
    "raw_sensor_payload": _RAW_SENSOR_PAYLOAD_WIDTHS,
    "occupancy_packet": _OCCUPANCY_PACKET_WIDTHS,
})

UNSUPPORTED_FEATURES = (
    "depth",
    "pose_estimation",
    "reversible_codec",
    "rtl",
    "translation",
)


def _logical_bit_format_json() -> dict[str, Any]:
    return {
        "format_id": LOGICAL_BIT_FORMAT["format_id"],
        "raw_sensor_payload": dict(_RAW_SENSOR_PAYLOAD_WIDTHS),
        "occupancy_packet": dict(_OCCUPANCY_PACKET_WIDTHS),
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InterfaceError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _parse_event_jsonl(blob: InputBlob) -> list[dict[str, Any]]:
    source = blob.path
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(blob.data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
            )
        except InterfaceError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InterfaceError(f"{source}:{line_number}: invalid UTF-8 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise InterfaceError(f"{source}:{line_number}: record must be an object")
        records.append(value)
    if not records:
        raise InterfaceError(f"{source}: JSONL stream is empty")
    return records


def _exact_keys(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InterfaceError(f"{where} must be an object")
    actual = set(value)
    if actual != keys:
        raise InterfaceError(
            f"{where} keys differ: missing={sorted(keys - actual)} "
            f"unknown={sorted(actual - keys)}"
        )
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InterfaceError(f"{where} must be a non-empty string")
    return value


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InterfaceError(f"{where} must be a non-negative integer")
    return value


def _positive_int(value: Any, where: str) -> int:
    result = _nonnegative_int(value, where)
    if result == 0:
        raise InterfaceError(f"{where} must be positive")
    return result


def _require_uint(value: Any, bits: int, where: str) -> int:
    """Require one declared unsigned logical field to fit without truncation."""

    if type(value) is not int or value < 0 or value >= 1 << bits:
        raise InterfaceError(
            f"{where} must fit the declared unsigned {bits}-bit field"
        )
    return value


def _timebase(value: Any, where: str) -> dict[str, str]:
    row = _exact_keys(value, {"clock_domain", "epoch", "unit"}, where)
    return {
        key: _string(row[key], f"{where}.{key}")
        for key in ("clock_domain", "epoch", "unit")
    }


def _rounded(value: float) -> float:
    result = round(float(value), 12)
    return 0.0 if result == 0.0 else result


def _ratio(numerator: float, denominator: float) -> float:
    return _rounded(numerator / denominator) if denominator else 0.0


def _load_events(
    blob: InputBlob,
    intrinsics: Intrinsics,
    pose_header: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = blob.path
    records = _parse_event_jsonl(blob)
    header_keys = {
        "schema",
        "record_type",
        "evidence_class",
        "camera_id",
        "intrinsics_id",
        "pose_stream_id",
        "coordinate_frame",
        "timebase",
        "declared_event_count",
    }
    header = _exact_keys(records[0], header_keys, f"{path}:1")
    if header["schema"] != EVENT_HEADER_SCHEMA or header["record_type"] != "header":
        raise InterfaceError(f"{path}: first record is not the MC-WTB v1 header")
    if header["evidence_class"] != EVIDENCE_CLASS:
        raise InterfaceError(f"{path}: only SYNTHETIC_DEMO is supported")
    if header["camera_id"] != intrinsics.camera_id:
        raise InterfaceError(f"{path}: camera binding mismatch")
    if header["intrinsics_id"] != intrinsics.intrinsics_id:
        raise InterfaceError(f"{path}: intrinsics binding mismatch")
    if header["pose_stream_id"] != pose_header["pose_stream_id"]:
        raise InterfaceError(f"{path}: pose-stream binding mismatch")
    if header["coordinate_frame"] != "sensor_image":
        raise InterfaceError(f"{path}: coordinate_frame must be sensor_image")
    event_timebase = _timebase(header["timebase"], f"{path}:1.timebase")
    pose_timebase = _timebase(pose_header["timebase"], "pose_header.timebase")
    if event_timebase != pose_timebase:
        raise InterfaceError(f"{path}: event timebase must exactly match pose timebase")
    declared_count = _nonnegative_int(
        header["declared_event_count"], f"{path}:1.declared_event_count"
    )

    event_keys = {
        "schema",
        "record_type",
        "event_id",
        "sequence_index",
        "timestamp_ns",
        "pose_version",
        "x",
        "y",
        "polarity",
    }
    events: list[dict[str, Any]] = []
    event_ids: set[int] = set()
    previous_timestamp = -1
    for line_number, raw in enumerate(records[1:], 2):
        where = f"{path}:{line_number}"
        event = dict(_exact_keys(raw, event_keys, where))
        if event["schema"] != EVENT_SCHEMA or event["record_type"] != "event":
            raise InterfaceError(f"{where}: expected {EVENT_SCHEMA} event record")
        event_id = _nonnegative_int(event["event_id"], f"{where}.event_id")
        if event_id in event_ids:
            raise InterfaceError(f"{where}: duplicate event_id {event_id}")
        event_ids.add(event_id)
        sequence = _nonnegative_int(event["sequence_index"], f"{where}.sequence_index")
        if sequence != len(events):
            raise InterfaceError(f"{where}: sequence_index must be contiguous JSONL order")
        timestamp = _require_uint(
            event["timestamp_ns"],
            _RAW_SENSOR_PAYLOAD_WIDTHS["timestamp_bits"],
            f"{where}.timestamp_ns",
        )
        if timestamp < previous_timestamp:
            raise InterfaceError(f"{where}: timestamp_ns must be nondecreasing")
        previous_timestamp = timestamp
        event["pose_version"] = _string(event["pose_version"], f"{where}.pose_version")
        event["x"] = _require_uint(
            event["x"], _RAW_SENSOR_PAYLOAD_WIDTHS["x_bits"], f"{where}.x"
        )
        event["y"] = _require_uint(
            event["y"], _RAW_SENSOR_PAYLOAD_WIDTHS["y_bits"], f"{where}.y"
        )
        if event["x"] >= intrinsics.width or event["y"] >= intrinsics.height:
            raise InterfaceError(f"{where}: sensor coordinate is out of bounds")
        if type(event["polarity"]) is not int or event["polarity"] not in (-1, 1):
            raise InterfaceError(f"{where}.polarity must be JSON integer -1 or 1")
        events.append(event)
    if declared_count != len(events):
        raise InterfaceError(
            f"{path}: declared_event_count={declared_count} but parsed {len(events)}"
        )
    return header, events


def _tile(x: int, y: int, tile_width: int, tile_height: int, columns: int) -> dict[str, int]:
    tile_x = _require_uint(
        x // tile_width, _OCCUPANCY_PACKET_WIDTHS["tile_x_bits"], "logical tile.x"
    )
    tile_y = _require_uint(
        y // tile_height, _OCCUPANCY_PACKET_WIDTHS["tile_y_bits"], "logical tile.y"
    )
    return {"x": tile_x, "y": tile_y, "index": tile_y * columns + tile_x}


def _bins(records: list[dict[str, Any]], coordinate_key: str) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        tile = record[coordinate_key]["tile"]
        groups[(record["polarity"], tile["x"], tile["y"])].append(record)
    result: list[dict[str, Any]] = []
    for polarity, tile_x, tile_y in sorted(groups):
        members = groups[(polarity, tile_x, tile_y)]
        pixels = {(item[coordinate_key]["x"], item[coordinate_key]["y"]) for item in members}
        result.append(
            {
                "polarity": polarity,
                "tile": {"x": tile_x, "y": tile_y},
                "event_count": len(members),
                "same_tile_extra_events": len(members) - 1,
                "unique_pixel_count": len(pixels),
                "member_event_ids": [item["event_id"] for item in members],
            }
        )
    return result


def _polarity_occupancy(bins: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for polarity, label in ((-1, "negative"), (1, "positive")):
        selected = [item for item in bins if item["polarity"] == polarity]
        result[label] = {
            "event_count": sum(item["event_count"] for item in selected),
            "occupied_tiles": len(selected),
            "same_tile_extra_events": sum(
                item["same_tile_extra_events"] for item in selected
            ),
            "max_same_tile_multiplicity": max(
                (item["event_count"] for item in selected), default=0
            ),
        }
    return result


def _adjacency(records: list[dict[str, Any]], coordinate_key: str) -> tuple[int, int]:
    same = 0
    pairs = 0
    for polarity in (-1, 1):
        selected = [item for item in records if item["polarity"] == polarity]
        for left, right in zip(selected, selected[1:]):
            pairs += 1
            same += left[coordinate_key]["tile"] == right[coordinate_key]["tile"]
    return same, pairs


def _mean_polarity_spread(records: list[dict[str, Any]], coordinate_key: str) -> float:
    spreads: list[float] = []
    for polarity in (-1, 1):
        selected = [item for item in records if item["polarity"] == polarity]
        if not selected:
            continue
        points = [
            (item[coordinate_key]["tile"]["x"], item[coordinate_key]["tile"]["y"])
            for item in selected
        ]
        center_x = sum(point[0] for point in points) / len(points)
        center_y = sum(point[1] for point in points) / len(points)
        variance = sum(
            (x - center_x) ** 2 + (y - center_y) ** 2 for x, y in points
        ) / len(points)
        spreads.append(math.sqrt(variance))
    return _rounded(sum(spreads) / len(spreads)) if spreads else 0.0


def _representation(
    records: list[dict[str, Any]], coordinate_key: str
) -> dict[str, Any]:
    bins = _bins(records, coordinate_key)
    count = len(records)
    same, pairs = _adjacency(records, coordinate_key)
    counts = [item["event_count"] for item in bins]
    return {
        "bins": bins,
        "occupancy_by_polarity": _polarity_occupancy(bins),
        "metrics": {
            "event_count": count,
            "occupied_spatial_tiles": len(
                {(item["tile"]["x"], item["tile"]["y"]) for item in bins}
            ),
            "occupied_tile_polarity_bins": len(bins),
            "same_tile_extra_events": count - len(bins),
            "same_tile_coalescing_ratio": _ratio(count - len(bins), count),
            "max_same_tile_multiplicity": max(counts, default=0),
            "same_tile_adjacency_pairs": same,
            "adjacency_pairs": pairs,
            "same_tile_adjacency_ratio": _ratio(same, pairs),
            "concentration_hhi": _rounded(
                sum((bin_count / count) ** 2 for bin_count in counts) if count else 0.0
            ),
            "max_bin_fraction": _ratio(max(counts, default=0), count),
            "mean_polarity_rms_tile_spread": _mean_polarity_spread(
                records, coordinate_key
            ),
        },
    }


def _logical_packets(
    records: list[dict[str, Any]], coordinate_key: str, time_bin_ns: int
) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        tile = record[coordinate_key]["tile"]
        time_bin_index = record["timestamp_ns"] // time_bin_ns
        key = (
            time_bin_index,
            record["pose_version_code"],
            record["polarity"],
            tile["x"],
            tile["y"],
        )
        groups[key].append(record)
    packets: list[dict[str, Any]] = []
    for time_bin, pose_code, polarity, tile_x, tile_y in sorted(groups):
        members = groups[(time_bin, pose_code, polarity, tile_x, tile_y)]
        time_bin_start = _require_uint(
            time_bin * time_bin_ns,
            _OCCUPANCY_PACKET_WIDTHS["time_bin_start_bits"],
            "logical packet.time_bin_start_ns",
        )
        pose_code = _require_uint(
            pose_code,
            _OCCUPANCY_PACKET_WIDTHS["pose_version_bits"],
            "logical packet.pose_version_code",
        )
        tile_x = _require_uint(
            tile_x, _OCCUPANCY_PACKET_WIDTHS["tile_x_bits"], "logical packet.tile.x"
        )
        tile_y = _require_uint(
            tile_y, _OCCUPANCY_PACKET_WIDTHS["tile_y_bits"], "logical packet.tile.y"
        )
        multiplicity = _require_uint(
            len(members),
            _OCCUPANCY_PACKET_WIDTHS["multiplicity_count_bits"],
            "logical packet.multiplicity_count",
        )
        packets.append(
            {
                "time_bin_index": time_bin,
                "time_bin_start_ns": time_bin_start,
                "pose_version": members[0]["pose_version"],
                "pose_version_code": pose_code,
                "polarity": polarity,
                "tile": {"x": tile_x, "y": tile_y},
                "multiplicity_count": multiplicity,
                "coalesced_event_id_count": len(members) - 1,
                "member_event_ids": [member["event_id"] for member in members],
            }
        )
    return packets


def _logical_bits(event_count: int, packet_count: int) -> dict[str, Any]:
    raw_width = sum(LOGICAL_BIT_FORMAT["raw_sensor_payload"].values())
    occupancy_width = sum(LOGICAL_BIT_FORMAT["occupancy_packet"].values())
    raw_bits = event_count * raw_width
    occupancy_bits = packet_count * occupancy_width
    return {
        "raw_sensor_payload_width_bits": raw_width,
        "raw_sensor_payload_total_bits": raw_bits,
        "occupancy_packet_width_bits": occupancy_width,
        "occupancy_packet_count": packet_count,
        "occupancy_projection_total_bits": occupancy_bits,
        "occupancy_projection_bits_per_input_event": _ratio(
            occupancy_bits, event_count
        ),
        "occupancy_projection_reduction_vs_raw_sensor_payload": _ratio(
            raw_bits - occupancy_bits, raw_bits
        ),
    }


class _PosixFileOps:
    """Thin syscall adapter used for deterministic namespace fault tests."""

    def open(
        self,
        path: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        return os.open(path, flags, mode, dir_fd=dir_fd)

    def write(self, descriptor: int, data: bytes | memoryview) -> int:
        return os.write(descriptor, data)

    def fstat(self, descriptor: int) -> os.stat_result:
        return os.fstat(descriptor)

    def statat(
        self,
        path: str | Path,
        *,
        dir_fd: int | None,
        follow_symlinks: bool,
    ) -> os.stat_result:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def renameat(
        self, source: str, target: str, *, src_dir_fd: int, dst_dir_fd: int
    ) -> None:
        os.rename(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def unlinkat(self, path: str, *, dir_fd: int) -> None:
        os.unlink(path, dir_fd=dir_fd)

    def close(self, descriptor: int) -> None:
        os.close(descriptor)


_FILE_OPS = _PosixFileOps()

_HARDENED_DIRFD_SUPPORTED = (
    all(hasattr(os, name) for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"))
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.rename, os.unlink)
    )
    and os.stat in os.supports_follow_symlinks
)


def _require_hardened_dirfd_support() -> None:
    if not _HARDENED_DIRFD_SUPPORTED:
        raise InterfaceError("hardened dirfd publication unsupported on this platform")


def _publish_json_dirfd(
    path: Path,
    value: Any,
    input_blobs: tuple[InputBlob, ...],
    *,
    ops: _PosixFileOps | None = None,
) -> None:
    """Publish complete JSON by same-directory rename in one pinned parent inode."""

    _require_hardened_dirfd_support()
    payload = (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    target_name = path.name
    if target_name in ("", ".", "..") or Path(target_name).name != target_name:
        raise InterfaceError("output target must be one ordinary basename component")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    file_ops = ops or _FILE_OPS
    parent_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None
    try:
        parent_fd = file_ops.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        pinned_parent = file_ops.fstat(parent_fd)
        if not stat.S_ISDIR(pinned_parent.st_mode):
            raise InterfaceError("output parent descriptor is not a directory")
        for _ in range(128):
            candidate = f".{target_name}.{secrets.token_hex(12)}.tmp"
            try:
                temporary_fd = file_ops.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if temporary_fd is None or temporary_name is None:
            raise InterfaceError("cannot allocate a unique hardened output temporary")

        offset = 0
        while offset < len(payload):
            try:
                written = file_ops.write(temporary_fd, memoryview(payload)[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise InterfaceError("hardened output write made no progress")
            offset += written
        file_ops.close(temporary_fd)
        temporary_fd = None

        try:
            current_parent = file_ops.statat(
                parent, dir_fd=None, follow_symlinks=False
            )
        except FileNotFoundError as exc:
            raise InterfaceError("output parent path disappeared before publication") from exc
        if (
            not stat.S_ISDIR(current_parent.st_mode)
            or (current_parent.st_dev, current_parent.st_ino)
            != (pinned_parent.st_dev, pinned_parent.st_ino)
        ):
            raise InterfaceError("output parent path no longer names the pinned directory")

        try:
            target_stat = file_ops.statat(
                target_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            if stat.S_ISLNK(target_stat.st_mode):
                raise InterfaceError("output target symlinks are forbidden")
            if not stat.S_ISREG(target_stat.st_mode):
                raise InterfaceError("existing output target must be a regular file")
            input_identities = {(blob.device, blob.inode) for blob in input_blobs}
            if (target_stat.st_dev, target_stat.st_ino) in input_identities:
                raise InterfaceError("output target aliases an immutable input inode")

        file_ops.renameat(
            temporary_name,
            target_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
    except InterfaceError:
        raise
    except OSError as exc:
        raise InterfaceError(f"hardened output publication failed: {exc}") from exc
    finally:
        if temporary_fd is not None:
            try:
                file_ops.close(temporary_fd)
            except OSError:
                pass
        if temporary_name is not None and parent_fd is not None:
            try:
                file_ops.unlinkat(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        if parent_fd is not None:
            try:
                file_ops.close(parent_fd)
            except OSError:
                pass


def _analyze_blobs(
    event_blob: InputBlob,
    intrinsics_blob: InputBlob,
    pose_blob: InputBlob,
    *,
    tile_width: int,
    tile_height: int,
    time_bin_ns: int,
    max_pose_age_ns: int,
) -> dict[str, Any]:
    """Analyze three already-pinned immutable blobs without further path reads."""

    intrinsics = parse_intrinsics_blob(intrinsics_blob)
    pose_header, poses = parse_pose_stream_blob(pose_blob, intrinsics)
    header, events = _load_events(event_blob, intrinsics, pose_header)
    if len(poses) > 2 ** _RAW_SENSOR_PAYLOAD_WIDTHS["pose_version_bits"]:
        raise InterfaceError("pose stream exceeds the fixed 16-bit pose-version dictionary")
    pose_by_version: dict[str, Pose] = {pose.pose_id: pose for pose in poses}
    pose_code_by_version = {pose.pose_id: index for index, pose in enumerate(poses)}
    pose_timestamps = [pose.timestamp.value for pose in poses]
    tile_columns = (intrinsics.width + tile_width - 1) // tile_width
    tile_rows = (intrinsics.height + tile_height - 1) // tile_height

    exact_records: list[dict[str, Any]] = []
    maximum_pose_age = 0
    for event in events:
        pose = pose_by_version.get(event["pose_version"])
        if pose is None:
            raise InterfaceError(
                f"event {event['event_id']}: unknown pose_version {event['pose_version']!r}"
            )
        if pose.timestamp.value > event["timestamp_ns"]:
            raise InterfaceError(
                f"event {event['event_id']}: pose_version is from the future"
            )
        latest_index = bisect_right(pose_timestamps, event["timestamp_ns"]) - 1
        if latest_index < 0:
            raise InterfaceError(
                f"event {event['event_id']}: no pose exists at or before its timestamp"
            )
        latest_pose = poses[latest_index]
        if pose.pose_id != latest_pose.pose_id:
            raise InterfaceError(
                f"event {event['event_id']}: pose_version {pose.pose_id!r} is not "
                f"the deterministic latest pose {latest_pose.pose_id!r} at or before "
                "the event timestamp"
            )
        pose_age = event["timestamp_ns"] - pose.timestamp.value
        if pose_age > max_pose_age_ns:
            raise InterfaceError(
                f"event {event['event_id']}: pose age {pose_age} exceeds inclusive "
                f"maximum {max_pose_age_ns} ns"
            )
        maximum_pose_age = max(maximum_pose_age, pose_age)
        reference = warp_pixel(
            event["x"],
            event["y"],
            intrinsics,
            pose.rotation_world_to_sensor,
            "sensor-to-world",
        )
        if reference["status"] != "in_fov":
            raise InterfaceError(
                f"event {event['event_id']}: sensor-to-reference warp is out_of_fov "
                f"({reference['reason']})"
            )
        pose_code = _require_uint(
            pose_code_by_version[event["pose_version"]],
            _RAW_SENSOR_PAYLOAD_WIDTHS["pose_version_bits"],
            f"event {event['event_id']}.pose_version_code",
        )
        exact_records.append(
            {
                "event_id": event["event_id"],
                "sequence_index": event["sequence_index"],
                "timestamp_ns": event["timestamp_ns"],
                "pose_version": event["pose_version"],
                "pose_version_code": pose_code,
                "pose_timestamp_ns": pose.timestamp.value,
                "pose_age_ns": pose_age,
                "polarity": event["polarity"],
                "sensor": {
                    "x": event["x"],
                    "y": event["y"],
                    "tile": _tile(
                        event["x"], event["y"], tile_width, tile_height, tile_columns
                    ),
                },
                "reference": {
                    "x": reference["x"],
                    "y": reference["y"],
                    "x_float": _rounded(reference["x_float"]),
                    "y_float": _rounded(reference["y_float"]),
                    "tile": _tile(
                        reference["x"],
                        reference["y"],
                        tile_width,
                        tile_height,
                        tile_columns,
                    ),
                },
            }
        )

    sensor = _representation(exact_records, "sensor")
    compensated = _representation(exact_records, "reference")
    sensor_packets = _logical_packets(exact_records, "sensor", time_bin_ns)
    compensated_packets = _logical_packets(exact_records, "reference", time_bin_ns)
    sensor["logical_occupancy_packets"] = sensor_packets
    compensated["logical_occupancy_packets"] = compensated_packets
    sensor["metrics"]["logical_occupancy_packet_count"] = len(sensor_packets)
    compensated["metrics"]["logical_occupancy_packet_count"] = len(compensated_packets)
    sensor_bits = _logical_bits(
        len(exact_records), len(sensor_packets)
    )
    compensated_bits = _logical_bits(
        len(exact_records), len(compensated_packets)
    )
    positive = sum(record["polarity"] == 1 for record in exact_records)
    negative = len(exact_records) - positive

    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "evidence_class": EVIDENCE_CLASS,
        "model_scope": {
            "coordinate_output": "fixed_reference_camera_image",
            "pose_source": "externally_supplied_explicit_pose_version",
            "rotation_only": True,
            "unsupported": list(UNSUPPORTED_FEATURES),
        },
        "analysis_contract": {
            "implementation_id": MODEL_IMPLEMENTATION_ID,
            "known_motion_blob_api_id": KNOWN_MOTION_BLOB_API_ID,
            "result_contract_revision": RESULT_CONTRACT_REVISION,
            "parameters": {
                "tile_width": tile_width,
                "tile_height": tile_height,
                "time_bin_ns": time_bin_ns,
                "max_pose_age_ns": max_pose_age_ns,
            },
            "logical_bit_format_id": LOGICAL_BIT_FORMAT["format_id"],
            "pose_lookup_rule": "latest-at-or-before-zoh",
        },
        "input_contract": {
            "camera_id": header["camera_id"],
            "intrinsics_id": header["intrinsics_id"],
            "pose_stream_id": header["pose_stream_id"],
            "timebase": header["timebase"],
            "atomic_event_fields": ["timestamp_ns", "pose_version"],
            "pose_lookup_rule": "deterministic latest supplied pose at or before timestamp",
            "pose_version_encoding": "16-bit index in timestamp-sorted supplied pose stream",
            "analysis_provenance_fields_excluded_from_raw_sensor_payload": [
                "event_id",
                "sequence_index",
            ],
        },
        "input_provenance": {
            "hash_algorithm": "SHA-256",
            "hash_scope": "exact immutable bytes consumed by each parser",
            "snapshot_scope": (
                "each file is independently snapshotted once from one pinned descriptor; "
                "no atomic three-file snapshot is claimed"
            ),
            "canonical_evidence_claimed": False,
            "events_sha256": event_blob.sha256,
            "intrinsics_sha256": intrinsics_blob.sha256,
            "poses_sha256": pose_blob.sha256,
        },
        "tiling": {
            "image_width": intrinsics.width,
            "image_height": intrinsics.height,
            "tile_width": tile_width,
            "tile_height": tile_height,
            "tile_columns": tile_columns,
            "tile_rows": tile_rows,
            "logical_time_bin_ns": time_bin_ns,
        },
        "exact_input_count_ledger": {
            "declared_input_events": header["declared_event_count"],
            "parsed_input_events": len(events),
            "atomic_timestamp_pose_bindings": len(exact_records),
            "sensor_fixed_assignments": len(exact_records),
            "pose_compensated_assignments": len(exact_records),
            "positive_events": positive,
            "negative_events": negative,
            "dropped_events": 0,
            "unaccounted_events": 0,
        },
        "exact_event_ledger": exact_records,
        "representations": {
            "sensor_fixed": sensor,
            "pose_compensated_reference": compensated,
        },
        "same_tile_multiplicity_disclosure": {
            "occupancy_projection_is_lossy": True,
            "reversible_codec_implemented": False,
            "logical_packet_key": [
                "time_bin_index",
                "pose_version",
                "polarity",
                "tile_x",
                "tile_y",
            ],
            "multiplicity_count_is_preserved": True,
            "sensor_fixed_coalesced_event_id_count": len(exact_records)
            - len(sensor_packets),
            "pose_compensated_coalesced_event_id_count": len(exact_records)
            - len(compensated_packets),
            "exact_event_ledger_remains_complete": True,
        },
        "logical_bit_accounting": {
            "format": _logical_bit_format_json(),
            "scope": (
                "declared logical fixed-width projection; not JSON bytes, actual wire "
                "bandwidth, RTL, or a codec"
            ),
            "provenance_exclusion": (
                "event_id and sequence_index support analysis traceability and are "
                "excluded from the raw sensor payload convention"
            ),
            "sensor_fixed": sensor_bits,
            "pose_compensated_reference": compensated_bits,
        },
        "bottleneck_metrics": {
            "1_packet_key_projection_not_wire_bandwidth": {
                "sensor_fixed_projected_bits": sensor_bits[
                    "occupancy_projection_total_bits"
                ],
                "pose_compensated_projected_bits": compensated_bits[
                    "occupancy_projection_total_bits"
                ],
                "projected_delta_bits": sensor_bits[
                    "occupancy_projection_total_bits"
                ]
                - compensated_bits["occupancy_projection_total_bits"],
                "projected_delta_ratio": _ratio(
                    sensor_bits["occupancy_projection_total_bits"]
                    - compensated_bits["occupancy_projection_total_bits"],
                    sensor_bits["occupancy_projection_total_bits"],
                ),
                "actual_wire_bandwidth_measured": False,
                "actual_codec_implemented": False,
                "caveat": (
                    "this is only a fixed packet-key projection comparison; it cannot "
                    "be quoted as actual wire bandwidth reduction. The projection preserves "
                    "time-bin, pose version, polarity, tile, and count, but omits per-event "
                    "identity, intra-bin timestamp, and intra-tile coordinate"
                ),
            },
            "5_timestamp_fidelity": {
                "atomic_timestamp_pose_bindings": len(exact_records),
                "binding_errors": 0,
                "timestamp_reorder_errors": 0,
                "maximum_pose_age_ns": maximum_pose_age,
                "transport_timestamp_error_measured": False,
            },
            "6_motion_reference_locality": {
                "sensor_fixed_mean_polarity_rms_tile_spread": sensor["metrics"][
                    "mean_polarity_rms_tile_spread"
                ],
                "pose_compensated_mean_polarity_rms_tile_spread": compensated[
                    "metrics"
                ]["mean_polarity_rms_tile_spread"],
                "spread_reduction": _rounded(
                    sensor["metrics"]["mean_polarity_rms_tile_spread"]
                    - compensated["metrics"]["mean_polarity_rms_tile_spread"]
                ),
                "sensor_fixed_same_tile_adjacency_ratio": sensor["metrics"][
                    "same_tile_adjacency_ratio"
                ],
                "pose_compensated_same_tile_adjacency_ratio": compensated[
                    "metrics"
                ]["same_tile_adjacency_ratio"],
                "same_tile_adjacency_gain": _rounded(
                    compensated["metrics"]["same_tile_adjacency_ratio"]
                    - sensor["metrics"]["same_tile_adjacency_ratio"]
                ),
                "sensor_fixed_concentration_hhi": sensor["metrics"][
                    "concentration_hhi"
                ],
                "pose_compensated_concentration_hhi": compensated["metrics"]
                ["concentration_hhi"],
                "concentration_gain": _rounded(
                    compensated["metrics"]["concentration_hhi"]
                    - sensor["metrics"]["concentration_hhi"]
                ),
                "world_reconstruction_error_measured": False,
            },
        },
        "output_semantics": {
            "atomic_visibility": (
                "mode-0600 temporary regular file plus same-directory dirfd-relative "
                "POSIX atomic rename in a pinned parent inode"
            ),
            "namespace_hardening": "required-posix-dirfd-no-weak-fallback",
            "crash_durability_guaranteed": False,
            "file_fsync_performed": False,
            "directory_fsync_performed": False,
        },
    }
    return result


def analyze_files(
    events_path: str | Path,
    intrinsics_path: str | Path,
    poses_path: str | Path,
    output_path: str | Path,
    *,
    tile_width: int,
    tile_height: int,
    time_bin_ns: int,
    max_pose_age_ns: int,
) -> dict[str, Any]:
    """Analyze one-read immutable inputs and publish through a pinned POSIX dirfd."""

    tile_width = _positive_int(tile_width, "tile_width")
    tile_height = _positive_int(tile_height, "tile_height")
    time_bin_ns = _positive_int(time_bin_ns, "time_bin_ns")
    max_pose_age_ns = _nonnegative_int(max_pose_age_ns, "max_pose_age_ns")
    with ExitStack() as stack:
        event_blob = stack.enter_context(open_input_blob(events_path, "events input"))
        intrinsics_blob = stack.enter_context(
            open_input_blob(intrinsics_path, "intrinsics input")
        )
        pose_blob = stack.enter_context(open_input_blob(poses_path, "poses input"))
        result = _analyze_blobs(
            event_blob,
            intrinsics_blob,
            pose_blob,
            tile_width=tile_width,
            tile_height=tile_height,
            time_bin_ns=time_bin_ns,
            max_pose_age_ns=max_pose_age_ns,
        )
        _publish_json_dirfd(
            Path(output_path), result, (event_blob, intrinsics_blob, pose_blob)
        )
        return result


__all__ = [
    "EVIDENCE_CLASS",
    "EVENT_HEADER_SCHEMA",
    "EVENT_SCHEMA",
    "InterfaceError",
    "LOGICAL_BIT_FORMAT",
    "MODEL_IMPLEMENTATION_ID",
    "RESULT_SCHEMA",
    "RESULT_CONTRACT_REVISION",
    "UNSUPPORTED_FEATURES",
    "analyze_files",
]
