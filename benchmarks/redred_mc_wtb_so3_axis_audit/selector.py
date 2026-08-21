"""Deterministic full-source SO(3) cohort selection without outcome inputs."""

from __future__ import annotations

import ast
import bisect
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .analyzer import PoseSample, RotationFrame, relative_rotation_vector


GRID_NS = 10_000_000
WARMUP_NS = 1_000_000
QUERY_NS = 1_000_000
DELAYED_DEADLINE_NS = 6_000_000
MINIMUM_START_SEPARATION_NS = 20_000_000
PURITY_MINIMUM = 0.8
LOW_MID_THRESHOLD = 0.452421574303
MID_HIGH_THRESHOLD = 1.037493378366
QUOTA_PER_CELL = 6
EXPECTED_WINDOW_COUNT = 108
RANK_SEED = "redred-uzh-axis-motion-v1"
OFFICIAL_SOURCE_LOCK_SHA256 = "0e0dbb17db4d170de650729fe9ad1cd3f18d20c1bddcd577c84999fcde045a4c"
OFFICIAL_SOURCE_MEMBERS = {
    "events": {"filename": "events.txt", "size_bytes": 509907771,
               "line_count": 23126288,
               "sha256": "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda"},
    "poses": {"filename": "groundtruth.txt", "size_bytes": 1379205,
              "line_count": 11883,
              "sha256": "bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb"},
    "calibration": {"filename": "calib.txt", "size_bytes": 128,
                    "line_count": 1,
                    "sha256": "ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd"},
}
OFFICIAL_EXCLUSIONS_SHA256 = "68c088d4fdaac2f61db016b837cbfa1391c2ff0f481ea1ea5ddd771955debb7b"
OFFICIAL_HISTORICAL_INTERVAL_COUNT = 26
OFFICIAL_HISTORICAL_INTERVALS_SHA256 = "121e3434f282ca47607d16e7613390700b725028faa91f085776c197663a48d4"
EXPECTED_HISTORICAL_POSE_IDS_SHA256 = "28265aa82efa09da1ca1323b7738a1facf9e0eb64d96f6f58b05fa53651403bb"
HISTORICAL_LEDGER_SHA256 = "b2c1e739a39443eb8cece286c805ce3cceff13d356847a97fdbb45a794486369"

_PACKAGE = Path(__file__).resolve().parent
DEFAULT_SOURCE_LOCK = _PACKAGE / "source_lock.json"
DEFAULT_EXCLUSIONS = _PACKAGE / "historical_exclusions.json"
DEFAULT_HISTORICAL_POSE_HALO = _PACKAGE / "historical_pose_halo.json"
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIME_RE = re.compile(rb"(0|[1-9][0-9]*)\.([0-9]{9})\Z")
_INT_RE = re.compile(rb"0|[1-9][0-9]*\Z")
_DECIMAL_RE = re.compile(rb"-?(?:0|[1-9][0-9]*)\.[0-9]+\Z")
_FORBIDDEN_INPUT_TOKENS = ("score", "loss", "quality", "metric", "arm_result")
_FORBIDDEN_DIRECT_CALLS = frozenset(("__import__", "eval", "exec", "compile"))
_ALLOWED_IMPORTS = {
    "__future__", "ast", "bisect", "dataclasses", "decimal", "hashlib",
    "json", "math", "pathlib", "re", "typing",
}


class SelectorError(ValueError):
    """A source, exclusion, selection, or registry invariant failed."""


