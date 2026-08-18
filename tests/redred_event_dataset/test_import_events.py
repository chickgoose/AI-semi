from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.clean_slate_aer.prepare_sv_trace import prepare_trace
from benchmarks.redred_event_dataset import import_events as importer


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "benchmarks" / "redred_event_dataset" / "fixtures"
SPEC_SCHEMA = "redred-event-import-v2"
LICENSE_SHA256 = "ed0743c87f04776de0f233a3f4b5f07862ed163b0c8f3b6b0e382f41f5995ec0"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def dataset_metadata() -> dict[str, object]:
    return {
        "provider": "REDRED tests",
        "dataset": "synthetic-events",
        "release": "adversarial",
        "version": "1",
        "original_artifact": "events.jsonl",
        "provenance": {"uri": "urn:redred:test:synthetic-events-v1"},
        "license": {
            "spdx_id": "CC0-1.0",
            "text_sha256": LICENSE_SHA256,
            "redistribution": "permitted",
        },
    }


def make_spec(source: Path, **overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "schema": SPEC_SCHEMA,
        "dataset": dataset_metadata(),
        "source": {"raw_sha256": digest(source)},
        "sensor": {"width": 4, "height": 3},
        "address_width": 4,
        "input": {
            "format": "canonical_jsonl",
            "time_unit": "ns",
            "polarity_encoding": "minus_plus_one",
        },
        "cycle_mapping": {
            "period_ns": "1",
            "origin": "first_event",
            "deadline_slack_cycles": 2,
        },
        "bounds_policy": "reject",
    }
    spec.update(overrides)
    return spec


def write_spec(path: Path, source: Path, **overrides: object) -> dict[str, object]:
    spec = make_spec(source, **overrides)
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return spec


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def rewrite_receipt_and_completion(result: Path, receipt: dict[str, object]) -> None:
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("ascii")
    (result / importer.RECEIPT_NAME).write_bytes(receipt_bytes)
    completion = read_json(result / importer.COMPLETION_NAME)
    completion["receipt_sha256"] = digest_bytes(receipt_bytes)
    (result / importer.COMPLETION_NAME).write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )


