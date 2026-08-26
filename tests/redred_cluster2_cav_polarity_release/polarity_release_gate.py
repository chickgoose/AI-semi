#!/usr/bin/env python3
"""Fail-closed gate for an integrated Cluster2/CAV polarity-v1 release.

Release counts are facts reconstructed from the authoritative raw addr/polarity
trace and a cycle-complete native ledger.  Event-ID ledgers and predeclared
delivery/overrun counters are deliberately outside this authority contract.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "redred.cluster2_cav_polarity_release/v2"
RECEIPT_SCHEMA = "redred.cluster2_cav_polarity_receipt/v2"
INTEGRATION_SCHEMA = "redred.cluster2_cav_polarity_integration_authority/v2"
RAW_LEDGER_SCHEMA = "redred.cluster2_cav_bridge.polarity_native_ledger/v1"
IDENTITY_SCOPE = (
    "SOURCE_FIFO_POLARITY_SEQUENCE_ONLY;"
    "IDENTICAL_SAME_SOURCE_EQUAL_POLARITY_EVENTS_UNOBSERVABLE;"
    "EVENT_ID_ORDER_INDEPENDENCE_NOT_CLAIMED"
)
DUPLICATE_SCOPE = "REPEATED_RAW_CYCLE_OR_CYCLE_SOURCE_RETIREMENT"
VERIFIER_REFERENCE_COMMIT = "da329e368f1496d8b39481bef51548d98d153148"
SOURCE_COMMIT = "44f8918c6e0085f7b75bb90fbe6c099abe1882cc"
EXPECTED_GENERATED = 8503
EXPECTED_TOP = "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity"
EXPECTED_TB_TOP = "redred_cluster2_polarity_v1_native_observational_tb"
EXPECTED_SOURCE_REPOSITORY = "https://github.com/GangHeeJo/AI-SEMI"
EXPECTED_SOURCE_PATHS = (
    "rtl/arbiter2.v",
    "rtl/arbiter4_tree.v",
    "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
)
DEFAULT_MANIFEST = "benchmarks/redred_cluster2_cav_bridge/polarity_release_authority.json"

REQUIRED_ROLES = {
    "polarity_v1_rtl": "source",
    "polarity_v1_tb": "receipt",
    "polarity_v1_runner": "receipt",
    "polarity_v1_trace": "source",
    "polarity_v1_cycle_ledger": "receipt",
    "polarity_v1_independent_verifier": "receipt",
    "polarity_v1_receipt": "receipt",
    "integration_authority": "integration",
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ROLE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_FORBIDDEN_FILELIST = re.compile(r"[?*\[\]{}$`\\]")
_TRACE_LINE = re.compile(r"(0|[1-9][0-9]*) ([0-9a-f]{4}) ([0-9a-f]{4})\Z")
_UINT = re.compile(r"0|[1-9][0-9]*\Z")
_HEX4 = re.compile(r"[0-9a-f]{4}\Z")
_HEX1 = re.compile(r"[0-9a-f]\Z")
_BIT = re.compile(r"[01]\Z")
_ROW = re.compile(r"[0-3]\Z")
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_CYCLE = (1 << 63) - 1
NATIVE_DRAIN_LIMIT = 100_000


class ReleaseHold(ValueError):
    """Required release evidence is absent, ambiguous, or inconsistent."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ReleaseHold("value is not canonical-JSON serializable") from error
    return (encoded + "\n").encode("ascii")


