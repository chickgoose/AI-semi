"""Frozen UZH ``events.txt`` cohort extraction for MC-WTB metric v3.

The production entry point is :func:`extract_cohorts`.  It scans and hashes the
entire source member, converts canonical nine-decimal timestamps to integer
nanoseconds with :class:`decimal.Decimal`, and fails closed unless every source,
window, raw-line, and ordered-event-ID pin matches ``cohorts.json``.

No derived event payload is written by this module.  In particular, the 509 MB
source member and extracted rows remain external to Git.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


SCHEMA = "redred.uzh_mc_wtb_motion_v3.cohorts/v1"
DEFAULT_SPEC_PATH = Path(__file__).with_name("cohorts.json")

# Updated only with an intentional review of the complete cohorts.json bytes.
EXPECTED_SPEC_SHA256 = "5a24829fdaaaec679e8ef82ac435158ee0225af5b644d3056895e7fcc94acef4"

OFFICIAL_SOURCE = {
    "basename": "events.txt",
    "size_bytes": 509_907_771,
    "line_count": 23_126_288,
    "sha256": "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda",
}

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{9}\Z")
_SOURCE_LINE_RE = re.compile(
    rb"(?P<timestamp>(?:0|[1-9][0-9]*)\.[0-9]{9}) "
    rb"(?P<x>(?:0|[1-9][0-9]*)) (?P<y>(?:0|[1-9][0-9]*)) "
    rb"(?P<polarity>[01])\n\Z"
)
_WINDOW_KEYS = {
    "id",
    "start_seconds_exact",
    "end_seconds_exact",
    "start_timestamp_ns_inclusive",
    "end_timestamp_ns_exclusive",
    "expected_event_count",
    "expected_first_dataset_event_index",
    "expected_last_dataset_event_index",
    "expected_first_timestamp_ns",
    "expected_last_timestamp_ns",
    "expected_polarity_0",
    "expected_polarity_1",
    "selected_raw_lines_sha256",
    "ordered_event_ids_sha256",
}


class CohortError(ValueError):
    """A source, manifest, split, or ordered-identity contract failed."""


@dataclass(frozen=True, slots=True)
class Event:
    dataset_event_index: int
    timestamp_seconds_exact: str
    timestamp_ns: int
    x: int
    y: int
    polarity_01: int
    raw_line: bytes


@dataclass(frozen=True, slots=True)
class WindowExtraction:
    window_id: str
    records: tuple[Event, ...]
    selected_raw_lines_sha256: str
    ordered_event_ids_sha256: str

    @property
    def event_ids(self) -> tuple[int, ...]:
        return tuple(record.dataset_event_index for record in self.records)


@dataclass(frozen=True, slots=True)
class CohortExtraction:
    source_sha256: str
    source_size_bytes: int
    source_line_count: int
    windows: Mapping[str, WindowExtraction]
    cohort_windows: Mapping[str, Mapping[str, str]]

    def window(self, cohort_id: str, role: str) -> WindowExtraction:
        if role not in ("anchor", "query"):
            raise CohortError(f"unknown window role: {role!r}")
        try:
            window_id = self.cohort_windows[cohort_id][role]
            return self.windows[window_id]
        except KeyError as exc:
            raise CohortError(f"unknown cohort/role: {cohort_id!r}/{role}") from exc


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CohortError(f"{context} must be an integer >= {minimum}")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise CohortError(f"{context} must be a non-empty string")
    return value


def _digest(value: object, context: str) -> str:
    text = _string(value, context)
    if _SHA256_RE.fullmatch(text) is None:
        raise CohortError(f"{context} must be a lowercase SHA-256 hex digest")
    return text


def _strict_mapping(value: object, keys: set[str], context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CohortError(f"{context} keys mismatch")
    return value


def decimal_seconds_to_ns(value: str | bytes) -> int:
    """Convert one canonical nine-decimal nonnegative second lexeme to ns.

    Float parsing, exponent notation, a shortened fractional part, signs, and
    whitespace are rejected.  This makes half-open boundary membership
    independent of platform floating-point behavior.
    """

    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CohortError("timestamp must be ASCII") from exc
    elif isinstance(value, str):
        text = value
    else:
        raise CohortError("timestamp must be str or bytes")
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise CohortError("timestamp must be canonical nonnegative seconds with nine decimals")
    try:
        scaled = Decimal(text) * Decimal(1_000_000_000)
    except InvalidOperation as exc:  # Defensive; the regular expression is stricter.
        raise CohortError("invalid decimal timestamp") from exc
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise CohortError("timestamp is not exactly representable in integer nanoseconds")
    return int(integral)


def parse_event_line(
    raw_line: bytes,
    dataset_event_index: int,
    *,
    width: int = 240,
    height: int = 180,
    max_line_bytes: int = 96,
) -> Event:
    """Parse one canonical UZH text-event source line without float use."""

    _integer(dataset_event_index, "dataset_event_index")
    _integer(width, "width", minimum=1)
    _integer(height, "height", minimum=1)
    _integer(max_line_bytes, "max_line_bytes", minimum=1)
    if not isinstance(raw_line, bytes) or not raw_line or len(raw_line) > max_line_bytes:
        raise CohortError("source line must be non-empty bytes within max_line_bytes")
    match = _SOURCE_LINE_RE.fullmatch(raw_line)
    if match is None:
        raise CohortError(f"events.txt line {dataset_event_index + 1} is not canonical")
    timestamp_bytes = match.group("timestamp")
    timestamp_ns = decimal_seconds_to_ns(timestamp_bytes)
    x = int(match.group("x"))
    y = int(match.group("y"))
    polarity = int(match.group("polarity"))
    if x >= width or y >= height:
        raise CohortError(f"events.txt line {dataset_event_index + 1} is outside sensor bounds")
    return Event(
        dataset_event_index=dataset_event_index,
        timestamp_seconds_exact=timestamp_bytes.decode("ascii"),
        timestamp_ns=timestamp_ns,
        x=x,
        y=y,
        polarity_01=polarity,
        raw_line=raw_line,
    )


def _validate_window(window: object, context: str) -> Mapping[str, object]:
    item = _strict_mapping(window, _WINDOW_KEYS, context)
    _string(item["id"], f"{context}.id")
    start_text = _string(item["start_seconds_exact"], f"{context}.start_seconds_exact")
    end_text = _string(item["end_seconds_exact"], f"{context}.end_seconds_exact")
    start = _integer(item["start_timestamp_ns_inclusive"], f"{context}.start")
    end = _integer(item["end_timestamp_ns_exclusive"], f"{context}.end", minimum=1)
    if decimal_seconds_to_ns(start_text) != start or decimal_seconds_to_ns(end_text) != end:
        raise CohortError(f"{context} exact seconds/ns mismatch")
    if start >= end:
        raise CohortError(f"{context} must be a non-empty half-open interval")
    count = _integer(item["expected_event_count"], f"{context}.expected_event_count", minimum=1)
    first_id = _integer(item["expected_first_dataset_event_index"], f"{context}.first_id")
    last_id = _integer(item["expected_last_dataset_event_index"], f"{context}.last_id")
    if last_id - first_id + 1 != count:
        raise CohortError(f"{context} event IDs are not one contiguous source slice")
    first_ns = _integer(item["expected_first_timestamp_ns"], f"{context}.first_timestamp_ns")
    last_ns = _integer(item["expected_last_timestamp_ns"], f"{context}.last_timestamp_ns")
    if not start <= first_ns <= last_ns < end:
        raise CohortError(f"{context} expected timestamps are outside the window")
    p0 = _integer(item["expected_polarity_0"], f"{context}.expected_polarity_0")
    p1 = _integer(item["expected_polarity_1"], f"{context}.expected_polarity_1")
    if p0 + p1 != count:
        raise CohortError(f"{context} polarity conservation mismatch")
    _digest(item["selected_raw_lines_sha256"], f"{context}.selected_raw_lines_sha256")
    _digest(item["ordered_event_ids_sha256"], f"{context}.ordered_event_ids_sha256")
    return item


def validate_spec(spec: object, *, require_official_source: bool = True) -> None:
    """Validate manifest structure, source pins, windows, and split isolation."""

    top = _strict_mapping(
        spec,
        {"schema", "dataset", "source", "sensor", "timebase", "identity", "split_policy", "splits", "cohorts"},
        "cohorts spec",
    )
    if top["schema"] != SCHEMA:
        raise CohortError("cohorts spec schema mismatch")

    dataset = _strict_mapping(
        top["dataset"],
        {"provider", "collection", "sequence", "sensor", "official_redred_traffic"},
        "dataset",
    )
    for key in ("provider", "collection", "sequence", "sensor"):
        _string(dataset[key], f"dataset.{key}")
    if dataset["sequence"] != "shapes_rotation" or dataset["sensor"] != "DAVIS240C":
        raise CohortError("dataset identity mismatch")
    if dataset["official_redred_traffic"] is not False:
        raise CohortError("UZH extension must not be labeled official REDRED traffic")

    source = _strict_mapping(top["source"], set(OFFICIAL_SOURCE), "source")
    _string(source["basename"], "source.basename")
    _integer(source["size_bytes"], "source.size_bytes", minimum=1)
    _integer(source["line_count"], "source.line_count", minimum=1)
    _digest(source["sha256"], "source.sha256")
    if require_official_source and dict(source) != OFFICIAL_SOURCE:
        raise CohortError("official events.txt immutable pins mismatch")

    sensor = _strict_mapping(top["sensor"], {"width", "height"}, "sensor")
    _integer(sensor["width"], "sensor.width", minimum=1)
    _integer(sensor["height"], "sensor.height", minimum=1)
    if require_official_source and dict(sensor) != {"width": 240, "height": 180}:
        raise CohortError("official DAVIS240C geometry mismatch")

    timebase = _strict_mapping(
        top["timebase"],
        {"unit", "source_timestamp_fractional_digits", "window_rule"},
        "timebase",
    )
    if timebase != {
        "unit": "integer_nanoseconds",
        "source_timestamp_fractional_digits": 9,
        "window_rule": "start_timestamp_ns_inclusive <= timestamp_ns < end_timestamp_ns_exclusive",
    }:
        raise CohortError("timebase contract mismatch")

    identity = _strict_mapping(
        top["identity"],
        {"dataset_event_index", "selected_raw_lines_sha256", "ordered_event_ids_sha256", "downstream_equal_ids_rule"},
        "identity",
    )
    expected_identity = {
        "dataset_event_index": "zero_based_physical_events_txt_line_index",
        "selected_raw_lines_sha256": "sha256_of_concatenated_original_selected_line_bytes_in_source_order",
        "ordered_event_ids_sha256": "sha256_of_each_base10_dataset_event_index_followed_by_LF_in_source_order",
        "downstream_equal_ids_rule": "every compared arm must contain exactly the ordered query dataset_event_index sequence",
    }
    if identity != expected_identity:
        raise CohortError("event identity contract mismatch")

    policy = _strict_mapping(
        top["split_policy"],
        {
            "development_origin", "anchor_duration_ns", "query_duration_ns", "holdout_search",
            "holdout_eligibility", "selected_holdout_offset_seconds", "metric_or_arm_scores_consulted",
            "holdout_remains_blinded_for_metric_threshold_selection",
        },
        "split_policy",
    )
    if _integer(policy["anchor_duration_ns"], "split_policy.anchor_duration_ns", minimum=1) != 250_000:
        raise CohortError("anchor duration must remain 0.25 ms")
    if _integer(policy["query_duration_ns"], "split_policy.query_duration_ns", minimum=1) != 1_000_000:
        raise CohortError("query duration must remain 1 ms")
    _integer(policy["selected_holdout_offset_seconds"], "split_policy.selected_holdout_offset_seconds", minimum=1)
    if policy["metric_or_arm_scores_consulted"] is not False:
        raise CohortError("holdout selection must be metric-blind")
    if policy["holdout_remains_blinded_for_metric_threshold_selection"] is not True:
        raise CohortError("holdout blindness declaration missing")
    for key in ("development_origin", "holdout_search", "holdout_eligibility"):
        _string(policy[key], f"split_policy.{key}")

    splits = _strict_mapping(top["splits"], {"development", "holdout"}, "splits")
    split_ids: dict[str, tuple[str, ...]] = {}
    for split in ("development", "holdout"):
        values = splits[split]
        if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
            raise CohortError(f"splits.{split} must be a non-empty string list")
        if len(values) != len(set(values)):
            raise CohortError(f"splits.{split} contains duplicates")
        split_ids[split] = tuple(values)
    if set(split_ids["development"]) & set(split_ids["holdout"]):
        raise CohortError("development and holdout cohort IDs overlap")

    cohorts = top["cohorts"]
    if not isinstance(cohorts, list) or not cohorts:
        raise CohortError("cohorts must be a non-empty list")
    found: dict[str, str] = {}
    intervals: list[tuple[int, int, str, str]] = []
    id_ranges: list[tuple[int, int, str, str]] = []
    window_ids: set[str] = set()
    for index, raw_cohort in enumerate(cohorts):
        cohort = _strict_mapping(raw_cohort, {"id", "split", "anchor", "query"}, f"cohorts[{index}]")
        cohort_id = _string(cohort["id"], f"cohorts[{index}].id")
        split = _string(cohort["split"], f"cohorts[{index}].split")
        if cohort_id in found or split not in split_ids or cohort_id not in split_ids[split]:
            raise CohortError(f"cohort {cohort_id!r} split membership mismatch")
        found[cohort_id] = split
        anchor = _validate_window(cohort["anchor"], f"{cohort_id}.anchor")
        query = _validate_window(cohort["query"], f"{cohort_id}.query")
        if anchor["end_timestamp_ns_exclusive"] != query["start_timestamp_ns_inclusive"]:
            raise CohortError(f"{cohort_id} anchor must end exactly at query start")
        if anchor["end_timestamp_ns_exclusive"] - anchor["start_timestamp_ns_inclusive"] != policy["anchor_duration_ns"]:
            raise CohortError(f"{cohort_id} anchor duration mismatch")
        if query["end_timestamp_ns_exclusive"] - query["start_timestamp_ns_inclusive"] != policy["query_duration_ns"]:
            raise CohortError(f"{cohort_id} query duration mismatch")
        for role, window in (("anchor", anchor), ("query", query)):
            window_id = str(window["id"])
            if window_id in window_ids:
                raise CohortError(f"duplicate window ID: {window_id}")
            window_ids.add(window_id)
            intervals.append((int(window["start_timestamp_ns_inclusive"]), int(window["end_timestamp_ns_exclusive"]), split, window_id))
            id_ranges.append((int(window["expected_first_dataset_event_index"]), int(window["expected_last_dataset_event_index"]) + 1, split, window_id))

    declared = set(split_ids["development"]) | set(split_ids["holdout"])
    if set(found) != declared:
        raise CohortError("splits contain missing or undeclared cohort IDs")
    for ranges, label in ((intervals, "timestamp windows"), (id_ranges, "event-ID ranges")):
        ordered = sorted(ranges)
        for left, right in zip(ordered, ordered[1:]):
            if left[1] > right[0]:
                raise CohortError(f"{label} overlap: {left[3]} and {right[3]}")
    if len(split_ids["development"]) == 1 and len(split_ids["holdout"]) == 1:
        by_cohort_id = {str(cohort["id"]): cohort for cohort in cohorts}
        development = by_cohort_id[split_ids["development"][0]]
        holdout = by_cohort_id[split_ids["holdout"][0]]
        declared_offset_ns = int(policy["selected_holdout_offset_seconds"]) * 1_000_000_000
        actual_offset_ns = (
            int(holdout["query"]["start_timestamp_ns_inclusive"])
            - int(development["query"]["start_timestamp_ns_inclusive"])
        )
        if actual_offset_ns != declared_offset_ns:
            raise CohortError("selected holdout offset does not match the frozen query windows")


def load_spec(
    path: str | Path = DEFAULT_SPEC_PATH,
    *,
    require_frozen_spec: bool | None = None,
) -> dict[str, object]:
    """Load and validate a cohort manifest.

    The repository-default path is byte-hash frozen and must contain the
    official source pins.  Explicit fixture manifests may opt out of the
    compiled hash while retaining all structural, split, and per-source pins.
    """

    source_path = Path(path)
    raw = source_path.read_bytes()
    if require_frozen_spec is None:
        require_frozen_spec = source_path.resolve() == DEFAULT_SPEC_PATH.resolve()
    if require_frozen_spec and hashlib.sha256(raw).hexdigest() != EXPECTED_SPEC_SHA256:
        raise CohortError("frozen cohorts.json byte hash mismatch")
    try:
        spec = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CohortError("cohorts manifest is not valid UTF-8 JSON") from exc
    validate_spec(spec, require_official_source=require_frozen_spec)
    return spec


def _window_specs(spec: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    result: list[Mapping[str, object]] = []
    for cohort in spec["cohorts"]:  # type: ignore[index]
        result.extend((cohort["anchor"], cohort["query"]))
    return tuple(result)


def _verify_extracted_window(window: Mapping[str, object], records: Sequence[Event], raw_sha: str, ids_sha: str) -> None:
    context = str(window["id"])
    if len(records) != window["expected_event_count"]:
        raise CohortError(f"{context} selected event count mismatch")
    if not records:
        raise CohortError(f"{context} unexpectedly empty")
    actual = {
        "expected_first_dataset_event_index": records[0].dataset_event_index,
        "expected_last_dataset_event_index": records[-1].dataset_event_index,
        "expected_first_timestamp_ns": records[0].timestamp_ns,
        "expected_last_timestamp_ns": records[-1].timestamp_ns,
        "expected_polarity_0": sum(record.polarity_01 == 0 for record in records),
        "expected_polarity_1": sum(record.polarity_01 == 1 for record in records),
        "selected_raw_lines_sha256": raw_sha,
        "ordered_event_ids_sha256": ids_sha,
    }
    for field, value in actual.items():
        if value != window[field]:
            raise CohortError(f"{context} {field} mismatch")
    expected_ids = range(records[0].dataset_event_index, records[0].dataset_event_index + len(records))
    if any(record.dataset_event_index != expected for record, expected in zip(records, expected_ids)):
        raise CohortError(f"{context} event IDs are not contiguous and source ordered")


def extract_cohorts(
    events_path: str | Path,
    *,
    spec_path: str | Path = DEFAULT_SPEC_PATH,
    spec: Mapping[str, object] | None = None,
) -> CohortExtraction:
    """Extract every frozen anchor/query window in one deterministic scan.

    Passing ``spec`` is intended for self-contained tests; production callers
    should omit it so the compiled ``cohorts.json`` hash and official source
    pins are enforced.
    """

    if spec is None:
        loaded = load_spec(spec_path)
    else:
        validate_spec(spec, require_official_source=False)
        loaded = dict(spec)
    source = loaded["source"]
    sensor = loaded["sensor"]
    assert isinstance(source, dict) and isinstance(sensor, dict)
    path = Path(events_path)
    if path.name != source["basename"]:
        raise CohortError("events source basename mismatch")
    try:
        stat_size = path.stat().st_size
    except OSError as exc:
        raise CohortError("cannot stat events source") from exc
    if stat_size != source["size_bytes"]:
        raise CohortError("events source size mismatch")

    windows = _window_specs(loaded)
    record_lists: dict[str, list[Event]] = {str(window["id"]): [] for window in windows}
    raw_digests = {str(window["id"]): hashlib.sha256() for window in windows}
    id_digests = {str(window["id"]): hashlib.sha256() for window in windows}
    source_digest = hashlib.sha256()
    source_size = 0
    line_count = 0
    previous_timestamp_ns = -1
    eligibility_counts: dict[int, list[int]] = {}
    policy = loaded["split_policy"]
    if policy["holdout_eligibility"] == "first_offset_whose_anchor_has_at_least_32_events_of_each_polarity":
        development_id = loaded["splits"]["development"][0]
        development = next(cohort for cohort in loaded["cohorts"] if cohort["id"] == development_id)
        base_query_ns = int(development["query"]["start_timestamp_ns_inclusive"])
        selected_offset = int(policy["selected_holdout_offset_seconds"])
        eligibility_counts = {offset: [0, 0] for offset in range(1, selected_offset + 1)}
    try:
        with path.open("rb") as stream:
            for dataset_event_index, raw_line in enumerate(stream):
                source_digest.update(raw_line)
                source_size += len(raw_line)
                line_count += 1
                event = parse_event_line(
                    raw_line,
                    dataset_event_index,
                    width=int(sensor["width"]),
                    height=int(sensor["height"]),
                )
                if event.timestamp_ns < previous_timestamp_ns:
                    raise CohortError(f"events timestamps decrease at source line {dataset_event_index + 1}")
                previous_timestamp_ns = event.timestamp_ns
                for offset, counts in eligibility_counts.items():
                    candidate_query_ns = base_query_ns + offset * 1_000_000_000
                    if candidate_query_ns - int(policy["anchor_duration_ns"]) <= event.timestamp_ns < candidate_query_ns:
                        counts[event.polarity_01] += 1
                for window in windows:
                    start = int(window["start_timestamp_ns_inclusive"])
                    end = int(window["end_timestamp_ns_exclusive"])
                    if start <= event.timestamp_ns < end:
                        window_id = str(window["id"])
                        record_lists[window_id].append(event)
                        raw_digests[window_id].update(raw_line)
                        id_digests[window_id].update(f"{dataset_event_index}\n".encode("ascii"))
    except OSError as exc:
        raise CohortError("cannot read events source") from exc

    actual_source_sha = source_digest.hexdigest()
    if source_size != source["size_bytes"] or line_count != source["line_count"] or actual_source_sha != source["sha256"]:
        raise CohortError("events source immutable size/line/hash pins mismatch")
    if eligibility_counts:
        selected_offset = int(policy["selected_holdout_offset_seconds"])
        for offset in range(1, selected_offset):
            if min(eligibility_counts[offset]) >= 32:
                raise CohortError("selected holdout is not the first polarity-eligible offset")
        if min(eligibility_counts[selected_offset]) < 32:
            raise CohortError("selected holdout does not meet the frozen polarity eligibility rule")

    extracted: dict[str, WindowExtraction] = {}
    for window in windows:
        window_id = str(window["id"])
        records = tuple(record_lists[window_id])
        raw_sha = raw_digests[window_id].hexdigest()
        ids_sha = id_digests[window_id].hexdigest()
        _verify_extracted_window(window, records, raw_sha, ids_sha)
        extracted[window_id] = WindowExtraction(window_id, records, raw_sha, ids_sha)

    cohort_windows: dict[str, Mapping[str, str]] = {}
    for cohort in loaded["cohorts"]:  # type: ignore[index]
        cohort_windows[str(cohort["id"])] = MappingProxyType(
            {"anchor": str(cohort["anchor"]["id"]), "query": str(cohort["query"]["id"])}
        )
    result = CohortExtraction(
        source_sha256=actual_source_sha,
        source_size_bytes=source_size,
        source_line_count=line_count,
        windows=MappingProxyType(extracted),
        cohort_windows=MappingProxyType(cohort_windows),
    )
    validate_dev_holdout(loaded, result)
    return result


def _event_id_tuple(values: Iterable[int], context: str) -> tuple[int, ...]:
    try:
        result = tuple(values)
    except TypeError as exc:
        raise CohortError(f"{context} event IDs are not iterable") from exc
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in result):
        raise CohortError(f"{context} event IDs must be nonnegative integers")
    if len(set(result)) != len(result):
        raise CohortError(f"{context} event IDs contain duplicates")
    return result


def require_equal_event_ids(
    expected_ids: Iterable[int],
    observed_by_name: Mapping[str, Iterable[int]],
) -> tuple[int, ...]:
    """Require exact ordered-ID equality for all downstream arms/artifacts."""

    expected = _event_id_tuple(expected_ids, "expected")
    if not isinstance(observed_by_name, Mapping) or not observed_by_name:
        raise CohortError("observed_by_name must be a non-empty mapping")
    for name in sorted(observed_by_name):
        if not isinstance(name, str) or not name:
            raise CohortError("observed event-ID labels must be non-empty strings")
        observed = _event_id_tuple(observed_by_name[name], name)
        if observed != expected:
            mismatch = next((index for index, pair in enumerate(zip(expected, observed)) if pair[0] != pair[1]), min(len(expected), len(observed)))
            raise CohortError(
                f"{name} ordered event IDs differ at position {mismatch}; "
                f"expected_count={len(expected)} observed_count={len(observed)}"
            )
    return expected


def validate_dev_holdout(spec: Mapping[str, object], extraction: CohortExtraction | None = None) -> None:
    """Recheck temporal and event-identity isolation between both splits."""

    validate_spec(spec, require_official_source=False)
    split_for_cohort: dict[str, str] = {}
    for split, cohort_ids in spec["splits"].items():  # type: ignore[union-attr]
        for cohort_id in cohort_ids:
            split_for_cohort[cohort_id] = split

    times: dict[str, list[tuple[int, int]]] = {"development": [], "holdout": []}
    declared_ids: dict[str, set[int]] = {"development": set(), "holdout": set()}
    extracted_ids: dict[str, set[int]] = {"development": set(), "holdout": set()}
    for cohort in spec["cohorts"]:  # type: ignore[index]
        cohort_id = str(cohort["id"])
        split = split_for_cohort[cohort_id]
        for role in ("anchor", "query"):
            window = cohort[role]
            times[split].append((int(window["start_timestamp_ns_inclusive"]), int(window["end_timestamp_ns_exclusive"])))
            first_id = int(window["expected_first_dataset_event_index"])
            last_id = int(window["expected_last_dataset_event_index"])
            ids = set(range(first_id, last_id + 1))
            if declared_ids[split] & ids:
                raise CohortError(f"duplicate declared event IDs within {split}")
            declared_ids[split].update(ids)
            if extraction is not None:
                actual = set(extraction.window(cohort_id, role).event_ids)
                if actual != ids:
                    raise CohortError(f"{cohort_id}/{role} extracted event IDs differ from pins")
                extracted_ids[split].update(actual)
    if declared_ids["development"] & declared_ids["holdout"]:
        raise CohortError("development and holdout declared event IDs overlap")
    if any(max(left[0], right[0]) < min(left[1], right[1]) for left in times["development"] for right in times["holdout"]):
        raise CohortError("development and holdout timestamp windows overlap")
    if extraction is not None and extracted_ids["development"] & extracted_ids["holdout"]:
        raise CohortError("development and holdout extracted event IDs overlap")


__all__ = [
    "CohortError",
    "CohortExtraction",
    "DEFAULT_SPEC_PATH",
    "Event",
    "EXPECTED_SPEC_SHA256",
    "OFFICIAL_SOURCE",
    "SCHEMA",
    "WindowExtraction",
    "decimal_seconds_to_ns",
    "extract_cohorts",
    "load_spec",
    "parse_event_line",
    "require_equal_event_ids",
    "validate_dev_holdout",
    "validate_spec",
]
