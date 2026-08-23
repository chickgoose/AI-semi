"""Independent cyclemask/native-ledger validator for the Ganghee seam.

This module uses only the Python standard library.  It derives occurrence IDs
from the pinned cyclemask and does not trust the observational testbench's IDs,
FIFO bookkeeping, coordinates, counts, or summary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


AUTHORITY_SCHEMA = "redred.cluster2_cav_bridge.ganghee_native_authority/v1"
LEDGER_SCHEMA = "redred.cluster2_cav_bridge.native_ledger/v1"
TRANSPORT_OUTCOME_SCHEMA = "redred.cluster2_cav_bridge.transport_outcome/v1"
GANGHEE_REPOSITORY_URL = "https://github.com/GangHeeJo/AI-SEMI"
GANGHEE_COMMIT = "5ac1f0e3c0e6991558afa699e64680f708ff625d"
GANGHEE_AUTHORITY_SHA256 = (
    "90e659358423368ce6a27850cdffa36a0eb85cea508babc66e72ecafb8e70530"
)
FILE_BYTES_AUTHORITY = "FILE_BYTES_AUTHORITY"
CLEAN_GIT_AUTHORITY = "CLEAN_GIT_AUTHORITY"

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_CYCLE = (1 << 63) - 1
NATIVE_DRAIN_LIMIT = 100_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CYCLEMASK_LINE = re.compile(r"(0|[1-9][0-9]*) ([0-9a-f]{4})\Z")
_UINT = re.compile(r"0|[1-9][0-9]*\Z")

EXPECTED_CODE_FILES = {
    "arbiter2": (
        "rtl/arbiter2.v",
        "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    ),
    "arbiter4_tree": (
        "rtl/arbiter4_tree.v",
        "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    ),
    "cyclemask_converter": (
        "scripts/convert_uzh_to_cyclemask.py",
        "321002a527f7286f1c43fce45ae125983dbfb855509e614e178f8057f29714d7",
    ),
    "cluster2_steal_buf_rtl": (
        "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v",
        "56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96",
    ),
    "strict_native_tb": (
        "tb/tb_steal_buf_trace_phantom_debug.v",
        "06123cc83e1682e7175c220a762fd3ff75bd40fd7795565e5f381ac55167556e",
    ),
}

TRACKED_CYCLEMASK_PATH = "common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
TRACKED_CYCLEMASK_RAW_SHA256 = {
    "LF": "850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea",
    "CRLF": "a50866f95430e3fe8d8af775c2e9692353e1e6bc9a1ecfedfed620143be48313",
}
TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256 = (
    "850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea"
)
EXPECTED_TRACKED_CYCLEMASK = {
    "accepted_raw_encodings": [
        {"line_endings": "LF", "sha256": TRACKED_CYCLEMASK_RAW_SHA256["LF"]},
        {"line_endings": "CRLF", "sha256": TRACKED_CYCLEMASK_RAW_SHA256["CRLF"]},
    ],
    "canonical_semantic_lf_sha256": TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256,
    "path": TRACKED_CYCLEMASK_PATH,
    "role": "tracked_cyclemask",
    "semantic_rule": "uniform-LF-or-CRLF-to-LF/v1",
}

EXPECTED_NATIVE_INTERFACE = {
    "buffer_depth_per_source": 2,
    "inputs": [
        {"name": "clk", "width": 1},
        {"name": "rst", "width": 1},
        {"name": "arrival", "width": 16},
    ],
    "legal_two_valid_row_pairs": [
        [0, 3], [1, 0], [1, 2], [1, 3], [2, 0], [2, 3],
    ],
    "max_delivered_events_per_cycle": 8,
    "module": "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf",
    "outputs": [{"name": "overrun", "width": 16}],
    "retire_lanes": [
        {
            "allowed_rows": [0, 1, 2],
            "allowed_rows_when_other_invalid": [1, 2],
            "col_mask": {"name": "col_mask0", "width": 4},
            "lane": 0,
            "row": {"name": "row0", "width": 2},
            "valid": {"name": "valid0", "width": 1},
        },
        {
            "allowed_rows": [0, 2, 3],
            "allowed_rows_when_other_invalid": [0, 3],
            "col_mask": {"name": "col_mask1", "width": 4},
            "lane": 1,
            "row": {"name": "row1", "width": 2},
            "valid": {"name": "valid1", "width": 1},
        },
    ],
}


class NativeLedgerError(ValueError):
    """The authority, cyclemask, or observational ledger is inconsistent."""


@dataclass(frozen=True)
class Occurrence:
    event_id: int
    source_index: int
    occurrence_cycle: int


@dataclass(frozen=True)
class CyclemaskEncoding:
    line_endings: str
    raw_sha256: str
    canonical_semantic_lf_sha256: str
    canonical_lf_bytes: bytes


@dataclass(frozen=True)
class LedgerRecord:
    event_id: int
    source_index: int
    occurrence_cycle: int
    outcome: str
    retire_cycle: object
    retire_native_lane: object
    retire_row: object
    retire_col: object


def _unique_object(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result = {}  # type: Dict[str, object]
    for key, value in pairs:
        if key in result:
            raise NativeLedgerError("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n").encode("ascii")


def _strict_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return set(left) == set(right) and all(  # type: ignore[arg-type]
            _strict_equal(left[key], right[key]) for key in left  # type: ignore[index]
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(  # type: ignore[arg-type]
            _strict_equal(a, b) for a, b in zip(left, right)  # type: ignore[arg-type]
        )
    return left == right


def _strict_lines(payload: bytes, where: str) -> Tuple[str, ...]:
    if not payload or len(payload) > MAX_INPUT_BYTES or not payload.endswith(b"\n"):
        raise NativeLedgerError("%s must be non-empty, bounded, and LF-terminated" % where)
    if b"\r" in payload:
        raise NativeLedgerError("%s must use LF line endings" % where)
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise NativeLedgerError("%s must be ASCII" % where) from error
    lines = tuple(text[:-1].split("\n"))
    if any(not line for line in lines):
        raise NativeLedgerError("%s contains a blank line" % where)
    if len(lines) > MAX_RECORDS + 2:
        raise NativeLedgerError("%s has too many records" % where)
    return lines


def inspect_cyclemask_encoding(payload: bytes) -> CyclemaskEncoding:
    """Classify uniform LF/CRLF bytes and expose the explicit LF semantic view."""
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise NativeLedgerError("cyclemask must be non-empty and bounded")
    if b"\r\n" in payload:
        without_crlf = payload.replace(b"\r\n", b"")
        if b"\r" in without_crlf or b"\n" in without_crlf or not payload.endswith(b"\r\n"):
            raise NativeLedgerError("cyclemask contains mixed or malformed line endings")
        line_endings = "CRLF"
        canonical_lf = payload.replace(b"\r\n", b"\n")
    else:
        if b"\r" in payload or not payload.endswith(b"\n"):
            raise NativeLedgerError("cyclemask contains mixed or malformed line endings")
        line_endings = "LF"
        canonical_lf = payload
    try:
        canonical_lf.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise NativeLedgerError("cyclemask must be ASCII") from error
    return CyclemaskEncoding(
        line_endings=line_endings,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        canonical_semantic_lf_sha256=hashlib.sha256(canonical_lf).hexdigest(),
        canonical_lf_bytes=canonical_lf,
    )


def normalized_relative_path(value: object, where: str = "path") -> str:
    if type(value) is not str or not value or "\\" in value:
        raise NativeLedgerError("%s must be a normalized relative POSIX path" % where)
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise NativeLedgerError("%s must be a normalized relative POSIX path" % where)
    return value


def load_native_authority(path: Path) -> Mapping[str, object]:
    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise NativeLedgerError("cannot read native authority") from error
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise NativeLedgerError("native authority byte size is invalid")
    if hashlib.sha256(payload).hexdigest() != GANGHEE_AUTHORITY_SHA256:
        raise NativeLedgerError("native authority canonical bytes differ")
    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                NativeLedgerError("non-finite JSON number: %s" % token)
            ),
        )
    except NativeLedgerError:
        raise
    except (UnicodeError, ValueError, TypeError) as error:
        raise NativeLedgerError("native authority is not strict JSON") from error
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "repository_url", "git_commit", "code_files",
        "tracked_cyclemask", "native_interface",
    }:
        raise NativeLedgerError("native authority field schema differs")
    if _canonical_json_bytes(value) != payload:
        raise NativeLedgerError("native authority is not canonical JSON")
    if value["schema"] != AUTHORITY_SCHEMA:
        raise NativeLedgerError("native authority schema differs")
    if value["repository_url"] != GANGHEE_REPOSITORY_URL:
        raise NativeLedgerError("Ganghee repository URL differs")
    if value["git_commit"] != GANGHEE_COMMIT:
        raise NativeLedgerError("Ganghee Git commit differs")
    if not _strict_equal(value["native_interface"], EXPECTED_NATIVE_INTERFACE):
        raise NativeLedgerError("Ganghee native interface differs")
    rows = value["code_files"]
    if not isinstance(rows, list) or len(rows) != len(EXPECTED_CODE_FILES):
        raise NativeLedgerError("native authority code file set differs")
    actual = {}
    observed_roles = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {"role", "path", "sha256"}:
            raise NativeLedgerError("native authority file row %d differs" % index)
        role = row["role"]
        if type(role) is not str or role in actual:
            raise NativeLedgerError("native authority file roles must be unique")
        observed_roles.append(role)
        relative = normalized_relative_path(row["path"], "native authority file path")
        digest = row["sha256"]
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise NativeLedgerError("native authority file SHA-256 is not full lowercase hex")
        actual[role] = (relative, digest)
    if actual != EXPECTED_CODE_FILES:
        raise NativeLedgerError("native authority pinned code identities differ")
    if observed_roles != list(EXPECTED_CODE_FILES):
        raise NativeLedgerError("native authority code file order differs")
    if not _strict_equal(value["tracked_cyclemask"], EXPECTED_TRACKED_CYCLEMASK):
        raise NativeLedgerError("native authority tracked cyclemask differs")
    return value


def _read_regular_file(path: Path, root: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise NativeLedgerError("authority file escapes or is unavailable: %s" % path) from error
    current = root
    for part in resolved.relative_to(root).parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise NativeLedgerError("authority path contains a symlink: %s" % path)
        except OSError as error:
            raise NativeLedgerError("cannot inspect authority path: %s" % path) from error
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(resolved), flags)
    except OSError as error:
        raise NativeLedgerError("cannot open authority file: %s" % path) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT_BYTES:
            raise NativeLedgerError("authority member is not a bounded regular file")
        payload = b""
        while len(payload) <= MAX_INPUT_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_INPUT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev, before.st_ino, before.st_mode, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev, after.st_ino, after.st_mode, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if before_identity != after_identity or len(payload) != before.st_size:
        raise NativeLedgerError("authority file changed while being captured")
    return payload


def _git(root: Path, arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root)] + list(arguments),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise NativeLedgerError("cannot verify Ganghee Git checkout") from error
    return completed.stdout.strip()


def verify_faer_checkout(
    faer_root: Path,
    authority_path: Path,
    cyclemask_relative: str,
    authority_mode: str = FILE_BYTES_AUTHORITY,
) -> Mapping[str, Path]:
    """Verify scoped bytes, optionally adding exact clean-Git state checks."""
    if authority_mode not in (FILE_BYTES_AUTHORITY, CLEAN_GIT_AUTHORITY):
        raise NativeLedgerError("unknown FAER authority mode")
    supplied_root = Path(faer_root)
    if not supplied_root.is_absolute():
        raise NativeLedgerError("caller FAER root must be absolute")
    try:
        root = supplied_root.resolve(strict=True)
    except OSError as error:
        raise NativeLedgerError("caller FAER root is unavailable") from error
    if supplied_root != root:
        raise NativeLedgerError("caller FAER root must be normalized and symlink-free")
    if not root.is_dir():
        raise NativeLedgerError("caller FAER root must be a directory")
    load_native_authority(Path(authority_path))
    trace_relative = normalized_relative_path(cyclemask_relative, "cyclemask relative path")
    if trace_relative != TRACKED_CYCLEMASK_PATH:
        raise NativeLedgerError("cyclemask path is not the pinned tracked trace")
    if authority_mode == CLEAN_GIT_AUTHORITY:
        if _git(root, ["rev-parse", "--verify", "HEAD^{commit}"]) != GANGHEE_COMMIT:
            raise NativeLedgerError("Ganghee checkout commit differs")
        if Path(_git(root, ["rev-parse", "--show-toplevel"])) != root:
            raise NativeLedgerError("caller FAER root is not the Ganghee Git top-level")
        origin_url = _git(root, ["config", "--get", "remote.origin.url"])
        if origin_url not in (GANGHEE_REPOSITORY_URL, GANGHEE_REPOSITORY_URL + ".git"):
            raise NativeLedgerError("Ganghee checkout origin URL differs")
        if _git(root, ["status", "--porcelain", "--untracked-files=no"]):
            raise NativeLedgerError("Ganghee checkout has tracked modifications")
    verified = {}
    for role, (relative, expected_digest) in EXPECTED_CODE_FILES.items():
        member = root / Path(*PurePosixPath(relative).parts)
        payload = _read_regular_file(member, root)
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise NativeLedgerError("Ganghee authority SHA-256 differs for %s" % role)
        verified[role] = member
    trace_member = root / Path(*PurePosixPath(TRACKED_CYCLEMASK_PATH).parts)
    trace_payload = _read_regular_file(trace_member, root)
    encoding = inspect_cyclemask_encoding(trace_payload)
    if (
        encoding.raw_sha256 != TRACKED_CYCLEMASK_RAW_SHA256[encoding.line_endings]
        or encoding.canonical_semantic_lf_sha256
        != TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256
    ):
        raise NativeLedgerError("Ganghee tracked cyclemask raw or semantic SHA-256 differs")
    verified["tracked_cyclemask"] = trace_member
    return verified


def derive_occurrences(cyclemask_payload: bytes) -> Tuple[Occurrence, ...]:
    """Derive zero-based event IDs in cycle-then-source order."""
    encoding = inspect_cyclemask_encoding(cyclemask_payload)
    occurrences = []  # type: List[Occurrence]
    previous_cycle = None
    for line_number, line in enumerate(
        _strict_lines(encoding.canonical_lf_bytes, "cyclemask semantic LF"), 1
    ):
        match = _CYCLEMASK_LINE.fullmatch(line)
        if match is None:
            raise NativeLedgerError("cyclemask line %d is not canonical" % line_number)
        cycle = int(match.group(1))
        mask = int(match.group(2), 16)
        if cycle > MAX_CYCLE or mask == 0:
            raise NativeLedgerError("cyclemask cycle or bitmap is out of range")
        if previous_cycle is not None and cycle <= previous_cycle:
            raise NativeLedgerError("cyclemask cycles must be strictly increasing")
        previous_cycle = cycle
        for source_index in range(16):
            if mask & (1 << source_index):
                occurrences.append(Occurrence(len(occurrences), source_index, cycle))
                if len(occurrences) > MAX_RECORDS:
                    raise NativeLedgerError("cyclemask occurrence count exceeds limit")
    return tuple(occurrences)


def _uint(token: str, where: str) -> int:
    if _UINT.fullmatch(token) is None:
        raise NativeLedgerError("%s is not a canonical unsigned integer" % where)
    value = int(token)
    if value > MAX_CYCLE:
        raise NativeLedgerError("%s exceeds supported range" % where)
    return value


def _parse_event_line(line: str, line_number: int) -> LedgerRecord:
    fields = line.split("|")
    if len(fields) != 9 or fields[0] != "EVENT":
        raise NativeLedgerError("ledger line %d field schema differs" % line_number)
    event_id = _uint(fields[1], "ledger event_id")
    source_index = _uint(fields[2], "ledger source_index")
    occurrence_cycle = _uint(fields[3], "ledger occurrence_cycle")
    outcome = fields[4]
    if source_index > 15:
        raise NativeLedgerError("ledger source_index is out of range")
    if outcome == "DELIVERED":
        values = tuple(_uint(token, "ledger retire field") for token in fields[5:9])
        retire_cycle, lane, row, col = values
        if lane not in (0, 1) or row > 3 or col > 3:
            raise NativeLedgerError("ledger native retirement coordinate is out of range")
        if source_index != row * 4 + col:
            raise NativeLedgerError("ledger native coordinate differs from source_index")
        if (lane == 0 and row not in (0, 1, 2)) or (
            lane == 1 and row not in (0, 2, 3)
        ):
            raise NativeLedgerError("ledger native lane cannot emit the observed row")
        if occurrence_cycle > retire_cycle:
            raise NativeLedgerError("ledger retires before occurrence")
        return LedgerRecord(
            event_id, source_index, occurrence_cycle, outcome,
            retire_cycle, lane, row, col,
        )
    if outcome == "OVERRUN":
        if fields[5:9] != ["-", "-", "-", "-"]:
            raise NativeLedgerError("OVERRUN ledger retire fields must all be null")
        return LedgerRecord(
            event_id, source_index, occurrence_cycle, outcome,
            None, None, None, None,
        )
    raise NativeLedgerError("ledger outcome differs")


def parse_native_ledger(
    cyclemask_payload: bytes,
    ledger_payload: bytes,
) -> Tuple[Mapping[str, object], ...]:
    """Validate a ledger independently and return transport_outcome/v1 rows."""
    occurrences = derive_occurrences(cyclemask_payload)
    expected_by_id = {occurrence.event_id: occurrence for occurrence in occurrences}
    lines = _strict_lines(ledger_payload, "native ledger")
    if len(lines) < 2 or lines[0] != "SCHEMA|" + LEDGER_SCHEMA:
        raise NativeLedgerError("native ledger schema header differs")
    summary_fields = lines[-1].split("|")
    if len(summary_fields) != 4 or summary_fields[0] != "SUMMARY":
        raise NativeLedgerError("native ledger summary differs")
    summary_generated = _uint(summary_fields[1], "ledger summary generated")
    summary_delivered = _uint(summary_fields[2], "ledger summary delivered")
    summary_overrun = _uint(summary_fields[3], "ledger summary overrun")
    records = []  # type: List[LedgerRecord]
    records_by_id = {}  # type: Dict[int, LedgerRecord]
    observation_keys = []  # type: List[Tuple[int, int, int, int, int]]
    rows_by_cycle_lane = {}  # type: Dict[Tuple[int, int], int]
    retire_slots = set()
    delivered_per_cycle = {}  # type: Dict[int, int]
    for line_number, line in enumerate(lines[1:-1], 2):
        record = _parse_event_line(line, line_number)
        if record.event_id in records_by_id:
            raise NativeLedgerError("native ledger event IDs must be unique")
        expected = expected_by_id.get(record.event_id)
        if expected is None or (
            record.source_index != expected.source_index
            or record.occurrence_cycle != expected.occurrence_cycle
        ):
            raise NativeLedgerError("ledger ID/source/occurrence differs from cyclemask")
        records_by_id[record.event_id] = record
        records.append(record)
        if record.outcome == "OVERRUN":
            observation_keys.append((
                record.occurrence_cycle, 0, record.source_index, 0, record.event_id,
            ))
            continue
        retire_cycle = record.retire_cycle
        lane = record.retire_native_lane
        row = record.retire_row
        col = record.retire_col
        assert all(isinstance(value, int) for value in (retire_cycle, lane, row, col))
        bitmap_key = (retire_cycle, lane)  # type: ignore[arg-type]
        prior_row = rows_by_cycle_lane.get(bitmap_key)
        if prior_row is not None and prior_row != row:
            raise NativeLedgerError("one native lane-cycle selects multiple rows")
        other_row = rows_by_cycle_lane.get((retire_cycle, 1 - lane))  # type: ignore[operator]
        if other_row == row:
            raise NativeLedgerError("two native lanes select one row in a cycle")
        rows_by_cycle_lane[bitmap_key] = row  # type: ignore[assignment]
        retire_slot = (retire_cycle, lane, col)
        if retire_slot in retire_slots:
            raise NativeLedgerError("native cycle/lane/column slot is duplicated")
        retire_slots.add(retire_slot)
        delivered_per_cycle[retire_cycle] = delivered_per_cycle.get(retire_cycle, 0) + 1  # type: ignore[arg-type]
        if delivered_per_cycle[retire_cycle] > 8:  # type: ignore[index]
            raise NativeLedgerError("more than eight events retire in one cycle")
        observation_keys.append((
            retire_cycle, 1, lane, col, record.event_id  # type: ignore[arg-type]
        ))
    if set(records_by_id) != set(expected_by_id):
        raise NativeLedgerError("ledger does not exactly partition cyclemask event IDs")
    latest_occurrence_cycle = max(
        occurrence.occurrence_cycle for occurrence in occurrences
    )
    if any(
        isinstance(record.retire_cycle, int)
        and record.retire_cycle > latest_occurrence_cycle + NATIVE_DRAIN_LIMIT
        for record in records
    ):
        raise NativeLedgerError("ledger retirement exceeds the bounded native drain")
    lane_rows_by_cycle = {}  # type: Dict[int, Dict[int, int]]
    for (cycle, lane), row in rows_by_cycle_lane.items():
        lane_rows_by_cycle.setdefault(cycle, {})[lane] = row
    legal_pairs = {(0, 3), (1, 0), (1, 2), (1, 3), (2, 0), (2, 3)}
    for cycle, lane_rows in lane_rows_by_cycle.items():
        if set(lane_rows) == {0, 1}:
            if (lane_rows[0], lane_rows[1]) not in legal_pairs:
                raise NativeLedgerError("native two-valid row pair is impossible")
        elif set(lane_rows) == {0} and lane_rows[0] not in (1, 2):
            raise NativeLedgerError("native lane0-only row is impossible")
        elif set(lane_rows) == {1} and lane_rows[1] not in (0, 3):
            raise NativeLedgerError("native lane1-only row is impossible")
    if observation_keys != sorted(observation_keys):
        raise NativeLedgerError("ledger rows are not in deterministic observation order")
    delivered_count = sum(record.outcome == "DELIVERED" for record in records)
    overrun_count = len(records) - delivered_count
    if (
        summary_generated != len(occurrences)
        or summary_delivered != delivered_count
        or summary_overrun != overrun_count
        or summary_generated != summary_delivered + summary_overrun
    ):
        raise NativeLedgerError("ledger count/conservation summary differs")

    occurrences_by_cycle = {}  # type: Dict[int, List[Occurrence]]
    deliveries_by_cycle = {}  # type: Dict[int, List[LedgerRecord]]
    for occurrence in occurrences:
        occurrences_by_cycle.setdefault(occurrence.occurrence_cycle, []).append(occurrence)
    for record in records:
        if record.outcome == "DELIVERED":
            assert isinstance(record.retire_cycle, int)
            deliveries_by_cycle.setdefault(record.retire_cycle, []).append(record)
    queues = [[] for _ in range(16)]  # type: List[List[int]]
    cycles = sorted(set(occurrences_by_cycle) | set(deliveries_by_cycle))
    for cycle in cycles:
        current_occurrences = occurrences_by_cycle.get(cycle, [])
        for occurrence in current_occurrences:
            record = records_by_id[occurrence.event_id]
            is_full = len(queues[occurrence.source_index]) == 2
            if is_full != (record.outcome == "OVERRUN"):
                raise NativeLedgerError("ledger overrun differs from pre-edge 2-deep state")
        current_deliveries = sorted(
            deliveries_by_cycle.get(cycle, []),
            key=lambda record: (
                record.retire_native_lane, record.retire_col, record.event_id
            ),
        )
        for record in current_deliveries:
            queue = queues[record.source_index]
            if not queue or queue[0] != record.event_id:
                raise NativeLedgerError("ledger contains a phantom or per-source FIFO reorder")
            queue.pop(0)
        for occurrence in current_occurrences:
            if records_by_id[occurrence.event_id].outcome == "DELIVERED":
                queue = queues[occurrence.source_index]
                queue.append(occurrence.event_id)
                if len(queue) > 2:
                    raise NativeLedgerError("ledger exceeds the native per-source depth")
    if any(queues):
        raise NativeLedgerError("ledger drain is incomplete")

    return tuple({
        "schema": TRANSPORT_OUTCOME_SCHEMA,
        "event_id": record.event_id,
        "source_index": record.source_index,
        "occurrence_cycle": record.occurrence_cycle,
        "outcome": record.outcome,
        "retire_cycle": record.retire_cycle,
        "retire_native_lane": record.retire_native_lane,
        "retire_row": record.retire_row,
        "retire_col": record.retire_col,
    } for record in sorted(records, key=lambda item: item.event_id))


def canonical_transport_outcome_jsonl(rows: Iterable[Mapping[str, object]]) -> bytes:
    payloads = []
    for row in rows:
        payloads.append(_canonical_json_bytes(row))
        if len(payloads) > MAX_RECORDS:
            raise NativeLedgerError("transport outcome count exceeds limit")
    if not payloads:
        raise NativeLedgerError("transport outcome stream must not be empty")
    payload = b"".join(payloads)
    if len(payload) > MAX_INPUT_BYTES:
        raise NativeLedgerError("transport outcome stream exceeds byte limit")
    return payload


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cyclemask", type=Path)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args(arguments)
    try:
        rows = parse_native_ledger(
            options.cyclemask.read_bytes(), options.ledger.read_bytes()
        )
        payload = canonical_transport_outcome_jsonl(rows)
        if options.output is None:
            sys.stdout.buffer.write(payload)
        else:
            options.output.write_bytes(payload)
    except (OSError, NativeLedgerError) as error:
        print("NATIVE_LEDGER_FAIL: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
