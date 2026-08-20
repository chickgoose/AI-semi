from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.cohort import (
    CohortError,
    EXPECTED_SPEC_SHA256,
    OFFICIAL_SOURCE,
    decimal_seconds_to_ns,
    extract_cohorts,
    load_spec,
    parse_event_line,
    require_equal_event_ids,
    validate_dev_holdout,
    validate_spec,
)


def _window(window_id: str, start: str, end: str, indexed_lines: list[tuple[int, bytes]]) -> dict[str, object]:
    rows = []
    for index, raw in indexed_lines:
        timestamp = raw.split(b" ", 1)[0]
        timestamp_ns = decimal_seconds_to_ns(timestamp)
        if decimal_seconds_to_ns(start) <= timestamp_ns < decimal_seconds_to_ns(end):
            rows.append((index, raw, timestamp_ns, int(raw.rstrip(b"\n").rsplit(b" ", 1)[1])))
    raw_digest = hashlib.sha256()
    ids_digest = hashlib.sha256()
    for index, raw, _, _ in rows:
        raw_digest.update(raw)
        ids_digest.update(f"{index}\n".encode("ascii"))
    return {
        "id": window_id,
        "start_seconds_exact": start,
        "end_seconds_exact": end,
        "start_timestamp_ns_inclusive": decimal_seconds_to_ns(start),
        "end_timestamp_ns_exclusive": decimal_seconds_to_ns(end),
        "expected_event_count": len(rows),
        "expected_first_dataset_event_index": rows[0][0],
        "expected_last_dataset_event_index": rows[-1][0],
        "expected_first_timestamp_ns": rows[0][2],
        "expected_last_timestamp_ns": rows[-1][2],
        "expected_polarity_0": sum(row[3] == 0 for row in rows),
        "expected_polarity_1": sum(row[3] == 1 for row in rows),
        "selected_raw_lines_sha256": raw_digest.hexdigest(),
        "ordered_event_ids_sha256": ids_digest.hexdigest(),
    }


def _synthetic_source_and_spec() -> tuple[bytes, dict[str, object]]:
    lines = [
        b"1.000000000 1 1 0\n",
        b"1.000000001 2 2 1\n",
        b"1.000000002 3 3 0\n",
        b"1.000000003 4 4 1\n",
        b"2.000000000 5 5 0\n",
        b"2.000000001 6 6 1\n",
        b"2.000000002 7 7 0\n",
        b"2.000000003 8 8 1\n",
    ]
    source = b"".join(lines)
    indexed = list(enumerate(lines))
    spec = {
        "schema": "redred.uzh_mc_wtb_motion_v3.cohorts/v1",
        "dataset": {
            "provider": "fixture",
            "collection": "fixture",
            "sequence": "shapes_rotation",
            "sensor": "DAVIS240C",
            "official_redred_traffic": False,
        },
        "source": {
            "basename": "events.txt",
            "size_bytes": len(source),
            "line_count": len(lines),
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "sensor": {"width": 240, "height": 180},
        "timebase": {
            "unit": "integer_nanoseconds",
            "source_timestamp_fractional_digits": 9,
            "window_rule": "start_timestamp_ns_inclusive <= timestamp_ns < end_timestamp_ns_exclusive",
        },
        "identity": {
            "dataset_event_index": "zero_based_physical_events_txt_line_index",
            "selected_raw_lines_sha256": "sha256_of_concatenated_original_selected_line_bytes_in_source_order",
            "ordered_event_ids_sha256": "sha256_of_each_base10_dataset_event_index_followed_by_LF_in_source_order",
            "downstream_equal_ids_rule": "every compared arm must contain exactly the ordered query dataset_event_index sequence",
        },
        "split_policy": {
            "development_origin": "fixture",
            "anchor_duration_ns": 250_000,
            "query_duration_ns": 1_000_000,
            "holdout_search": "fixture",
            "holdout_eligibility": "fixture",
            "selected_holdout_offset_seconds": 1,
            "metric_or_arm_scores_consulted": False,
            "holdout_remains_blinded_for_metric_threshold_selection": True,
        },
        "splits": {"development": ["dev"], "holdout": ["holdout"]},
        "cohorts": [
            {
                "id": "dev",
                "split": "development",
                "anchor": _window("dev_anchor", "0.999750002", "1.000000002", indexed),
                "query": _window("dev_query", "1.000000002", "1.001000002", indexed),
            },
            {
                "id": "holdout",
                "split": "holdout",
                "anchor": _window("holdout_anchor", "1.999750002", "2.000000002", indexed),
                "query": _window("holdout_query", "2.000000002", "2.001000002", indexed),
            },
        ],
    }
    return source, spec


class ExactTimestampTests(unittest.TestCase):
    def test_decimal_nanoseconds_are_exact_and_strict(self):
        self.assertEqual(decimal_seconds_to_ns("41.321000001"), 41_321_000_001)
        self.assertEqual(decimal_seconds_to_ns(b"0.000000000"), 0)
        for invalid in ("41.321", "41.3210000000", "4.1321000001e1", "+41.321000000", " 41.321000000", "NaN"):
            with self.subTest(invalid=invalid), self.assertRaises(CohortError):
                decimal_seconds_to_ns(invalid)

    def test_canonical_source_line_and_sensor_bounds(self):
        event = parse_event_line(b"41.321000001 239 179 1\n", 17)
        self.assertEqual((event.dataset_event_index, event.timestamp_ns, event.x, event.y, event.polarity_01), (17, 41_321_000_001, 239, 179, 1))
        for raw in (b"41.321 1 1 0\n", b"41.321000001 240 1 0\n", b"41.321000001 1 1 2\n", b"41.321000001 1 1 0"):
            with self.subTest(raw=raw), self.assertRaises(CohortError):
                parse_event_line(raw, 0)


class FrozenManifestTests(unittest.TestCase):
    def test_repository_manifest_is_byte_frozen_and_source_pinned(self):
        spec = load_spec()
        manifest = Path("benchmarks/redred_uzh_mc_wtb_motion_v3/cohorts.json")
        self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), EXPECTED_SPEC_SHA256)
        self.assertEqual(spec["source"], OFFICIAL_SOURCE)
        self.assertEqual(spec["splits"], {
            "development": ["shapes_rotation_dev_41_321"],
            "holdout": ["shapes_rotation_holdout_43_321"],
        })
        validate_dev_holdout(spec)

    def test_manifest_overlap_and_id_overlap_are_rejected(self):
        _, spec = _synthetic_source_and_spec()
        overlapping_time = copy.deepcopy(spec)
        holdout = overlapping_time["cohorts"][1]
        holdout["anchor"]["start_seconds_exact"] = "1.000000002"
        holdout["anchor"]["end_seconds_exact"] = "1.000250002"
        holdout["anchor"]["start_timestamp_ns_inclusive"] = 1_000_000_002
        holdout["anchor"]["end_timestamp_ns_exclusive"] = 1_000_250_002
        holdout["anchor"]["expected_first_timestamp_ns"] = 1_000_000_002
        holdout["anchor"]["expected_last_timestamp_ns"] = 1_000_000_003
        holdout["query"]["start_seconds_exact"] = "1.000250002"
        holdout["query"]["end_seconds_exact"] = "1.001250002"
        holdout["query"]["start_timestamp_ns_inclusive"] = 1_000_250_002
        holdout["query"]["end_timestamp_ns_exclusive"] = 1_001_250_002
        holdout["query"]["expected_first_timestamp_ns"] = 1_000_250_002
        holdout["query"]["expected_last_timestamp_ns"] = 1_000_250_003
        with self.assertRaisesRegex(CohortError, "overlap"):
            validate_spec(overlapping_time, require_official_source=False)

        overlapping_ids = copy.deepcopy(spec)
        dev_query = overlapping_ids["cohorts"][0]["query"]
        holdout_query = overlapping_ids["cohorts"][1]["query"]
        for key in ("expected_first_dataset_event_index", "expected_last_dataset_event_index"):
            holdout_query[key] = dev_query[key]
        with self.assertRaisesRegex(CohortError, "event-ID ranges overlap"):
            validate_spec(overlapping_ids, require_official_source=False)


