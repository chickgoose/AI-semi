#!/usr/bin/env python3
"""Stream, project, package, and inspect the pinned UZH shapes_rotation window."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator


SPEC_SCHEMA = "redred-uzh-shapes-projection-spec-v1"
RECEIPT_SCHEMA = "redred-uzh-shapes-projection-receipt-v1"
COMPLETION_SCHEMA = "redred-uzh-shapes-projection-completion-v1"
STATUS = "PUBLIC_PROJECTED_EXTENSION_UNREPLAYED"
HOLD = "HOLD"
QUALIFY_STATUS = "HOLD_PUBLIC_PROJECTED_EXTENSION_UNREPLAYED"
PROJECTION_NAME = "projected_events.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETION_NAME = "COMPLETE.json"
LICENSE_NAME = "LICENSE.txt"
TRACE_FIELDS = (
    "occurrence_cycle",
    "tb_only_event_id",
    "logical_source",
    "x",
    "y",
    "polarity",
    "event_type",
    "relation_id",
    "relation_role",
    "deadline",
)
PROJECTION_FIELDS = (
    "dataset_event_index",
    "timestamp_seconds",
    "x",
    "y",
    "polarity",
    "bx",
    "by",
    "logical_source",
)
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(rb"(0|[1-9][0-9]{0,9})\.([0-9]{9})\Z")
INTEGER_RE = re.compile(rb"0|[1-9][0-9]{0,9}\Z")


class ProjectionFailure(ValueError):
    """The source, specification, output, or completed package is invalid."""


@dataclass(frozen=True)
class Event:
    dataset_event_index: int
    timestamp_text: str
    timestamp_ns: int
    x: int
    y: int
    polarity: int
    bx: int
    by: int
    source: int


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_keys(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectionFailure(f"{where} must be an object")
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing or extra:
        raise ProjectionFailure(f"{where} keys mismatch: missing={missing} extra={extra}")
    return value


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectionFailure(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_float(text: str) -> Any:
    raise ProjectionFailure(f"JSON floating point is forbidden: {text}")


def _json_bytes(data: bytes, where: str) -> Any:
    try:
        return json.loads(
            data.decode("ascii"), object_pairs_hook=_json_pairs,
            parse_float=_reject_float, parse_constant=_reject_float,
        )
    except ProjectionFailure:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ProjectionFailure(f"invalid JSON in {where}: {error}") from error


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise ProjectionFailure(f"cannot inspect path component {current}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise ProjectionFailure(f"symlink path component is forbidden: {current}")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _stable_open(path: Path) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    _reject_symlink_components(path)
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ProjectionFailure(f"cannot stat {path}: {error}") from error
    if not stat.S_ISREG(before.st_mode):
        raise ProjectionFailure(f"input is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProjectionFailure(f"cannot open {path}: {error}") from error
    stream = os.fdopen(descriptor, "rb", closefd=False)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise ProjectionFailure(f"input changed before open: {path}")
        yield stream, opened
        stream.flush()
        after = os.fstat(descriptor)
        final = path.stat(follow_symlinks=False)
        if _identity(after) != _identity(before) or _identity(final) != _identity(before):
            raise ProjectionFailure(f"input changed during read: {path}")
    except OSError as error:
        raise ProjectionFailure(f"stable read failed for {path}: {error}") from error
    finally:
        stream.close()
        os.close(descriptor)


def _stream_digest(stream: BinaryIO, max_bytes: int | None = None) -> tuple[str, int, bytes | None]:
    digest = hashlib.sha256()
    size = 0
    capture = bytearray() if max_bytes is not None else None
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            raise ProjectionFailure(f"artifact exceeds resource limit of {max_bytes} bytes")
        digest.update(chunk)
        if capture is not None:
            capture.extend(chunk)
    return digest.hexdigest(), size, bytes(capture) if capture is not None else None


def _artifact(row: Any, where: str, *, crc: bool = False) -> dict[str, Any]:
    keys = {"basename", "size_bytes", "sha256"}
    if where.endswith("archive"):
        keys |= {"events_member", "events_member_size_bytes", "events_member_crc32"}
    value = _strict_keys(row, keys, where)
    if not isinstance(value["basename"], str) or Path(value["basename"]).name != value["basename"]:
        raise ProjectionFailure(f"{where}.basename must be a plain basename")
    if isinstance(value["size_bytes"], bool) or not isinstance(value["size_bytes"], int) or value["size_bytes"] <= 0:
        raise ProjectionFailure(f"{where}.size_bytes must be positive")
    if not isinstance(value["sha256"], str) or not SHA_RE.fullmatch(value["sha256"]):
        raise ProjectionFailure(f"{where}.sha256 is invalid")
    if crc and not re.fullmatch(r"[0-9a-f]{8}", value["events_member_crc32"]):
        raise ProjectionFailure(f"{where}.events_member_crc32 is invalid")
    return value


def validate_spec(spec: Any) -> dict[str, Any]:
    top = _strict_keys(spec, {
        "schema", "status", "release_status", "dataset", "artifacts", "input_format",
        "sensor_geometry", "projection", "window", "clock", "scenarios",
        "resource_limits", "lineage",
    }, "spec")
    if top["schema"] != SPEC_SCHEMA or top["status"] != STATUS or top["release_status"] != HOLD:
        raise ProjectionFailure("spec status/schema must remain unreplayed HOLD")
    dataset = _strict_keys(top["dataset"], {
        "provider", "collection", "sequence", "sensor", "license_spdx",
        "canonical_redred_traffic", "official_redred_traffic",
    }, "dataset")
    if (dataset["sequence"] != "shapes_rotation" or dataset["sensor"] != "DAVIS240C" or
            dataset["license_spdx"] != "CC-BY-NC-SA-3.0" or
            dataset["canonical_redred_traffic"] is not False or
            dataset["official_redred_traffic"] is not False):
        raise ProjectionFailure("dataset identity/classification mismatch")
    artifacts = _strict_keys(top["artifacts"], {"archive", "events", "license"}, "artifacts")
    _artifact(artifacts["archive"], "artifacts.archive", crc=True)
    events_artifact = _strict_keys(artifacts["events"], {"basename", "size_bytes", "line_count", "sha256"}, "artifacts.events")
    _artifact({key: events_artifact[key] for key in ("basename", "size_bytes", "sha256")}, "events")
    if isinstance(events_artifact["line_count"], bool) or not isinstance(events_artifact["line_count"], int) or events_artifact["line_count"] <= 0:
        raise ProjectionFailure("events line_count must be positive")
    _artifact(artifacts["license"], "license")
    fmt = _strict_keys(top["input_format"], {"fields", "separator", "timestamp_fractional_digits", "polarity_values"}, "input_format")
    if (fmt["fields"] != ["timestamp_seconds", "x", "y", "polarity"] or
            fmt["separator"] != "single_ascii_space" or fmt["timestamp_fractional_digits"] != 9 or
            fmt["polarity_values"] != [0, 1]):
        raise ProjectionFailure("input format mismatch")
    if top["sensor_geometry"] != {"width": 240, "height": 180}:
        raise ProjectionFailure("sensor geometry must be 240x180")
    projection = _strict_keys(top["projection"], {
        "width", "height", "bx_formula", "by_formula", "source_formula",
        "preserve_every_event", "preserve_collisions",
    }, "projection")
    if projection != {
        "width": 4, "height": 4, "bx_formula": "floor(x*4/240)",
        "by_formula": "floor(y*4/180)", "source_formula": "4*by+bx",
        "preserve_every_event": True, "preserve_collisions": True,
    }:
        raise ProjectionFailure("projection contract mismatch")
    window = _strict_keys(top["window"], {
        "start_seconds_inclusive", "end_seconds_exclusive", "expected_event_count",
        "expected_first_dataset_event_index", "expected_last_dataset_event_index",
    }, "window")
    for key in ("start_seconds_inclusive", "end_seconds_exclusive"):
        if not isinstance(window[key], str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})\.[0-9]{1,9}", window[key]):
            raise ProjectionFailure(f"window.{key} must be an exact canonical decimal")
    start = _seconds_to_ns(window["start_seconds_inclusive"])
    end = _seconds_to_ns(window["end_seconds_exclusive"])
    if start >= end or not all(isinstance(window[key], int) and not isinstance(window[key], bool) for key in (
        "expected_event_count", "expected_first_dataset_event_index", "expected_last_dataset_event_index"
    )):
        raise ProjectionFailure("window bounds/counts are invalid")
    clock = _strict_keys(top["clock"], {"period_ns", "period_rational_ns", "cycle_formula"}, "clock")
    if clock != {
        "period_ns": "6.5", "period_rational_ns": {"numerator": 13, "denominator": 2},
        "cycle_formula": "floor((timestamp_ns-window_start_ns)*2/(13*time_compression))",
    }:
        raise ProjectionFailure("clock/cycle formula mismatch")
    scenarios = top["scenarios"]
    if scenarios != [
        {"id": "1x", "time_compression": 1},
        {"id": "64x", "time_compression": 64},
        {"id": "256x", "time_compression": 256},
    ]:
        raise ProjectionFailure("scenario set/order mismatch")
    limits = _strict_keys(top["resource_limits"], {
        "max_input_line_bytes", "max_selected_events", "max_license_bytes", "max_zip_entries",
    }, "resource_limits")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ProjectionFailure("resource limits must be positive integers")
    lineage = _strict_keys(top["lineage"], {
        "interface", "p6_evidence_used", "actual_replay_receipt", "replay_status",
    }, "lineage")
    if lineage != {
        "interface": "SINGLE_EDGE_REPLAY_PREPARER_INPUT_ONLY", "p6_evidence_used": False,
        "actual_replay_receipt": None, "replay_status": "UNREPLAYED",
    }:
        raise ProjectionFailure("lineage must remain single-edge-only and unreplayed")
    return top


def _seconds_to_ns(text: str) -> int:
    if not isinstance(text, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,9})", text):
        raise ProjectionFailure(f"invalid exact seconds decimal: {text!r}")
    whole, fractional = text.split(".")
    return int(whole) * 1_000_000_000 + int(fractional.ljust(9, "0"))


def _load_spec(path: Path) -> tuple[dict[str, Any], bytes]:
    with _stable_open(path) as (stream, info):
        if info.st_size > 1024 * 1024:
            raise ProjectionFailure("specification exceeds 1 MiB")
        data = stream.read()
    return validate_spec(_json_bytes(data, "specification")), data


def _verify_zip(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    expected = spec["artifacts"]["archive"]
    events_expected = spec["artifacts"]["events"]
    with _stable_open(path) as (stream, info):
        digest, size, _ = _stream_digest(stream)
        if size != info.st_size or size != expected["size_bytes"] or digest != expected["sha256"]:
            raise ProjectionFailure("archive size/SHA mismatch")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                if len(infos) > spec["resource_limits"]["max_zip_entries"]:
                    raise ProjectionFailure("zip entry count exceeds resource limit")
                matches = [row for row in infos if row.filename == expected["events_member"]]
                if len(matches) != 1:
                    raise ProjectionFailure("archive must contain exactly one events.txt member")
                member = matches[0]
                if (member.is_dir() or member.file_size != expected["events_member_size_bytes"] or
                        f"{member.CRC:08x}" != expected["events_member_crc32"]):
                    raise ProjectionFailure("archive events member metadata mismatch")
                member_digest = hashlib.sha256()
                member_size = 0
                with archive.open(member, "r") as member_stream:
                    while True:
                        chunk = member_stream.read(1024 * 1024)
                        if not chunk:
                            break
                        member_size += len(chunk)
                        member_digest.update(chunk)
                if member_size != events_expected["size_bytes"] or member_digest.hexdigest() != events_expected["sha256"]:
                    raise ProjectionFailure("archive events member does not match extracted events identity")
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ProjectionFailure(f"invalid archive: {error}") from error
    return {"basename": expected["basename"], "size_bytes": size, "sha256": digest,
            "events_member": expected["events_member"], "events_member_sha256": events_expected["sha256"]}


def _parse_source_line(raw: bytes, index: int, width: int, height: int) -> tuple[str, int, int, int, int]:
    if not raw.endswith(b"\n"):
        raise ProjectionFailure(f"events line {index + 1} lacks LF terminator")
    payload = raw[:-1]
    if payload.endswith(b"\r") or b"\t" in payload or payload.count(b" ") != 3:
        raise ProjectionFailure(f"events line {index + 1} is not four single-space fields")
    timestamp, x_text, y_text, polarity_text = payload.split(b" ")
    match = TIMESTAMP_RE.fullmatch(timestamp)
    if match is None:
        raise ProjectionFailure(f"events line {index + 1} timestamp is not an exact 9-digit decimal")
    if INTEGER_RE.fullmatch(x_text) is None or INTEGER_RE.fullmatch(y_text) is None or polarity_text not in (b"0", b"1"):
        raise ProjectionFailure(f"events line {index + 1} has a noncanonical coordinate/polarity")
    x, y, polarity = int(x_text), int(y_text), int(polarity_text)
    if not 0 <= x < width or not 0 <= y < height:
        raise ProjectionFailure(f"events line {index + 1} coordinate is outside {width}x{height}")
    timestamp_ns = int(match.group(1)) * 1_000_000_000 + int(match.group(2))
    return timestamp.decode("ascii"), timestamp_ns, x, y, polarity


def _scan_events(path: Path, spec: dict[str, Any]) -> tuple[list[Event], dict[str, Any]]:
    expected = spec["artifacts"]["events"]
    width, height = spec["sensor_geometry"]["width"], spec["sensor_geometry"]["height"]
    start = _seconds_to_ns(spec["window"]["start_seconds_inclusive"])
    end = _seconds_to_ns(spec["window"]["end_seconds_exclusive"])
    max_line = spec["resource_limits"]["max_input_line_bytes"]
    max_selected = spec["resource_limits"]["max_selected_events"]
    selected: list[Event] = []
    digest = hashlib.sha256()
    total_size = 0
    line_count = 0
    previous_timestamp = -1
    with _stable_open(path) as (stream, info):
        for index, raw in enumerate(stream):
            line_count += 1
            total_size += len(raw)
            digest.update(raw)
            if len(raw) > max_line:
                raise ProjectionFailure(f"events line {line_count} exceeds resource limit")
            timestamp_text, timestamp_ns, x, y, polarity = _parse_source_line(raw, index, width, height)
            if timestamp_ns < previous_timestamp:
                raise ProjectionFailure(f"events timestamps decrease at line {line_count}")
            previous_timestamp = timestamp_ns
            if start <= timestamp_ns < end:
                bx = x * 4 // width
                by = y * 4 // height
                source = 4 * by + bx
                if not 0 <= bx < 4 or not 0 <= by < 4 or not 0 <= source < 16:
                    raise ProjectionFailure("internal binning result is outside 4x4")
                selected.append(Event(index, timestamp_text, timestamp_ns, x, y, polarity, bx, by, source))
                if len(selected) > max_selected:
                    raise ProjectionFailure("selected event count exceeds resource limit")
        if total_size != info.st_size:
            raise ProjectionFailure("events byte accounting mismatch")
    actual_sha = digest.hexdigest()
    if total_size != expected["size_bytes"] or actual_sha != expected["sha256"] or line_count != expected["line_count"]:
        raise ProjectionFailure("events size/SHA/line-count mismatch")
    window = spec["window"]
    if (len(selected) != window["expected_event_count"] or not selected or
            selected[0].dataset_event_index != window["expected_first_dataset_event_index"] or
            selected[-1].dataset_event_index != window["expected_last_dataset_event_index"]):
        raise ProjectionFailure("window count/index anchors mismatch")
    return selected, {"basename": expected["basename"], "size_bytes": total_size,
                      "line_count": line_count, "sha256": actual_sha}


def _read_license(path: Path, spec: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    expected = spec["artifacts"]["license"]
    with _stable_open(path) as (stream, info):
        digest, size, data = _stream_digest(stream, spec["resource_limits"]["max_license_bytes"])
        if size != info.st_size or size != expected["size_bytes"] or digest != expected["sha256"]:
            raise ProjectionFailure("license size/SHA mismatch")
    assert data is not None
    if not data.startswith(b"Creative Commons Legal Code\n\nAttribution-NonCommercial-ShareAlike 3.0 Unported\n"):
        raise ProjectionFailure("license content identity header mismatch")
    return data, {"basename": expected["basename"], "size_bytes": size, "sha256": digest,
                  "spdx_id": spec["dataset"]["license_spdx"]}


def _projection_row(event: Event) -> dict[str, Any]:
    return {
        "dataset_event_index": event.dataset_event_index,
        "timestamp_seconds": event.timestamp_text,
        "x": event.x,
        "y": event.y,
        "polarity": event.polarity,
        "bx": event.bx,
        "by": event.by,
        "logical_source": event.source,
    }


def _cycle(event: Event, start_ns: int, compression: int) -> int:
    delta = event.timestamp_ns - start_ns
    if delta < 0:
        raise ProjectionFailure("event precedes cycle origin")
    return (delta * 2) // (13 * compression)


def _trace_row(event: Event, event_id: int, cycle: int, deadline: int) -> dict[str, Any]:
    return {
        "occurrence_cycle": cycle,
        "tb_only_event_id": event_id,
        "logical_source": event.source,
        "x": event.bx,
        "y": event.by,
        "polarity": 1 if event.polarity == 1 else -1,
        "event_type": "public_projected_event",
        "relation_id": None,
        "relation_role": None,
        "deadline": deadline,
    }


def _jsonl(rows: Iterator[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(row) + b"\n" for row in rows)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o444)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise ProjectionFailure(f"cannot exclusively write {path}: {error}") from error


def _create_result_dir(path: Path) -> None:
    _reject_symlink_components(path.parent)
    try:
        path.mkdir(mode=0o755, parents=False, exist_ok=False)
    except FileExistsError as error:
        raise ProjectionFailure(f"result path already exists: {path}") from error
    except OSError as error:
        raise ProjectionFailure(f"cannot create result directory {path}: {error}") from error


def project(archive_path: Path, events_path: Path, license_path: Path,
            spec_path: Path, result_dir: Path,
            *, _write: Callable[[Path, bytes], None] = _write_exclusive) -> dict[str, Any]:
    spec, spec_bytes = _load_spec(spec_path)
    archive_identity = _verify_zip(archive_path, spec)
    selected, events_identity = _scan_events(events_path, spec)
    license_bytes, license_identity = _read_license(license_path, spec)
    start_ns = _seconds_to_ns(spec["window"]["start_seconds_inclusive"])
    _create_result_dir(result_dir)
    artifacts: dict[str, dict[str, Any]] = {}

    def publish(name: str, payload: bytes, kind: str) -> None:
        _write(result_dir / name, payload)
        artifacts[name] = {"kind": kind, "size_bytes": len(payload), "sha256": _sha(payload)}

    publish(LICENSE_NAME, license_bytes, "LICENSE_TEXT")
    projection_payload = _jsonl(_projection_row(event) for event in selected)
    publish(PROJECTION_NAME, projection_payload, "LOSSLESS_WINDOW_PROJECTION_JSONL")

    scenario_receipts: list[dict[str, Any]] = []
    timestamp_tie_extras = sum(count - 1 for count in Counter(event.timestamp_ns for event in selected).values())
    for scenario in spec["scenarios"]:
        compression = scenario["time_compression"]
        cycles = [_cycle(event, start_ns, compression) for event in selected]
        max_cycle = max(cycles)
        deadline = max_cycle + len(selected) + 1
        trace_name = f"trace_{scenario['id']}.jsonl"
        payload = _jsonl(
            _trace_row(event, event_id, cycle, deadline)
            for event_id, (event, cycle) in enumerate(zip(selected, cycles))
        )
        publish(trace_name, payload, "SINGLE_EDGE_REPLAY_PREPARER_INPUT_JSONL")
        cycle_counts = Counter(cycles)
        source_cycle_counts = Counter((cycle, event.source) for event, cycle in zip(selected, cycles))
        scenario_receipts.append({
            "id": scenario["id"],
            "time_compression": compression,
            "trace_file": trace_name,
            "event_count": len(selected),
            "first_cycle": min(cycles),
            "last_cycle": max_cycle,
            "distinct_cycles": len(cycle_counts),
            "cycle_collision_extras": sum(count - 1 for count in cycle_counts.values()),
            "same_source_cycle_collision_extras": sum(count - 1 for count in source_cycle_counts.values()),
            "max_events_in_one_cycle": max(cycle_counts.values()),
            "deadline_field_policy": "last_projected_cycle_plus_event_count_plus_one_preparation_only",
            "deadline_value": deadline,
            "cycle_mapping": {
                "expression": "floor((timestamp_ns-window_start_ns)*2/(13*time_compression))",
                "numerator_multiplier": 2,
                "denominator": 13 * compression,
                "binary_floating_point_used": False,
            },
        })

    projection_duplicates = Counter(
        (event.timestamp_text, event.x, event.y, event.polarity) for event in selected
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": STATUS,
        "release_status": HOLD,
        "evidence_class": "PUBLIC_DATASET_PROJECTED_EXTENSION",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "specification": {"basename": spec_path.name, "sha256": _sha(spec_bytes)},
        "dataset": spec["dataset"],
        "source_artifacts": {
            "archive": archive_identity,
            "events": events_identity,
            "license": license_identity,
        },
        "window": spec["window"],
        "projection": spec["projection"],
        "conservation": {
            "input_window_events": len(selected),
            "projected_events": len(selected),
            "events_dropped": 0,
            "identity_rule": "tb_only_event_id order equals projected_events line order equals source dataset_event_index order",
            "timestamp_tie_collision_extras": timestamp_tie_extras,
            "exact_duplicate_input_extras": sum(count - 1 for count in projection_duplicates.values()),
        },
        "clock": spec["clock"],
        "scenarios": scenario_receipts,
        "trace_contract": {
            "consumer": "SINGLE_EDGE_REPLAY_PREPARER",
            "fields": list(TRACE_FIELDS),
            "geometry": {"width": 4, "height": 4},
            "identity_mode": "address_only",
            "required_relation": "logical_source == 4*y+x",
            "replay_status": "UNREPLAYED",
        },
        "lineage": spec["lineage"],
        "artifacts": artifacts,
    }
    receipt_payload = _pretty(receipt)
    _write(result_dir / RECEIPT_NAME, receipt_payload)
    completion = {
        "schema": COMPLETION_SCHEMA,
        "status": STATUS,
        "release_status": HOLD,
        "receipt_sha256": _sha(receipt_payload),
        "artifact_sha256": {name: row["sha256"] for name, row in artifacts.items()},
        "actual_replay_receipt_sha256": None,
    }
    _write(result_dir / COMPLETION_NAME, _pretty(completion))
    inspected = inspect(result_dir)
    if inspected["status"] != QUALIFY_STATUS:
        raise ProjectionFailure("post-publication inspection did not remain HOLD")
    return receipt


def _read_regular(path: Path, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    with _stable_open(path) as (stream, info):
        if info.st_size > max_bytes:
            raise ProjectionFailure(f"package artifact exceeds {max_bytes} bytes: {path.name}")
        return stream.read()


def _read_jsonl(data: bytes, fields: tuple[str, ...], where: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(data.splitlines(keepends=True), start=1):
        if not raw.endswith(b"\n"):
            raise ProjectionFailure(f"{where}:{line_number} lacks LF")
        row = _strict_keys(_json_bytes(raw[:-1], f"{where}:{line_number}"), set(fields), f"{where}:{line_number}")
        rows.append(row)
    return rows


def inspect(result_dir: Path) -> dict[str, Any]:
    _reject_symlink_components(result_dir)
    if not result_dir.is_dir():
        raise ProjectionFailure("result directory is absent")
    expected_names = {LICENSE_NAME, PROJECTION_NAME, RECEIPT_NAME, COMPLETION_NAME,
                      "trace_1x.jsonl", "trace_64x.jsonl", "trace_256x.jsonl"}
    actual_names = {path.name for path in result_dir.iterdir()}
    if actual_names != expected_names:
        raise ProjectionFailure(f"result artifact set mismatch: {sorted(actual_names)}")
    completion_bytes = _read_regular(result_dir / COMPLETION_NAME)
    completion = _strict_keys(_json_bytes(completion_bytes, COMPLETION_NAME), {
        "schema", "status", "release_status", "receipt_sha256", "artifact_sha256",
        "actual_replay_receipt_sha256",
    }, "completion")
    if (completion["schema"] != COMPLETION_SCHEMA or completion["status"] != STATUS or
            completion["release_status"] != HOLD or completion["actual_replay_receipt_sha256"] is not None):
        raise ProjectionFailure("completion is not unreplayed HOLD")
    receipt_bytes = _read_regular(result_dir / RECEIPT_NAME)
    if _sha(receipt_bytes) != completion["receipt_sha256"]:
        raise ProjectionFailure("receipt SHA mismatch")
    receipt = _json_bytes(receipt_bytes, RECEIPT_NAME)
    required_receipt = {
        "schema", "status", "release_status", "evidence_class", "canonical_redred_traffic",
        "official_redred_traffic", "specification", "dataset", "source_artifacts", "window",
        "projection", "conservation", "clock", "scenarios", "trace_contract", "lineage", "artifacts",
    }
    _strict_keys(receipt, required_receipt, "receipt")
    if (receipt["schema"] != RECEIPT_SCHEMA or receipt["status"] != STATUS or
            receipt["release_status"] != HOLD or receipt["canonical_redred_traffic"] is not False or
            receipt["official_redred_traffic"] is not False or
            receipt["lineage"].get("p6_evidence_used") is not False or
            receipt["lineage"].get("actual_replay_receipt") is not None or
            receipt["trace_contract"].get("replay_status") != "UNREPLAYED"):
        raise ProjectionFailure("receipt classification/lineage was promoted")
    artifacts = receipt["artifacts"]
    if set(artifacts) != expected_names - {RECEIPT_NAME, COMPLETION_NAME}:
        raise ProjectionFailure("receipt artifact inventory mismatch")
    if completion["artifact_sha256"] != {name: row["sha256"] for name, row in artifacts.items()}:
        raise ProjectionFailure("completion artifact inventory mismatch")
    payloads: dict[str, bytes] = {}
    for name, row in artifacts.items():
        payload = _read_regular(result_dir / name)
        if len(payload) != row["size_bytes"] or _sha(payload) != row["sha256"]:
            raise ProjectionFailure(f"artifact size/SHA mismatch: {name}")
        payloads[name] = payload
    projections = _read_jsonl(payloads[PROJECTION_NAME], PROJECTION_FIELDS, PROJECTION_NAME)
    count = receipt["conservation"]["input_window_events"]
    if (len(projections) != count or receipt["conservation"]["projected_events"] != count or
            receipt["conservation"]["events_dropped"] != 0):
        raise ProjectionFailure("projection conservation mismatch")
    previous_index = None
    for row in projections:
        if (not isinstance(row["dataset_event_index"], int) or isinstance(row["dataset_event_index"], bool) or
                not isinstance(row["x"], int) or not isinstance(row["y"], int) or
                row["polarity"] not in (0, 1)):
            raise ProjectionFailure("projection row type mismatch")
        if previous_index is not None and row["dataset_event_index"] != previous_index + 1:
            raise ProjectionFailure("projection dataset indices are not contiguous")
        previous_index = row["dataset_event_index"]
        timestamp_ns = _seconds_to_ns(row["timestamp_seconds"])
        if not (_seconds_to_ns(receipt["window"]["start_seconds_inclusive"]) <= timestamp_ns <
                _seconds_to_ns(receipt["window"]["end_seconds_exclusive"])):
            raise ProjectionFailure("projection timestamp outside window")
        bx, by = row["x"] * 4 // 240, row["y"] * 4 // 180
        if row["bx"] != bx or row["by"] != by or row["logical_source"] != 4 * by + bx:
            raise ProjectionFailure("projection bin/source mismatch")
    if projections and (projections[0]["dataset_event_index"] != receipt["window"]["expected_first_dataset_event_index"] or
                        projections[-1]["dataset_event_index"] != receipt["window"]["expected_last_dataset_event_index"]):
        raise ProjectionFailure("projection index anchors mismatch")
    scenario_by_id = {row["id"]: row for row in receipt["scenarios"]}
    if set(scenario_by_id) != {"1x", "64x", "256x"}:
        raise ProjectionFailure("scenario receipt set mismatch")
    start_ns = _seconds_to_ns(receipt["window"]["start_seconds_inclusive"])
    for scenario_id, compression in (("1x", 1), ("64x", 64), ("256x", 256)):
        trace = _read_jsonl(payloads[f"trace_{scenario_id}.jsonl"], TRACE_FIELDS, f"trace_{scenario_id}")
        scenario = scenario_by_id[scenario_id]
        if len(trace) != count or scenario["event_count"] != count or scenario["time_compression"] != compression:
            raise ProjectionFailure("trace event count/scenario mismatch")
        cycles: list[int] = []
        source_cycles: list[tuple[int, int]] = []
        for event_id, (source_row, trace_row) in enumerate(zip(projections, trace)):
            timestamp_ns = _seconds_to_ns(source_row["timestamp_seconds"])
            expected_cycle = ((timestamp_ns - start_ns) * 2) // (13 * compression)
            if (trace_row["tb_only_event_id"] != event_id or trace_row["occurrence_cycle"] != expected_cycle or
                    trace_row["logical_source"] != source_row["logical_source"] or
                    trace_row["x"] != source_row["bx"] or trace_row["y"] != source_row["by"] or
                    trace_row["polarity"] != (1 if source_row["polarity"] else -1) or
                    trace_row["event_type"] != "public_projected_event" or
                    trace_row["relation_id"] is not None or trace_row["relation_role"] is not None or
                    trace_row["deadline"] != scenario["deadline_value"]):
                raise ProjectionFailure(f"trace {scenario_id} row {event_id} mismatch")
            cycles.append(expected_cycle)
            source_cycles.append((expected_cycle, source_row["logical_source"]))
        cycle_counts = Counter(cycles)
        source_counts = Counter(source_cycles)
        if (scenario["first_cycle"] != min(cycles) or scenario["last_cycle"] != max(cycles) or
                scenario["distinct_cycles"] != len(cycle_counts) or
                scenario["cycle_collision_extras"] != sum(value - 1 for value in cycle_counts.values()) or
                scenario["same_source_cycle_collision_extras"] != sum(value - 1 for value in source_counts.values()) or
                scenario["max_events_in_one_cycle"] != max(cycle_counts.values())):
            raise ProjectionFailure(f"trace {scenario_id} collision accounting mismatch")
    return {
        "status": QUALIFY_STATUS,
        "release_status": HOLD,
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "actual_replay_bound": False,
        "receipt_sha256": _sha(receipt_bytes),
    }


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--qualify", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--license", dest="license_path", type=Path)
    parser.add_argument("--spec", type=Path, default=Path(__file__).with_name("projection_spec.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _args(sys.argv[1:] if argv is None else argv)
    try:
        if args.qualify:
            if any(value is not None for value in (args.archive, args.events, args.license_path)):
                raise ProjectionFailure("qualification accepts only --result-dir and --spec")
            result = inspect(args.result_dir)
            print(json.dumps(result, sort_keys=True))
            return 3
        if args.archive is None or args.events is None or args.license_path is None:
            raise ProjectionFailure("projection requires --archive, --events, and --license")
        receipt = project(args.archive, args.events, args.license_path, args.spec, args.result_dir)
        print(json.dumps({"status": receipt["status"], "release_status": HOLD,
                          "events": receipt["conservation"]["projected_events"]}, sort_keys=True))
        return 0
    except ProjectionFailure as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
