"""Fail-closed UZH-raw to Cluster2 cyclemask crosswalk.

The pinned Ganghee converter reduces an event to a 4x4 source bit and uses
``int(float(timestamp) / 0.001)`` as its cycle bin.  A cyclemask cannot retain
two events in the same cycle/source slot, so this module rejects that lossy
case instead of inventing identities for it.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Dict, Tuple

from benchmarks.redred_cluster2_cav_bridge.native_ledger import (
    NativeLedgerError,
    derive_occurrences,
)


PATCH_X_MIN = 110
PATCH_X_MAX = 113
PATCH_Y_MIN = 85
PATCH_Y_MAX = 88
SENSOR_WIDTH = 240
SENSOR_HEIGHT = 180
BIN_SECONDS = 0.001
BIN_NS = 1_000_000

# Generic APIs are bounded even when the caller is the byte authority.  The raw
# cap deliberately admits the exact 509 MB official member below without
# turning "streaming" into an unbounded-file promise.
MAX_RAW_EVENTS_BYTES = 512 * 1024 * 1024
MAX_CYCLEMASK_BYTES = 64 * 1024 * 1024
MAX_PATCH_SLOTS = 1_000_000

_OFFICIAL_UZH_EVENTS_SIZE_BYTES = 509_907_771
_OFFICIAL_UZH_EVENTS_SHA256 = (
    "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda"
)
_OFFICIAL_CYCLEMASK_SHA256 = (
    ("LF", "850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea"),
    ("CRLF", "a50866f95430e3fe8d8af775c2e9692353e1e6bc9a1ecfedfed620143be48313"),
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RAW_LINE = re.compile(
    rb"(0|[1-9][0-9]{0,9})\.([0-9]{9}) "
    rb"(0|[1-9][0-9]{0,2}) (0|[1-9][0-9]{0,2}) ([01])\n\Z"
)
_MAX_RAW_LINE_BYTES = 96


class SourceCrosswalkError(ValueError):
    """The caller authority, UZH source, or cyclemask is inconsistent."""


@dataclass(frozen=True)
class SourceCrosswalkEvent:
    """One losslessly recovered raw event, identified by native occurrence."""

    event_id: int
    timestamp_ns: int
    x: int
    y: int
    polarity: int
    source_index: int
    occurrence_cycle: int


def _checked_sha256(payload: bytes, expected: str, where: str) -> None:
    _validate_expected_sha256(expected, where)
    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise SourceCrosswalkError("%s bytes differ from caller SHA-256" % where)


def _validate_expected_sha256(expected: str, where: str) -> None:
    if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
        raise SourceCrosswalkError(
            "%s caller SHA-256 must be 64 lowercase hexadecimal digits" % where
        )


def _check_byte_count(size: int, maximum: int, where: str) -> None:
    if size > maximum:
        raise SourceCrosswalkError("%s exceeds byte limit" % where)


def _parse_raw_line(raw: bytes, line_number: int) -> Tuple[int, int, int, int, int]:
    if len(raw) > _MAX_RAW_LINE_BYTES:
        raise SourceCrosswalkError("raw event line %d exceeds byte limit" % line_number)
    match = _RAW_LINE.fullmatch(raw)
    if match is None:
        raise SourceCrosswalkError("raw event line %d is not canonical" % line_number)

    seconds = int(match.group(1))
    fractional_ns = int(match.group(2))
    x = int(match.group(3))
    y = int(match.group(4))
    polarity = int(match.group(5))
    if x >= SENSOR_WIDTH or y >= SENSOR_HEIGHT:
        raise SourceCrosswalkError(
            "raw event line %d lies outside the DAVIS240C lattice" % line_number
        )

    timestamp_ns = seconds * 1_000_000_000 + fractional_ns
    # This deliberately mirrors the pinned converter rather than deriving the
    # bin from timestamp_ns.  The binary-float operation is part of the source
    # conversion contract being audited.
    timestamp_text = raw.split(b" ", 1)[0].decode("ascii")
    occurrence_cycle = int(float(timestamp_text) / BIN_SECONDS)
    return timestamp_ns, x, y, polarity, occurrence_cycle


def _read_raw_stream(
    stream: BinaryIO,
    digest=None,
) -> Dict[Tuple[int, int], Tuple[int, int, int, int]]:
    """Parse and optionally hash the exact same immutable line snapshots."""

    raw_by_slot = {}  # type: Dict[Tuple[int, int], Tuple[int, int, int, int]]
    line_number = 0
    total_bytes = 0
    while True:
        raw = stream.readline(_MAX_RAW_LINE_BYTES + 2)
        if not raw:
            break
        total_bytes += len(raw)
        _check_byte_count(total_bytes, MAX_RAW_EVENTS_BYTES, "raw events")
        if digest is not None:
            digest.update(raw)
        line_number += 1
        timestamp_ns, x, y, polarity, occurrence_cycle = _parse_raw_line(
            raw, line_number
        )
        if not (
            PATCH_X_MIN <= x <= PATCH_X_MAX
            and PATCH_Y_MIN <= y <= PATCH_Y_MAX
        ):
            continue
        source_index = (y - PATCH_Y_MIN) * 4 + (x - PATCH_X_MIN)
        slot = (occurrence_cycle, source_index)
        if slot in raw_by_slot:
            raise SourceCrosswalkError(
                "raw events collide at cycle %d source %d" % slot
            )
        if len(raw_by_slot) >= MAX_PATCH_SLOTS:
            raise SourceCrosswalkError("raw patch slot count exceeds limit")
        raw_by_slot[slot] = (timestamp_ns, x, y, polarity)

    if line_number == 0:
        raise SourceCrosswalkError("raw events payload is empty")
    return raw_by_slot


def _join_crosswalk(
    raw_by_slot: Dict[Tuple[int, int], Tuple[int, int, int, int]],
    cyclemask_payload: bytes,
) -> Tuple[SourceCrosswalkEvent, ...]:

    if len(raw_by_slot) > MAX_PATCH_SLOTS:
        raise SourceCrosswalkError("raw patch slot count exceeds limit")
    try:
        occurrences = derive_occurrences(cyclemask_payload)
    except NativeLedgerError as error:
        raise SourceCrosswalkError("invalid cyclemask: %s" % error) from error
    if len(occurrences) > MAX_PATCH_SLOTS:
        raise SourceCrosswalkError("cyclemask patch slot count exceeds limit")
    cyclemask_slots = {
        (occurrence.occurrence_cycle, occurrence.source_index)
        for occurrence in occurrences
    }
    raw_slots = set(raw_by_slot)
    if raw_slots != cyclemask_slots:
        missing = sorted(raw_slots - cyclemask_slots)
        extra = sorted(cyclemask_slots - raw_slots)
        raise SourceCrosswalkError(
            "raw/cyclemask slot sets differ: missing_from_cyclemask=%r "
            "missing_from_raw=%r" % (missing[:8], extra[:8])
        )

    result = []
    for occurrence in occurrences:
        slot = (occurrence.occurrence_cycle, occurrence.source_index)
        timestamp_ns, x, y, polarity = raw_by_slot[slot]
        result.append(SourceCrosswalkEvent(
            event_id=occurrence.event_id,
            timestamp_ns=timestamp_ns,
            x=x,
            y=y,
            polarity=polarity,
            source_index=occurrence.source_index,
            occurrence_cycle=occurrence.occurrence_cycle,
        ))
    return tuple(result)


def derive_source_crosswalk(
    raw_events_payload: bytes,
    cyclemask_payload: bytes,
    expected_raw_sha256: str,
    expected_cyclemask_sha256: str,
) -> Tuple[SourceCrosswalkEvent, ...]:
    """Caller-bound generic conversion; this does not authenticate official UZH.

    Both expected digests are mandatory caller authorities.  Event IDs are
    assigned only after exact set equality is established, in the native
    cycle-then-source order used by :func:`derive_occurrences`.
    """

    if type(raw_events_payload) is not bytes or type(cyclemask_payload) is not bytes:
        raise SourceCrosswalkError("raw events and cyclemask payloads must be bytes")
    _check_byte_count(len(raw_events_payload), MAX_RAW_EVENTS_BYTES, "raw events")
    _check_byte_count(len(cyclemask_payload), MAX_CYCLEMASK_BYTES, "cyclemask")
    _checked_sha256(raw_events_payload, expected_raw_sha256, "raw events")
    _checked_sha256(cyclemask_payload, expected_cyclemask_sha256, "cyclemask")
    raw_by_slot = _read_raw_stream(io.BytesIO(raw_events_payload))
    return _join_crosswalk(raw_by_slot, cyclemask_payload)


def _file_identity(value: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_regular(path: Path, where: str) -> Tuple[BinaryIO, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceCrosswalkError("%s must be a regular file" % where)
        return os.fdopen(descriptor, "rb"), before
    except SourceCrosswalkError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise SourceCrosswalkError("cannot open %s: %s" % (where, error)) from error


def _derive_source_crosswalk_files(
    raw_events_path: Path,
    cyclemask_path: Path,
    expected_raw_sha256: str,
    accepted_cyclemask_sha256: Tuple[str, ...],
    expected_raw_size: int = -1,
) -> Tuple[SourceCrosswalkEvent, ...]:
    """Shared bounded file implementation for generic and pinned authorities."""

    if not isinstance(raw_events_path, Path) or not isinstance(cyclemask_path, Path):
        raise SourceCrosswalkError("raw events and cyclemask paths must be pathlib.Path")
    _validate_expected_sha256(expected_raw_sha256, "raw events")
    if not accepted_cyclemask_sha256:
        raise SourceCrosswalkError("cyclemask SHA-256 authority set is empty")
    for expected in accepted_cyclemask_sha256:
        _validate_expected_sha256(expected, "cyclemask")

    mask_stream, mask_before = _open_regular(cyclemask_path, "cyclemask")
    try:
        _check_byte_count(mask_before.st_size, MAX_CYCLEMASK_BYTES, "cyclemask")
        cyclemask_payload = mask_stream.read(MAX_CYCLEMASK_BYTES + 1)
        _check_byte_count(len(cyclemask_payload), MAX_CYCLEMASK_BYTES, "cyclemask")
        mask_after = os.fstat(mask_stream.fileno())
    finally:
        mask_stream.close()
    if _file_identity(mask_before) != _file_identity(mask_after):
        raise SourceCrosswalkError("cyclemask changed during read")
    actual_cyclemask_sha256 = hashlib.sha256(cyclemask_payload).hexdigest()
    if not any(
        hmac.compare_digest(actual_cyclemask_sha256, expected)
        for expected in accepted_cyclemask_sha256
    ):
        raise SourceCrosswalkError("cyclemask bytes differ from accepted SHA-256 authority")

    raw_stream, raw_before = _open_regular(raw_events_path, "raw events")
    try:
        _check_byte_count(raw_before.st_size, MAX_RAW_EVENTS_BYTES, "raw events")
        if expected_raw_size >= 0 and raw_before.st_size != expected_raw_size:
            raise SourceCrosswalkError("raw events size differs from pinned official size")
        # Hash and parse each returned bytes object once.  Rewinding and
        # parsing the live descriptor in a second pass would let a same-size
        # immediate overwrite substitute different semantic bytes when file
        # metadata cannot distinguish the two states.
        raw_digest = hashlib.sha256()
        raw_by_slot = _read_raw_stream(raw_stream, raw_digest)
        actual_raw_sha256 = raw_digest.hexdigest()
        if not hmac.compare_digest(actual_raw_sha256, expected_raw_sha256):
            raise SourceCrosswalkError(
                "raw events bytes differ from accepted SHA-256 authority"
            )
        raw_after = os.fstat(raw_stream.fileno())
    finally:
        raw_stream.close()
    if _file_identity(raw_before) != _file_identity(raw_after):
        raise SourceCrosswalkError("raw events changed during read")
    return _join_crosswalk(raw_by_slot, cyclemask_payload)


def derive_source_crosswalk_files(
    raw_events_path: Path,
    cyclemask_path: Path,
    expected_raw_sha256: str,
    expected_cyclemask_sha256: str,
) -> Tuple[SourceCrosswalkEvent, ...]:
    """Bounded caller-SHA file conversion; not an official-source assertion."""

    return _derive_source_crosswalk_files(
        raw_events_path,
        cyclemask_path,
        expected_raw_sha256,
        (expected_cyclemask_sha256,),
    )


def derive_official_uzh_source_crosswalk_files(
    raw_events_path: Path,
    cyclemask_path: Path,
) -> Tuple[SourceCrosswalkEvent, ...]:
    """Production conversion pinned to exact official events and LF/CRLF masks."""

    return _derive_source_crosswalk_files(
        raw_events_path,
        cyclemask_path,
        _OFFICIAL_UZH_EVENTS_SHA256,
        tuple(digest for _, digest in _OFFICIAL_CYCLEMASK_SHA256),
        expected_raw_size=_OFFICIAL_UZH_EVENTS_SIZE_BYTES,
    )


# A descriptive spelling for callers that treat the operation as construction.
build_source_crosswalk = derive_source_crosswalk


__all__ = [
    "BIN_NS",
    "BIN_SECONDS",
    "MAX_CYCLEMASK_BYTES",
    "MAX_PATCH_SLOTS",
    "MAX_RAW_EVENTS_BYTES",
    "PATCH_X_MAX",
    "PATCH_X_MIN",
    "PATCH_Y_MAX",
    "PATCH_Y_MIN",
    "SourceCrosswalkError",
    "SourceCrosswalkEvent",
    "build_source_crosswalk",
    "derive_source_crosswalk",
    "derive_source_crosswalk_files",
    "derive_official_uzh_source_crosswalk_files",
]