@dataclass(frozen=True)
class _Pose:
    index: int
    sample: PoseSample


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    query_start_ns: int
    rotation_vector_rad: Tuple[float, float, float]
    purity: float
    motion_proxy: float
    axis: str
    sign: str
    motion_bin: str
    axis_pose_support_indices: Tuple[int, ...]
    pose_support_indices: Tuple[int, ...]
    rank_sha256: str

    @property
    def cell(self) -> Tuple[str, str, str]:
        return self.axis, self.sign, self.motion_bin


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pairs(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SelectorError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_score_like(value: object, where: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _FORBIDDEN_INPUT_TOKENS):
                raise SelectorError("score-like input is forbidden at %s.%s" % (where, key))
            _reject_score_like(child, "%s.%s" % (where, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_score_like(child, "%s[%d]" % (where, index))


def _load_json(path: Path) -> Tuple[Mapping[str, object], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                           parse_constant=lambda text: (_ for _ in ()).throw(
                               SelectorError("non-finite JSON number: %s" % text)))
    except SelectorError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SelectorError("invalid JSON: %s" % path) from error
    if not isinstance(value, Mapping):
        raise SelectorError("JSON root must be an object: %s" % path)
    _reject_score_like(value)
    return value, _sha_bytes(raw)


def _exact(value: Mapping[str, object], keys: Iterable[str], where: str) -> None:
    expected = set(keys)
    if set(value) != expected:
        raise SelectorError("%s fields differ: expected %s" % (where, sorted(expected)))


def _integer(value: object, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SelectorError("%s must be an integer >= %d" % (where, minimum))
    return value


def _digest(value: object, where: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise SelectorError("%s must be a lowercase SHA-256" % where)
    return value


def audit_score_free_imports(source_text: str) -> None:
    """Reject imports outside the selector's standard-library geometry boundary."""

    try:
        tree = ast.parse(source_text)
    except SyntaxError as error:
        raise SelectorError("selector source is not valid Python") from error
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_DIRECT_CALLS):
            raise SelectorError("direct dynamic import or code-loading call is forbidden")
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(root not in _ALLOWED_IMPORTS for root in roots):
                raise SelectorError("score-like or non-allowlisted import is forbidden")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module == "analyzer":
                continue
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in _ALLOWED_IMPORTS:
                raise SelectorError("score-like or non-allowlisted import is forbidden")


def _audit_self() -> str:
    raw = Path(__file__).read_bytes()
    audit_score_free_imports(raw.decode("utf-8"))
    return _sha_bytes(raw)


def _source_lock(path: Path) -> Tuple[Mapping[str, Mapping[str, object]], str]:
    top, artifact_sha = _load_json(path)
    _exact(top, {"schema", "sequence", "members"}, "source lock")
    if top["schema"] != "redred.mc_wtb_so3_axis_audit.source_lock/v1":
        raise SelectorError("source lock schema differs")
    members = top["members"]
    if not isinstance(members, Mapping):
        raise SelectorError("source lock members must be an object")
    _exact(members, {"events", "poses", "calibration"}, "source members")
    parsed: Dict[str, Mapping[str, object]] = {}
    for role in ("events", "poses", "calibration"):
        row = members[role]
        if not isinstance(row, Mapping):
            raise SelectorError("source member %s must be an object" % role)
        _exact(row, {"filename", "size_bytes", "line_count", "sha256"}, role)
        if not isinstance(row["filename"], str) or Path(row["filename"]).name != row["filename"]:
            raise SelectorError("source filename must be one basename")
        _integer(row["size_bytes"], "%s size" % role, 1)
        _integer(row["line_count"], "%s line count" % role, 1)
        _digest(row["sha256"], "%s sha256" % role)
        parsed[role] = row
    if artifact_sha != OFFICIAL_SOURCE_LOCK_SHA256 or parsed != OFFICIAL_SOURCE_MEMBERS:
        raise SelectorError("official source lock identity differs")
    return parsed, artifact_sha


def _exclusions(path: Path) -> Tuple[Tuple[Tuple[int, int], ...], str]:
    top, artifact_sha = _load_json(path)
    _exact(top, {"schema", "documents", "intervals"}, "exclusions")
    if top["schema"] != "redred.mc_wtb_so3_axis_audit.historical_exclusions/v1":
        raise SelectorError("historical exclusion schema differs")
    if not isinstance(top["documents"], list) or not isinstance(top["intervals"], list):
        raise SelectorError("historical exclusions arrays are invalid")
    intervals: List[Tuple[int, int]] = []
    ids = set()
    for row in top["intervals"]:
        if not isinstance(row, Mapping):
            raise SelectorError("historical interval must be an object")
        _exact(row, {"id", "start_ns_inclusive", "end_ns_exclusive"}, "interval")
        if not isinstance(row["id"], str) or not row["id"] or row["id"] in ids:
            raise SelectorError("historical interval IDs must be unique")
        ids.add(row["id"])
        start = _integer(row["start_ns_inclusive"], "historical start")
        end = _integer(row["end_ns_exclusive"], "historical end", 1)
        if start >= end:
            raise SelectorError("historical interval is empty")
        intervals.append((start, end))
    ordered = sorted(intervals)
    if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
        raise SelectorError("historical intervals overlap")
    interval_identity = b"".join(
        ("%d %d\n" % (start, end)).encode("ascii") for start, end in intervals
    )
    if (artifact_sha != OFFICIAL_EXCLUSIONS_SHA256
            or len(intervals) != OFFICIAL_HISTORICAL_INTERVAL_COUNT
            or _sha_bytes(interval_identity) != OFFICIAL_HISTORICAL_INTERVALS_SHA256):
        raise SelectorError("official historical exclusion ledger differs")
    return tuple(ordered), artifact_sha


def _halo(path: Path, poses_sha256: str) -> Tuple[frozenset[int], str]:
    top, artifact_sha = _load_json(path)
    _exact(top, {"schema", "complete", "source_poses_sha256", "historical_ledger_sha256",
                 "derivation", "pose_support_indices", "pose_support_indices_sha256"},
           "historical pose halo")
    if top["schema"] != "redred.mc_wtb_so3_axis_audit.historical_pose_halo/v1" or top["complete"] is not True:
        raise SelectorError("historical pose-support halo must declare completeness")
    if _digest(top["source_poses_sha256"], "halo source poses") != poses_sha256:
        raise SelectorError("historical pose-support halo source hash mismatch")
    if _digest(top["historical_ledger_sha256"], "historical ledger") != HISTORICAL_LEDGER_SHA256:
        raise SelectorError("historical ledger binding mismatch")
    raw_ids = top["pose_support_indices"]
    if not isinstance(raw_ids, list):
        raise SelectorError("historical pose-support indices must be an array")
    ids = tuple(_integer(value, "historical pose-support index") for value in raw_ids)
    if len(ids) != len(set(ids)) or tuple(sorted(ids)) != ids:
        raise SelectorError("historical pose-support indices must be sorted and unique")
    identity = b"".join((str(value) + "\n").encode("ascii") for value in ids)
    if (_digest(top["pose_support_indices_sha256"], "halo indices hash")
            != EXPECTED_HISTORICAL_POSE_IDS_SHA256
            or _sha_bytes(identity) != EXPECTED_HISTORICAL_POSE_IDS_SHA256
            or len(ids) != 126):
        raise SelectorError("historical pose-support hash mismatch")
    return frozenset(ids), artifact_sha


def _timestamp(raw: bytes, where: str) -> int:
    match = _TIME_RE.fullmatch(raw)
    if match is None:
        raise SelectorError("%s timestamp must have nine fractional digits" % where)
    return int(match.group(1)) * 1_000_000_000 + int(match.group(2))


def _verify_identity(path: Path, lock: Mapping[str, object], count: int) -> str:
    actual = _sha_file(path)
    if path.stat().st_size != lock["size_bytes"] or count != lock["line_count"] or actual != lock["sha256"]:
        raise SelectorError("source hash/count mismatch: %s" % path.name)
    return actual


def _read_poses(path: Path, lock: Mapping[str, object]) -> Tuple[_Pose, ...]:
    result = []
    previous = -1
    with path.open("rb") as stream:
        for index, raw in enumerate(stream):
            fields = raw.rstrip(b"\n").split(b" ")
            if not raw.endswith(b"\n") or len(fields) != 8 or raw[:-1].count(b" ") != 7:
                raise SelectorError("pose source format differs at line %d" % (index + 1))
            timestamp = _timestamp(fields[0], "pose")
            if timestamp <= previous:
                raise SelectorError("pose timestamps must be strictly increasing")
            previous = timestamp
            if any(_DECIMAL_RE.fullmatch(value) is None for value in fields[1:]):
                raise SelectorError("pose source contains a noncanonical decimal")
            quaternion = tuple(float(value) for value in fields[4:8])
            result.append(_Pose(index, PoseSample(timestamp, quaternion)))
    _verify_identity(path, lock, len(result))
    if len(result) < 2:
        raise SelectorError("full pose source must contain at least two records")
    return tuple(result)


def _read_calibration(path: Path, lock: Mapping[str, object]) -> Tuple[float, float]:
    rows = path.read_bytes().splitlines(keepends=True)
    _verify_identity(path, lock, len(rows))
    if len(rows) != 1 or not rows[0].endswith(b"\n"):
        raise SelectorError("calibration must contain exactly one LF-terminated row")
    fields = rows[0][:-1].split(b" ")
    if len(fields) != 9 or any(_DECIMAL_RE.fullmatch(value) is None for value in fields):
        raise SelectorError("calibration format differs")
    fx, fy = float(fields[0]), float(fields[1])
    if not math.isfinite(fx) or not math.isfinite(fy) or fx <= 0.0 or fy <= 0.0:
        raise SelectorError("calibration focal lengths must be positive and finite")
    return fx, fy


def _multiply(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (lw*rx + lx*rw + ly*rz - lz*ry,
            lw*ry - lx*rz + ly*rw + lz*rx,
            lw*rz + lx*ry - ly*rx + lz*rw,
            lw*rw - lx*rx - ly*ry - lz*rz)


def _slerp(left: Sequence[float], right: Sequence[float], alpha: float) -> Tuple[float, float, float, float]:
    dot = math.fsum(a * b for a, b in zip(left, right))
    if dot < 0.0:
        right = tuple(-value for value in right)
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        raw = tuple((1.0-alpha)*a + alpha*b for a, b in zip(left, right))
    else:
        theta = math.acos(dot)
        sine = math.sin(theta)
        raw = tuple((math.sin((1.0-alpha)*theta)*a + math.sin(alpha*theta)*b) / sine
                    for a, b in zip(left, right))
    norm = math.sqrt(math.fsum(value * value for value in raw))
    return tuple(value / norm for value in raw)  # type: ignore[return-value]


def _orientation_at(poses: Sequence[_Pose], times: Sequence[int], timestamp: int) -> Tuple[Tuple[float, ...], Tuple[int, ...]]:
    right = bisect.bisect_left(times, timestamp)
    if right < len(poses) and times[right] == timestamp:
        return poses[right].sample.quaternion_xyzw, (poses[right].index,)
    left = right - 1
    if left < 0 or right >= len(poses):
        raise SelectorError("query grid lacks a closed pose bracket")
    alpha = float(timestamp - times[left]) / float(times[right] - times[left])
    return (_slerp(poses[left].sample.quaternion_xyzw,
                   poses[right].sample.quaternion_xyzw, alpha),
            (poses[left].index, poses[right].index))


def _overlaps(start: int, end: int, intervals: Sequence[Tuple[int, int]]) -> bool:
    return any(max(start, left) < min(end, right) for left, right in intervals)


def _candidate(query_start: int, poses: Sequence[_Pose], times: Sequence[int], focal: float,
               forbidden_intervals: Sequence[Tuple[int, int]], forbidden_support: frozenset[int],
               source_sha256s: Sequence[str]) -> Optional[_Candidate]:
    if _overlaps(query_start - WARMUP_NS, query_start + QUERY_NS, forbidden_intervals):
        return None
    before, left_support = _orientation_at(poses, times, query_start)
    after, right_support = _orientation_at(poses, times, query_start + QUERY_NS)
    axis_support = tuple(sorted(set(left_support) | set(right_support)))
    history_end = bisect.bisect_right(times, query_start - WARMUP_NS)
    if history_end < 3:
        return None
    support_end = bisect.bisect_right(times, query_start + QUERY_NS + DELAYED_DEADLINE_NS)
    support = tuple(row.index for row in poses[history_end - 3:support_end])
    if set(support) & forbidden_support:
        return None
    vector = relative_rotation_vector(before, after, frame=RotationFrame.BODY)
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm <= 0.0:
        return None
    absolute = tuple(abs(value) for value in vector)
    axis_index = max(range(3), key=lambda index: (absolute[index], -index))
    purity = absolute[axis_index] / norm
    if purity < PURITY_MINIMUM:
        return None
    proxy = focal * norm
    motion_bin = "LOW" if proxy < LOW_MID_THRESHOLD else "MID" if proxy < MID_HIGH_THRESHOLD else "HIGH"
    axis = ("X", "Y", "Z")[axis_index]
    sign = "POSITIVE" if vector[axis_index] > 0.0 else "NEGATIVE"
    candidate_id = "shapes_rotation/query_start_ns=%d" % query_start
    rank_material = "\n".join((RANK_SEED, *source_sha256s, candidate_id)) + "\n"
    rank = _sha_bytes(rank_material.encode("ascii"))
    return _Candidate(candidate_id, query_start, vector, purity, proxy, axis, sign,
                      motion_bin, axis_support, support, rank)


def _geometric_candidates(poses: Sequence[_Pose], focal: float,
                          forbidden_intervals: Sequence[Tuple[int, int]],
                          forbidden_support: frozenset[int],
                          source_sha256s: Sequence[str]) -> Tuple[_Candidate, ...]:
    times = tuple(row.sample.timestamp_ns for row in poses)
    first = ((times[0] + WARMUP_NS + GRID_NS - 1) // GRID_NS) * GRID_NS
    last = ((times[-1] - QUERY_NS) // GRID_NS) * GRID_NS
    candidates = []
    for query_start in range(first, last + 1, GRID_NS):
        row = _candidate(query_start, poses, times, focal, forbidden_intervals,
                         forbidden_support, source_sha256s)
        if row is not None:
            candidates.append(row)
    return tuple(candidates)


def _select_candidates(candidates: Sequence[_Candidate]) -> Tuple[_Candidate, ...]:
    by_cell: Dict[Tuple[str, str, str], List[_Candidate]] = {}
    for row in candidates:
        by_cell.setdefault(row.cell, []).append(row)
    for rows in by_cell.values():
        rows.sort(key=lambda item: (item.rank_sha256, item.candidate_id))
    selected: List[_Candidate] = []
    selected_support = set()
    signed_cells = (("X", "NEGATIVE"), ("X", "POSITIVE"),
                    ("Y", "NEGATIVE"), ("Y", "POSITIVE"),
                    ("Z", "NEGATIVE"), ("Z", "POSITIVE"))
    for _round in range(QUOTA_PER_CELL):
        for motion_bin in ("LOW", "MID", "HIGH"):
            for axis, sign in signed_cells:
                cell = (axis, sign, motion_bin)
                chosen = None
                for row in by_cell.get(cell, []):
                    if row in selected:
                        continue
                    if any(abs(row.query_start_ns - other.query_start_ns)
                           < MINIMUM_START_SEPARATION_NS for other in selected):
                        continue
                    if selected_support.intersection(row.pose_support_indices):
                        continue
                    chosen = row
                    break
                if chosen is None:
                    raise SelectorError("insufficient signed axis-motion quota: %s" % "/".join(cell))
                selected.append(chosen)
                selected_support.update(chosen.pose_support_indices)
    if len(selected) != EXPECTED_WINDOW_COUNT:
        raise SelectorError("selected cohort count mismatch")
    return tuple(sorted(selected, key=lambda item: item.query_start_ns))


def _event_evidence(path: Path, lock: Mapping[str, object], query_starts: Sequence[int]) -> Dict[int, Mapping[str, object]]:
    result: Dict[int, Dict[str, object]] = {
        start: {"warmup": [], "query": [], "raw": hashlib.sha256()}
        for start in query_starts
    }
    ordered_query_starts = tuple(sorted(query_starts))
    starts = tuple(start - WARMUP_NS for start in ordered_query_starts)
    previous_timestamp = -1
    count = 0
    with path.open("rb") as stream:
        for event_id, raw in enumerate(stream):
            count += 1
            fields = raw.rstrip(b"\n").split(b" ")
            if (not raw.endswith(b"\n") or len(fields) != 4 or raw[:-1].count(b" ") != 3
                    or _INT_RE.fullmatch(fields[1]) is None or _INT_RE.fullmatch(fields[2]) is None
                    or fields[3] not in (b"0", b"1")):
                raise SelectorError("event source format differs at line %d" % count)
            timestamp = _timestamp(fields[0], "event")
            if timestamp < previous_timestamp:
                raise SelectorError("event timestamps must be monotonic")
            previous_timestamp = timestamp
            index = bisect.bisect_right(starts, timestamp) - 1
            if index >= 0:
                query_start = ordered_query_starts[index]
                if timestamp < query_start + QUERY_NS:
                    target = result[query_start]["warmup" if timestamp < query_start else "query"]
                    assert isinstance(target, list)
                    target.append(event_id)
                    digest = result[query_start]["raw"]
                    assert hasattr(digest, "update")
                    digest.update(raw)
    _verify_identity(path, lock, count)
    finalized: Dict[int, Mapping[str, object]] = {}
    for start, row in result.items():
        warmup = row["warmup"]
        query = row["query"]
        digest = row["raw"]
        assert isinstance(warmup, list) and isinstance(query, list)
        finalized[start] = {
            "warmup": warmup, "query": query,
            "selected_raw_event_lines_sha256": digest.hexdigest(),
        }
    return finalized


def _ids_hash(values: Sequence[int]) -> str:
    return _sha_bytes(b"".join((str(value) + "\n").encode("ascii") for value in values))


def _contract_mapping() -> Mapping[str, object]:
    return {
        "grid_ns": GRID_NS, "warmup_ns": WARMUP_NS, "query_ns": QUERY_NS,
        "delayed_deadline_ns": DELAYED_DEADLINE_NS,
        "rotation": "BODY_Log(R_WC(tq)^T_R_WC(tq+1ms))",
        "purity_minimum": PURITY_MINIMUM,
        "low_mid_threshold": LOW_MID_THRESHOLD,
        "mid_high_threshold": MID_HIGH_THRESHOLD,
        "quota_per_cell": QUOTA_PER_CELL,
        "rank_seed": RANK_SEED,
        "rank_material": "seed+LF+events_sha256+LF+poses_sha256+LF+calibration_sha256+LF+candidate_id+LF",
        "selection_schedule": "six_rounds_each_LOW_then_MID_then_HIGH_each_X-,X+,Y-,Y+,Z-,Z+_first_conflict_free_cell_rank",
        "minimum_start_separation_ns": MINIMUM_START_SEPARATION_NS,
    }


def select_full_source(
    dataset_directory: Path,
    *,
    source_lock_path: Path = DEFAULT_SOURCE_LOCK,
    exclusions_path: Path = DEFAULT_EXCLUSIONS,
    historical_pose_halo_path: Path = DEFAULT_HISTORICAL_POSE_HALO,
) -> Mapping[str, object]:
    """Select and seal the deterministic 108-window full-source cohort."""

    selector_sha = _audit_self()
    locks, source_lock_sha = _source_lock(Path(source_lock_path))
    intervals, exclusions_sha = _exclusions(Path(exclusions_path))
    root = Path(dataset_directory)
    paths = {role: root / str(locks[role]["filename"])
             for role in ("events", "poses", "calibration")}
    if any(not path.is_file() for path in paths.values()):
        raise SelectorError("all three pinned full-source members are required")
    poses = _read_poses(paths["poses"], locks["poses"])
    fx, fy = _read_calibration(paths["calibration"], locks["calibration"])
    forbidden_support, halo_sha = _halo(Path(historical_pose_halo_path),
                                        str(locks["poses"]["sha256"]))
    source_sha256s = tuple(str(locks[role]["sha256"])
                           for role in ("events", "poses", "calibration"))
    geometric = _geometric_candidates(poses, (fx + fy) / 2.0, intervals,
                                      forbidden_support, source_sha256s)
    all_evidence = _event_evidence(paths["events"], locks["events"],
                                   [row.query_start_ns for row in geometric])
    eligible = tuple(row for row in geometric
                     if all_evidence[row.query_start_ns]["warmup"]
                     and all_evidence[row.query_start_ns]["query"])
    selected = _select_candidates(eligible)
    windows = []
    for row in selected:
        evidence = all_evidence[row.query_start_ns]
        warmup_ids = evidence["warmup"]
        query_ids = evidence["query"]
        assert isinstance(warmup_ids, list) and isinstance(query_ids, list)
        windows.append({
            "candidate_id": row.candidate_id,
            "query_start_ns": row.query_start_ns,
            "warmup_start_ns": row.query_start_ns - WARMUP_NS,
            "query_end_ns_exclusive": row.query_start_ns + QUERY_NS,
            "axis": row.axis,
            "sign": row.sign,
            "motion_bin": row.motion_bin,
            "rotation_vector_rad": list(row.rotation_vector_rad),
            "purity": row.purity,
            "motion_proxy": row.motion_proxy,
            "axis_pose_support_indices": list(row.axis_pose_support_indices),
            "pose_support_indices": list(row.pose_support_indices),
            "warmup_event_ids": warmup_ids,
            "query_event_ids": query_ids,
            "warmup_event_ids_sha256": _ids_hash(warmup_ids),
            "query_event_ids_sha256": _ids_hash(query_ids),
            "selected_raw_event_lines_sha256": evidence["selected_raw_event_lines_sha256"],
            "rank_sha256": row.rank_sha256,
        })
    registry: Dict[str, object] = {
        "schema": "redred.mc_wtb_so3_axis_audit.cohort_registry/v1",
        "contract": _contract_mapping(),
        "bindings": {
            "source_lock_sha256": source_lock_sha,
            "selector_py_sha256": selector_sha,
            "historical_exclusions_sha256": exclusions_sha,
            "historical_pose_halo_sha256": halo_sha,
            "source_member_sha256": {role: locks[role]["sha256"] for role in locks},
            "source_member_line_count": {role: locks[role]["line_count"] for role in locks},
        },
        "window_count": len(windows),
        "windows": windows,
    }
    registry["registry_sha256"] = _sha_bytes(_canonical(registry))
    verify_registry(registry, exclusions_path=Path(exclusions_path),
                    historical_pose_halo_path=Path(historical_pose_halo_path),
                    source_lock_path=Path(source_lock_path), dataset_directory=root)
    return registry


def verify_registry(registry: Mapping[str, object], *, exclusions_path: Path,
                    historical_pose_halo_path: Path, source_lock_path: Path,
                    dataset_directory: Path) -> None:
    """Fail closed on registry hashes, quotas, overlap, or shared identities."""

    _reject_score_like(registry, "registry")
    if not isinstance(registry, Mapping):
        raise SelectorError("registry must be an object")
    _exact(registry, {"schema", "contract", "bindings", "window_count", "windows",
                      "registry_sha256"}, "registry")
    if registry["schema"] != "redred.mc_wtb_so3_axis_audit.cohort_registry/v1":
        raise SelectorError("registry schema differs")
    expected_hash = _digest(registry.get("registry_sha256"), "registry sha256")
    unsigned = dict(registry)
    unsigned.pop("registry_sha256", None)
    if _sha_bytes(_canonical(unsigned)) != expected_hash:
        raise SelectorError("registry hash mismatch")
    locks, source_lock_sha = _source_lock(Path(source_lock_path))
    intervals, exclusions_sha = _exclusions(Path(exclusions_path))
    forbidden_support, halo_sha = _halo(Path(historical_pose_halo_path),
                                        str(locks["poses"]["sha256"]))
    bindings = registry.get("bindings")
    if registry.get("contract") != _contract_mapping():
        raise SelectorError("frozen selector contract differs")
    expected_member_sha = {role: locks[role]["sha256"] for role in locks}
    expected_member_count = {role: locks[role]["line_count"] for role in locks}
    if not isinstance(bindings, Mapping):
        raise SelectorError("registry bindings must be an object")
    _exact(bindings, {"source_lock_sha256", "selector_py_sha256",
                      "historical_exclusions_sha256", "historical_pose_halo_sha256",
                      "source_member_sha256", "source_member_line_count"}, "bindings")
    if (bindings.get("source_lock_sha256") != source_lock_sha
            or bindings.get("historical_exclusions_sha256") != exclusions_sha
            or bindings.get("historical_pose_halo_sha256") != halo_sha
            or bindings.get("selector_py_sha256") != _audit_self()
            or bindings.get("source_member_sha256") != expected_member_sha
            or bindings.get("source_member_line_count") != expected_member_count):
        raise SelectorError("frozen source/selector/exclusion binding mismatch")

    root = Path(dataset_directory)
    paths = {role: root / str(locks[role]["filename"])
             for role in ("events", "poses", "calibration")}
    if any(not path.is_file() for path in paths.values()):
        raise SelectorError("all three pinned full-source members are required")
    poses = _read_poses(paths["poses"], locks["poses"])
    fx, fy = _read_calibration(paths["calibration"], locks["calibration"])
    source_sha256s = tuple(str(locks[role]["sha256"])
                           for role in ("events", "poses", "calibration"))
    geometric = _geometric_candidates(poses, (fx + fy) / 2.0, intervals,
                                      forbidden_support, source_sha256s)
    all_evidence = _event_evidence(paths["events"], locks["events"],
                                   [row.query_start_ns for row in geometric])
    eligible = tuple(row for row in geometric
                     if all_evidence[row.query_start_ns]["warmup"]
                     and all_evidence[row.query_start_ns]["query"])
    expected_selected = _select_candidates(eligible)
    expected_by_id = {row.candidate_id: row for row in expected_selected}
    windows = registry.get("windows")
    if not isinstance(windows, list) or registry.get("window_count") != EXPECTED_WINDOW_COUNT or len(windows) != EXPECTED_WINDOW_COUNT:
        raise SelectorError("registry window count mismatch")
    counts: Dict[Tuple[str, str, str], int] = {}
    event_ids = set()
    pose_ids = set()
    starts = []
    for row in windows:
        if not isinstance(row, Mapping):
            raise SelectorError("registry window must be an object")
        _exact(row, {"candidate_id", "query_start_ns", "warmup_start_ns",
                     "query_end_ns_exclusive", "axis", "sign", "motion_bin",
                     "rotation_vector_rad", "purity", "motion_proxy",
                     "axis_pose_support_indices", "pose_support_indices",
                     "warmup_event_ids", "query_event_ids",
                     "warmup_event_ids_sha256", "query_event_ids_sha256",
                     "selected_raw_event_lines_sha256", "rank_sha256"}, "window")
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in expected_by_id:
            raise SelectorError("registry candidate identity differs from frozen selection")
        expected = expected_by_id[candidate_id]
        start = _integer(row.get("query_start_ns"), "query start")
        if start % GRID_NS or row.get("warmup_start_ns") != start - WARMUP_NS or row.get("query_end_ns_exclusive") != start + QUERY_NS:
            raise SelectorError("registry window timing differs")
        if _overlaps(start - WARMUP_NS, start + QUERY_NS, intervals):
            raise SelectorError("registry overlaps a historical interval")
        starts.append(start)
        if (start != expected.query_start_ns
                or row.get("axis") != expected.axis
                or row.get("sign") != expected.sign
                or row.get("motion_bin") != expected.motion_bin
                or row.get("rotation_vector_rad") != list(expected.rotation_vector_rad)
                or row.get("purity") != expected.purity
                or row.get("motion_proxy") != expected.motion_proxy
                or row.get("axis_pose_support_indices") != list(expected.axis_pose_support_indices)
                or row.get("pose_support_indices") != list(expected.pose_support_indices)):
            raise SelectorError("registry motion or pose support differs from source")
        rank_material = "\n".join((RANK_SEED, *source_sha256s, candidate_id)) + "\n"
        if row.get("rank_sha256") != _sha_bytes(rank_material.encode("ascii")):
            raise SelectorError("candidate rank material differs")
        cell = (str(row.get("axis")), str(row.get("sign")), str(row.get("motion_bin")))
        counts[cell] = counts.get(cell, 0) + 1
        warmup_values = row.get("warmup_event_ids")
        query_values = row.get("query_event_ids")
        if not isinstance(warmup_values, list) or not isinstance(query_values, list) or not warmup_values or not query_values:
            raise SelectorError("warmup and query must each contain real events")
        local_events = warmup_values + query_values
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in local_events) or len(local_events) != len(set(local_events)) or event_ids.intersection(local_events):
            raise SelectorError("shared or invalid event IDs")
        event_ids.update(local_events)
        local_poses = row.get("pose_support_indices")
        if not isinstance(local_poses, list) or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in local_poses) or len(local_poses) != len(set(local_poses)) or pose_ids.intersection(local_poses) or forbidden_support.intersection(local_poses):
            raise SelectorError("shared or forbidden pose support")
        pose_ids.update(local_poses)
        if row.get("warmup_event_ids_sha256") != _ids_hash(row.get("warmup_event_ids", [])) or row.get("query_event_ids_sha256") != _ids_hash(row.get("query_event_ids", [])):
            raise SelectorError("event ID hash mismatch")
        evidence = all_evidence[start]
        if (warmup_values != evidence["warmup"] or query_values != evidence["query"]
                or row.get("selected_raw_event_lines_sha256")
                != evidence["selected_raw_event_lines_sha256"]):
            raise SelectorError("selected raw event evidence differs from source")
    expected_cells = {(axis, sign, motion_bin): QUOTA_PER_CELL for axis in ("X", "Y", "Z") for sign in ("NEGATIVE", "POSITIVE") for motion_bin in ("LOW", "MID", "HIGH")}
    if counts != expected_cells:
        raise SelectorError("registry signed axis-motion quota mismatch")
    expected_candidate_ids = tuple(row.candidate_id for row in expected_selected)
    actual_candidate_ids = tuple(str(row["candidate_id"]) for row in windows)
    if actual_candidate_ids != expected_candidate_ids:
        raise SelectorError("registry ordered candidate IDs differ from frozen selection")
    ordered = sorted(starts)
    if len(ordered) != len(set(ordered)) or any(right - left < MINIMUM_START_SEPARATION_NS for left, right in zip(ordered, ordered[1:])):
        raise SelectorError("registry windows overlap or violate start separation")


__all__ = ["SelectorError", "audit_score_free_imports", "select_full_source", "verify_registry"]