class SyntheticExtractionTests(unittest.TestCase):
    def setUp(self):
        self.source, self.spec = _synthetic_source_and_spec()
        self.temporary = tempfile.TemporaryDirectory()
        self.events_path = Path(self.temporary.name) / "events.txt"
        self.events_path.write_bytes(self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def test_deterministic_extraction_split_and_ordered_identity(self):
        first = extract_cohorts(self.events_path, spec=self.spec)
        second = extract_cohorts(self.events_path, spec=self.spec)
        self.assertEqual(first.source_sha256, hashlib.sha256(self.source).hexdigest())
        self.assertEqual(first.window("dev", "anchor").event_ids, (0, 1))
        self.assertEqual(first.window("dev", "query").event_ids, (2, 3))
        self.assertEqual(first.window("holdout", "anchor").event_ids, (4, 5))
        self.assertEqual(first.window("holdout", "query").event_ids, (6, 7))
        self.assertEqual(first.windows, second.windows)
        require_equal_event_ids(
            first.window("dev", "query").event_ids,
            {"RAW": (2, 3), "MC_CORRECT": [2, 3], "SENSOR_FIXED": iter((2, 3))},
        )

    def test_equal_ids_rejects_missing_reordered_duplicate_and_bool(self):
        bad = {
            "missing": [10, 11],
            "reordered": [11, 10, 12],
            "duplicate": [10, 11, 11],
            "bool": [10, True, 12],
        }
        for name, ids in bad.items():
            with self.subTest(name=name), self.assertRaises(CohortError):
                require_equal_event_ids((10, 11, 12), {name: ids})

    def test_source_tamper_and_nonmonotonic_source_fail_closed(self):
        tampered = bytearray(self.source)
        location = tampered.index(b"1 1 0")
        tampered[location] = ord("9")
        self.events_path.write_bytes(tampered)
        with self.assertRaisesRegex(CohortError, "immutable"):
            extract_cohorts(self.events_path, spec=self.spec)

        self.events_path.write_bytes(b"".join(self.source.splitlines(keepends=True)[::-1]))
        nonmonotonic = copy.deepcopy(self.spec)
        nonmonotonic["source"]["sha256"] = hashlib.sha256(self.events_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(CohortError, "decrease"):
            extract_cohorts(self.events_path, spec=nonmonotonic)


class OptionalOfficialSourceTests(unittest.TestCase):
    def test_official_source_full_scan(self):
        root = os.environ.get("REDRED_UZH_SHAPES_ROTATION_ROOT")
        if root is None:
            self.skipTest("set REDRED_UZH_SHAPES_ROTATION_ROOT for immutable 509 MB source validation")
        extraction = extract_cohorts(Path(root) / "events.txt")
        self.assertEqual(extraction.source_sha256, OFFICIAL_SOURCE["sha256"])
        self.assertEqual(len(extraction.window("shapes_rotation_dev_41_321", "anchor").records), 251)
        self.assertEqual(len(extraction.window("shapes_rotation_dev_41_321", "query").records), 1100)
        self.assertEqual(len(extraction.window("shapes_rotation_holdout_43_321", "anchor").records), 102)
        self.assertEqual(len(extraction.window("shapes_rotation_holdout_43_321", "query").records), 370)


if __name__ == "__main__":
    unittest.main()
