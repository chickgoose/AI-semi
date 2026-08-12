#!/usr/bin/env python3
"""Read-only verifier for historical W7 attempt 0FfaT8kp.

This tool never creates an official receipt.  A successful audit remains HOLD
because the archived binding provenance explicitly contains a workspace diff.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.a4_w7_fovea_cluster2_compare.run_w7_compare import (  # noqa: E402
    CAPACITY_MANIFEST_SHA,
    FULL_MANIFEST_SHA,
    OFFICIAL_SHA,
    Candidate,
    W7Error,
    load_json,
    load_official,
    scan_xcelium,
    sha256,
    validate_generation,
    validate_outputs,
)


ATTEMPT_ID = "eval-fovea-cluster2.0FfaT8kp"
ARTIFACT_COUNT = 338
SNAPSHOT_HEAD = "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be"
SNAPSHOT_ARCHIVE_SHA = "3a970fd551e9ec7e2cc645e559889e30eaac4d9ec64152631e2e336e2c9664c3"
RESULT_ARCHIVE_SHA = "0600293426d41441cb597f8b43ff635df6251dcb0c8289e0e258b7c49d633b96"
WORKSPACE_DIFF_VALUE = "workspace-diff"
CANDIDATE_NAMES = {
    "fovea": "ganghee-native-coordinate-source-projection",
    "cluster2": "ganghee-cluster2-row-bitmap",
}
ANALYSIS_FILES = {
    "pairwise_contention_identity": "pairwise_contention_identity.pairs.json",
    "pairwise_contention_affine": "pairwise_contention_affine.pairs.json",
    "phase_transition_s3501": "phase_transition_s3501.phase.json",
    "phase_transition_s3502": "phase_transition_s3502.phase.json",
    "timing_pair_s3901": "timing_pair_s3901.timing.json",
    "timing_pair_s3902": "timing_pair_s3902.timing.json",
    "mixed_phase_always_ready_identity": "mixed_phase_always_ready_identity.mixed.json",
    "mixed_phase_always_ready_bit_reverse": "mixed_phase_always_ready_bit_reverse.mixed.json",
}
TOP_RESULT_FILES = {
    "elaborate.log", "elaborate.history",
    "full50-nonmixed48.aggregate.json", "full50-nonmixed48.event-runs.csv",
    "capacity22-nonmixed20.aggregate.json", "capacity22-nonmixed20.event-runs.csv",
    "pairwise-identity-vs-affine.json", "pairwise-cross-map.status",
}
MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  (/.+)$")


def parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        if not raw or raw.startswith("sh: warning:") or raw.startswith("TOOL:\t"):
            continue
        if "=" not in raw:
            raise W7Error(f"{path.name}:{number}: malformed provenance line")
        key, value = raw.split("=", 1)
        if not key or key in values:
            raise W7Error(f"{path.name}:{number}: empty/duplicate provenance key")
        values[key] = value
    return values


def validate_provenance(root: Path) -> dict[str, str]:
    values = parse_key_values(root / "provenance.txt")
    required = {
        "snapshot_head": SNAPSHOT_HEAD,
        "binding_reset_quiet_arming_patch": WORKSPACE_DIFF_VALUE,
        "snapshot_archive_sha256": SNAPSHOT_ARCHIVE_SHA,
        "canonical_rtl_date_kst": "2026-08-09",
        "attempt": f"/tmp/aer-eval-47e1f2f/{ATTEMPT_ID}",
        "hostname": "snu.polaris.09",
        "start_utc": "2026-08-12T03:48:15Z",
        "finish_utc": "2026-08-12T03:49:51Z",
    }
    for key, expected in required.items():
        if values.get(key) != expected:
            raise W7Error(f"legacy provenance mismatch: {key}")
    return values


def expected_artifacts(full50: Sequence[str]) -> set[str]:
    expected: set[str] = set()
    for candidate in CANDIDATE_NAMES:
        base = f"results/{candidate}"
        expected.update(f"{base}/{name}" for name in TOP_RESULT_FILES)
        expected.update(f"{base}/reset/basic_reset_drain.{suffix}"
                        for suffix in ("csv", "events.csv", "log"))
        for stem in full50:
            expected.update(f"{base}/runs/{stem}/trace.{suffix}"
                            for suffix in ("csv", "events.csv", "log"))
        expected.update(f"{base}/runs/{stem}/{report}"
                        for stem, report in ANALYSIS_FILES.items())
    if len(expected) != ARTIFACT_COUNT:
        raise AssertionError(f"internal artifact contract is {len(expected)}, not 338")
    return expected


def validate_artifact_manifest(root: Path, full50: Sequence[str]) -> dict[str, str]:
    path = root / "result-artifacts.sha256"
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) != ARTIFACT_COUNT:
        raise W7Error(f"artifact manifest cardinality is {len(lines)}, expected 338")
    found: dict[str, str] = {}
    marker = f"/{ATTEMPT_ID}/"
    for number, line in enumerate(lines, 1):
        match = MANIFEST_LINE.fullmatch(line)
        if not match or marker not in match.group(2):
            raise W7Error(f"artifact manifest line {number} is malformed/unrelocatable")
        digest, archived = match.groups()
        relative = archived.split(marker, 1)[1]
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in found:
            raise W7Error(f"artifact manifest line {number} escapes or duplicates")
        found[relative] = digest
    expected = expected_artifacts(full50)
    if set(found) != expected:
        missing, extra = sorted(expected - set(found)), sorted(set(found) - expected)
        raise W7Error(f"artifact set mismatch missing={missing[:3]} extra={extra[:3]}")
    resolved_root = root.resolve()
    for relative, digest in found.items():
        artifact = root / relative
        if artifact.is_symlink() or not artifact.is_file():
            raise W7Error(f"manifest artifact is not a regular file: {relative}")
        try:
            artifact.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise W7Error(f"manifest artifact escapes attempt root: {relative}") from exc
        if sha256(artifact) != digest:
            raise W7Error(f"manifest digest mismatch: {relative}")
    return found


def read_stems(path: Path, expected: Sequence[str], label: str) -> list[str]:
    stems = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if stems != list(expected) or len(stems) != len(set(stems)):
        raise W7Error(f"{label} exact order/cardinality mismatch")
    return stems


def validate_capacity_generation(root: Path, official: Any) -> None:
    trace_root = root / "traces-capacity22"
    index = load_json(trace_root / "generation-index.json")
    rows = index.get("runs")
    if index.get("schema_version") != 1 or index.get("generator_version") != "4.0" or not isinstance(rows, list):
        raise W7Error("capacity generation-index version mismatch")
    names = [row.get("run", {}).get("name") for row in rows]
    if names != list(official.CAPACITY22) or len(names) != 22 or len(names) != len(set(names)):
        raise W7Error("capacity generation exact order/cardinality mismatch")
    for row in rows:
        name = row["run"]["name"]
        trace = trace_root / row["trace_file"]
        if (row.get("trace_sha256") != official.TRACE_SHA256[name] or
                row.get("event_identity_mode") != "address_only" or
                row.get("dut_address_fields") != ["logical_source"] or
                row.get("dut_payload_fields") != [] or sha256(trace) != official.TRACE_SHA256[name] or
                load_json(trace_root / f"{name}.manifest.json") != row):
            raise W7Error(f"{name}: capacity trace/address-only provenance mismatch")


def validate_reset(candidate_root: Path, candidate: str) -> dict[str, str]:
    base = candidate_root / "reset/basic_reset_drain"
    log, summary, events = base.with_suffix(".log"), base.with_suffix(".csv"), Path(str(base) + ".events.csv")
    scan_xcelium(log, "AER_CLEAN_TEST_PASS basic_reset_drain")
    text = log.read_text(encoding="utf-8", errors="replace")
    marker = "AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16"
    if text.splitlines().count(marker) != 1:
        raise W7Error(f"{candidate}: reset drain marker missing/duplicate")
    with summary.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    with events.open(newline="", encoding="utf-8") as stream:
        event_rows = list(csv.DictReader(stream))
    if (len(rows) != 1 or rows[0].get("candidate") != CANDIDATE_NAMES[candidate] or
            rows[0].get("test") != "basic_reset_drain" or int(rows[0].get("errors", -1)) != 0 or
            tuple(int(rows[0].get(key, -1)) for key in ("generated", "accepted", "delivered")) != (16, 16, 16) or
            len(event_rows) != 16 or any(row.get("event_state") != "delivered" for row in event_rows)):
        raise W7Error(f"{candidate}: reset CSV correctness/provenance mismatch")
    return {"log_sha256": sha256(log), "summary_sha256": sha256(summary),
            "events_sha256": sha256(events)}


def validate_candidate_results(root: Path, candidate_key: str,
                               rows: Sequence[dict[str, Any]], capacity: Sequence[str]) -> dict[str, Any]:
    candidate_root = root / "results" / candidate_key
    report_candidate = CANDIDATE_NAMES[candidate_key]
    scan_xcelium(candidate_root / "elaborate.log")
    scan_xcelium(candidate_root / "elaborate.history")
    run_dirs = {path.name for path in (candidate_root / "runs").iterdir() if path.is_dir()}
    full50 = [row["run"]["name"] for row in rows]
    if run_dirs != set(full50) or len(run_dirs) != 50:
        raise W7Error(f"{candidate_key}: run directory set is not exact full50")
    candidate = Candidate(candidate_key, report_candidate, "legacy_import_only", Path("unused"),
                          None, "", "", Path("unused"), 1 if candidate_key == "fovea" else 8)
    totals = {key: 0 for key in ("generated", "source_overrun", "accepted", "delivered",
                                 "measurement_delivered", "measurement_cycles")}
    for metadata in rows:
        stem = metadata["run"]["name"]
        run_root = candidate_root / "runs" / stem
        report_group = metadata.get("report_group", stem)
        scan_xcelium(run_root / "trace.log", f"AER_CLEAN_TEST_PASS {report_group}")
        result = validate_outputs(metadata, candidate, run_root / "trace.csv", run_root / "trace.events.csv")
        for key in totals:
            totals[key] += int(result[key])
    for stem, filename in ANALYSIS_FILES.items():
        doc = load_json(candidate_root / "runs" / stem / filename)
        if doc.get("candidate") != report_candidate:
            raise W7Error(f"{candidate_key}/{stem}: analyzer candidate mismatch")
    cross = load_json(candidate_root / "pairwise-identity-vs-affine.json")
    if cross.get("candidate") != report_candidate or not isinstance(cross.get("rankable"), bool):
        raise W7Error(f"{candidate_key}: cross-map candidate/rankability mismatch")
    status_text = (candidate_root / "pairwise-cross-map.status").read_text(encoding="ascii").strip()
    if status_text not in {"0", "3"} or (status_text == "0") != cross["rankable"]:
        raise W7Error(f"{candidate_key}: cross-map status disagrees with report")
    for view, expected_rows in (("full50-nonmixed48.aggregate.json", 48),
                                ("capacity22-nonmixed20.aggregate.json", 20)):
        doc = load_json(candidate_root / view)
        event_runs = doc.get("event_runs")
        if not isinstance(event_runs, list) or len(event_runs) != expected_rows or any(
                row.get("candidate") != report_candidate for row in event_runs):
            raise W7Error(f"{candidate_key}: {view} candidate provenance mismatch")
        csv_path = candidate_root / view.replace(".aggregate.json", ".event-runs.csv")
        with csv_path.open(newline="", encoding="utf-8") as stream:
            csv_rows = list(csv.DictReader(stream))
        if len(csv_rows) != expected_rows or any(row.get("candidate") != report_candidate for row in csv_rows):
            raise W7Error(f"{candidate_key}: {csv_path.name} cardinality/provenance mismatch")
    return {
        "artifact_count": 169,
        "compile_evidence_count": 1,
        "trace_run_count": 50,
        "capacity22_analysis_only_run_count": len(capacity),
        "reset_run_count": 1,
        "special_analyzer_count": 8,
        "cross_map_rankable": cross["rankable"],
        "reset": validate_reset(candidate_root, candidate_key),
        "full50_totals": totals,
    }


def safe_extract_archive(archive: Path, destination: Path) -> tuple[Path, int, int]:
    if archive.is_symlink() or not archive.is_file():
        raise W7Error(f"archive is not a regular file: {archive}")
    skipped_links = 0
    skipped_supplements = 0
    permitted_supplements = {"run_ganghee_fovea_cluster2_v4_eval.sh", "eval-driver-final.log"}
    seen: set[str] = set()
    with tarfile.open(archive, "r:*") as stream:
        for member in stream:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise W7Error(f"unsafe/unexpected archive member: {member.name}")
            if member.name in seen:
                raise W7Error(f"duplicate archive member: {member.name}")
            seen.add(member.name)
            if len(pure.parts) == 1 and pure.name in permitted_supplements and member.isfile():
                # Original driver/log are context only, not members of the frozen
                # 338-artifact evidence set.  Do not extract or trust them.
                skipped_supplements += 1
                continue
            if pure.parts[0] != ATTEMPT_ID:
                raise W7Error(f"unsafe/unexpected archive member: {member.name}")
            target = destination.joinpath(*pure.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise W7Error(f"cannot read archive member: {member.name}")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
            elif member.issym() or member.islnk():
                # The historical tar has two unmanifested xcelium.d snapshot links.
                # They are neither extracted nor trusted as evidence.
                if "/results/" not in member.name or "/xcelium.d/" not in member.name:
                    raise W7Error(f"link outside ignored Xcelium build area: {member.name}")
                skipped_links += 1
            else:
                raise W7Error(f"unsupported archive member type: {member.name}")
    root = destination / ATTEMPT_ID
    if not root.is_dir():
        raise W7Error("archive attempt root missing")
    if skipped_supplements != len(permitted_supplements):
        raise W7Error("archive supplemental driver/log set mismatch")
    return root, skipped_links, skipped_supplements


@contextmanager
def source_root(*, archive: Path | None, attempt_root: Path | None) -> Iterator[tuple[Path, dict[str, Any]]]:
    if (archive is None) == (attempt_root is None):
        raise W7Error("set exactly one of --archive or --attempt-root")
    if attempt_root is not None:
        if attempt_root.is_symlink() or not attempt_root.is_dir() or attempt_root.name != ATTEMPT_ID:
            raise W7Error("attempt root must be a regular directory with the exact attempt ID")
        yield attempt_root, {"kind": "directory", "path": str(attempt_root.resolve()),
                             "ignored_archive_links": 0, "ignored_archive_supplements": 0}
        return
    assert archive is not None
    archive_digest = sha256(archive)
    if archive_digest != RESULT_ARCHIVE_SHA:
        raise W7Error("historical result archive SHA-256 mismatch")
    with tempfile.TemporaryDirectory(prefix="a4-w7-legacy-import.") as temp:
        root, skipped, supplements = safe_extract_archive(archive, Path(temp))
        yield root, {"kind": "archive", "path": str(archive.resolve()),
                     "sha256": archive_digest, "ignored_archive_links": skipped,
                     "ignored_archive_supplements": supplements}


def audit(root: Path, a1: Path, source: dict[str, Any]) -> dict[str, Any]:
    official_path = a1 / "scripts/common_suite_official.py"
    if sha256(official_path) != OFFICIAL_SHA:
        raise W7Error("current official suite specification hash mismatch")
    official = load_official(official_path)
    if (official.SUITES["full50"]["manifest_sha256"] != FULL_MANIFEST_SHA or
            official.SUITES["capacity22"]["manifest_sha256"] != CAPACITY_MANIFEST_SHA):
        raise W7Error("current official manifest identity mismatch")
    full = read_stems(root / "full-stems.txt", official.FULL50, "full50")
    capacity = read_stems(root / "capacity-stems.txt", official.CAPACITY22, "capacity22")
    provenance = validate_provenance(root)
    artifacts = validate_artifact_manifest(root, full)
    rows = validate_generation(root / "traces-full50", official)
    validate_capacity_generation(root, official)
    prepared = sorted((root / "prepared-v4").glob("*.svtrace"))
    if [path.stem for path in prepared] != sorted(full) or any(path.is_symlink() or not path.is_file() for path in prepared):
        raise W7Error("prepared-v4 is not the exact regular full50 set")
    candidates = {key: validate_candidate_results(root, key, rows, capacity)
                  for key in CANDIDATE_NAMES}
    return {
        "schema": "a4_w7_legacy_import_audit_v1",
        "status": "IMPORTED_LEGACY_EVIDENCE_HOLD",
        "official_receipt_eligible": False,
        "official_receipt_generated": False,
        "attempt_id": ATTEMPT_ID,
        "source": source,
        "contract": {
            "artifact_manifest_count": len(artifacts),
            "candidates": 2,
            "compile_evidence_per_candidate": 1,
            "trace_runs_per_candidate": 50,
            "reset_runs_per_candidate": 1,
            "capacity22": "analysis_only_subset_of_the_same_50_archived_runs",
            "address_identity": "address_only",
        },
        "provenance": provenance,
        "evidence_sha256": {
            "artifact_manifest": sha256(root / "result-artifacts.sha256"),
            "provenance": sha256(root / "provenance.txt"),
            "full_stems": sha256(root / "full-stems.txt"),
            "capacity_stems": sha256(root / "capacity-stems.txt"),
            "full_generation_index": sha256(root / "traces-full50/generation-index.json"),
            "capacity_generation_index": sha256(root / "traces-capacity22/generation-index.json"),
        },
        "candidates": candidates,
        "caveats": [
            "binding_reset_quiet_arming_patch=workspace-diff",
            "The archived binding change is not represented by an immutable clean commit.",
            "This is historical imported evidence, not a W7 official receipt or rerun.",
            "Archive validation cannot reconstruct or certify the uncommitted workspace diff.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--attempt-root", type=Path)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--a1-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    args = parser.parse_args(argv)
    try:
        if args.audit_output.name.lower() == "receipt.json" or "receipt" in args.audit_output.stem.lower():
            raise W7Error("legacy import output must not be named or represented as a receipt")
        if args.audit_output.exists() or args.audit_output.is_symlink():
            raise W7Error(f"refusing to overwrite audit output: {args.audit_output}")
        with source_root(archive=args.archive, attempt_root=args.attempt_root) as (root, source_info):
            try:
                args.audit_output.resolve().relative_to(root.resolve())
            except ValueError:
                pass
            else:
                raise W7Error("audit output must be outside the read-only attempt root")
            report = audit(root, args.a1_root.resolve(), source_info)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        decoded = json.loads(encoded)
        if (decoded.get("status") != "IMPORTED_LEGACY_EVIDENCE_HOLD" or
                decoded.get("official_receipt_eligible") is not False):
            raise W7Error("internal audit status invariant failed")
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        with args.audit_output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        print("A4_W7_LEGACY_IMPORT_VALID_HOLD artifacts=338 candidates=2 trace_runs=100 reset_runs=2")
        return 0
    except (OSError, ValueError, KeyError, W7Error, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
