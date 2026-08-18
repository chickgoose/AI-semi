#!/usr/bin/env python3
"""Import provenance-bound event-camera data into an exclusive AER package."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit


SPEC_SCHEMA = "redred-event-import-v2"
RECEIPT_SCHEMA = "redred-event-import-receipt-v2"
COMPLETION_SCHEMA = "redred-event-import-completion-v2"
TRACE_NAME = "events.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETION_NAME = "COMPLETE.json"
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
COUNTER_FIELDS = {
    "input_event_records",
    "blank_lines",
    "comment_lines",
    "timestamp_tied_events",
    "same_cycle_events",
    "same_source_cycle_retriggers",
    "out_of_range_events",
    "x_below_range",
    "x_above_range",
    "y_below_range",
    "y_above_range",
    "clipped_events",
    "clipped_coordinates",
    "events_emitted",
    "events_dropped",
}
TIME_TO_NS = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXACT_DECIMAL_RE = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?(?:0|[1-9][0-9]*))?\Z"
)
INTEGER_TEXT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
SPDX_RE = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9.-]*|LicenseRef-[A-Za-z0-9.-]+)\Z")
ACQUISITION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}\Z")


class ImportFailure(ValueError):
    """The input, result package, or import contract is invalid."""


class ImportHold(ImportFailure):
    """A complete HOLD package was emitted for an intentionally unsupported format."""


@dataclass(frozen=True)
class StableBytes:
    data: bytes
    raw_sha256: str


@dataclass(frozen=True)
class RawEvent:
    timestamp: Fraction
    x: int
    y: int
    polarity: int
    source_record_index: int


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _stable_read(
    path: Path, *, _after_read_hook: Callable[[], None] | None = None
) -> StableBytes:
    """Read exact bytes once and reject identity/size/mtime changes around the read."""
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ImportFailure(f"cannot stat artifact {path}: {error}") from error
    if not stat.S_ISREG(before.st_mode):
        raise ImportFailure(f"artifact is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ImportFailure(f"cannot open artifact {path}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if _stat_identity(opened) != _stat_identity(before):
            raise ImportFailure(f"artifact changed before stable read: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if _after_read_hook is not None:
            _after_read_hook()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ImportFailure(f"artifact disappeared after stable read: {path}: {error}") from error
    identity = _stat_identity(before)
    if _stat_identity(opened) != identity or _stat_identity(after) != identity or _stat_identity(final) != identity:
        raise ImportFailure(f"artifact changed during stable read: {path}")
    if len(data) != before.st_size:
        raise ImportFailure(f"artifact size changed during stable read: {path}")
    return StableBytes(data=data, raw_sha256=_sha256_bytes(data))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ImportFailure(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_load_bytes(data: bytes, where: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ImportFailure(f"{where} is not valid UTF-8: {error}") from error
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ImportFailure:
        raise
    except json.JSONDecodeError as error:
        raise ImportFailure(f"invalid JSON in {where}: {error}") from error


def _json_load_line(line: str, where: str) -> Any:
    try:
        return json.loads(line, object_pairs_hook=_reject_duplicate_keys)
    except ImportFailure:
        raise
    except json.JSONDecodeError as error:
        raise ImportFailure(f"invalid JSON in {where}: {error}") from error


def _strict_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ImportFailure(f"{where}: {'; '.join(details)}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(
        ord(character) < 0x20 for character in value
    ):
        raise ImportFailure(f"{field} must be a non-empty trimmed string without control characters")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImportFailure(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImportFailure(f"{field} must be a nonnegative integer")
    return value


def _exact_fraction(value: Any, field: str) -> Fraction:
    if not isinstance(value, str) or not EXACT_DECIMAL_RE.fullmatch(value):
        raise ImportFailure(
            f"{field} must match the exact-decimal grammar "
            "-?(0|[1-9][0-9]*)(.[0-9]+)?([eE][+-]?(0|[1-9][0-9]*))?"
        )
    mantissa, exponent_text = re.split(r"[eE]", value, maxsplit=1) if re.search(r"[eE]", value) else (value, "0")
    exponent = int(exponent_text, 10)
    negative = mantissa.startswith("-")
    unsigned = mantissa[1:] if negative else mantissa
    if "." in unsigned:
        whole, fractional = unsigned.split(".", 1)
    else:
        whole, fractional = unsigned, ""
    coefficient = int(whole + fractional, 10)
    if negative:
        coefficient = -coefficient
    scale = len(fractional) - exponent
    if scale >= 0:
        return Fraction(coefficient, 10**scale)
    return Fraction(coefficient * (10 ** (-scale)), 1)


def _fraction_decimal(value: Fraction) -> str:
    if value == 0:
        return "0"
    negative = value < 0
    numerator = abs(value.numerator)
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ImportFailure("internal exact decimal has a non-terminating denominator")
    scale = max(twos, fives)
    numerator *= (2 ** (scale - twos)) * (5 ** (scale - fives))
    digits = str(numerator).rjust(scale + 1, "0")
    if scale:
        text = digits[:-scale] + "." + digits[-scale:]
        text = text.rstrip("0").rstrip(".")
    else:
        text = digits
    return "-" + text if negative else text


def _integer(value: Any, field: str, *, json_input: bool) -> int:
    if isinstance(value, bool):
        raise ImportFailure(f"{field} must be an integer")
    if json_input:
        if not isinstance(value, int):
            raise ImportFailure(f"{field} must be a JSON integer")
        return value
    if not isinstance(value, str) or not INTEGER_TEXT_RE.fullmatch(value):
        raise ImportFailure(f"{field} must be a canonical base-10 integer")
    return int(value, 10)


def _normalize_polarity(value: Any, encoding: str, field: str, *, json_input: bool) -> int:
    raw = _integer(value, field, json_input=json_input)
    allowed = (-1, 1) if encoding == "minus_plus_one" else (0, 1)
    if raw not in allowed:
        raise ImportFailure(f"{field} is invalid for {encoding}")
    return raw if encoding == "minus_plus_one" else (1 if raw else -1)


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ImportFailure(f"{field} must be a lowercase SHA-256 digest")
    return value


def _validate_source_spec(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ImportFailure("source must be an object")
    _strict_keys(source, {"raw_sha256"}, "source")
    _validate_sha(source["raw_sha256"], "source.raw_sha256")
    return source


def _validate_dataset(dataset: Any) -> dict[str, Any]:
    if not isinstance(dataset, dict):
        raise ImportFailure("dataset must be an object")
    _strict_keys(
        dataset,
        {"provider", "dataset", "release", "version", "original_artifact", "provenance", "license"},
        "dataset",
    )
    for field in ("provider", "dataset", "release", "version", "original_artifact"):
        _text(dataset[field], f"dataset.{field}")
    provenance = dataset["provenance"]
    if not isinstance(provenance, dict) or set(provenance) not in ({"uri"}, {"acquisition_id"}):
        raise ImportFailure("dataset.provenance must contain exactly one of uri or acquisition_id")
    if "uri" in provenance:
        uri = _text(provenance["uri"], "dataset.provenance.uri")
        if not urlsplit(uri).scheme or any(character.isspace() for character in uri):
            raise ImportFailure("dataset.provenance.uri must be an absolute URI without whitespace")
    else:
        acquisition = _text(provenance["acquisition_id"], "dataset.provenance.acquisition_id")
        if not ACQUISITION_RE.fullmatch(acquisition):
            raise ImportFailure("dataset.provenance.acquisition_id has invalid syntax")
    license_info = dataset["license"]
    if not isinstance(license_info, dict):
        raise ImportFailure("dataset.license must be an object")
    _strict_keys(license_info, {"spdx_id", "text_sha256", "redistribution"}, "dataset.license")
    spdx = _text(license_info["spdx_id"], "dataset.license.spdx_id")
    if not SPDX_RE.fullmatch(spdx):
        raise ImportFailure("dataset.license.spdx_id has invalid SPDX identifier syntax")
    _validate_sha(license_info["text_sha256"], "dataset.license.text_sha256")
    if license_info["redistribution"] not in {"permitted", "prohibited", "unknown"}:
        raise ImportFailure("dataset.license.redistribution is invalid")
    return dataset


def _load_spec(artifact: StableBytes) -> tuple[dict[str, Any], str]:
    value = _json_load_bytes(artifact.data, "import specification")
    if not isinstance(value, dict):
        raise ImportFailure("import specification must be a JSON object")
    if value.get("schema") != SPEC_SCHEMA:
        raise ImportFailure(f"import specification schema must be {SPEC_SCHEMA!r}")
    semantic = copy.deepcopy(value)
    cycle = semantic.get("cycle_mapping")
    if isinstance(cycle, dict) and "period_ns" in cycle:
        cycle["period_ns"] = _fraction_decimal(
            _exact_fraction(cycle["period_ns"], "cycle_mapping.period_ns")
        )
    return value, _canonical_sha256(semantic)


def _validate_spec(
    spec: dict[str, Any],
) -> tuple[str, int | None, int | None, int | None, dict[str, Any] | None, dict[str, Any] | None]:
    input_spec = spec.get("input")
    if not isinstance(input_spec, dict):
        raise ImportFailure("input must be an object")
    source_format = input_spec.get("format")
    if source_format == "samsung_official":
        _strict_keys(spec, {"schema", "source", "input"}, "Samsung HOLD specification")
        _strict_keys(input_spec, {"format"}, "input")
        _validate_source_spec(spec["source"])
        return source_format, None, None, None, input_spec, None
    _strict_keys(
        spec,
        {"schema", "dataset", "source", "sensor", "address_width", "input", "cycle_mapping", "bounds_policy"},
        "import specification",
    )
    _validate_dataset(spec["dataset"])
    _validate_source_spec(spec["source"])
    sensor = spec["sensor"]
    if not isinstance(sensor, dict):
        raise ImportFailure("sensor must be an object")
    _strict_keys(sensor, {"width", "height"}, "sensor")
    width = _positive_int(sensor["width"], "sensor.width")
    height = _positive_int(sensor["height"], "sensor.height")
    address_width = _positive_int(spec["address_width"], "address_width")
    if width * height > 2**address_width:
        raise ImportFailure("sensor geometry cannot be represented by address_width")
    if input_spec.get("time_unit") not in TIME_TO_NS:
        raise ImportFailure("input.time_unit must be one of s, ms, us, or ns")
    if input_spec.get("polarity_encoding") not in {"minus_plus_one", "zero_one"}:
        raise ImportFailure("input.polarity_encoding must be minus_plus_one or zero_one")
    cycle = spec["cycle_mapping"]
    if not isinstance(cycle, dict):
        raise ImportFailure("cycle_mapping must be an object")
    _strict_keys(cycle, {"period_ns", "origin", "deadline_slack_cycles"}, "cycle_mapping")
    if _exact_fraction(cycle["period_ns"], "cycle_mapping.period_ns") <= 0:
        raise ImportFailure("cycle_mapping.period_ns must be positive")
    if cycle["origin"] != "first_event":
        raise ImportFailure("cycle_mapping.origin must be first_event")
    _nonnegative_int(cycle["deadline_slack_cycles"], "cycle_mapping.deadline_slack_cycles")
    if spec["bounds_policy"] not in {"reject", "clip"}:
        raise ImportFailure("bounds_policy must be reject or clip")
    return source_format, width, height, address_width, input_spec, cycle


def _decode_source(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ImportFailure(f"source is not valid UTF-8: {error}") from error


def _parse_canonical_jsonl(data: bytes, input_spec: dict[str, Any]) -> tuple[list[RawEvent], dict[str, int]]:
    _strict_keys(input_spec, {"format", "time_unit", "polarity_encoding"}, "input")
    result: list[RawEvent] = []
    blank_lines = 0
    for line_number, line in enumerate(_decode_source(data).splitlines(), 1):
        if not line:
            blank_lines += 1
            continue
        if line != line.strip():
            raise ImportFailure(f"source line {line_number} has leading or trailing whitespace")
        row = _json_load_line(line, f"source line {line_number}")
        if not isinstance(row, dict):
            raise ImportFailure(f"source line {line_number}: event must be an object")
        _strict_keys(row, {"timestamp", "x", "y", "polarity"}, f"source line {line_number}")
        record = len(result)
        result.append(
            RawEvent(
                _exact_fraction(row["timestamp"], f"record {record} timestamp"),
                _integer(row["x"], f"record {record} x", json_input=True),
                _integer(row["y"], f"record {record} y", json_input=True),
                _normalize_polarity(
                    row["polarity"], input_spec["polarity_encoding"],
                    f"record {record} polarity", json_input=True,
                ),
                record,
            )
        )
    if not result:
        raise ImportFailure("source contains no event records")
    return result, {"blank_lines": blank_lines, "comment_lines": 0}


def _delimited_rows(
    data: bytes, delimiter: str, comment_prefix: str | None
) -> tuple[list[tuple[int, list[str]]], dict[str, int]]:
    rows: list[tuple[int, list[str]]] = []
    blank_lines = 0
    comment_lines = 0
    for line_number, line in enumerate(io.StringIO(_decode_source(data), newline=""), 1):
        if line in {"\n", "\r\n", ""}:
            blank_lines += 1
            continue
        if comment_prefix is not None and line.startswith(comment_prefix):
            comment_lines += 1
            continue
        try:
            fields = line.split() if delimiter == "whitespace" else next(
                csv.reader([line], delimiter=delimiter, strict=True)
            )
        except csv.Error as error:
            raise ImportFailure(f"source line {line_number}: invalid delimited record: {error}") from error
        rows.append((line_number, fields))
    return rows, {"blank_lines": blank_lines, "comment_lines": comment_lines}


def _parse_delimited(data: bytes, input_spec: dict[str, Any]) -> tuple[list[RawEvent], dict[str, int]]:
    _strict_keys(
        input_spec,
        {"format", "time_unit", "polarity_encoding", "delimiter", "header", "comment_prefix", "columns"},
        "input",
    )
    delimiter = input_spec["delimiter"]
    if delimiter != "whitespace" and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise ImportFailure("input.delimiter must be one character or whitespace")
    header = input_spec["header"]
    if not isinstance(header, bool):
        raise ImportFailure("input.header must be boolean")
    comment_prefix = input_spec["comment_prefix"]
    if comment_prefix is not None:
        _text(comment_prefix, "input.comment_prefix")
        if "\n" in comment_prefix or "\r" in comment_prefix:
            raise ImportFailure("input.comment_prefix must be single-line")
    columns = input_spec["columns"]
    if not isinstance(columns, dict):
        raise ImportFailure("input.columns must be an object")
    _strict_keys(columns, {"timestamp", "x", "y", "polarity"}, "input.columns")
    mappings = list(columns.values())
    if header:
        if any(not isinstance(value, str) or not value or value != value.strip() for value in mappings):
            raise ImportFailure("header-based column mappings must be trimmed non-empty strings")
    elif any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in mappings):
        raise ImportFailure("positional column mappings must be nonnegative integers")
    if len(set(mappings)) != 4:
        raise ImportFailure("input.columns mappings must be distinct")
    rows, counters = _delimited_rows(data, delimiter, comment_prefix)
    if not rows:
        raise ImportFailure("source contains no rows")
    if header:
        header_line, names = rows.pop(0)
        if len(names) != len(set(names)):
            raise ImportFailure(f"source line {header_line}: duplicate header names")
        missing = [name for name in mappings if name not in names]
        if missing:
            raise ImportFailure(f"source line {header_line}: missing columns: {', '.join(missing)}")
        indices = {field: names.index(name) for field, name in columns.items()}
    else:
        indices = dict(columns)
    if not rows:
        raise ImportFailure("source contains no event records")
    result: list[RawEvent] = []
    required_index = max(indices.values())
    for line_number, row in rows:
        record = len(result)
        if len(row) <= required_index:
            raise ImportFailure(f"source line {line_number}: row has too few columns")
        result.append(
            RawEvent(
                _exact_fraction(row[indices["timestamp"]], f"record {record} timestamp"),
                _integer(row[indices["x"]], f"record {record} x", json_input=False),
                _integer(row[indices["y"]], f"record {record} y", json_input=False),
                _normalize_polarity(
                    row[indices["polarity"]], input_spec["polarity_encoding"],
                    f"record {record} polarity", json_input=False,
                ),
                record,
            )
        )
    return result, counters


def _parse_events(data: bytes, input_spec: dict[str, Any]) -> tuple[list[RawEvent], dict[str, int]]:
    if input_spec["format"] == "canonical_jsonl":
        return _parse_canonical_jsonl(data, input_spec)
    if input_spec["format"] == "generic_delimited":
        return _parse_delimited(data, input_spec)
    raise ImportFailure("input.format must be canonical_jsonl or generic_delimited")


def _source_semantic_sha256(events: Iterable[RawEvent]) -> str:
    semantic = [
        {
            "timestamp": [str(event.timestamp.numerator), str(event.timestamp.denominator)],
            "x": event.x,
            "y": event.y,
            "polarity": event.polarity,
            "source_record_index": event.source_record_index,
        }
        for event in events
    ]
    return _canonical_sha256(semantic)


def _convert(
    raw_events: list[RawEvent], spec: dict[str, Any], width: int, height: int,
    address_width: int, input_spec: dict[str, Any], cycle_spec: dict[str, Any],
    parse_counters: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    ordered = sorted(raw_events, key=lambda event: (event.timestamp, event.source_record_index))
    origin = ordered[0].timestamp
    period_ns = _exact_fraction(cycle_spec["period_ns"], "cycle_mapping.period_ns")
    scale = TIME_TO_NS[input_spec["time_unit"]]
    slack = cycle_spec["deadline_slack_cycles"]
    counters = {
        "input_event_records": len(ordered), **parse_counters,
        "timestamp_tied_events": 0, "same_cycle_events": 0,
        "same_source_cycle_retriggers": 0, "out_of_range_events": 0,
        "x_below_range": 0, "x_above_range": 0, "y_below_range": 0,
        "y_above_range": 0, "clipped_events": 0, "clipped_coordinates": 0,
        "events_emitted": 0, "events_dropped": 0,
    }
    previous_timestamp: Fraction | None = None
    previous_cycle: int | None = None
    seen_source_cycles: set[tuple[int, int]] = set()
    result: list[dict[str, Any]] = []
    for event_id, raw in enumerate(ordered):
        if raw.timestamp == previous_timestamp:
            counters["timestamp_tied_events"] += 1
        previous_timestamp = raw.timestamp
        elapsed = (raw.timestamp - origin) * scale / period_ns
        cycle = elapsed.numerator // elapsed.denominator
        if cycle < 0:
            raise ImportFailure("internal ordering error produced a negative occurrence cycle")
        if cycle == previous_cycle:
            counters["same_cycle_events"] += 1
        previous_cycle = cycle
        x, y = raw.x, raw.y
        x_low, x_high, y_low, y_high = x < 0, x >= width, y < 0, y >= height
        out_of_range = x_low or x_high or y_low or y_high
        counters["x_below_range"] += int(x_low)
        counters["x_above_range"] += int(x_high)
        counters["y_below_range"] += int(y_low)
        counters["y_above_range"] += int(y_high)
        counters["out_of_range_events"] += int(out_of_range)
        if out_of_range:
            if spec["bounds_policy"] == "reject":
                raise ImportFailure(
                    f"record {raw.source_record_index}: coordinate ({x}, {y}) is outside "
                    f"sensor {width}x{height}; bounds_policy=reject"
                )
            clipped_x, clipped_y = min(max(x, 0), width - 1), min(max(y, 0), height - 1)
            counters["clipped_events"] += 1
            counters["clipped_coordinates"] += int(clipped_x != x) + int(clipped_y != y)
            x, y = clipped_x, clipped_y
        logical_source = y * width + x
        if not 0 <= logical_source < 2**address_width:
            raise ImportFailure("logical source exceeds declared address_width")
        source_cycle = (logical_source, cycle)
        if source_cycle in seen_source_cycles:
            counters["same_source_cycle_retriggers"] += 1
        seen_source_cycles.add(source_cycle)
        result.append(
            {
                "occurrence_cycle": cycle,
                "tb_only_event_id": event_id,
                "logical_source": logical_source,
                "x": x,
                "y": y,
                "polarity": raw.polarity,
                "event_type": "dataset_event",
                "relation_id": None,
                "relation_role": None,
                "deadline": cycle + slack,
            }
        )
    counters["events_emitted"] = len(result)
    if counters["input_event_records"] != counters["events_emitted"] + counters["events_dropped"]:
        raise ImportFailure("event conservation failure during conversion")
    return result, counters, _fraction_decimal(origin)


def _trace_bytes(events: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(event) + b"\n" for event in events)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ImportFailure(f"cannot exclusively create package artifact {path}: {error}") from error
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise ImportFailure(f"cannot finalize package artifact {path}: {error}") from error


def _publish_package(result_dir: Path, receipt: dict[str, Any], trace: bytes | None) -> None:
    if os.path.lexists(result_dir):
        raise ImportFailure(f"result directory already exists; refusing overwrite: {result_dir}")
    parent = result_dir.parent
    try:
        parent_info = parent.stat(follow_symlinks=False)
    except OSError as error:
        raise ImportFailure(f"result parent is unavailable: {parent}: {error}") from error
    if not stat.S_ISDIR(parent_info.st_mode):
        raise ImportFailure(f"result parent is not a directory: {parent}")
    try:
        os.mkdir(result_dir, 0o700)
    except OSError as error:
        raise ImportFailure(f"cannot exclusively create result directory {result_dir}: {error}") from error
    if trace is not None:
        _write_exclusive(result_dir / TRACE_NAME, trace)
    receipt_bytes = json.dumps(
        receipt, indent=2, sort_keys=True, ensure_ascii=True
    ).encode("ascii") + b"\n"
    _write_exclusive(result_dir / RECEIPT_NAME, receipt_bytes)
    completion = {
        "schema": COMPLETION_SCHEMA,
        "status": receipt["status"],
        "receipt_sha256": _sha256_bytes(receipt_bytes),
        "trace_sha256": _sha256_bytes(trace) if trace is not None else None,
    }
    completion_bytes = json.dumps(
        completion, indent=2, sort_keys=True, ensure_ascii=True
    ).encode("ascii") + b"\n"
    try:
        descriptor = os.open(result_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ImportFailure(f"cannot sync package before completion {result_dir}: {error}") from error
    # The completion sentinel is the final operation. If any earlier write or
    # directory sync fails, the directory cannot qualify as a result package.
    _write_exclusive(result_dir / COMPLETION_NAME, completion_bytes)


def import_dataset(source_path: Path, spec_path: Path, result_dir: Path) -> dict[str, Any]:
    if os.path.lexists(result_dir):
        raise ImportFailure(f"result directory already exists; refusing overwrite: {result_dir}")
    spec_artifact = _stable_read(spec_path)
    spec, spec_semantic_sha256 = _load_spec(spec_artifact)
    source_format, width, height, address_width, input_spec, cycle_spec = _validate_spec(spec)
    source_artifact = _stable_read(source_path)
    expected_sha256 = spec["source"]["raw_sha256"]
    if source_artifact.raw_sha256 != expected_sha256:
        raise ImportFailure(
            f"source raw SHA-256 mismatch: expected {expected_sha256}, got {source_artifact.raw_sha256}"
        )
    spec_hashes = {
        "raw_sha256": spec_artifact.raw_sha256,
        "semantic_sha256": spec_semantic_sha256,
    }
    if source_format == "samsung_official":
        reason = "official Samsung event format is unsupported until its actual specification is provided"
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "HOLD",
            "reason": reason,
            "source": {"file": source_path.name, "raw_sha256": source_artifact.raw_sha256},
            "specification": spec_hashes,
        }
        _publish_package(result_dir, receipt, None)
        raise ImportHold(f"HOLD: {reason}")
    assert width is not None and height is not None and address_width is not None
    assert input_spec is not None and cycle_spec is not None
    raw_events, parse_counters = _parse_events(source_artifact.data, input_spec)
    source_semantic_sha256 = _source_semantic_sha256(raw_events)
    events, counters, origin = _convert(
        raw_events, spec, width, height, address_width, input_spec, cycle_spec, parse_counters
    )
    trace = _trace_bytes(events)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "dataset": spec["dataset"],
        "source": {
            "file": source_path.name,
            "raw_sha256": source_artifact.raw_sha256,
            "semantic_sha256": source_semantic_sha256,
        },
        "specification": spec_hashes,
        "input_contract": {
            "format": input_spec["format"],
            "time_unit": input_spec["time_unit"],
            "polarity_encoding": input_spec["polarity_encoding"],
            "sensor": {"width": width, "height": height},
            "address_width": address_width,
            "bounds_policy": spec["bounds_policy"],
        },
        "ordering": {
            "key": ["timestamp", "source_record_index"],
            "timestamp_tie_policy": "preserve_source_record_order",
            "cycle_quantization": "floor_exact_fraction",
        },
        "cycle_mapping": {
            "period_ns": _fraction_decimal(_exact_fraction(cycle_spec["period_ns"], "cycle_mapping.period_ns")),
            "origin": "first_event",
            "origin_timestamp": origin,
            "deadline_slack_cycles": cycle_spec["deadline_slack_cycles"],
        },
        "conservation": "input_event_records == events_emitted + events_dropped",
        "counts": counters,
        "trace": {
            "file": TRACE_NAME,
            "raw_sha256": _sha256_bytes(trace),
            "event_count": len(events),
            "event_schema": list(TRACE_FIELDS),
            "identity_mode": "address_only",
            "required_relation": "logical_source == y * sensor.width + x",
        },
    }
    _qualify_receipt_and_trace(receipt, trace)
    _publish_package(result_dir, receipt, trace)
    qualify_result_dir(result_dir)
    return receipt


def _validate_hash_pair(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise ImportFailure(f"{where} must be an object")
    _strict_keys(value, {"raw_sha256", "semantic_sha256"}, where)
    _validate_sha(value["raw_sha256"], f"{where}.raw_sha256")
    _validate_sha(value["semantic_sha256"], f"{where}.semantic_sha256")


def _qualify_receipt_and_trace(receipt: dict[str, Any], trace_data: bytes | None) -> None:
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ImportFailure("receipt schema mismatch")
    status = receipt.get("status")
    if status == "HOLD":
        _strict_keys(receipt, {"schema", "status", "reason", "source", "specification"}, "HOLD receipt")
        _text(receipt["reason"], "HOLD receipt.reason")
        source = receipt["source"]
        if not isinstance(source, dict):
            raise ImportFailure("HOLD receipt.source must be an object")
        _strict_keys(source, {"file", "raw_sha256"}, "HOLD receipt.source")
        _text(source["file"], "HOLD receipt.source.file")
        _validate_sha(source["raw_sha256"], "HOLD receipt.source.raw_sha256")
        _validate_hash_pair(receipt["specification"], "HOLD receipt.specification")
        if trace_data is not None:
            raise ImportFailure("HOLD package must not contain a trace")
        return
    if status != "PASS":
        raise ImportFailure("receipt status must be PASS or HOLD")
    _strict_keys(
        receipt,
        {"schema", "status", "dataset", "source", "specification", "input_contract", "ordering", "cycle_mapping", "conservation", "counts", "trace"},
        "PASS receipt",
    )
    _validate_dataset(receipt["dataset"])
    source = receipt["source"]
    if not isinstance(source, dict):
        raise ImportFailure("PASS receipt.source must be an object")
    _strict_keys(source, {"file", "raw_sha256", "semantic_sha256"}, "PASS receipt.source")
    _text(source["file"], "PASS receipt.source.file")
    _validate_sha(source["raw_sha256"], "PASS receipt.source.raw_sha256")
    _validate_sha(source["semantic_sha256"], "PASS receipt.source.semantic_sha256")
    _validate_hash_pair(receipt["specification"], "PASS receipt.specification")
    contract = receipt["input_contract"]
    if not isinstance(contract, dict):
        raise ImportFailure("input_contract must be an object")
    _strict_keys(contract, {"format", "time_unit", "polarity_encoding", "sensor", "address_width", "bounds_policy"}, "input_contract")
    if contract["format"] not in {"canonical_jsonl", "generic_delimited"}:
        raise ImportFailure("PASS input format is unsupported")
    if contract["time_unit"] not in TIME_TO_NS or contract["polarity_encoding"] not in {"minus_plus_one", "zero_one"}:
        raise ImportFailure("PASS input unit or polarity encoding is invalid")
    sensor = contract["sensor"]
    if not isinstance(sensor, dict):
        raise ImportFailure("input_contract.sensor must be an object")
    _strict_keys(sensor, {"width", "height"}, "input_contract.sensor")
    width = _positive_int(sensor["width"], "input_contract.sensor.width")
    height = _positive_int(sensor["height"], "input_contract.sensor.height")
    address_width = _positive_int(contract["address_width"], "input_contract.address_width")
    if width * height > 2**address_width:
        raise ImportFailure("receipt geometry exceeds address width")
    if contract["bounds_policy"] not in {"reject", "clip"}:
        raise ImportFailure("receipt bounds policy is invalid")
    ordering = receipt["ordering"]
    if ordering != {
        "key": ["timestamp", "source_record_index"],
        "timestamp_tie_policy": "preserve_source_record_order",
        "cycle_quantization": "floor_exact_fraction",
    }:
        raise ImportFailure("receipt ordering contract mismatch")
    cycle_mapping = receipt["cycle_mapping"]
    if not isinstance(cycle_mapping, dict):
        raise ImportFailure("cycle_mapping must be an object")
    _strict_keys(cycle_mapping, {"period_ns", "origin", "origin_timestamp", "deadline_slack_cycles"}, "receipt cycle_mapping")
    if _exact_fraction(cycle_mapping["period_ns"], "receipt period_ns") <= 0:
        raise ImportFailure("receipt period_ns must be positive")
    _exact_fraction(cycle_mapping["origin_timestamp"], "receipt origin_timestamp")
    if cycle_mapping["origin"] != "first_event":
        raise ImportFailure("receipt origin must be first_event")
    slack = _nonnegative_int(cycle_mapping["deadline_slack_cycles"], "receipt deadline_slack_cycles")
    if receipt["conservation"] != "input_event_records == events_emitted + events_dropped":
        raise ImportFailure("receipt conservation contract mismatch")
    counts = receipt["counts"]
    if not isinstance(counts, dict):
        raise ImportFailure("counts must be an object")
    _strict_keys(counts, COUNTER_FIELDS, "counts")
    for key, value in counts.items():
        _nonnegative_int(value, f"counts.{key}")
    if counts["events_dropped"] != 0 or counts["input_event_records"] != counts["events_emitted"]:
        raise ImportFailure("zero-drop import conservation failed")
    if contract["bounds_policy"] == "clip":
        if counts["clipped_events"] != counts["out_of_range_events"]:
            raise ImportFailure("clip/out-of-range counters disagree")
    elif counts["out_of_range_events"] or counts["clipped_events"] or counts["clipped_coordinates"]:
        raise ImportFailure("reject-policy PASS receipt contains out-of-range events")
    if not counts["clipped_events"] <= counts["clipped_coordinates"] <= 2 * counts["clipped_events"]:
        raise ImportFailure("clipped coordinate counters are inconsistent")
    axis_violations = sum(
        counts[key]
        for key in ("x_below_range", "x_above_range", "y_below_range", "y_above_range")
    )
    if axis_violations != counts["clipped_coordinates"]:
        raise ImportFailure("axis violation and clipped-coordinate counters disagree")
    for key in ("timestamp_tied_events", "same_cycle_events", "same_source_cycle_retriggers"):
        if counts[key] >= counts["input_event_records"]:
            raise ImportFailure(f"counts.{key} exceeds possible event relationships")
    trace = receipt["trace"]
    if not isinstance(trace, dict):
        raise ImportFailure("PASS trace metadata must be an object")
    _strict_keys(trace, {"file", "raw_sha256", "event_count", "event_schema", "identity_mode", "required_relation"}, "trace")
    if trace["file"] != TRACE_NAME or trace["event_schema"] != list(TRACE_FIELDS):
        raise ImportFailure("trace filename or schema mismatch")
    _validate_sha(trace["raw_sha256"], "trace.raw_sha256")
    event_count = _positive_int(trace["event_count"], "trace.event_count")
    if trace["identity_mode"] != "address_only" or trace["required_relation"] != "logical_source == y * sensor.width + x":
        raise ImportFailure("trace identity contract mismatch")
    if trace_data is None or _sha256_bytes(trace_data) != trace["raw_sha256"]:
        raise ImportFailure("trace raw SHA-256 mismatch")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(_decode_source(trace_data).splitlines(), 1):
        if not line or line != line.strip():
            raise ImportFailure(f"trace line {line_number} is blank or noncanonical")
        event = _json_load_line(line, f"trace line {line_number}")
        if not isinstance(event, dict):
            raise ImportFailure(f"trace line {line_number} must be an object")
        _strict_keys(event, set(TRACE_FIELDS), f"trace line {line_number}")
        if line.encode("ascii") != _canonical_bytes(event):
            raise ImportFailure(f"trace line {line_number} is not canonical JSON")
        events.append(event)
    if len(events) != event_count or event_count != counts["events_emitted"]:
        raise ImportFailure("trace event_count/counters/cardinality mismatch")
    previous_cycle = -1
    seen_source_cycles: set[tuple[int, int]] = set()
    computed_same_cycle = 0
    computed_retriggers = 0
    for expected_id, event in enumerate(events):
        cycle = _nonnegative_int(event["occurrence_cycle"], f"event {expected_id}.occurrence_cycle")
        if cycle < previous_cycle:
            raise ImportFailure("trace occurrence cycles are not ordered")
        if cycle == previous_cycle:
            computed_same_cycle += 1
        previous_cycle = cycle
        trace_id = _nonnegative_int(event["tb_only_event_id"], f"event {expected_id}.tb_only_event_id")
        if trace_id != expected_id:
            raise ImportFailure("trace event IDs are not contiguous")
        x = _nonnegative_int(event["x"], f"event {expected_id}.x")
        y = _nonnegative_int(event["y"], f"event {expected_id}.y")
        if x >= width or y >= height:
            raise ImportFailure("trace coordinate is outside declared sensor")
        logical_source = _nonnegative_int(event["logical_source"], f"event {expected_id}.logical_source")
        if logical_source != y * width + x or logical_source >= 2**address_width:
            raise ImportFailure("trace logical source/address relation failed")
        polarity = _integer(event["polarity"], f"event {expected_id}.polarity", json_input=True)
        if polarity not in (-1, 1) or event["event_type"] != "dataset_event":
            raise ImportFailure("trace polarity or event type is invalid")
        if event["relation_id"] is not None or event["relation_role"] is not None:
            raise ImportFailure("dataset trace relation fields must be null")
        deadline = _nonnegative_int(event["deadline"], f"event {expected_id}.deadline")
        if deadline != cycle + slack:
            raise ImportFailure("trace deadline mapping mismatch")
        source_cycle = (logical_source, cycle)
        if source_cycle in seen_source_cycles:
            computed_retriggers += 1
        seen_source_cycles.add(source_cycle)
    if computed_same_cycle != counts["same_cycle_events"] or computed_retriggers != counts["same_source_cycle_retriggers"]:
        raise ImportFailure("trace collision counters disagree")


def qualify_result_dir(result_dir: Path) -> dict[str, Any]:
    try:
        info = result_dir.stat(follow_symlinks=False)
    except OSError as error:
        raise ImportFailure(f"result directory is unavailable: {error}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise ImportFailure("result path is not a directory")
    try:
        names = {entry.name for entry in result_dir.iterdir()}
    except OSError as error:
        raise ImportFailure(f"cannot inventory result directory: {error}") from error
    if COMPLETION_NAME not in names:
        raise ImportFailure("result package is incomplete: completion sentinel is absent")
    completion_artifact = _stable_read(result_dir / COMPLETION_NAME)
    completion = _json_load_bytes(completion_artifact.data, "completion sentinel")
    if not isinstance(completion, dict):
        raise ImportFailure("completion sentinel must be an object")
    _strict_keys(completion, {"schema", "status", "receipt_sha256", "trace_sha256"}, "completion sentinel")
    if completion["schema"] != COMPLETION_SCHEMA or completion["status"] not in {"PASS", "HOLD"}:
        raise ImportFailure("completion sentinel schema/status mismatch")
    _validate_sha(completion["receipt_sha256"], "completion receipt_sha256")
    receipt_artifact = _stable_read(result_dir / RECEIPT_NAME)
    if receipt_artifact.raw_sha256 != completion["receipt_sha256"]:
        raise ImportFailure("completion/receipt SHA-256 mismatch")
    receipt = _json_load_bytes(receipt_artifact.data, "receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != completion["status"]:
        raise ImportFailure("completion/receipt status mismatch")
    if completion["status"] == "HOLD":
        if names != {RECEIPT_NAME, COMPLETION_NAME} or completion["trace_sha256"] is not None:
            raise ImportFailure("HOLD package shape is invalid")
        _qualify_receipt_and_trace(receipt, None)
    else:
        if names != {TRACE_NAME, RECEIPT_NAME, COMPLETION_NAME}:
            raise ImportFailure("PASS package shape is invalid")
        _validate_sha(completion["trace_sha256"], "completion trace_sha256")
        trace_artifact = _stable_read(result_dir / TRACE_NAME)
        if trace_artifact.raw_sha256 != completion["trace_sha256"]:
            raise ImportFailure("completion/trace SHA-256 mismatch")
        _qualify_receipt_and_trace(receipt, trace_artifact.data)
    return receipt


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--qualify", action="store_true", help="qualify an existing result directory only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.qualify:
            if args.source is not None or args.spec is not None:
                raise ImportFailure("--qualify cannot be combined with --source or --spec")
            receipt = qualify_result_dir(args.result_dir)
        else:
            if args.source is None or args.spec is None:
                raise ImportFailure("--source and --spec are required unless --qualify is used")
            receipt = import_dataset(args.source, args.spec, args.result_dir)
    except ImportHold as error:
        print(str(error), file=sys.stderr)
        return 3
    except ImportFailure as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if receipt["status"] == "HOLD":
        print(f"EVENT_DATASET_IMPORT_HOLD result_dir={args.result_dir}")
        return 3
    counts = receipt["counts"]
    print(
        "EVENT_DATASET_IMPORT_PASS "
        f"events={counts['events_emitted']} clipped={counts['clipped_events']} "
        f"out_of_range={counts['out_of_range_events']} "
        f"trace_sha256={receipt['trace']['raw_sha256']} result_dir={args.result_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
