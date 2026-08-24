"""Fail-closed reader for the sealed Cluster2 native outcome bundle.

This module is deliberately self-contained and Python 3.8 compatible.  It
binds the receipt, compressed bundle, member set, member digests, cyclemask,
native ledger, and canonical transport JSONL before returning latency rows.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import zlib
from typing import Dict, List, Mapping, Sequence, Tuple


RECEIPT_SCHEMA = "redred.cluster2_cav_bridge.server_native_observation_receipt/v1"
LEDGER_SCHEMA = "redred.cluster2_cav_bridge.native_ledger/v1"
TRANSPORT_OUTCOME_SCHEMA = "redred.cluster2_cav_bridge.transport_outcome/v1"
SEALED_RECEIPT_RELATIVE_PATH = (
    "benchmarks/redred_cluster2_cav_bridge/server_native_observation_receipt.json"
)
SEALED_RECEIPT_SHA256 = (
    "b619b50713a56f68043ad661e4a97a00d5f380b4aca40c37cb530216e3b36776"
)
SEALED_BUNDLE_RELATIVE_PATH = (
    "benchmarks/redred_cluster2_cav_bridge/evidence/"
    "server_native_observation_ca446aa.tgz"
)
SEALED_BUNDLE_SHA256 = (
    "63565e95dfc29bbc651e206a49a55dcd0ef15b37b5aec903d8ff89c458021460"
)

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_TAR_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_TAR_HEADER_BYTES = 64 * 1024
GZIP_INPUT_CHUNK_BYTES = 64 * 1024
MAX_RECORDS = 1_000_000
MAX_CYCLE = (1 << 63) - 1
NATIVE_DRAIN_LIMIT = 100_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_UINT = re.compile(r"0|[1-9][0-9]*\Z")
_CYCLEMASK_LINE = re.compile(r"(0|[1-9][0-9]*) ([0-9a-f]{4})\Z")
_RECEIPT_FIELDS = frozenset((
    "artifact_bundle", "artifact_digests", "authority", "counts",
    "execution", "input_authority", "invariants", "post_run_observations",
    "schema", "scope", "server_reported_completed_at",
))
_COUNT_FIELDS = frozenset((
    "delivered", "generated", "native_ledger_lines", "overrun",
    "transport_outcome_rows",
))
_INPUT_AUTHORITY_FIELDS = frozenset(("code_files", "cyclemask"))
_CYCLEMASK_AUTHORITY_FIELDS = frozenset((
    "canonical_semantic_lf_sha256", "line_endings", "path", "raw_sha256",
))
_OUTCOME_FIELDS = frozenset((
    "schema", "event_id", "source_index", "occurrence_cycle", "outcome",
    "retire_cycle", "retire_native_lane", "retire_row", "retire_col",
))
_MEMBERS = (
    "native_ledger.psv",
    "transport_outcomes.jsonl",
    "run.log",
    "xrun.log",
    "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt",
    "faer_snapshot/rtl/arbiter2.v",
    "faer_snapshot/rtl/arbiter4_tree.v",
    "faer_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v",
    "bridge_snapshot/redred_cluster2_native_observational_tb.sv",
    "owner-native-ca446aa.stdout",
    "owner-native-ca446aa.exitcode",
    "owner-bridge-commit-ca446aa.txt",
)
_CYCLEMASK_SOURCE_PATH = "common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
_CYCLEMASK_MEMBER = "faer_snapshot/" + _CYCLEMASK_SOURCE_PATH
_LEGAL_TWO_VALID_ROW_PAIRS = frozenset((
    (0, 3), (1, 0), (1, 2), (1, 3), (2, 0), (2, 3),
))
_DIGEST_MEMBER_BY_FIELD = {
    "bridge_commit_txt_sha256": "owner-bridge-commit-ca446aa.txt",
    "compiled_arbiter2_sha256": "faer_snapshot/rtl/arbiter2.v",
    "compiled_arbiter4_tree_sha256": "faer_snapshot/rtl/arbiter4_tree.v",
    "compiled_cluster2_steal_buf_rtl_sha256": (
        "faer_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v"
    ),
    "native_ledger_psv_sha256": "native_ledger.psv",
    "cyclemask_input_sha256": (
        "faer_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
    ),
    "outer_runner_exitcode_sha256": "owner-native-ca446aa.exitcode",
    "outer_runner_stdout_sha256": "owner-native-ca446aa.stdout",
    "observational_tb_sha256": (
        "bridge_snapshot/redred_cluster2_native_observational_tb.sv"
    ),
    "run_log_sha256": "run.log",
    "transport_outcomes_jsonl_sha256": "transport_outcomes.jsonl",
    "xrun_log_sha256": "xrun.log",
}


class NativeOutcomeBundleError(ValueError):
    """The receipt, bundle, or native event identities are inconsistent."""


@dataclass(frozen=True)
class NativeOutcome:
    """One delivered native event with transport latency in native cycles."""

    event_id: int
    source: int
    occurrence_cycle: int
    retire_cycle: int
    latency: int

    @property
    def source_index(self) -> int:
        """Expose the sealed JSONL name for ``source``."""

        return self.source

    @property
    def latency_cycles(self) -> int:
        """Expose the unit carried by the cycle-domain ``latency`` field."""

        return self.latency

    def to_mapping(self) -> Mapping[str, int]:
        """Return exactly the requested native outcome projection."""

        return {
            "event_id": self.event_id,
            "source": self.source,
            "occurrence_cycle": self.occurrence_cycle,
            "retire_cycle": self.retire_cycle,
            "latency": self.latency,
        }


@dataclass(frozen=True)
class _LedgerRecord:
    event_id: int
    source_index: int
    occurrence_cycle: int
    outcome: str
    retire_cycle: object
    retire_native_lane: object
    retire_row: object
    retire_col: object

    def identity(self) -> Tuple[object, ...]:
        return (
            self.event_id,
            self.source_index,
            self.occurrence_cycle,
            self.outcome,
            self.retire_cycle,
            self.retire_native_lane,
            self.retire_row,
            self.retire_col,
        )


def _fail(message: str) -> None:
    raise NativeOutcomeBundleError(message)


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("%s must be a lowercase full SHA-256" % where)
    return value  # type: ignore[return-value]


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_CYCLE:
        _fail("%s must be an integer in [0, %d]" % (where, MAX_CYCLE))
    return value  # type: ignore[return-value]


def _exact_mapping(
    value: object, fields: frozenset, where: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        _fail("%s field schema differs" % where)
    return value  # type: ignore[return-value]


def _relative_path(value: object, where: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        _fail("%s must be a normalized relative POSIX path" % where)
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ("", ".", "..") for part in path.parts
    ):
        _fail("%s must be a normalized relative POSIX path" % where)
    return value  # type: ignore[return-value]


def _unique_object(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result = {}  # type: Dict[str, object]
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    _fail("non-finite JSON number: %s" % token)


def _strict_json(payload: bytes, where: str) -> object:
    try:
        text = payload.decode("ascii", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except NativeOutcomeBundleError:
        raise
    except (UnicodeError, TypeError, ValueError) as error:
        raise NativeOutcomeBundleError("%s is not strict ASCII JSON" % where) from error
    return value


def _canonical_json_line(value: object) -> bytes:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise NativeOutcomeBundleError("JSON row is not canonicalizable") from error
    return (payload + "\n").encode("ascii")


def _read_regular(root: Path, relative: str, limit: int, where: str) -> bytes:
    normalized = _relative_path(relative, where + " path")
    try:
        root_path = Path(root).resolve(strict=True)
        if not root_path.is_dir():
            _fail("repository root must be a directory")
        path = root_path / Path(*PurePosixPath(normalized).parts)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_path)
    except NativeOutcomeBundleError:
        raise
    except (OSError, ValueError) as error:
        raise NativeOutcomeBundleError("%s is unavailable or escapes root" % where) from error
    current = root_path
    for part in resolved.relative_to(root_path).parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                _fail("%s path contains a symlink" % where)
        except OSError as error:
            raise NativeOutcomeBundleError("cannot inspect %s path" % where) from error
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(resolved), flags)
    except OSError as error:
        raise NativeOutcomeBundleError("cannot open %s" % where) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            _fail("%s must be a bounded regular file" % where)
        chunks = []  # type: List[bytes]
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
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
        _fail("%s changed while being captured" % where)
    if len(payload) > limit:
        _fail("%s exceeds byte limit" % where)
    return payload


def _validate_receipt(value: object) -> Mapping[str, object]:
    receipt = _exact_mapping(value, _RECEIPT_FIELDS, "native receipt")
    if receipt["schema"] != RECEIPT_SCHEMA:
        _fail("native receipt schema differs")
    bundle = _exact_mapping(
        receipt["artifact_bundle"], frozenset(("path", "sha256")),
        "artifact_bundle",
    )
    if _relative_path(bundle["path"], "artifact bundle") != SEALED_BUNDLE_RELATIVE_PATH:
        _fail("sealed artifact bundle path differs")
    _sha256(bundle["sha256"], "artifact bundle authority")

    digests = _exact_mapping(
        receipt["artifact_digests"],
        frozenset(_DIGEST_MEMBER_BY_FIELD),
        "artifact_digests",
    )
    for field, digest in digests.items():
        _sha256(digest, "artifact digest %s" % field)

    input_authority = _exact_mapping(
        receipt["input_authority"],
        _INPUT_AUTHORITY_FIELDS,
        "input_authority",
    )
    code_files = input_authority["code_files"]
    if not isinstance(code_files, list) or not code_files:
        _fail("input_authority.code_files must be a non-empty list")
    cyclemask = _exact_mapping(
        input_authority["cyclemask"],
        _CYCLEMASK_AUTHORITY_FIELDS,
        "input_authority.cyclemask",
    )
    if (
        _relative_path(cyclemask["path"], "cyclemask authority")
        != _CYCLEMASK_SOURCE_PATH
        or cyclemask["line_endings"] not in ("LF", "CRLF")
    ):
        _fail("cyclemask path or line-ending authority differs")
    _sha256(cyclemask["raw_sha256"], "cyclemask raw authority")
    _sha256(
        cyclemask["canonical_semantic_lf_sha256"],
        "cyclemask semantic LF authority",
    )

    counts = _exact_mapping(receipt["counts"], _COUNT_FIELDS, "counts")
    checked_counts = dict(
        (field, _nonnegative_int(counts[field], "counts.%s" % field))
        for field in _COUNT_FIELDS
    )
    generated = checked_counts["generated"]
    if (
        generated == 0
        or checked_counts["delivered"] != generated
        or checked_counts["overrun"] != 0
        or checked_counts["transport_outcome_rows"] != generated
        or checked_counts["native_ledger_lines"] != generated + 2
    ):
        _fail("sealed receipt counts or zero-overrun partition differs")

    authority = _exact_mapping(
        receipt["authority"],
        frozenset((
            "bridge_commit", "ganghee_content_provenance_commit", "mode",
            "repository", "source_repository",
        )),
        "authority",
    )
    if (
        type(authority["bridge_commit"]) is not str
        or _SHA1.fullmatch(authority["bridge_commit"]) is None
        or authority["mode"] != "FILE_BYTES_AUTHORITY"
    ):
        _fail("native receipt authority differs")

    execution = receipt["execution"]
    if not isinstance(execution, Mapping):
        _fail("execution must be an object")
    if (
        execution.get("status") != "PASS"
        or type(execution.get("errors")) is not int
        or execution.get("errors") != 0
        or type(execution.get("fatals")) is not int
        or execution.get("fatals") != 0
    ):
        _fail("native execution did not close PASS with zero errors/fatals")

    invariants = receipt["invariants"]
    if not isinstance(invariants, Mapping):
        _fail("invariants must be an object")
    required_true = (
        "conservation_holds", "event_id_partition_exact",
        "native_lane_row_bitmap_rules_hold", "no_duplicate_retirement",
        "no_phantom_retirement", "per_source_fifo_order_holds",
        "pre_edge_overrun_rule_holds",
    )
    if any(type(invariants.get(field)) is not bool or not invariants[field]
           for field in required_true):
        _fail("native receipt invariant claims differ")
    return receipt


def _bounded_gzip_tar(payload: bytes) -> bytes:
    if not payload or len(payload) > MAX_FILE_BYTES:
        _fail("gzip artifact bundle must be non-empty and bounded")
    output = []  # type: List[bytes]
    produced_bytes = 0
    offset = 0
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        while offset < len(payload):
            if decompressor.eof:
                _fail("gzip artifact bundle contains trailing or multiple streams")
            pending = payload[offset:offset + GZIP_INPUT_CHUNK_BYTES]
            offset += len(pending)
            while pending:
                prior_length = len(pending)
                produced = decompressor.decompress(
                    pending,
                    MAX_DECOMPRESSED_TAR_BYTES - produced_bytes + 1,
                )
                if produced:
                    output.append(produced)
                    produced_bytes += len(produced)
                    if produced_bytes > MAX_DECOMPRESSED_TAR_BYTES:
                        _fail("decompressed tar exceeds explicit byte limit")
                pending = decompressor.unconsumed_tail
                if decompressor.unused_data:
                    _fail("gzip artifact bundle contains trailing or multiple streams")
                if decompressor.eof:
                    if pending or offset != len(payload):
                        _fail(
                            "gzip artifact bundle contains trailing or multiple streams"
                        )
                    break
                if pending and len(pending) >= prior_length and not produced:
                    _fail("gzip artifact bundle decompressor made no progress")

        flushed = decompressor.flush(
            MAX_DECOMPRESSED_TAR_BYTES - produced_bytes + 1
        )
        if flushed:
            output.append(flushed)
            produced_bytes += len(flushed)
        if produced_bytes > MAX_DECOMPRESSED_TAR_BYTES:
            _fail("decompressed tar exceeds explicit byte limit")
        if (
            not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            _fail("gzip artifact bundle is truncated or malformed")
        raw_tar = b"".join(output)
    except NativeOutcomeBundleError:
        raise
    except MemoryError as error:
        raise NativeOutcomeBundleError(
            "gzip artifact bundle exhausted bounded memory"
        ) from error
    except (OverflowError, ValueError, zlib.error) as error:
        raise NativeOutcomeBundleError(
            "gzip artifact bundle is malformed"
        ) from error
    if not raw_tar or len(raw_tar) != produced_bytes:
        _fail("decompressed tar byte accounting differs")
    return raw_tar


def _validate_raw_tar_eoa(
    raw_tar: bytes, last_data_end: int, last_padded_end: int
) -> None:
    block_size = tarfile.BLOCKSIZE
    if (
        len(raw_tar) % block_size != 0
        or last_data_end < 0
        or last_padded_end < last_data_end
        or last_padded_end % block_size != 0
        or last_padded_end > len(raw_tar)
    ):
        _fail("raw tar EOA/padding differs")
    if any(raw_tar[last_data_end:last_padded_end]):
        _fail("raw tar EOA/padding differs")
    trailing = raw_tar[last_padded_end:]
    if (
        len(trailing) < 2 * block_size
        or len(trailing) % block_size != 0
        or any(trailing)
    ):
        _fail("raw tar EOA/padding differs")


def _validate_raw_tar_member_padding(
    raw_tar: bytes, data_end: int, padded_end: int
) -> None:
    if (
        data_end < 0
        or padded_end < data_end
        or padded_end % tarfile.BLOCKSIZE != 0
        or padded_end > len(raw_tar)
        or any(raw_tar[data_end:padded_end])
    ):
        _fail("raw tar member padding differs")


def _read_bundle_members(payload: bytes) -> Dict[str, bytes]:
    artifacts = {}  # type: Dict[str, bytes]
    try:
        raw_tar = _bounded_gzip_tar(payload)
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r|") as archive:
            expanded = 0
            last_data_end = -1
            last_padded_end = -1
            for expected_name in _MEMBERS:
                member = archive.next()
                if member is None or member.name != expected_name:
                    _fail("native bundle member set/order differs")
                header_bytes = member.offset_data - member.offset
                if (
                    not member.isfile()
                    or _relative_path(member.name, "tar member") != member.name
                    or type(member.size) is not int
                    or member.size < 0
                    or member.size > MAX_MEMBER_BYTES
                    or type(header_bytes) is not int
                    or header_bytes < tarfile.BLOCKSIZE
                    or header_bytes > MAX_TAR_HEADER_BYTES
                    or header_bytes % tarfile.BLOCKSIZE != 0
                ):
                    _fail("native bundle member header/type/size differs")
                expanded += member.size
                if expanded > MAX_EXPANDED_BYTES:
                    _fail("native bundle expanded byte limit exceeded")
                stream = archive.extractfile(member)
                if stream is None:
                    _fail("native bundle member is unreadable")
                try:
                    member_payload = stream.read(MAX_MEMBER_BYTES + 1)
                finally:
                    stream.close()
                if len(member_payload) != member.size:
                    _fail("native bundle member size differs")
                data_end = member.offset_data + member.size
                padded_end = (
                    (data_end + tarfile.BLOCKSIZE - 1)
                    // tarfile.BLOCKSIZE
                    * tarfile.BLOCKSIZE
                )
                _validate_raw_tar_member_padding(
                    raw_tar, data_end, padded_end
                )
                artifacts[member.name] = member_payload
                last_data_end = data_end
                last_padded_end = padded_end
            if archive.next() is not None:
                _fail("native bundle contains an extra member")
            _validate_raw_tar_eoa(
                raw_tar, last_data_end, last_padded_end
            )
    except NativeOutcomeBundleError:
        raise
    except MemoryError as error:
        raise NativeOutcomeBundleError(
            "bounded raw tar exhausted memory"
        ) from error
    except (EOFError, OSError, tarfile.TarError) as error:
        raise NativeOutcomeBundleError("artifact bundle is not a valid raw tar") from error
    return artifacts


def _validate_member_digests(
    receipt: Mapping[str, object], artifacts: Mapping[str, bytes]
) -> None:
    digests = receipt["artifact_digests"]
    assert isinstance(digests, Mapping)
    for field, member in _DIGEST_MEMBER_BY_FIELD.items():
        if hashlib.sha256(artifacts[member]).hexdigest() != digests[field]:
            _fail("artifact member digest differs for %s" % member)
    authority = receipt["authority"]
    assert isinstance(authority, Mapping)
    expected_commit = authority["bridge_commit"]
    if artifacts["owner-bridge-commit-ca446aa.txt"] != (
        str(expected_commit) + "\n"
    ).encode("ascii"):
        _fail("bundled bridge commit authority differs")


def _cyclemask_encoding(payload: bytes) -> Tuple[str, str, str, bytes]:
    if not payload or len(payload) > MAX_MEMBER_BYTES:
        _fail("cyclemask must be non-empty and bounded")
    if b"\r\n" in payload:
        without_crlf = payload.replace(b"\r\n", b"")
        if (
            b"\r" in without_crlf
            or b"\n" in without_crlf
            or not payload.endswith(b"\r\n")
        ):
            _fail("cyclemask contains mixed or malformed line endings")
        line_endings = "CRLF"
        canonical_lf = payload.replace(b"\r\n", b"\n")
    else:
        if b"\r" in payload or not payload.endswith(b"\n"):
            _fail("cyclemask contains mixed or malformed line endings")
        line_endings = "LF"
        canonical_lf = payload
    _strict_lines(canonical_lf, "cyclemask semantic LF")
    return (
        line_endings,
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(canonical_lf).hexdigest(),
        canonical_lf,
    )


def _validate_cyclemask_authority(
    receipt: Mapping[str, object], artifacts: Mapping[str, bytes]
) -> None:
    input_authority = receipt["input_authority"]
    assert isinstance(input_authority, Mapping)
    cyclemask = input_authority["cyclemask"]
    assert isinstance(cyclemask, Mapping)
    observed = _cyclemask_encoding(artifacts[_CYCLEMASK_MEMBER])
    expected = (
        cyclemask["line_endings"],
        cyclemask["raw_sha256"],
        cyclemask["canonical_semantic_lf_sha256"],
    )
    if observed[:3] != expected:
        _fail("cyclemask member differs from receipt input authority")


def _strict_lines(payload: bytes, where: str) -> Tuple[str, ...]:
    if not payload or len(payload) > MAX_MEMBER_BYTES:
        _fail("%s must be non-empty and bounded" % where)
    if b"\r" in payload or not payload.endswith(b"\n"):
        _fail("%s must use uniform terminal LF" % where)
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise NativeOutcomeBundleError("%s must be ASCII" % where) from error
    lines = tuple(text[:-1].split("\n"))
    if not lines or any(not line for line in lines):
        _fail("%s contains a blank line" % where)
    return lines


def _uint_token(token: str, where: str) -> int:
    if _UINT.fullmatch(token) is None:
        _fail("%s is not a canonical unsigned integer" % where)
    return _nonnegative_int(int(token), where)


def _derive_occurrences(cyclemask: bytes) -> Tuple[Tuple[int, int, int], ...]:
    occurrences = []  # type: List[Tuple[int, int, int]]
    previous_cycle = -1
    canonical_lf = _cyclemask_encoding(cyclemask)[3]
    for line_number, line in enumerate(
        _strict_lines(canonical_lf, "cyclemask semantic LF"), 1
    ):
        match = _CYCLEMASK_LINE.fullmatch(line)
        if match is None:
            _fail("cyclemask line %d is not canonical" % line_number)
        cycle = _uint_token(match.group(1), "cyclemask cycle")
        mask = int(match.group(2), 16)
        if cycle <= previous_cycle or mask == 0:
            _fail("cyclemask cycles/bitmaps are invalid")
        previous_cycle = cycle
        for source_index in range(16):
            if mask & (1 << source_index):
                occurrences.append((len(occurrences), source_index, cycle))
                if len(occurrences) > MAX_RECORDS:
                    _fail("cyclemask occurrence count exceeds limit")
    return tuple(occurrences)


def _parse_transport_outcomes(payload: bytes) -> Tuple[Mapping[str, object], ...]:
    rows = []  # type: List[Mapping[str, object]]
    previous_retire_by_source = {}  # type: Dict[int, int]
    retire_slots = set()
    for line_number, line in enumerate(
        _strict_lines(payload, "transport outcomes"), 1
    ):
        raw_line = (line + "\n").encode("ascii")
        row = _exact_mapping(
            _strict_json(raw_line, "transport outcome line %d" % line_number),
            _OUTCOME_FIELDS,
            "transport outcome line %d" % line_number,
        )
        if _canonical_json_line(row) != raw_line:
            _fail("transport outcome line %d is not canonical JSONL" % line_number)
        event_id = _nonnegative_int(row["event_id"], "event_id")
        if event_id != len(rows):
            _fail("transport event IDs must be contiguous in source order")
        source = _nonnegative_int(row["source_index"], "source_index")
        occurrence = _nonnegative_int(row["occurrence_cycle"], "occurrence_cycle")
        retire = _nonnegative_int(row["retire_cycle"], "retire_cycle")
        lane = _nonnegative_int(row["retire_native_lane"], "retire_native_lane")
        native_row = _nonnegative_int(row["retire_row"], "retire_row")
        column = _nonnegative_int(row["retire_col"], "retire_col")
        if row["schema"] != TRANSPORT_OUTCOME_SCHEMA or row["outcome"] != "DELIVERED":
            _fail("sealed transport outcome must be DELIVERED v1")
        if source > 15 or lane not in (0, 1) or native_row > 3 or column > 3:
            _fail("native source/retirement coordinate is out of range")
        if source != native_row * 4 + column:
            _fail("native retirement coordinate differs from source_index")
        if (lane == 0 and native_row not in (0, 1, 2)) or (
            lane == 1 and native_row not in (0, 2, 3)
        ):
            _fail("native lane cannot emit the observed row")
        if retire <= occurrence:
            _fail("native retirement must follow occurrence")
        previous = previous_retire_by_source.get(source)
        if previous is not None and retire <= previous:
            _fail("per-source retire cycles must be strictly increasing")
        previous_retire_by_source[source] = retire
        slot = (retire, lane, column)
        if slot in retire_slots:
            _fail("native retire cycle/lane/column slot is duplicated")
        retire_slots.add(slot)
        rows.append(row)
        if len(rows) > MAX_RECORDS:
            _fail("transport outcome record count exceeds limit")
    return tuple(rows)


def _ledger_rows(payload: bytes) -> Tuple[
    Tuple[_LedgerRecord, ...],
    Dict[int, _LedgerRecord],
    Tuple[int, int, int],
]:
    lines = _strict_lines(payload, "native ledger")
    if len(lines) < 2 or lines[0] != "SCHEMA|" + LEDGER_SCHEMA:
        _fail("native ledger schema header differs")
    summary = lines[-1].split("|")
    if len(summary) != 4 or summary[0] != "SUMMARY":
        _fail("native ledger summary differs")
    counts = tuple(
        _uint_token(token, "native ledger summary") for token in summary[1:]
    )
    records = []  # type: List[_LedgerRecord]
    result = {}  # type: Dict[int, _LedgerRecord]
    observation_keys = []  # type: List[Tuple[int, int, int, int, int]]
    rows_by_cycle_lane = {}  # type: Dict[Tuple[int, int], int]
    retire_slots = set()
    delivered_per_cycle = {}  # type: Dict[int, int]
    for line_number, line in enumerate(lines[1:-1], 2):
        fields = line.split("|")
        if len(fields) != 9 or fields[0] != "EVENT":
            _fail("native ledger line %d schema differs" % line_number)
        values = tuple(
            _uint_token(token, "native ledger line %d" % line_number)
            for token in fields[1:4]
        )
        event_id, source, occurrence = values
        if event_id in result or source > 15:
            _fail("native ledger IDs/sources differ")
        outcome = fields[4]
        if outcome == "DELIVERED":
            retire_values = tuple(
                _uint_token(token, "native ledger retire field")
                for token in fields[5:9]
            )
            retire, lane, native_row, column = retire_values
            if (
                lane not in (0, 1)
                or native_row > 3
                or column > 3
                or source != native_row * 4 + column
                or occurrence > retire
            ):
                _fail("native ledger retirement coordinate/time differs")
            if (lane == 0 and native_row not in (0, 1, 2)) or (
                lane == 1 and native_row not in (0, 2, 3)
            ):
                _fail("native ledger lane cannot emit the observed row")
            record = _LedgerRecord(
                event_id, source, occurrence, outcome,
                retire, lane, native_row, column,
            )
            bitmap_key = (retire, lane)
            prior_row = rows_by_cycle_lane.get(bitmap_key)
            if prior_row is not None and prior_row != native_row:
                _fail("one native lane-cycle selects multiple rows")
            other_row = rows_by_cycle_lane.get((retire, 1 - lane))
            if other_row == native_row:
                _fail("two native lanes select the same row in one cycle")
            rows_by_cycle_lane[bitmap_key] = native_row
            slot = (retire, lane, column)
            if slot in retire_slots:
                _fail("native retire cycle/lane/column slot is duplicated")
            retire_slots.add(slot)
            delivered_per_cycle[retire] = delivered_per_cycle.get(retire, 0) + 1
            if delivered_per_cycle[retire] > 8:
                _fail("more than eight native events retire in one cycle")
            observation_keys.append((retire, 1, lane, column, event_id))
        elif outcome == "OVERRUN":
            if fields[5:9] != ["-", "-", "-", "-"]:
                _fail("OVERRUN ledger retire fields must all be null")
            record = _LedgerRecord(
                event_id, source, occurrence, outcome,
                None, None, None, None,
            )
            observation_keys.append((occurrence, 0, source, 0, event_id))
        else:
            _fail("native ledger outcome differs")
        records.append(record)
        result[event_id] = record
        if len(records) > MAX_RECORDS:
            _fail("native ledger record count exceeds limit")

    if observation_keys != sorted(observation_keys):
        _fail("native ledger rows are not in deterministic observation order")
    lane_rows_by_cycle = {}  # type: Dict[int, Dict[int, int]]
    for (cycle, lane), native_row in rows_by_cycle_lane.items():
        lane_rows_by_cycle.setdefault(cycle, {})[lane] = native_row
    for lane_rows in lane_rows_by_cycle.values():
        if set(lane_rows) == {0, 1}:
            pair = (lane_rows[0], lane_rows[1])
            if pair not in _LEGAL_TWO_VALID_ROW_PAIRS:
                _fail("native two-valid row pair is impossible")
        elif set(lane_rows) == {0} and lane_rows[0] not in (1, 2):
            _fail("native lane0-only row is impossible")
        elif set(lane_rows) == {1} and lane_rows[1] not in (0, 3):
            _fail("native lane1-only row is impossible")

    delivered_count = sum(record.outcome == "DELIVERED" for record in records)
    overrun_count = len(records) - delivered_count
    if counts != (len(records), delivered_count, overrun_count):
        _fail("native ledger count/conservation summary differs")
    return tuple(records), result, counts  # type: ignore[return-value]


def _replay_native_ledger(
    occurrences: Tuple[Tuple[int, int, int], ...],
    records: Tuple[_LedgerRecord, ...],
    records_by_id: Mapping[int, _LedgerRecord],
) -> None:
    expected_by_id = dict((row[0], row) for row in occurrences)
    if set(records_by_id) != set(expected_by_id):
        _fail("native ledger does not exactly partition cyclemask event IDs")
    for event_id, source, occurrence in occurrences:
        record = records_by_id[event_id]
        if (
            record.source_index != source
            or record.occurrence_cycle != occurrence
        ):
            _fail("native ledger ID/source/occurrence differs from cyclemask")

    latest_occurrence = max(row[2] for row in occurrences)
    for record in records:
        if (
            type(record.retire_cycle) is int
            and record.retire_cycle > latest_occurrence + NATIVE_DRAIN_LIMIT
        ):
            _fail("native ledger retirement exceeds bounded drain")

    occurrences_by_cycle = {}  # type: Dict[int, List[Tuple[int, int, int]]]
    deliveries_by_cycle = {}  # type: Dict[int, List[_LedgerRecord]]
    for occurrence in occurrences:
        occurrences_by_cycle.setdefault(occurrence[2], []).append(occurrence)
    for record in records:
        if record.outcome == "DELIVERED":
            assert isinstance(record.retire_cycle, int)
            deliveries_by_cycle.setdefault(record.retire_cycle, []).append(record)

    queues = [[] for _ in range(16)]  # type: List[List[int]]
    cycles = sorted(set(occurrences_by_cycle) | set(deliveries_by_cycle))
    for cycle in cycles:
        current_occurrences = occurrences_by_cycle.get(cycle, [])
        for event_id, source, _ in current_occurrences:
            record = records_by_id[event_id]
            is_full = len(queues[source]) == 2
            if is_full != (record.outcome == "OVERRUN"):
                _fail("native ledger overrun differs from pre-edge depth-2 state")

        current_deliveries = sorted(
            deliveries_by_cycle.get(cycle, []),
            key=lambda record: (
                record.retire_native_lane,
                record.retire_col,
                record.event_id,
            ),
        )
        for record in current_deliveries:
            queue = queues[record.source_index]
            if not queue or queue[0] != record.event_id:
                _fail("native ledger contains phantom retirement or FIFO reorder")
            queue.pop(0)
        for event_id, source, _ in current_occurrences:
            if records_by_id[event_id].outcome == "DELIVERED":
                queues[source].append(event_id)
                if len(queues[source]) > 2:
                    _fail("native ledger exceeds per-source depth two")
    if any(queues):
        _fail("native ledger drain is incomplete")


def _cross_validate(
    receipt: Mapping[str, object], artifacts: Mapping[str, bytes]
) -> Tuple[NativeOutcome, ...]:
    rows = _parse_transport_outcomes(artifacts["transport_outcomes.jsonl"])
    _validate_cyclemask_authority(receipt, artifacts)
    occurrences = _derive_occurrences(artifacts[_CYCLEMASK_MEMBER])
    if len(rows) != len(occurrences):
        _fail("cyclemask and transport population sizes differ")
    ledger_records, ledger_by_id, ledger_counts = _ledger_rows(
        artifacts["native_ledger.psv"]
    )
    if len(ledger_by_id) != len(rows) or set(ledger_by_id) != set(range(len(rows))):
        _fail("native ledger does not exactly partition transport event IDs")
    _replay_native_ledger(occurrences, ledger_records, ledger_by_id)

    outcomes = []  # type: List[NativeOutcome]
    for row, expected in zip(rows, occurrences):
        event_id = row["event_id"]
        assert isinstance(event_id, int)
        identity = (row["event_id"], row["source_index"], row["occurrence_cycle"])
        if identity != expected:
            _fail("transport ID/source/occurrence differs from cyclemask")
        ledger_identity = (
            row["event_id"], row["source_index"], row["occurrence_cycle"],
            row["outcome"], row["retire_cycle"], row["retire_native_lane"],
            row["retire_row"], row["retire_col"],
        )
        if ledger_by_id[event_id].identity() != ledger_identity:
            _fail("transport outcome differs from native ledger")
        occurrence = row["occurrence_cycle"]
        retire = row["retire_cycle"]
        assert isinstance(occurrence, int) and isinstance(retire, int)
        outcomes.append(NativeOutcome(
            event_id,
            row["source_index"],  # type: ignore[arg-type]
            occurrence,
            retire,
            retire - occurrence,
        ))

    counts = receipt["counts"]
    assert isinstance(counts, Mapping)
    expected_count = counts["generated"]
    if len(outcomes) != expected_count or ledger_counts != (
        expected_count, expected_count, 0
    ):
        _fail("receipt, ledger, and outcome counts differ")
    return tuple(outcomes)


def load_native_outcome_bundle(
    repository_root: Path,
    receipt_relative_path: str,
    receipt_authority_sha256: str,
) -> Tuple[NativeOutcome, ...]:
    """Load a digest-authorized native receipt and return delivered outcomes."""

    expected_receipt = _sha256(
        receipt_authority_sha256, "caller receipt authority"
    )
    receipt_payload = _read_regular(
        Path(repository_root), receipt_relative_path, MAX_FILE_BYTES, "native receipt"
    )
    if hashlib.sha256(receipt_payload).hexdigest() != expected_receipt:
        _fail("native receipt SHA-256 differs from caller authority")
    receipt = _validate_receipt(_strict_json(receipt_payload, "native receipt"))
    bundle_row = receipt["artifact_bundle"]
    assert isinstance(bundle_row, Mapping)
    bundle_payload = _read_regular(
        Path(repository_root),
        bundle_row["path"],  # type: ignore[arg-type]
        MAX_FILE_BYTES,
        "artifact bundle",
    )
    if hashlib.sha256(bundle_payload).hexdigest() != bundle_row["sha256"]:
        _fail("artifact bundle SHA-256 differs from receipt")
    artifacts = _read_bundle_members(bundle_payload)
    _validate_member_digests(receipt, artifacts)
    return _cross_validate(receipt, artifacts)


def load_abaa094_native_outcomes(repository_root: Path) -> Tuple[NativeOutcome, ...]:
    """Load the exact outcome evidence sealed at repository commit ``abaa094``."""

    outcomes = load_native_outcome_bundle(
        repository_root,
        SEALED_RECEIPT_RELATIVE_PATH,
        SEALED_RECEIPT_SHA256,
    )
    # The receipt binds this too; retaining the explicit constant makes the
    # convenience entry point fail if its sealed evidence identity is refrozen.
    receipt_payload = _read_regular(
        Path(repository_root),
        SEALED_RECEIPT_RELATIVE_PATH,
        MAX_FILE_BYTES,
        "sealed native receipt",
    )
    receipt = _validate_receipt(_strict_json(receipt_payload, "sealed native receipt"))
    bundle = receipt["artifact_bundle"]
    assert isinstance(bundle, Mapping)
    if bundle["sha256"] != SEALED_BUNDLE_SHA256:
        _fail("abaa094 sealed bundle authority differs")
    return outcomes


__all__ = (
    "RECEIPT_SCHEMA",
    "LEDGER_SCHEMA",
    "TRANSPORT_OUTCOME_SCHEMA",
    "SEALED_RECEIPT_RELATIVE_PATH",
    "SEALED_RECEIPT_SHA256",
    "SEALED_BUNDLE_RELATIVE_PATH",
    "SEALED_BUNDLE_SHA256",
    "NativeOutcomeBundleError",
    "NativeOutcome",
    "load_native_outcome_bundle",
    "load_abaa094_native_outcomes",
)
