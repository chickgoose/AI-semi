from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.redred_event_dataset.import_events import (
    ImportFailure,
    ImportHold,
    import_dataset,
    main,
)
from benchmarks.clean_slate_aer.prepare_sv_trace import prepare_trace


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "benchmarks" / "redred_event_dataset" / "fixtures"
SCHEMA = "redred-event-import-v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_spec(path: Path, source_path: Path, **overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "schema": SCHEMA,
        "dataset_label": "test-fixture",
        "source": {"sha256": digest(source_path)},
        "sensor": {"width": 4, "height": 3},
        "input": {
            "format": "canonical_jsonl",
            "time_unit": "us",
            "polarity_encoding": "minus_plus_one",
        },
        "cycle_mapping": {
            "period_ns": "1000",
            "origin": "first_event",
            "deadline_slack_cycles": 2,
        },
        "bounds_policy": "reject",
    }
    spec.update(overrides)
    path.write_text(json.dumps(spec), encoding="utf-8")
    return spec


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ImportEventsTest(unittest.TestCase):
    def test_generic_fixture_is_lossless_stable_and_counts_clipping(self) -> None:
        source = FIXTURES / "generic_events.csv"
        spec = FIXTURES / "generic_import.json"
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir)
            second = Path(second_dir)
            receipt = import_dataset(source, spec, first / "events.jsonl", first / "receipt.json")
            receipt_again = import_dataset(
                source, spec, second / "events.jsonl", second / "receipt.json"
            )
            events = read_jsonl(first / "events.jsonl")

            self.assertEqual(digest(first / "events.jsonl"), digest(second / "events.jsonl"))
            self.assertEqual(receipt["trace"]["sha256"], receipt_again["trace"]["sha256"])
            self.assertEqual([event["tb_only_event_id"] for event in events], [0, 1, 2, 3])
            self.assertEqual([event["occurrence_cycle"] for event in events], [0, 0, 1, 1])
            self.assertEqual([event["logical_source"] for event in events], [0, 0, 1, 3])
            self.assertEqual([event["polarity"] for event in events], [-1, 1, 1, 1])
            self.assertEqual((events[-1]["x"], events[-1]["y"]), (3, 0))
            counts = receipt["counts"]
            self.assertEqual(counts["input_event_records"], 4)
            self.assertEqual(counts["events_emitted"], 4)
            self.assertEqual(counts["events_dropped"], 0)
            self.assertEqual(counts["timestamp_tied_events"], 1)
            self.assertEqual(counts["same_cycle_events"], 2)
            self.assertEqual(counts["same_source_cycle_retriggers"], 1)
            self.assertEqual(counts["out_of_range_events"], 1)
            self.assertEqual(counts["clipped_events"], 1)
            self.assertEqual(counts["clipped_coordinates"], 2)
            self.assertEqual(counts["x_above_range"], 1)
            self.assertEqual(counts["y_below_range"], 1)
            self.assertEqual(receipt["source"]["sha256"], digest(source))

    def test_canonical_jsonl_uses_exact_decimal_time_and_source_order_for_ties(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = import_dataset(source, spec, root / "events.jsonl", root / "receipt.json")
            events = read_jsonl(root / "events.jsonl")
        self.assertEqual([event["occurrence_cycle"] for event in events], [0, 0, 1])
        self.assertEqual([event["logical_source"] for event in events], [3, 4, 5])
        self.assertEqual([event["polarity"] for event in events], [1, -1, -1])
        self.assertEqual(receipt["cycle_mapping"]["origin_timestamp"], "1.0")
        self.assertEqual(receipt["counts"]["timestamp_tied_events"], 1)
        self.assertEqual(receipt["counts"]["events_dropped"], 0)

    def test_headerless_whitespace_column_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.txt"
            source.write_text("1 1 0 -1\n0 2 1 1\n", encoding="utf-8")
            input_spec = {
                "format": "generic_delimited",
                "time_unit": "us",
                "polarity_encoding": "minus_plus_one",
                "delimiter": "whitespace",
                "header": False,
                "comment_prefix": None,
                "columns": {"timestamp": 0, "x": 1, "y": 2, "polarity": 3},
            }
            spec = root / "spec.json"
            write_spec(spec, source, input=input_spec)
            import_dataset(source, spec, root / "events.jsonl", root / "receipt.json")
            events = read_jsonl(root / "events.jsonl")
        self.assertEqual([event["logical_source"] for event in events], [6, 1])
        self.assertEqual([event["occurrence_cycle"] for event in events], [0, 1])

    def test_output_is_accepted_by_existing_logical_aer_trace_preparer(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "events.jsonl"
            receipt_path = root / "receipt.json"
            receipt = import_dataset(source, spec, trace, receipt_path)
            run_manifest = {
                "schema_version": 1,
                "run": {
                    "name": "dataset_fixture",
                    "geometry": {"width": 3, "height": 2},
                    "stim_cycles": 3,
                    "load": "1",
                    "seed": 0,
                    "sink": {"mode": "always"},
                },
                "report_group": "dataset_fixture",
                "trace_file": trace.name,
                "trace_sha256": receipt["trace"]["sha256"],
                "event_count": 3,
                "event_identity_mode": "address_only",
            }
            manifest = root / "run.manifest.json"
            manifest.write_text(json.dumps(run_manifest), encoding="utf-8")
            result = prepare_trace(trace, manifest, root / "events.numeric", addr_width=3)
            numeric_lines = (root / "events.numeric").read_text(encoding="ascii").splitlines()
        self.assertEqual(result["event_count"], 3)
        self.assertEqual(numeric_lines[1:], ["0 0 3 3 4", "0 1 4 4 4", "1 2 5 5 5"])

    def test_source_hash_is_mandatory_and_checked_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text('{"timestamp":0,"x":0,"y":0,"polarity":1}\n', encoding="utf-8")
            spec = root / "spec.json"
            write_spec(spec, source, source={"sha256": "0" * 64})
            with self.assertRaisesRegex(ImportFailure, "SHA-256 mismatch"):
                import_dataset(source, spec, root / "events.out.jsonl", root / "receipt.json")
            self.assertFalse((root / "events.out.jsonl").exists())
            self.assertFalse((root / "receipt.json").exists())

    def test_reject_policy_does_not_emit_partial_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(
                '{"timestamp":0,"x":0,"y":0,"polarity":1}\n'
                '{"timestamp":1,"x":4,"y":0,"polarity":-1}\n',
                encoding="utf-8",
            )
            spec = root / "spec.json"
            write_spec(spec, source)
            with self.assertRaisesRegex(ImportFailure, "bounds_policy=reject"):
                import_dataset(source, spec, root / "events.out.jsonl", root / "receipt.json")
            self.assertFalse((root / "events.out.jsonl").exists())

    def test_malformed_record_rejects_whole_import_instead_of_dropping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(
                '{"timestamp":0,"x":0,"y":0,"polarity":1}\n'
                '{"timestamp":1,"x":0,"y":0}\n',
                encoding="utf-8",
            )
            spec = root / "spec.json"
            write_spec(spec, source)
            with self.assertRaisesRegex(ImportFailure, "missing polarity"):
                import_dataset(source, spec, root / "events.out.jsonl", root / "receipt.json")
            self.assertFalse((root / "events.out.jsonl").exists())

    def test_canonical_json_float_timestamp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text('{"timestamp":0.1,"x":0,"y":0,"polarity":1}\n', encoding="utf-8")
            spec = root / "spec.json"
            write_spec(spec, source)
            with self.assertRaisesRegex(ImportFailure, "not a float"):
                import_dataset(source, spec, root / "events.out.jsonl", root / "receipt.json")

    def test_samsung_identifier_produces_hold_receipt_without_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unknown.dat"
            source.write_bytes(b"unpublished-format\n")
            samsung_input = {"format": "samsung_official"}
            spec = root / "spec.json"
            write_spec(spec, source, input=samsung_input)
            output = root / "events.jsonl"
            receipt_path = root / "receipt.json"
            with self.assertRaisesRegex(ImportHold, "HOLD"):
                import_dataset(source, spec, output, receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "HOLD")
            self.assertIn("actual specification", receipt["reason"])
            self.assertEqual(receipt["source"]["sha256"], digest(source))
            self.assertIsNone(receipt["trace"])
            self.assertFalse(output.exists())

    def test_cli_return_codes_distinguish_pass_failure_and_hold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "events.jsonl"
            receipt = root / "receipt.json"
            args = [
                "--source", str(FIXTURES / "canonical_events.jsonl"),
                "--spec", str(FIXTURES / "canonical_import.json"),
                "--output", str(output), "--receipt", str(receipt),
            ]
            self.assertEqual(main(args), 0)
            bad = json.loads((FIXTURES / "canonical_import.json").read_text(encoding="utf-8"))
            bad["source"]["sha256"] = "f" * 64
            bad_spec = root / "bad.json"
            bad_spec.write_text(json.dumps(bad), encoding="utf-8")
            bad_args = list(args)
            bad_args[3] = str(bad_spec)
            self.assertEqual(main(bad_args), 2)

    def test_schema_and_fixture_hashes_are_valid(self) -> None:
        schema = json.loads(
            (ROOT / "benchmarks" / "redred_event_dataset" / "import_spec.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema"]["const"], SCHEMA)
        receipt_schema = json.loads(
            (ROOT / "benchmarks" / "redred_event_dataset" / "import_receipt.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt_schema["properties"]["schema"]["const"],
            "redred-event-import-receipt-v1",
        )
        for stem in ("generic", "canonical"):
            spec = json.loads((FIXTURES / f"{stem}_import.json").read_text(encoding="utf-8"))
            source_suffix = "csv" if stem == "generic" else "jsonl"
            self.assertEqual(
                spec["source"]["sha256"], digest(FIXTURES / f"{stem}_events.{source_suffix}")
            )


if __name__ == "__main__":
    unittest.main()