class ImportEventsTest(unittest.TestCase):
    def test_generic_fixture_is_lossless_stable_and_counts_clipping(self) -> None:
        source = FIXTURES / "generic_events.csv"
        spec = FIXTURES / "generic_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            receipt = importer.import_dataset(source, spec, first)
            receipt_again = importer.import_dataset(source, spec, second)
            events = read_jsonl(first / importer.TRACE_NAME)
            self.assertEqual(digest(first / importer.TRACE_NAME), digest(second / importer.TRACE_NAME))
            self.assertEqual(receipt["trace"]["raw_sha256"], receipt_again["trace"]["raw_sha256"])
            self.assertEqual([event["occurrence_cycle"] for event in events], [0, 0, 1, 1])
            self.assertEqual([event["logical_source"] for event in events], [0, 0, 1, 3])
            self.assertEqual([event["polarity"] for event in events], [-1, 1, 1, 1])
            self.assertEqual((events[-1]["x"], events[-1]["y"]), (3, 0))
            counts = receipt["counts"]
            self.assertEqual(counts["input_event_records"], 4)
            self.assertEqual(counts["events_emitted"], 4)
            self.assertEqual(counts["events_dropped"], 0)
            self.assertEqual(counts["timestamp_tied_events"], 1)
            self.assertEqual(counts["same_source_cycle_retriggers"], 1)
            self.assertEqual(counts["out_of_range_events"], 1)
            self.assertEqual(counts["clipped_coordinates"], 2)
            self.assertEqual(receipt["source"]["raw_sha256"], digest(source))
            self.assertRegex(receipt["source"]["semantic_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(importer.qualify_result_dir(first), receipt)

    def test_exact_fraction_boundaries_beyond_decimal_context_precision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            timestamps = [
                "0",
                "0.99999999999999999999999999999",
                "1.00000000000000000000000000000",
                "1.00000000000000000000000000001",
            ]
            source.write_text(
                "".join(
                    json.dumps({"timestamp": value, "x": index, "y": 0, "polarity": 1}) + "\n"
                    for index, value in enumerate(timestamps)
                ),
                encoding="utf-8",
            )
            spec = root / "spec.json"
            write_spec(spec, source)
            result = root / "result"
            receipt = importer.import_dataset(source, spec, result)
            events = read_jsonl(result / importer.TRACE_NAME)
            self.assertEqual([event["occurrence_cycle"] for event in events], [0, 0, 1, 1])
            self.assertEqual(receipt["cycle_mapping"]["period_ns"], "1")

    def test_noncanonical_exact_decimal_lexemes_fail_without_package(self) -> None:
        invalid = [" 1", "1 ", "1_0", "+.5", ".5", "+1", "01", "1.", "1e01"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, value in enumerate(invalid):
                source = root / f"events-{index}.jsonl"
                source.write_text(
                    json.dumps({"timestamp": value, "x": 0, "y": 0, "polarity": 1}) + "\n",
                    encoding="utf-8",
                )
                spec = root / f"spec-{index}.json"
                write_spec(spec, source)
                result = root / f"result-{index}"
                with self.assertRaisesRegex(importer.ImportFailure, "exact-decimal grammar"):
                    importer.import_dataset(source, spec, result)
                self.assertFalse(result.exists())

    def test_duplicate_spec_and_event_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text('{"timestamp":"0","x":0,"x":1,"y":0,"polarity":1}\n', encoding="utf-8")
            spec = root / "spec.json"
            write_spec(spec, source)
            with self.assertRaisesRegex(importer.ImportFailure, "duplicate JSON key 'x'"):
                importer.import_dataset(source, spec, root / "event-result")
            clean_source = root / "clean.jsonl"
            clean_source.write_text('{"timestamp":"0","x":0,"y":0,"polarity":1}\n', encoding="utf-8")
            clean = json.dumps(make_spec(clean_source))
            duplicate = clean.replace(
                '"schema": "redred-event-import-v2"',
                '"schema": "redred-event-import-v2", "schema": "redred-event-import-v2"',
                1,
            )
            duplicate_spec = root / "duplicate-spec.json"
            duplicate_spec.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportFailure, "duplicate JSON key 'schema'"):
                importer.import_dataset(clean_source, duplicate_spec, root / "spec-result")

    def test_stable_read_detects_source_mutation_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.dat"
            source.write_bytes(b"before")

            def mutate() -> None:
                source.write_bytes(b"after-with-different-size")

            with self.assertRaisesRegex(importer.ImportFailure, "changed during stable read"):
                importer._stable_read(source, _after_read_hook=mutate)

    def test_preexisting_result_directory_or_file_is_never_overwritten(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_dir = root / "existing-dir"
            existing_dir.mkdir()
            marker = existing_dir / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportFailure, "already exists"):
                importer.import_dataset(source, spec, existing_dir)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            existing_file = root / "existing-file"
            existing_file.write_text("keep-file", encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportFailure, "already exists"):
                importer.import_dataset(source, spec, existing_file)
            self.assertEqual(existing_file.read_text(encoding="utf-8"), "keep-file")

    def test_receipt_or_completion_failure_leaves_unqualified_orphan(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            for failed_name in (importer.RECEIPT_NAME, importer.COMPLETION_NAME):
                result = Path(directory) / f"orphan-{failed_name}"
                original = importer._write_exclusive

                def fail_artifact(path: Path, data: bytes, target: str = failed_name) -> None:
                    if path.name == target:
                        raise importer.ImportFailure(f"injected {target} failure")
                    original(path, data)

                with mock.patch.object(importer, "_write_exclusive", side_effect=fail_artifact):
                    with self.assertRaisesRegex(importer.ImportFailure, "injected"):
                        importer.import_dataset(source, spec, result)
                self.assertTrue((result / importer.TRACE_NAME).exists())
                self.assertFalse((result / importer.COMPLETION_NAME).exists())
                with self.assertRaisesRegex(importer.ImportFailure, "completion sentinel is absent"):
                    importer.qualify_result_dir(result)

    def test_samsung_hold_has_exclusive_shape_and_cannot_mix_with_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unknown.dat"
            source.write_bytes(b"unpublished-format\n")
            spec = root / "samsung.json"
            spec.write_text(
                json.dumps({
                    "schema": SPEC_SCHEMA,
                    "source": {"raw_sha256": digest(source)},
                    "input": {"format": "samsung_official"},
                }),
                encoding="utf-8",
            )
            result = root / "hold"
            with self.assertRaisesRegex(importer.ImportHold, "HOLD"):
                importer.import_dataset(source, spec, result)
            self.assertEqual(
                {path.name for path in result.iterdir()},
                {importer.RECEIPT_NAME, importer.COMPLETION_NAME},
            )
            receipt = importer.qualify_result_dir(result)
            self.assertEqual(receipt["status"], "HOLD")
            self.assertNotIn("dataset", receipt)
            stale = root / "stale"
            stale.mkdir()
            stale_trace = stale / importer.TRACE_NAME
            stale_trace.write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportFailure, "already exists"):
                importer.import_dataset(source, spec, stale)
            self.assertEqual(stale_trace.read_text(encoding="utf-8"), "stale")

    def test_invalid_license_and_provenance_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text('{"timestamp":"0","x":0,"y":0,"polarity":1}\n', encoding="utf-8")
            cases: list[tuple[str, Callable[[dict[str, object]], None]]] = []

            def bad_license(spec: dict[str, object]) -> None:
                spec["dataset"]["license"]["spdx_id"] = "bad license!"

            def bad_uri(spec: dict[str, object]) -> None:
                spec["dataset"]["provenance"] = {"uri": "relative/path"}

            def both_ids(spec: dict[str, object]) -> None:
                spec["dataset"]["provenance"] = {"uri": "urn:test:x", "acquisition_id": "x"}

            cases.extend([("SPDX", bad_license), ("absolute URI", bad_uri), ("exactly one", both_ids)])
            for index, (message, mutate) in enumerate(cases):
                spec_value = make_spec(source)
                mutate(spec_value)
                spec = root / f"bad-{index}.json"
                spec.write_text(json.dumps(spec_value), encoding="utf-8")
                with self.assertRaisesRegex(importer.ImportFailure, message):
                    importer.import_dataset(source, spec, root / f"result-{index}")

    def test_incompatible_geometry_and_address_width_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text('{"timestamp":"0","x":0,"y":0,"polarity":1}\n', encoding="utf-8")
            spec = root / "spec.json"
            write_spec(spec, source, sensor={"width": 5, "height": 4}, address_width=4)
            with self.assertRaisesRegex(importer.ImportFailure, "cannot be represented"):
                importer.import_dataset(source, spec, root / "result")

    def test_qualifier_rejects_schema_contradiction_even_with_rehashed_sentinel(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            importer.import_dataset(source, spec, result)
            receipt = read_json(result / importer.RECEIPT_NAME)
            receipt["counts"]["events_dropped"] = 1
            rewrite_receipt_and_completion(result, receipt)
            with self.assertRaisesRegex(importer.ImportFailure, "zero-drop import conservation"):
                importer.qualify_result_dir(result)

    def test_qualifier_cross_checks_hash_event_count_and_pass_shape(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case in ("hash", "event_count", "shape"):
                result = root / case
                importer.import_dataset(source, spec, result)
                if case == "hash":
                    completion = read_json(result / importer.COMPLETION_NAME)
                    completion["trace_sha256"] = "0" * 64
                    (result / importer.COMPLETION_NAME).write_text(
                        json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="ascii"
                    )
                    expected = "completion/trace SHA-256 mismatch"
                else:
                    receipt = read_json(result / importer.RECEIPT_NAME)
                    if case == "event_count":
                        receipt["trace"]["event_count"] = 4
                        expected = "event_count/counters/cardinality"
                    else:
                        receipt["reason"] = "PASS cannot contain a HOLD reason"
                        expected = "PASS receipt.*unexpected reason"
                    rewrite_receipt_and_completion(result, receipt)
                with self.assertRaisesRegex(importer.ImportFailure, expected):
                    importer.qualify_result_dir(result)

    def test_output_is_accepted_by_existing_preparer_at_declared_address_width(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_dir = root / "result"
            receipt = importer.import_dataset(source, spec, result_dir)
            trace = result_dir / importer.TRACE_NAME
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
                "trace_sha256": receipt["trace"]["raw_sha256"],
                "event_count": 3,
                "event_identity_mode": "address_only",
            }
            manifest = root / "run.manifest.json"
            manifest.write_text(json.dumps(run_manifest), encoding="utf-8")
            numeric = root / "events.numeric"
            prepared = prepare_trace(
                trace, manifest, numeric,
                addr_width=receipt["input_contract"]["address_width"],
            )
            lines = numeric.read_text(encoding="ascii").splitlines()
            self.assertEqual(prepared["event_count"], 3)
            self.assertEqual(lines[1:], ["0 0 3 3 4", "0 1 4 4 4", "1 2 5 5 5"])

    def test_raw_and_semantic_hashes_and_strict_unknown_keys(self) -> None:
        source = FIXTURES / "canonical_events.jsonl"
        spec = FIXTURES / "canonical_import.json"
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            receipt = importer.import_dataset(source, spec, result)
            self.assertEqual(receipt["source"]["raw_sha256"], digest(source))
            self.assertEqual(receipt["specification"]["raw_sha256"], digest(spec))
            semantic_spec = json.loads(spec.read_text(encoding="utf-8"))
            semantic_spec["cycle_mapping"]["period_ns"] = "1000"
            semantic = importer._canonical_sha256(semantic_spec)
            self.assertEqual(receipt["specification"]["semantic_sha256"], semantic)
            equivalent = json.loads(spec.read_text(encoding="utf-8"))
            equivalent["cycle_mapping"]["period_ns"] = "1.000e+3"
            equivalent_path = Path(directory) / "equivalent.json"
            equivalent_path.write_text(json.dumps(equivalent), encoding="utf-8")
            equivalent_artifact = importer._stable_read(equivalent_path)
            _, equivalent_semantic = importer._load_spec(equivalent_artifact)
            self.assertEqual(equivalent_semantic, semantic)
            bad = make_spec(source)
            bad["unexpected"] = True
            bad_spec = Path(directory) / "bad.json"
            bad_spec.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportFailure, "unexpected unexpected"):
                importer.import_dataset(source, bad_spec, Path(directory) / "bad-result")

    def test_cli_uses_exclusive_result_directory_and_qualifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "cli-result"
            args = [
                "--source", str(FIXTURES / "canonical_events.jsonl"),
                "--spec", str(FIXTURES / "canonical_import.json"),
                "--result-dir", str(result),
            ]
            self.assertEqual(importer.main(args), 0)
            self.assertEqual(importer.main(["--result-dir", str(result), "--qualify"]), 0)
            self.assertEqual(importer.main(args), 2)

    def test_schemas_and_fixture_provenance_are_consistent(self) -> None:
        spec_schema = read_json(ROOT / "benchmarks" / "redred_event_dataset" / "import_spec.schema.json")
        receipt_schema = read_json(ROOT / "benchmarks" / "redred_event_dataset" / "import_receipt.schema.json")
        completion_schema = read_json(ROOT / "benchmarks" / "redred_event_dataset" / "completion.schema.json")
        self.assertEqual(spec_schema["$defs"]["supported"]["properties"]["schema"]["const"], SPEC_SCHEMA)
        self.assertEqual(receipt_schema["$defs"]["pass"]["properties"]["schema"]["const"], importer.RECEIPT_SCHEMA)
        self.assertEqual(completion_schema["properties"]["schema"]["const"], importer.COMPLETION_SCHEMA)
        exact_pattern = re.compile(spec_schema["$defs"]["exactDecimal"]["pattern"])
        for value in ("0", "-1", "1.25", "1e+3"):
            self.assertIsNotNone(exact_pattern.fullmatch(value))
        for value in (" 1", "1 ", "1_0", "+.5", ".5", "+1", "01", "1.", "1e01"):
            self.assertIsNone(exact_pattern.fullmatch(value))
        self.assertEqual(digest(FIXTURES / "LICENSE.txt"), LICENSE_SHA256)
        for stem, suffix in (("generic", "csv"), ("canonical", "jsonl")):
            spec = read_json(FIXTURES / f"{stem}_import.json")
            self.assertEqual(spec["source"]["raw_sha256"], digest(FIXTURES / f"{stem}_events.{suffix}"))
            self.assertEqual(spec["address_width"], 4)
            self.assertEqual(spec["dataset"]["license"]["text_sha256"], LICENSE_SHA256)


if __name__ == "__main__":
    unittest.main()
