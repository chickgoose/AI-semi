#!/usr/bin/env python3
"""Independently validate a raw RTL run and emit an exact retire receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict, deque
from pathlib import Path


START_NS = 41_321_000_000
PERIOD_NUMERATOR_NS = 13
PERIOD_DENOMINATOR = 2
EXPECTED = 1_100
SOURCE_EPOCH = "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction"
EXPECTED_A23_SHA256 = "7eb025d9ba6de3dcd538311e75b11b55c51439ba9fc8fbf747213af1577053e0"
EXPECTED_POSE_JOIN_EVENTS_SHA256 = "a49b7d813fde313bfbcc27526e337c7268ab11803a19898feee8f27afc576796"
EXPECTED_JOIN_SPEC_SHA256 = "04a81a809164556f744e55b075b94cbc7e2042ccb714e0e03fab8d4aa55a177e"
EXPECTED_SOURCE_RECORDS_SHA256 = "5a2dbab0766c60e78b25a726b63b79b61cf15bd4a0589c1ac4a7f88b51da85cd"
EXPECTED_STIMULUS_SHA256 = "98a2afba1e19a52c9d3456af05e6d051bf679355cef0a9573023bac8f43b1186"
EXPECTED_STIMULUS_MANIFEST_SHA256 = "57a13c8e726a3e083ae6af3a81a828d0976ac3b505cbbf3d99588d235cc42e8d"
REPO_ROOT = Path(__file__).resolve().parents[2]
COMMIT_BOUND_PATHS = (
    "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv",
    "rtl/candidates/mc_wtb_occurrence_baseline/mc_wtb_occurrence_baseline_top.sv",
    "tests/mc_wtb_occurrence_baseline/tb.sv",
    "tests/mc_wtb_occurrence_baseline/prepare.py",
    "tests/mc_wtb_occurrence_baseline/inspect_run.py",
)


class InspectionFailure(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_implementation_commit(implementation_commit: str) -> None:
    if len(implementation_commit) != 40 or any(
        character not in "0123456789abcdef" for character in implementation_commit
    ):
        raise InspectionFailure("implementation commit is not a full SHA-1")
    existence = subprocess.run(
        ["git", "cat-file", "-e", f"{implementation_commit}^{{commit}}"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if existence.returncode != 0:
        raise InspectionFailure("implementation commit does not exist in this repository")
    for relative_path in COMMIT_BOUND_PATHS:
        committed = subprocess.run(
            ["git", "show", f"{implementation_commit}:{relative_path}"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if committed.returncode != 0 or committed.stdout != (REPO_ROOT / relative_path).read_bytes():
            raise InspectionFailure(
                f"current production input differs from implementation commit: {relative_path}"
            )


def read_jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except Exception as error:
        raise InspectionFailure(f"invalid JSONL {path}: {error}") from error


def decode_payload(payload_hex: str) -> dict[str, int]:
    if len(payload_hex) != 26 or any(c not in "0123456789abcdef" for c in payload_hex):
        raise InspectionFailure("payload is not canonical 102-bit lowercase hex")
    value = int(payload_hex, 16)
    widths = (24, 11, 36, 8, 8, 1, 14)
    names = (
        "dataset_event_index",
        "join_sequence_index",
        "occurrence_timestamp_ns",
        "x",
        "y",
        "polarity_01",
        "causal_pose_source_index",
    )
    decoded = {}
    for name, width in zip(names, widths):
        decoded[name] = value & ((1 << width) - 1)
        value >>= width
    if value:
        raise InspectionFailure("payload exceeds 102 bits")
    return decoded


def mapped_ns(cycle: int) -> int:
    if cycle < 0:
        raise InspectionFailure("negative endpoint cycle")
    # Exact ceil(cycle * 13 / 2), bounded to <=0.5 ns quantization error.
    return START_NS + (cycle * PERIOD_NUMERATOR_NS + PERIOD_DENOMINATOR - 1) // PERIOD_DENOMINATOR


def reference_endpoint_schedule(
    source: list[dict],
) -> tuple[list[tuple[int, int, int, str]], list[tuple[int, int, int, str]], list[tuple[int, int, int, str]]]:
    arrivals: dict[int, list[dict]] = defaultdict(list)
    ingress = []
    prior_cycle = None
    lane = 0
    for row in source:
        if row["occurrence_cycle"] != prior_cycle:
            prior_cycle = row["occurrence_cycle"]
            lane = 0
        arrivals[row["occurrence_cycle"]].append(row)
        ingress.append((row["occurrence_cycle"], lane, row["logical_source"], row["payload_hex"]))
        lane += 1

    calendar = (1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2)
    token = 0
    row_pointer = [0, 0, 0, 0]
    queues = {index: deque() for index in range(16)}
    accepts = []
    last_arrival = max(arrivals)
    cycle = 0
    while cycle <= last_arrival or any(queues[index] for index in range(16)):
        active = {index for index in range(16) if queues[index]}
        selected = []
        scan_pointer = list(row_pointer)
        for output_lane in range(2):
            active_rows = {source_index // 4 for source_index in active}
            if not active_rows:
                break
            preferred = calendar[(token + output_lane) % len(calendar)]
            chosen_row = next(
                row for offset in range(4)
                if (row := (preferred + offset) % 4) in active_rows
            )
            chosen_column = None
            for offset in range(4):
                candidate_column = (scan_pointer[chosen_row] + offset) % 4
                candidate_source = chosen_row * 4 + candidate_column
                if candidate_source in active:
                    chosen_column = candidate_column
                    break
            if chosen_column is None:
                raise InspectionFailure("reference scheduler failed to select an active column")
            chosen_source = chosen_row * 4 + chosen_column
            selected.append(chosen_source)
            active.remove(chosen_source)
            scan_pointer[chosen_row] = (chosen_column + 1) % 4
        if selected:
            token = (token + len(selected)) % len(calendar)
            row_pointer = scan_pointer
            for output_lane, source_index in enumerate(selected):
                payload = queues[source_index].popleft()
                accepts.append((cycle, output_lane, source_index, payload))
        for row in arrivals.get(cycle, ()):
            queues[row["logical_source"]].append(row["payload_hex"])
        cycle += 1
        if cycle > last_arrival + 128:
            raise InspectionFailure("reference scheduler did not drain within 128 cycles")
    retires = [(cycle + 1, lane, source_index, payload) for cycle, lane, source_index, payload in accepts]
    return ingress, accepts, retires


def inspect(
    source_records_path: Path,
    stimulus_path: Path,
    stimulus_manifest_path: Path,
    raw_log_path: Path,
    status_path: Path,
    simulator_log_path: Path,
    implementation_commit: str,
    run_id: str,
    require_production_authority: bool = True,
) -> tuple[bytes, bytes, dict]:
    source_raw = source_records_path.read_bytes()
    source = read_jsonl(source_records_path)
    manifest_raw = stimulus_manifest_path.read_bytes()
    stimulus_raw = stimulus_path.read_bytes()
    try:
        manifest = json.loads(manifest_raw)
    except Exception as error:
        raise InspectionFailure(f"invalid stimulus manifest: {error}") from error
    if (
        len(source) != EXPECTED
        or manifest.get("record_count") != EXPECTED
        or manifest.get("source_records_sha256") != digest(source_raw)
        or manifest.get("clock_period_ps") != 6500
        or manifest.get("source_epoch_start_ns") != START_NS
        or manifest.get("admission_cycle_mapping")
        != "ceil((occurrence_timestamp_ns-source_epoch_start_ns)*2/13)"
        or manifest.get("never_admit_before_occurrence") is not True
    ):
        raise InspectionFailure("source/manifest binding differs")
    if require_production_authority:
        if (
            digest(source_raw) != EXPECTED_SOURCE_RECORDS_SHA256
            or digest(stimulus_raw) != EXPECTED_STIMULUS_SHA256
            or digest(manifest_raw) != EXPECTED_STIMULUS_MANIFEST_SHA256
        ):
            raise InspectionFailure("production source/stimulus/manifest bytes differ")
        expected_manifest = {
            "schema": "redred.mc_wtb_occurrence_baseline.stimulus/v1",
            "record_count": 1100,
            "group_count": 642,
            "first_cycle": 0,
            "last_cycle": 153693,
            "max_events_per_cycle": 6,
            "max_same_source_per_cycle": 3,
            "payload_width": 102,
            "clock_period_ps": 6500,
            "source_epoch_start_ns": START_NS,
            "admission_cycle_mapping": "ceil((occurrence_timestamp_ns-source_epoch_start_ns)*2/13)",
            "never_admit_before_occurrence": True,
            "stimulus_sha256": digest(stimulus_raw),
            "source_records_sha256": digest(source_raw),
            "pose_join_events_sha256": EXPECTED_POSE_JOIN_EVENTS_SHA256,
            "a23_archive_sha256": EXPECTED_A23_SHA256,
            "join_spec_sha256": EXPECTED_JOIN_SPEC_SHA256,
        }
        if manifest != expected_manifest:
            raise InspectionFailure("production stimulus authority/pins differ")
        expected_source_keys = {
            "dataset_event_index", "join_sequence_index",
            "occurrence_timestamp_ns", "occurrence_cycle",
            "projection_floor_cycle", "logical_source", "x", "y",
            "polarity_01", "causal_pose_source_index", "payload_hex",
        }
        for ordinal, row in enumerate(source):
            delta_ns = row.get("occurrence_timestamp_ns", -1) - START_NS
            if (
                set(row) != expected_source_keys
                or row.get("dataset_event_index") != 13_856_250 + ordinal
                or row.get("join_sequence_index") != ordinal
                or not (0 <= delta_ns < 1_000_000)
                or row.get("projection_floor_cycle") != (delta_ns * 2) // 13
                or row.get("occurrence_cycle") != (delta_ns * 2 + 12) // 13
                or row.get("logical_source")
                != (row.get("y", -1) * 4 // 180) * 4 + row.get("x", -1) * 4 // 240
                or not (0 <= row.get("x", -1) < 240)
                or not (0 <= row.get("y", -1) < 180)
                or row.get("polarity_01") not in (0, 1)
            ):
                raise InspectionFailure(f"production source record differs at {ordinal}")

    by_payload = {row["payload_hex"]: row for row in source}
    if len(by_payload) != EXPECTED:
        raise InspectionFailure("source payload identities are not unique")
    raw = raw_log_path.read_bytes()
    simulator_log = simulator_log_path.read_bytes()
    if (
        b"MC_WTB_OCCURRENCE_BASELINE_RTL_PASS" not in simulator_log
        or b"Simulation complete via $finish" not in simulator_log
        or b"*F," in simulator_log
        or b"occurrence batch rejected" in simulator_log
    ):
        raise InspectionFailure("simulator transcript is not a clean completed PASS")
    observed: dict[str, list[tuple[int, int, int, str]]] = defaultdict(list)
    prior_raw_cycle = -1
    for line_number, raw_line in enumerate(raw.decode("ascii").splitlines(), 1):
        columns = raw_line.split(",")
        if len(columns) != 5 or columns[0] not in {"INGRESS", "ACCEPT", "RETIRE"}:
            raise InspectionFailure(f"malformed raw row {line_number}")
        kind, cycle_text, lane_text, source_text, payload_hex = columns
        try:
            cycle, lane, logical_source = map(int, (cycle_text, lane_text, source_text))
        except ValueError as error:
            raise InspectionFailure(f"non-integer raw field at row {line_number}") from error
        decoded = decode_payload(payload_hex)
        if cycle < prior_raw_cycle:
            raise InspectionFailure(f"raw cycles are not monotonic at row {line_number}")
        prior_raw_cycle = cycle
        expected = by_payload.get(payload_hex)
        if expected is None or decoded["dataset_event_index"] != expected["dataset_event_index"]:
            raise InspectionFailure(f"unknown/corrupt payload at row {line_number}")
        for field in (
            "join_sequence_index",
            "occurrence_timestamp_ns",
            "x",
            "y",
            "polarity_01",
            "causal_pose_source_index",
        ):
            if decoded[field] != expected[field]:
                raise InspectionFailure(f"payload metadata differs at row {line_number}: {field}")
        if logical_source != expected["logical_source"] or lane not in (0, 1, 2, 3, 4, 5):
            raise InspectionFailure(f"source/lane differs at row {line_number}")
        if kind != "INGRESS" and lane > 1:
            raise InspectionFailure(f"endpoint output lane differs at row {line_number}")
        observed[kind].append((cycle, lane, logical_source, payload_hex))

    if any(len(observed[name]) != EXPECTED for name in ("INGRESS", "ACCEPT", "RETIRE")):
        raise InspectionFailure("generated/accepted/retired count is not 1100/1100/1100")
    expected_ingress_payloads = [row["payload_hex"] for row in source]
    expected_ingress, expected_accepts, expected_retires = reference_endpoint_schedule(source)
    if observed["INGRESS"] != expected_ingress:
        raise InspectionFailure("ingress cycle/lane/source/identity differs from immutable schedule")
    if observed["ACCEPT"] != expected_accepts:
        raise InspectionFailure("accept cycle/lane/source/identity differs from independent A2 model")
    if observed["RETIRE"] != expected_retires:
        raise InspectionFailure("retire cycle/lane/source/identity differs from independent raw-link model")
    if Counter(row[3] for row in observed["ACCEPT"]) != Counter(expected_ingress_payloads):
        raise InspectionFailure("accept identity multiplicity differs")
    if Counter(row[3] for row in observed["RETIRE"]) != Counter(expected_ingress_payloads):
        raise InspectionFailure("retire identity multiplicity differs")

    source_accept_queues = {index: deque() for index in range(16)}
    for row in source:
        source_accept_queues[row["logical_source"]].append(row["payload_hex"])
    for cycle, _lane, logical_source, payload_hex in observed["ACCEPT"]:
        if not source_accept_queues[logical_source] or source_accept_queues[logical_source].popleft() != payload_hex:
            raise InspectionFailure("per-source FIFO acceptance order differs")
        source_row = by_payload[payload_hex]
        delta_ns = source_row["occurrence_timestamp_ns"] - START_NS
        expected_admission_cycle = (delta_ns * 2 + 12) // 13
        if (
            source_row["occurrence_cycle"] != expected_admission_cycle
            or source_row["occurrence_cycle"] * 13 < delta_ns * 2
        ):
            raise InspectionFailure("causal admission edge precedes source occurrence")
        if cycle <= source_row["occurrence_cycle"]:
            raise InspectionFailure("acceptance did not occur after admission edge")
    if any(source_accept_queues[index] for index in range(16)):
        raise InspectionFailure("per-source acceptance queue did not drain")

    accept_sequence = [row[3] for row in observed["ACCEPT"]]
    retire_sequence = [row[3] for row in observed["RETIRE"]]
    if retire_sequence != accept_sequence:
        raise InspectionFailure("raw link changed acceptance order")
    accept_cycle = {row[3]: row[0] for row in observed["ACCEPT"]}
    retire_cycle = {row[3]: row[0] for row in observed["RETIRE"]}
    if any(retire_cycle[payload] != accept_cycle[payload] + 1 for payload in accept_sequence):
        raise InspectionFailure("raw link latency is not exactly one cycle")

    status = status_path.read_text(encoding="ascii").strip()
    status_match = re.fullmatch(
        r"PASS ingress=1100 accepted=1100 retired=1100 last_cycle=(\d+) "
        r"overflow=0 protocol_error=0",
        status,
    )
    if status_match is None:
        raise InspectionFailure("server status is not an exact clean 1100/1100/1100 PASS")
    if int(status_match.group(1)) != max(retire_cycle.values()) + 1:
        raise InspectionFailure("server final cycle is not bound to the raw retirement tail")
    if require_production_authority:
        validate_implementation_commit(implementation_commit)
    elif len(implementation_commit) != 40 or any(
        character not in "0123456789abcdef" for character in implementation_commit
    ):
        raise InspectionFailure("implementation commit is not a full SHA-1")

    mapping = {
        "schema": "redred.mc_wtb_occurrence_baseline.timebase/v1",
        "anchor_cycle": 0,
        "anchor_source_timestamp_ns": START_NS,
        "clock_period_numerator_ns": PERIOD_NUMERATOR_NS,
        "clock_period_denominator": PERIOD_DENOMINATOR,
        "rounding": "ceil_to_integer_ns",
        "maximum_quantization_error_ns_inclusive": 0.5,
        "retire_cycle_min": min(retire_cycle.values()),
        "retire_cycle_max": max(retire_cycle.values()),
        "latency_cycles": 1,
        "raw_log_sha256": digest(raw),
        "simulator_log_sha256": digest(simulator_log),
        "validated": require_production_authority,
    }
    mapping_raw = canonical(mapping)
    ordered_id_hash = digest(b"".join(f"{row['dataset_event_index']}\n".encode("ascii") for row in source))
    header = {
        "schema": "redred.uzh_mc_wtb_controls.retire_stream/v1",
        "record_type": "header",
        "provenance_class": (
            "OBSERVED_ENDPOINT_RUN" if require_production_authority
            else "SYNTHETIC_TEST_FIXTURE"
        ),
        "producer": {
            "implementation_id": "mc_wtb_occurrence_baseline_xcelium_observer_v1",
            "implementation_commit": implementation_commit,
            "config_sha256": digest(manifest_raw),
            "run_id": run_id,
            "raw_run_artifact_sha256": digest(raw),
        },
        "source_timebase": {"unit": "ns", "epoch": SOURCE_EPOCH},
        "retire_clock": {
            "clock_domain": "mc_wtb_occurrence_baseline.clk_i",
            "unit": "cycle",
            "epoch": "first_active_posedge_after_reset_release",
        },
        "mapping_to_source_timebase": {
            "method": "anchor_ns_plus_ceil(retire_cycle*13/2)",
            "evidence_sha256": digest(mapping_raw),
            "validated": require_production_authority,
        },
        "record_count": EXPECTED,
        "ordered_dataset_event_index_sha256": ordered_id_hash,
    }
    receipt_rows = [header]
    retire_by_payload = {row[3]: row[0] for row in observed["RETIRE"]}
    for row in source:
        retire_ns = mapped_ns(retire_by_payload[row["payload_hex"]])
        if retire_ns < row["occurrence_timestamp_ns"]:
            raise InspectionFailure("mapped retirement precedes occurrence")
        receipt_rows.append(
            {
                "schema": "redred.uzh_mc_wtb_controls.retire_record/v1",
                "record_type": "retire",
                "dataset_event_index": row["dataset_event_index"],
                "join_sequence_index": row["join_sequence_index"],
                "occurrence_timestamp_ns": row["occurrence_timestamp_ns"],
                "accepted_count": 1,
                "retired_count": 1,
                "retire_timestamp_ns": retire_ns,
            }
        )
    receipt_raw = b"".join(canonical(row) for row in receipt_rows)
    summary = {
        "status": (
            "PASS_MC_WTB_OCCURRENCE_BASELINE_OBSERVED_RETIRE_SCOPED"
            if require_production_authority
            else "PASS_MC_WTB_OCCURRENCE_BASELINE_SYNTHETIC_INSPECTOR_FIXTURE"
        ),
        "generated": EXPECTED,
        "accepted": EXPECTED,
        "retired": EXPECTED,
        "missing": 0,
        "duplicate": 0,
        "source_overrun": 0,
        "reordered_by_arbiter": sum(
            retire_sequence[index] != expected_ingress_payloads[index] for index in range(EXPECTED)
        ),
        "raw_log_sha256": digest(raw),
        "status_sha256": digest(status_path.read_bytes()),
        "simulator_log_sha256": digest(simulator_log),
        "mapping_sha256": digest(mapping_raw),
        "receipt_sha256": digest(receipt_raw),
        "claim_scope": {
            "exact_fixed_cohort_occurrence_retire": require_production_authority,
            "codec_or_compression": False,
            "wire_width_benefit": False,
            "motion_benefit": False,
            "ppa": False,
            "phase5_innovation_started": False,
        },
    }
    return receipt_raw, mapping_raw, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-records", type=Path, required=True)
    parser.add_argument("--stimulus", type=Path, required=True)
    parser.add_argument("--stimulus-manifest", type=Path, required=True)
    parser.add_argument("--raw-log", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--simulator-log", type=Path, required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt, mapping, summary = inspect(
        args.source_records,
        args.stimulus,
        args.stimulus_manifest,
        args.raw_log,
        args.status,
        args.simulator_log,
        args.implementation_commit,
        args.run_id,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "retire_receipt.jsonl").write_bytes(receipt)
    (args.output_dir / "timebase_mapping.json").write_bytes(mapping)
    (args.output_dir / "inspection.json").write_bytes(canonical(summary))
    print(summary["status"], json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
