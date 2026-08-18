#!/usr/bin/env python3
"""Import provenance-bound event-camera data into the logical AER JSONL ABI."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any, Iterable


SPEC_SCHEMA = "redred-event-import-v1"
RECEIPT_SCHEMA = "redred-event-import-receipt-v1"
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
TIME_TO_NS = {
    "s": Decimal("1000000000"),
    "ms": Decimal("1000000"),
    "us": Decimal("1000"),
    "ns": Decimal("1"),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ImportFailure(ValueError):
    """The input or import contract is malformed or contradictory."""


class ImportHold(ImportFailure):
    """A named format is intentionally unsupported pending its real contract."""


@dataclass(frozen=True)
class RawEvent:
    timestamp: Decimal
    x: int
    y: int
    polarity: int
    source_record_index: int


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ImportFailure(f"source is not a regular file: {path}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ImportFailure(f"cannot read source {path}: {error}") from error


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _load_spec(path: Path) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImportFailure(f"cannot read import specification {path}: {error}") from error
    if not isinstance(value, dict):
        raise ImportFailure("import specification must be a JSON object")
    if value.get("schema") != SPEC_SCHEMA:
        raise ImportFailure(f"import specification schema must be {SPEC_SCHEMA!r}")
    return value, _canonical_sha256(value)


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


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImportFailure(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImportFailure(f"{field} must be a nonnegative integer")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ImportFailure(f"{field} must be an integer or decimal string, not a float")
    try:
        result = Decimal(value if isinstance(value, str) else str(value))
    except (InvalidOperation, ValueError) as error:
        raise ImportFailure(f"{field} must be a decimal number") from error
    if not result.is_finite():
        raise ImportFailure(f"{field} must be finite")
    return result


def _integer(value: Any, field: str, *, json_input: bool) -> int:
    if isinstance(value, bool):
        raise ImportFailure(f"{field} must be an integer")
    if json_input:
        if not isinstance(value, int):
            raise ImportFailure(f"{field} must be a JSON integer")
        return value
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?[0-9]+", text):
        raise ImportFailure(f"{field} must be a base-10 integer")
    return int(text, 10)


def _normalize_polarity(value: Any, encoding: str, field: str, *, json_input: bool) -> int:
    raw = _integer(value, field, json_input=json_input)
    if encoding == "minus_plus_one":
        if raw not in (-1, 1):
            raise ImportFailure(f"{field} must be -1 or 1 for minus_plus_one")
        return raw
    if encoding == "zero_one":
        if raw not in (0, 1):
            raise ImportFailure(f"{field} must be 0 or 1 for zero_one")
        return 1 if raw == 1 else -1
    raise ImportFailure("input.polarity_encoding must be minus_plus_one or zero_one")


def _validate_common_spec(spec: dict[str, Any]) -> tuple[int, int, dict[str, Any], dict[str, Any]]:
    _strict_keys(
        spec,
        {"schema", "dataset_label", "source", "sensor", "input", "cycle_mapping", "bounds_policy"},
        "import specification",
    )
    if not isinstance(spec["dataset_label"], str) or not spec["dataset_label"].strip():
        raise ImportFailure("dataset_label must be a non-empty string")

    source = spec["source"]
    if not isinstance(source, dict):
        raise ImportFailure("source must be an object")
    _strict_keys(source, {"sha256"}, "source")
    if not isinstance(source["sha256"], str) or not SHA256_RE.fullmatch(source["sha256"]):
        raise ImportFailure("source.sha256 must be a lowercase SHA-256 digest")

    sensor = spec["sensor"]
    if not isinstance(sensor, dict):
        raise ImportFailure("sensor must be an object")
    _strict_keys(sensor, {"width", "height"}, "sensor")
    width = _positive_int(sensor["width"], "sensor.width")
    height = _positive_int(sensor["height"], "sensor.height")

    input_spec = spec["input"]
    if not isinstance(input_spec, dict):
        raise ImportFailure("input must be an object")
    if input_spec.get("format") == "samsung_official":
        _strict_keys(input_spec, {"format"}, "input")
    else:
        time_unit = input_spec.get("time_unit")
        if time_unit not in TIME_TO_NS:
            raise ImportFailure("input.time_unit must be one of s, ms, us, or ns")
        if input_spec.get("polarity_encoding") not in {"minus_plus_one", "zero_one"}:
            raise ImportFailure("input.polarity_encoding must be minus_plus_one or zero_one")

    cycle = spec["cycle_mapping"]
    if not isinstance(cycle, dict):
        raise ImportFailure("cycle_mapping must be an object")
    _strict_keys(cycle, {"period_ns", "origin", "deadline_slack_cycles"}, "cycle_mapping")
    period_ns = _decimal(cycle["period_ns"], "cycle_mapping.period_ns")
    if period_ns <= 0:
        raise ImportFailure("cycle_mapping.period_ns must be positive")
    if cycle["origin"] != "first_event":
        raise ImportFailure("cycle_mapping.origin must be first_event")
    _nonnegative_int(cycle["deadline_slack_cycles"], "cycle_mapping.deadline_slack_cycles")
    if spec["bounds_policy"] not in {"reject", "clip"}:
        raise ImportFailure("bounds_policy must be reject or clip")
    return width, height, input_spec, cycle


def _parse_canonical_jsonl(
    source: Path, input_spec: dict[str, Any]
) -> tuple[list[RawEvent], dict[str, int]]:
    _strict_keys(input_spec, {"format", "time_unit", "polarity_encoding"}, "input")
    result: list[RawEvent] = []
    blank_lines = 0
    try:
        with source.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    blank_lines += 1
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ImportFailure(f"{source}:{line_number}: invalid JSON: {error}") from error
                if not isinstance(row, dict):
                    raise ImportFailure(f"{source}:{line_number}: event must be an object")
                _strict_keys(row, {"timestamp", "x", "y", "polarity"}, f"{source}:{line_number}")
                record = len(result)
                result.append(
                    RawEvent(
                        _decimal(row["timestamp"], f"record {record} timestamp"),
                        _integer(row["x"], f"record {record} x", json_input=True),
                        _integer(row["y"], f"record {record} y", json_input=True),
                        _normalize_polarity(
                            row["polarity"], input_spec["polarity_encoding"],
                            f"record {record} polarity", json_input=True,
                        ),
                        record,
                    )
                )
    except UnicodeError as error:
        raise ImportFailure(f"source is not valid UTF-8: {error}") from error
    if not result:
        raise ImportFailure("source contains no event records")
    return result, {"blank_lines": blank_lines, "comment_lines": 0}


def _delimited_rows(
    source: Path, delimiter: str, comment_prefix: str | None
) -> tuple[list[tuple[int, list[str]]], dict[str, int]]:
    rows: list[tuple[int, list[str]]] = []
    blank_lines = 0
    comment_lines = 0
    try:
        with source.open(encoding="utf-8", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    blank_lines += 1
                    continue
                if comment_prefix is not None and line.lstrip().startswith(comment_prefix):
                    comment_lines += 1
                    continue
                if delimiter == "whitespace":
                    fields = line.split()
                else:
                    fields = next(csv.reader([line], delimiter=delimiter, strict=True))
                    fields = [field.strip() for field in fields]
                rows.append((line_number, fields))
    except (OSError, UnicodeError, csv.Error) as error:
        raise ImportFailure(f"cannot parse delimited source {source}: {error}") from error
    return rows, {"blank_lines": blank_lines, "comment_lines": comment_lines}


def _parse_delimited(
    source: Path, input_spec: dict[str, Any]
) -> tuple[list[RawEvent], dict[str, int]]:
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
    if comment_prefix is not None and (
        not isinstance(comment_prefix, str) or not comment_prefix or "\n" in comment_prefix
    ):
        raise ImportFailure("input.comment_prefix must be null or a non-empty single-line string")
    columns = input_spec["columns"]
    if not isinstance(columns, dict):
        raise ImportFailure("input.columns must be an object")
    _strict_keys(columns, {"timestamp", "x", "y", "polarity"}, "input.columns")
    mappings = list(columns.values())
    if header:
        if any(not isinstance(value, str) or not value for value in mappings):
            raise ImportFailure("header-based column mappings must be non-empty strings")
    elif any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in mappings):
        raise ImportFailure("positional column mappings must be nonnegative integers")
    if len(set(mappings)) != 4:
        raise ImportFailure("input.columns mappings must be distinct")

    rows, counters = _delimited_rows(source, delimiter, comment_prefix)
    if not rows:
        raise ImportFailure("source contains no rows")
    indices: dict[str, int]
    if header:
        header_line, names = rows.pop(0)
        if len(names) != len(set(names)):
            raise ImportFailure(f"{source}:{header_line}: duplicate header names")
        missing = [name for name in mappings if name not in names]
        if missing:
            raise ImportFailure(f"{source}:{header_line}: missing columns: {', '.join(missing)}")
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
            raise ImportFailure(f"{source}:{line_number}: row has too few columns")
        result.append(
            RawEvent(
                _decimal(row[indices["timestamp"]], f"record {record} timestamp"),
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


def _parse_events(
    source: Path, input_spec: dict[str, Any]
) -> tuple[list[RawEvent], dict[str, int]]:
    source_format = input_spec.get("format")
    if source_format == "samsung_official":
        raise ImportHold(
            "HOLD: official Samsung event format is unsupported until its actual specification is provided"
        )
    if source_format == "canonical_jsonl":
        return _parse_canonical_jsonl(source, input_spec)
    if source_format == "generic_delimited":
        return _parse_delimited(source, input_spec)
    raise ImportFailure("input.format must be canonical_jsonl, generic_delimited, or samsung_official")


def _convert(
    raw_events: Iterable[RawEvent], spec: dict[str, Any], width: int, height: int,
    input_spec: dict[str, Any], cycle_spec: dict[str, Any], parse_counters: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    ordered = sorted(raw_events, key=lambda event: (event.timestamp, event.source_record_index))
    origin = ordered[0].timestamp
    time_scale = TIME_TO_NS[input_spec["time_unit"]]
    period_ns = _decimal(cycle_spec["period_ns"], "cycle_mapping.period_ns")
    slack = cycle_spec["deadline_slack_cycles"]
    bounds_policy = spec["bounds_policy"]
    counters = {
        "input_event_records": len(ordered),
        **parse_counters,
        "timestamp_tied_events": 0,
        "same_cycle_events": 0,
        "same_source_cycle_retriggers": 0,
        "out_of_range_events": 0,
        "x_below_range": 0,
        "x_above_range": 0,
        "y_below_range": 0,
        "y_above_range": 0,
        "clipped_events": 0,
        "clipped_coordinates": 0,
        "events_emitted": 0,
        "events_dropped": 0,
    }
    previous_timestamp: Decimal | None = None
    previous_cycle: int | None = None
    seen_source_cycles: set[tuple[int, int]] = set()
    result: list[dict[str, Any]] = []
    for event_id, raw in enumerate(ordered):
        if raw.timestamp == previous_timestamp:
            counters["timestamp_tied_events"] += 1
        previous_timestamp = raw.timestamp
        elapsed_ns = (raw.timestamp - origin) * time_scale
        cycle = int((elapsed_ns / period_ns).to_integral_value(rounding=ROUND_FLOOR))
        if cycle < 0:
            raise ImportFailure("internal ordering error produced a negative occurrence cycle")
        if cycle == previous_cycle:
            counters["same_cycle_events"] += 1
        previous_cycle = cycle

        x, y = raw.x, raw.y
        x_low, x_high = x < 0, x >= width
        y_low, y_high = y < 0, y >= height
        out_of_range = x_low or x_high or y_low or y_high
        counters["x_below_range"] += int(x_low)
        counters["x_above_range"] += int(x_high)
        counters["y_below_range"] += int(y_low)
        counters["y_above_range"] += int(y_high)
        counters["out_of_range_events"] += int(out_of_range)
        if out_of_range:
            if bounds_policy == "reject":
                raise ImportFailure(
                    f"record {raw.source_record_index}: coordinate ({x}, {y}) is outside "
                    f"sensor {width}x{height}; bounds_policy=reject"
                )
            clipped_x = min(max(x, 0), width - 1)
            clipped_y = min(max(y, 0), height - 1)
            counters["clipped_events"] += 1
            counters["clipped_coordinates"] += int(clipped_x != x) + int(clipped_y != y)
            x, y = clipped_x, clipped_y
        source_id = y * width + x
        source_cycle = (source_id, cycle)
        if source_cycle in seen_source_cycles:
            counters["same_source_cycle_retriggers"] += 1
        seen_source_cycles.add(source_cycle)
        result.append(
            {
                "occurrence_cycle": cycle,
                "tb_only_event_id": event_id,
                "logical_source": source_id,
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
    if counters["events_emitted"] != counters["input_event_records"]:
        raise ImportFailure("event conservation failure during conversion")
    origin_text = format(origin, "f")
    return result, counters, origin_text


def _write_jsonl(path: Path, events: Iterable[dict[str, Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("w", encoding="ascii", newline="\n") as output:
            for event in events:
                line = json.dumps(event, separators=(",", ":"), ensure_ascii=True) + "\n"
                output.write(line)
                digest.update(line.encode("ascii"))
                count += 1
        temporary.replace(path)
    except OSError as error:
        raise ImportFailure(f"cannot write output trace {path}: {error}") from error
    return count, digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="ascii",
        )
        temporary.replace(path)
    except OSError as error:
        raise ImportFailure(f"cannot write receipt {path}: {error}") from error


def import_dataset(source: Path, spec_path: Path, output: Path, receipt_path: Path) -> dict[str, Any]:
    resolved = {
        "source": source.resolve(),
        "specification": spec_path.resolve(),
        "output": output.resolve(),
        "receipt": receipt_path.resolve(),
    }
    if len(set(resolved.values())) != len(resolved):
        aliases = ", ".join(f"{name}={path}" for name, path in resolved.items())
        raise ImportFailure(f"source, specification, output, and receipt paths must be distinct: {aliases}")
    spec, spec_sha256 = _load_spec(spec_path)
    width, height, input_spec, cycle_spec = _validate_common_spec(spec)
    actual_source_sha256 = _sha256(source)
    expected_source_sha256 = spec["source"]["sha256"]
    if actual_source_sha256 != expected_source_sha256:
        raise ImportFailure(
            f"source SHA-256 mismatch: expected {expected_source_sha256}, got {actual_source_sha256}"
        )
    try:
        raw_events, parse_counters = _parse_events(source, input_spec)
    except ImportHold as hold:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "HOLD",
            "reason": str(hold).removeprefix("HOLD: ").strip(),
            "dataset_label": spec["dataset_label"],
            "source": {"file": source.name, "sha256": actual_source_sha256},
            "spec_sha256": spec_sha256,
            "trace": None,
        }
        _write_json(receipt_path, receipt)
        raise
    events, counters, origin = _convert(
        raw_events, spec, width, height, input_spec, cycle_spec, parse_counters
    )
    event_count, trace_sha256 = _write_jsonl(output, events)
    if event_count != counters["events_emitted"]:
        raise ImportFailure("written trace cardinality does not match conversion counters")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "dataset_label": spec["dataset_label"],
        "source": {"file": source.name, "sha256": actual_source_sha256},
        "spec_sha256": spec_sha256,
        "input_contract": {
            "format": input_spec["format"],
            "time_unit": input_spec["time_unit"],
            "polarity_encoding": input_spec["polarity_encoding"],
            "sensor": {"width": width, "height": height},
            "bounds_policy": spec["bounds_policy"],
        },
        "ordering": {
            "key": ["timestamp", "source_record_index"],
            "timestamp_tie_policy": "preserve_source_record_order",
            "cycle_quantization": "floor",
        },
        "cycle_mapping": {
            "period_ns": str(cycle_spec["period_ns"]),
            "origin": "first_event",
            "origin_timestamp": origin,
            "deadline_slack_cycles": cycle_spec["deadline_slack_cycles"],
        },
        "conservation": "input_event_records == events_emitted + events_dropped",
        "counts": counters,
        "trace": {
            "file": output.name,
            "sha256": trace_sha256,
            "event_count": event_count,
            "event_schema": list(TRACE_FIELDS),
            "identity_mode": "address_only",
            "required_relation": "logical_source == y * sensor.width + x",
        },
    }
    _write_json(receipt_path, receipt)
    return receipt


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        receipt = import_dataset(args.source, args.spec, args.output, args.receipt)
    except ImportHold as error:
        print(str(error), file=sys.stderr)
        return 3
    except ImportFailure as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    counts = receipt["counts"]
    print(
        "EVENT_DATASET_IMPORT_PASS "
        f"events={counts['events_emitted']} clipped={counts['clipped_events']} "
        f"out_of_range={counts['out_of_range_events']} trace_sha256={receipt['trace']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