def _unique_object(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseHold("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReleaseHold("non-finite JSON number: %s" % value)


def parse_canonical_json(data: bytes, where: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            data.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ReleaseHold:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ReleaseHold("%s is not canonical ASCII JSON" % where) from error
    if not isinstance(value, Mapping) or canonical_json(value) != data:
        raise ReleaseHold("%s is not byte-canonical JSON" % where)
    return value


def _exact(value: object, keys: Iterable[str], where: str) -> Mapping[str, object]:
    expected = frozenset(keys)
    if not isinstance(value, Mapping) or frozenset(value) != expected:
        raise ReleaseHold("%s keys differ" % where)
    return value


def _digest(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ReleaseHold("%s must be a full lowercase SHA-256" % where)
    return value


def _commit(value: object, where: str) -> str:
    if type(value) is not str or _COMMIT.fullmatch(value) is None:
        raise ReleaseHold("%s must be a full lowercase commit SHA" % where)
    return value


def _path(value: object, where: str) -> str:
    if (
        type(value) is not str or not value or value[0] in ("-", "+", "#")
        or any(character.isspace() or character == "\x00" for character in value)
        or _FORBIDDEN_FILELIST.search(value)
    ):
        raise ReleaseHold("%s must be one explicit normalized path" % where)
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or value != parsed.as_posix() or any(
        part in ("", ".", "..") for part in parsed.parts
    ):
        raise ReleaseHold("%s must be one explicit normalized path" % where)
    return value


def _regular_bytes(root: Path, relative: str, where: str) -> bytes:
    normalized = _path(relative, where)
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ReleaseHold("%s is unavailable" % where) from error
        if stat.S_ISLNK(mode):
            raise ReleaseHold("%s traverses a symlink" % where)
    try:
        data = candidate.read_bytes()
    except OSError as error:
        raise ReleaseHold("%s is unreadable" % where) from error
    if not candidate.is_file():
        raise ReleaseHold("%s is not a regular file" % where)
    return data


def _text_semantics(value: object, raw: bytes, where: str) -> bytes:
    semantics = _exact(value, ("line_endings", "semantic_lf_sha256"), where)
    expected = _digest(semantics["semantic_lf_sha256"], where + ".semantic_lf_sha256")
    ending = semantics["line_endings"]
    if ending == "LF":
        if b"\r" in raw or not raw.endswith(b"\n"):
            raise ReleaseHold("%s does not have canonical LF endings" % where)
        normalized = raw
    elif ending == "CRLF":
        remainder = raw.replace(b"\r\n", b"")
        if not raw.endswith(b"\r\n") or b"\r" in remainder or b"\n" in remainder:
            raise ReleaseHold("%s does not match declared CRLF semantics" % where)
        normalized = raw.replace(b"\r\n", b"\n")
    else:
        raise ReleaseHold("%s line_endings must be LF or CRLF" % where)
    if sha256(normalized) != expected:
        raise ReleaseHold("%s semantic LF hash differs" % where)
    return normalized


def _binding(value: object, where: str, with_role: bool = True) -> Mapping[str, object]:
    keys = ("path", "sha256", "text", "role", "scope") if with_role else (
        "path", "sha256", "text"
    )
    binding = _exact(value, keys, where)
    _path(binding["path"], where + ".path")
    _digest(binding["sha256"], where + ".sha256")
    if with_role:
        if type(binding["role"]) is not str or _ROLE.fullmatch(binding["role"]) is None:
            raise ReleaseHold("%s.role is invalid" % where)
        if binding["scope"] not in ("source", "receipt", "integration"):
            raise ReleaseHold("%s.scope is invalid" % where)
    return binding


def _capture_binding(root: Path, binding: Mapping[str, object], where: str) -> Tuple[bytes, bytes]:
    raw = _regular_bytes(root, str(binding["path"]), where)
    if sha256(raw) != binding["sha256"]:
        raise ReleaseHold("%s raw SHA-256 differs" % where)
    return raw, _text_semantics(binding["text"], raw, where + ".text")


def _safe_git(root: Path, *args: str) -> bytes:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "LANG": "C", "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode:
        raise ReleaseHold("git authority check failed: %s" % completed.stderr.decode(
            "utf-8", errors="replace").strip())
    return completed.stdout


def _git_blob(root: Path, commit: str, path: str) -> bytes:
    return _safe_git(root, "show", "%s:%s" % (commit, path))


def _trace_lines(payload: bytes) -> Tuple[Tuple[str, ...], str]:
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise ReleaseHold("addrpol trace must be non-empty and bounded")
    if b"\r\n" in payload:
        remainder = payload.replace(b"\r\n", b"")
        if b"\r" in remainder or b"\n" in remainder or not payload.endswith(b"\r\n"):
            raise ReleaseHold("addrpol trace has mixed or malformed line endings")
        normalized, endings = payload.replace(b"\r\n", b"\n"), "CRLF"
    else:
        if b"\r" in payload or not payload.endswith(b"\n"):
            raise ReleaseHold("addrpol trace has mixed or malformed line endings")
        normalized, endings = payload, "LF"
    try:
        lines = tuple(normalized.decode("ascii", errors="strict")[:-1].split("\n"))
    except UnicodeError as error:
        raise ReleaseHold("addrpol trace must be ASCII") from error
    if any(not line for line in lines) or len(lines) > MAX_RECORDS:
        raise ReleaseHold("addrpol trace has blank or excessive records")
    return lines, endings


def _ledger_lines(payload: bytes) -> Tuple[str, ...]:
    if not payload or len(payload) > MAX_INPUT_BYTES or not payload.endswith(b"\n"):
        raise ReleaseHold("raw polarity ledger must be non-empty, bounded, and LF-terminated")
    if b"\r" in payload:
        raise ReleaseHold("raw polarity ledger must use canonical LF endings")
    try:
        lines = tuple(payload.decode("ascii", errors="strict")[:-1].split("\n"))
    except UnicodeError as error:
        raise ReleaseHold("raw polarity ledger must be ASCII") from error
    if any(not line for line in lines) or len(lines) > MAX_RECORDS + 3:
        raise ReleaseHold("raw polarity ledger has blank or excessive records")
    return lines


def _uint(token: str, where: str) -> int:
    if _UINT.fullmatch(token) is None:
        raise ReleaseHold("%s is not a canonical unsigned integer" % where)
    value = int(token)
    if value > MAX_CYCLE:
        raise ReleaseHold("%s exceeds the supported bound" % where)
    return value


def _parse_trace(payload: bytes) -> Tuple[List[Tuple[int, int, int]], str]:
    lines, endings = _trace_lines(payload)
    occurrences: List[Tuple[int, int, int]] = []
    previous = -1
    for number, line in enumerate(lines, 1):
        match = _TRACE_LINE.fullmatch(line)
        if match is None:
            raise ReleaseHold("addrpol line %d is not canonical 'cycle addr_mask polarity_mask'" % number)
        cycle = _uint(match.group(1), "addrpol cycle")
        address, polarity = int(match.group(2), 16), int(match.group(3), 16)
        if cycle <= previous:
            raise ReleaseHold("addrpol cycles must be strictly increasing")
        if not address or polarity & ~address:
            raise ReleaseHold("addrpol address/polarity mask is invalid")
        previous = cycle
        for source in range(16):
            if address & (1 << source):
                occurrences.append((cycle, source, (polarity >> source) & 1))
                if len(occurrences) > MAX_RECORDS:
                    raise ReleaseHold("addrpol occurrence count exceeds limit")
    return occurrences, endings


def _parse_lane(fields: Sequence[str], offset: int, lane: int, number: int) -> Tuple[int, int, int, int]:
    valid, row, columns, polarity = fields[offset:offset + 4]
    if _BIT.fullmatch(valid) is None or _ROW.fullmatch(row) is None:
        raise ReleaseHold("cycle line %d lane%d valid/row is malformed" % (number, lane))
    if _HEX1.fullmatch(columns) is None or _HEX1.fullmatch(polarity) is None:
        raise ReleaseHold("cycle line %d lane%d masks are malformed" % (number, lane))
    parsed = (int(valid), int(row), int(columns, 16), int(polarity, 16))
    if not parsed[0] and any(parsed[1:]):
        raise ReleaseHold("invalid native lane must be canonical all-zero")
    # The pinned RTL registers the full four-bit pol_front_bus slice for the
    # selected row.  Bits outside col_mask are therefore observable but do not
    # represent retirements; only selected columns have polarity meaning.
    if parsed[0] and not parsed[2]:
        raise ReleaseHold("valid native lane has an empty column mask")
    return parsed


def _parse_ledger(payload: bytes) -> Tuple[List[Tuple[int, int, Tuple[int, int, int, int], Tuple[int, int, int, int]]], Tuple[int, ...]]:
    lines = _ledger_lines(payload)
    if len(lines) < 4 or lines[0] != "SCHEMA|" + RAW_LEDGER_SCHEMA:
        raise ReleaseHold("raw polarity ledger schema differs")
    if lines[1] != "SCOPE|" + IDENTITY_SCOPE:
        raise ReleaseHold("raw polarity ledger observational scope differs")
    summary = lines[-1].split("|")
    if len(summary) != 7 or summary[0] != "SUMMARY":
        raise ReleaseHold("raw polarity ledger summary is malformed")
    summary_values = tuple(_uint(token, "raw polarity ledger summary") for token in summary[1:])
    observations = []
    previous = -1
    for number, line in enumerate(lines[2:-1], 3):
        fields = line.split("|")
        if len(fields) != 11 or fields[0] != "CYCLE":
            raise ReleaseHold("raw cycle line %d is malformed" % number)
        cycle = _uint(fields[1], "raw observation cycle")
        if cycle != previous + 1:
            raise ReleaseHold("raw observations must contain each cycle exactly once starting at zero")
        if _HEX4.fullmatch(fields[2]) is None:
            raise ReleaseHold("raw pre-edge overrun mask is malformed")
        observations.append((
            cycle, int(fields[2], 16), _parse_lane(fields, 3, 0, number),
            _parse_lane(fields, 7, 1, number),
        ))
        previous = cycle
    if not observations:
        raise ReleaseHold("raw polarity ledger has no cycle observations")
    return observations, summary_values


def _validate_geometry(lane0: Tuple[int, int, int, int], lane1: Tuple[int, int, int, int]) -> None:
    if lane0[0] and lane0[1] not in (0, 1, 2):
        raise ReleaseHold("native lane0 selected an impossible row")
    if lane1[0] and lane1[1] not in (0, 2, 3):
        raise ReleaseHold("native lane1 selected an impossible row")
    if lane0[0] and lane1[0]:
        if (lane0[1], lane1[1]) not in {(0, 3), (1, 0), (1, 2), (1, 3), (2, 0), (2, 3)}:
            raise ReleaseHold("native lanes selected an impossible row pair")
    elif lane0[0] and lane0[1] not in (1, 2):
        raise ReleaseHold("native lane0-only row is impossible")
    elif lane1[0] and lane1[1] not in (0, 3):
        raise ReleaseHold("native lane1-only row is impossible")


def verify_raw_cycle_evidence(trace_payload: bytes, ledger_payload: bytes) -> Mapping[str, object]:
    """Replay da329e3 CYCLE framing with the pinned RTL's full-row polarity semantics."""
    occurrences, endings = _parse_trace(trace_payload)
    observations, summary = _parse_ledger(ledger_payload)
    latest = max(cycle for cycle, _, _ in occurrences)
    final_cycle = observations[-1][0]
    if final_cycle <= latest or final_cycle > latest + NATIVE_DRAIN_LIMIT:
        raise ReleaseHold("raw ledger lacks a bounded post-trace drain witness")
    if observations[-1][1] or observations[-1][2][0] or observations[-1][3][0]:
        raise ReleaseHold("final drain witness is not quiescent")

    arrivals_by_cycle: Dict[int, Dict[int, int]] = {}
    for cycle, source, polarity in occurrences:
        arrivals_by_cycle.setdefault(cycle, {})[source] = polarity
    queues: List[Deque[int]] = [deque() for _ in range(16)]
    delivered = overrun = 0
    retired_slots = set()
    for cycle, observed_overrun, lane0, lane1 in observations:
        _validate_geometry(lane0, lane1)
        arrivals = arrivals_by_cycle.get(cycle, {})
        address_mask = sum(1 << source for source in arrivals)
        expected_overrun = sum(1 << source for source in arrivals if len(queues[source]) == 2)
        if observed_overrun & ~address_mask:
            raise ReleaseHold("pre-edge overrun is asserted without an arrival")
        if observed_overrun != expected_overrun:
            raise ReleaseHold("pre-edge overrun differs from arrival-and-full")
        overrun += observed_overrun.bit_count()
        sources_this_cycle = set()
        for lane in (lane0, lane1):
            if not lane[0]:
                continue
            for column in range(4):
                if not lane[2] & (1 << column):
                    continue
                source = lane[1] * 4 + column
                slot = (cycle, source)
                if slot in retired_slots or source in sources_this_cycle:
                    raise ReleaseHold("duplicate raw cycle/source retirement")
                retired_slots.add(slot)
                sources_this_cycle.add(source)
                if not queues[source]:
                    raise ReleaseHold("phantom native retirement from an empty source FIFO")
                if ((lane[3] >> column) & 1) != queues[source][0]:
                    raise ReleaseHold("hw_polarity differs from the per-source FIFO front")
                queues[source].popleft()
                delivered += 1
        if len(sources_this_cycle) > 8:
            raise ReleaseHold("more than eight events retired in one cycle")
        for source, polarity in arrivals.items():
            if not observed_overrun & (1 << source):
                queues[source].append(polarity)
                if len(queues[source]) > 2:
                    raise ReleaseHold("native per-source FIFO exceeded depth two")
    if any(queues):
        raise ReleaseHold("native polarity FIFO drain is incomplete")
    generated = len(occurrences)
    if generated != delivered + overrun:
        raise ReleaseHold("generated != delivered + overrun")
    if summary != (generated, delivered, overrun, 0, 0, 1):
        raise ReleaseHold("raw polarity ledger summary differs from verified facts")
    return {
        "schema": RAW_LEDGER_SCHEMA,
        "generated": generated, "delivered": delivered, "overrun": overrun,
        "phantom": 0, "duplicate": 0, "drain_empty": True,
        "observed_cycles": len(observations), "final_cycle": final_cycle,
        "trace_line_endings": endings, "identity_scope": IDENTITY_SCOPE,
        "identity_order_independence_claimed": False,
        "duplicate_scope": DUPLICATE_SCOPE,
    }


def _verify_filelist(filelist: bytes, declared_files: Sequence[Mapping[str, object]]) -> None:
    try:
        lines = filelist.decode("ascii", errors="strict").splitlines()
    except UnicodeError as error:
        raise ReleaseHold("source filelist must be ASCII") from error
    if not lines or any(not line or line.strip() != line for line in lines):
        raise ReleaseHold("source filelist has blank or padded entries")
    paths = [_path(line, "source filelist entry") for line in lines]
    if len(paths) != len(set(paths)):
        raise ReleaseHold("source filelist contains duplicates")
    declared = [str(binding["path"]) for binding in declared_files]
    if paths != declared or tuple(paths) != EXPECTED_SOURCE_PATHS:
        raise ReleaseHold("source filelist differs from the exact polarity-v1 source closure")


def _report_digest(report: Mapping[str, object]) -> str:
    return sha256(canonical_json(report))


def validate_release(root: Path, document: Mapping[str, object]) -> Mapping[str, object]:
    root = Path(root).resolve()
    release = _exact(document, ("schema", "authority", "source", "artifacts", "integration"), "release manifest")
    if release["schema"] != SCHEMA:
        raise ReleaseHold("release manifest schema differs")
    authority = _exact(release["authority"], ("source_repository", "source_commit", "receipt_commit"), "authority")
    if authority["source_repository"] != EXPECTED_SOURCE_REPOSITORY:
        raise ReleaseHold("source_repository differs from the exact Ganghee authority")
    source_commit = _commit(authority["source_commit"], "source_commit")
    if source_commit != SOURCE_COMMIT:
        raise ReleaseHold("source_commit differs from the external Ganghee polarity-v1 authority")
    receipt_commit = _commit(authority["receipt_commit"], "receipt_commit")
    if source_commit == receipt_commit:
        raise ReleaseHold("source commit must be distinct from receipt commit")

    source = _exact(release["source"], ("top", "filelist", "files"), "source")
    if source["top"] != EXPECTED_TOP:
        raise ReleaseHold("polarity-v1 top differs")
    filelist_binding = _binding(source["filelist"], "source.filelist", with_role=False)
    if not isinstance(source["files"], list) or not source["files"]:
        raise ReleaseHold("source.files must be a nonempty ordered list")
    source_files = [_binding(value, "source.files[%d]" % index, with_role=False) for index, value in enumerate(source["files"])]
    if tuple(str(value["path"]) for value in source_files) != EXPECTED_SOURCE_PATHS:
        raise ReleaseHold("source.files differs from the exact polarity-v1 source closure")

    if not isinstance(release["artifacts"], list):
        raise ReleaseHold("artifacts must be a list")
    artifacts: Dict[str, Mapping[str, object]] = {}
    for index, value in enumerate(release["artifacts"]):
        binding = _binding(value, "artifacts[%d]" % index)
        role = str(binding["role"])
        if role in artifacts:
            raise ReleaseHold("artifact role is duplicated: %s" % role)
        artifacts[role] = binding
    if set(artifacts) != set(REQUIRED_ROLES):
        raise ReleaseHold("polarity-v1 artifact role inventory differs")
    for role, scope in REQUIRED_ROLES.items():
        if artifacts[role]["scope"] != scope:
            raise ReleaseHold("%s scope differs" % role)
    if artifacts["polarity_v1_rtl"]["path"] != EXPECTED_SOURCE_PATHS[-1]:
        raise ReleaseHold("polarity-v1 RTL role does not select the v1 source")

    raw_by_role: Dict[str, bytes] = {}
    normalized_by_role: Dict[str, bytes] = {}
    for role, binding in artifacts.items():
        raw_by_role[role], normalized_by_role[role] = _capture_binding(root, binding, role)
    filelist_raw, filelist_normalized = _capture_binding(root, filelist_binding, "source filelist")
    if filelist_raw != filelist_normalized:
        raise ReleaseHold("source filelist must use canonical LF")
    for index, binding in enumerate(source_files):
        _capture_binding(root, binding, "source file %d" % index)
    _verify_filelist(filelist_normalized, source_files)

    rtl_text = normalized_by_role["polarity_v1_rtl"].decode("ascii", errors="strict")
    for token in ("module " + EXPECTED_TOP, "polarity_in", "pol_mask0", "pol_mask1"):
        if token not in rtl_text:
            raise ReleaseHold("polarity-v1 RTL token is absent: %s" % token)
    tb_text = normalized_by_role["polarity_v1_tb"].decode("ascii", errors="strict")
    for token in (
        "module " + EXPECTED_TB_TOP, EXPECTED_TOP,
        ".polarity_in(polarity_in)", ".pol_mask0(pol_mask0)", ".pol_mask1(pol_mask1)",
        "REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%0d delivered=%0d overrun=%0d phantom=0 duplicate=0 drain_empty=1",
    ):
        if token not in tb_text:
            raise ReleaseHold("polarity-v1 observational TB token is absent: %s" % token)
    runner_text = normalized_by_role["polarity_v1_runner"].decode("ascii", errors="strict")
    for token in (
        'PINNED_COMMIT = "' + SOURCE_COMMIT + '"',
        'TRACE_SHA256 = "' + str(artifacts["polarity_v1_trace"]["sha256"]) + '"',
        'TB_SHA256 = "' + str(artifacts["polarity_v1_tb"]["sha256"]) + '"',
        "POLARITY_V1_NATIVE_PASS commit=%s simulator=%s events=%d ",
        "identity_order_independence_claimed=false output_root=%s",
    ):
        if token not in runner_text:
            raise ReleaseHold("polarity-v1 observational runner token is absent: %s" % token)
    verifier_text = normalized_by_role["polarity_v1_independent_verifier"].decode("ascii", errors="strict")
    for token in (RAW_LEDGER_SCHEMA, IDENTITY_SCOPE, "verify_polarity_native_ledger"):
        if token not in verifier_text:
            raise ReleaseHold("independent verifier compatibility token is absent")

    report = verify_raw_cycle_evidence(
        raw_by_role["polarity_v1_trace"], raw_by_role["polarity_v1_cycle_ledger"]
    )
    if report["generated"] != EXPECTED_GENERATED:
        raise ReleaseHold("raw trace does not independently expand to 8503 generated events")

    receipt = parse_canonical_json(normalized_by_role["polarity_v1_receipt"], "polarity receipt")
    _exact(receipt, ("schema", "status", "source", "compatibility", "bindings", "report"), "polarity receipt")
    receipt_source = _exact(receipt["source"], ("repository", "commit"), "polarity receipt source")
    compatibility = _exact(
        receipt["compatibility"],
        ("reference_commit", "raw_ledger_schema", "identity_scope", "counts_source"),
        "polarity receipt compatibility",
    )
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["status"] != "PASS":
        raise ReleaseHold("polarity receipt is not an exact PASS receipt")
    if receipt_source != {"repository": EXPECTED_SOURCE_REPOSITORY, "commit": source_commit}:
        raise ReleaseHold("polarity receipt source authority differs")
    if compatibility != {
        "reference_commit": VERIFIER_REFERENCE_COMMIT,
        "raw_ledger_schema": RAW_LEDGER_SCHEMA,
        "identity_scope": IDENTITY_SCOPE,
        "counts_source": "INDEPENDENT_RAW_TRACE_PLUS_CYCLE_LEDGER_REPLAY",
    }:
        raise ReleaseHold("polarity receipt raw-cycle compatibility provenance differs")
    receipt_binding_roles = (
        "polarity_v1_rtl", "polarity_v1_tb", "polarity_v1_runner",
        "polarity_v1_trace", "polarity_v1_cycle_ledger",
        "polarity_v1_independent_verifier",
    )
    receipt_bindings = _exact(receipt["bindings"], receipt_binding_roles, "polarity receipt bindings")
    for role in receipt_binding_roles:
        if receipt_bindings[role] != artifacts[role]["sha256"]:
            raise ReleaseHold("polarity receipt binding differs: %s" % role)
    if receipt["report"] != report:
        raise ReleaseHold("polarity receipt report differs from independently replayed facts")

    integration = _exact(
        release["integration"],
        ("authority_mode", "release_authority", "polarity_transport", "commit", "authority_artifact_role"),
        "integration",
    )
    integration_commit = _commit(integration["commit"], "integration.commit")
    if receipt_commit == integration_commit:
        raise ReleaseHold("receipt commit must be distinct from integration commit")
    expected_integration = {
        "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
        "release_authority": True,
        "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
        "commit": integration_commit,
        "authority_artifact_role": "integration_authority",
    }
    if integration != expected_integration:
        raise ReleaseHold("explicit integration authority differs")

    integration_authority = parse_canonical_json(normalized_by_role["integration_authority"], "integration authority")
    _exact(
        integration_authority,
        ("schema", "status", "authority_mode", "source", "receipt_commit", "top",
         "filelist_sha256", "bindings", "verification_report_sha256", "polarity_transport"),
        "integration authority",
    )
    expected_binding_hashes = {role: binding["sha256"] for role, binding in artifacts.items() if role != "integration_authority"}
    expected_authority = {
        "schema": INTEGRATION_SCHEMA, "status": "GO",
        "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
        "source": {"repository": EXPECTED_SOURCE_REPOSITORY, "commit": source_commit},
        "receipt_commit": receipt_commit, "top": EXPECTED_TOP,
        "filelist_sha256": filelist_binding["sha256"], "bindings": expected_binding_hashes,
        "verification_report_sha256": _report_digest(report),
        "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
    }
    if integration_authority != expected_authority:
        raise ReleaseHold("integration authority content differs")

    # The source SHA belongs to an external repository and is intentionally not
    # resolved here.  Only receipt and integration history are local authority.
    _safe_git(root, "cat-file", "-e", receipt_commit + "^{commit}")
    _safe_git(root, "cat-file", "-e", integration_commit + "^{commit}")
    _safe_git(root, "merge-base", "--is-ancestor", receipt_commit, integration_commit)
    _safe_git(root, "merge-base", "--is-ancestor", integration_commit, "HEAD")
    every_binding = [filelist_binding] + source_files + list(artifacts.values())
    for binding in every_binding:
        path = str(binding["path"])
        if sha256(_git_blob(root, integration_commit, path)) != binding["sha256"]:
            raise ReleaseHold("integration commit blob differs: %s" % path)
        if binding.get("scope") == "receipt":
            if sha256(_git_blob(root, receipt_commit, path)) != binding["sha256"]:
                raise ReleaseHold("receipt commit blob differs: %s" % path)

    return {
        "status": "GO", "schema": SCHEMA, "source_commit": source_commit,
        "receipt_commit": receipt_commit, "integration_commit": integration_commit,
        "generated": report["generated"], "delivered": report["delivered"],
        "overrun": report["overrun"], "polarity_mismatch": 0,
        "verification_report_sha256": _report_digest(report),
    }


def evaluate_repository(root: Path, manifest_relative: str = DEFAULT_MANIFEST) -> Mapping[str, object]:
    root = Path(root).resolve()
    try:
        raw = _regular_bytes(root, manifest_relative, "release manifest")
        return validate_release(root, parse_canonical_json(raw, "release manifest"))
    except ReleaseHold as error:
        return {"status": "HOLD", "reason": str(error)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate_repository(args.root, args.manifest)
    if args.json:
        sys.stdout.buffer.write(canonical_json(result))
    elif result["status"] == "GO":
        print(
            "REDRED_CLUSTER2_CAV_POLARITY_RELEASE_GO generated=%d delivered=%d overrun=%d polarity_mismatch=0"
            % (result["generated"], result["delivered"], result["overrun"])
        )
    else:
        print("REDRED_CLUSTER2_CAV_POLARITY_RELEASE_HOLD reason=%s" % result["reason"])
    return 0 if result["status"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
