"""Canonical, score-free observer and campaign sealer for Stage-4 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import Arm
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    run_delayed_unbounded_diagnostic,
)

from . import adapter as integration_adapter


class SealingError(ValueError):
    """The official score-free seal could not be closed."""


@dataclass(frozen=True)
class SealResult:
    output_dir: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "_FileIdentity":
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )


_ASSAY_MANIFEST = "stage4_input_manifest.json"
_ASSAY_STREAMS = (
    "stage4_events.jsonl",
    "stage4_occurrence_batches.jsonl",
    "stage4_occurrence_pose_snapshots.jsonl",
    "stage4_dataset_pose_packets.jsonl",
    "oracle_resampled_groundtruth_1khz.jsonl",
    "stage4_oracle_window_schedule.jsonl",
)
_ARM_ORDER = tuple(arm.value for arm in Arm)
_LEAF_FILES = (
    "full-cycle-result.json",
    "cycle-receipts.json",
    "query-decision-records.json",
    "decision-receipt.json",
    "score-free-accounting.json",
    "score-free-accounting-evidence.json",
    "score-boundary-evidence.json",
    "score-input-manifest.json",
)
_DELAYED_DIAGNOSTIC_FILE = "delayed-unbounded-depth-diagnostic.json"
_DELAYED_DIAGNOSTIC_KIND = "delayed_unbounded_depth_diagnostic"
_REPLAY_REQUIRED = "UNBOUNDED_REPLAY_REQUIRED_FOR_MINIMUM_ZERO_LOSS_DEPTH"
_DIAGNOSTIC_SCHEMA = (
    "redred.mc_wtb.stage4_delayed_unbounded_depth_diagnostic/v3"
)
_DIAGNOSTIC_CONFIG = {
    "schema": "redred.mc_wtb.stage4_delayed_unbounded_depth_config/v2",
    "arm": "delayed_exact",
    "arm_semantic_label": "DIAGNOSTIC_UPPER_BOUND",
    "clock_period_ps": 6500,
    "timestamp_to_cycle_rule": (
        "ceil((timestamp_ns-window_start_ns)*1000/6500)"
    ),
    "raw_ingress_lanes": 6,
    "ingress_staging_entries": 6,
    "ingress_order": "atomic_capture_then_stable_event_id_two_per_cycle",
    "event_lanes": 2,
    "transform_pipeline_cycles": 1,
    "delayed_deadline_ns": 6_000_000,
    "delayed_deadline_cycles": 923_077,
    "pose_visibility_rule": (
        "commit_cycle_strictly_less_than_observation_cycle"
    ),
    "cycle_priority": (
        "visible_pose_then_ordered_retire_then_atomic_capture_then_"
        "stable_admit_then_consecutive_ready_head_launch"
    ),
    "fifo_policy": "unbounded_remove_only_fifo_full_pressure_action",
    "removed_bounded_fifo_entries": 1_024,
    "removed_pressure_reason": "fifo_full_forced_bypass",
    "termination_guard_rule": "iterations<=10*input_count+2*pose_count+32",
    "queue_bound_rule": "fifo_occupancy<=input_event_count_at_all_times",
    "event_record_bits": 102,
    "pose_ring_entries": 16,
    "pose_ring_state_bits": 3_072,
}
_DIAGNOSTIC_FIELDS = frozenset((
    "schema",
    "window_id",
    "window_start_ns",
    "arm",
    "arm_semantic_label",
    "config",
    "config_identity_sha256",
    "input_events",
    "input_poses",
    "input_events_sha256",
    "input_poses_sha256",
    "input_event_ids",
    "retired_event_ids",
    "input_event_ids_sha256",
    "retired_event_ids_sha256",
    "input_count",
    "input_pose_count",
    "retired_count",
    "exact_once_ordered_conservation",
    "no_full_pressure_reasons",
    "termination_proven",
    "queue_never_exceeded_input_count",
    "simulation_iterations",
    "termination_iteration_bound",
    "peak_fifo_depth",
    "peak_ingress_staging_occupancy",
    "records",
    "decision_records_sha256",
    "cycle_receipts",
    "cycle_receipts_sha256",
    "common_serializer_cycles",
    "always_bypass_retire_cycles",
    "policy_added_latency_cycles",
    "synthetic_test_mode",
    "all_event_pose_indices_verified",
    "pose_ring_accounting",
    "pose_ring_accounting_sha256",
    "evidence_sha256",
))
_DIAGNOSTIC_EVENT_FIELDS = frozenset((
    "event_id",
    "timestamp_ns",
    "transform_guard_valid",
    "causal_pose_index",
))
_DIAGNOSTIC_POSE_FIELDS = frozenset((
    "pose_id",
    "timestamp_ns",
    "commit_cycle",
    "source",
    "pose_sha256",
    "value_valid",
    "arithmetic_valid",
))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha(value: Any, where: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SealingError("%s must be a lowercase SHA-256" % where)
    return value


def _require_int(value: Any, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SealingError("%s must be an integer >= %d" % (where, minimum))
    return value


def _require_bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise SealingError("%s must be bool" % where)
    return value


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SealingError("%s must be an object" % where)
    return value


def _require_array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise SealingError("%s must be an array" % where)
    return value


def _object_without_duplicates(
    pairs: Iterable[Tuple[str, Any]]
) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise SealingError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _decode_json(payload: bytes, where: str) -> Any:
    try:
        value = json.loads(
            payload.decode("ascii"), object_pairs_hook=_object_without_duplicates
        )
    except SealingError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise SealingError("%s is not strict ASCII JSON" % where) from exc
    if canonical_json_bytes(value) != payload:
        raise SealingError("%s is not canonical JSON" % where)
    return value


def _safe_relative(relative: str, where: str) -> Tuple[str, ...]:
    if type(relative) is not str or not relative:
        raise SealingError("%s path must be non-empty text" % where)
    path = Path(relative)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or str(path) != relative
    ):
        raise SealingError("%s path is not canonical and root-relative" % where)
    return path.parts


def _directory_fd(path: Path, where: str) -> int:
    absolute = Path(os.path.abspath(str(path)))
    parts = absolute.parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parts[0], flags)
    except OSError as exc:
        raise SealingError("cannot open %s root safely" % where) from exc
    try:
        for part in parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise SealingError(
                    "%s root contains a symlink or unsafe component" % where
                ) from exc
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise SealingError("%s root must be a directory" % where)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _stable_regular_file(root: Path, relative: str, where: str) -> bytes:
    """Read one root-contained, single-link regular file through stable FDs."""

    parts = _safe_relative(relative, where)
    descriptors = []  # type: List[int]
    try:
        directory = _directory_fd(Path(root), where)
        descriptors.append(directory)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        for part in parts[:-1]:
            try:
                child = os.open(part, directory_flags, dir_fd=directory)
            except OSError as exc:
                raise SealingError("%s traverses an unsafe directory" % where) from exc
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                raise SealingError("%s traverses a non-directory" % where)
            descriptors.append(child)
            directory = child

        try:
            before_stat = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            raise SealingError("cannot inspect %s" % where) from exc
        before = _FileIdentity.from_stat(before_stat)
        if not stat.S_ISREG(before.mode):
            raise SealingError("%s must be a regular file, not a symlink" % where)
        if before.links != 1:
            raise SealingError("%s must not be a hard-linked file" % where)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory)
        except OSError as exc:
            raise SealingError("cannot open %s safely" % where) from exc
        descriptors.append(descriptor)
        opened = _FileIdentity.from_stat(os.fstat(descriptor))
        if opened != before:
            raise SealingError("%s changed while opening" % where)

        chunks = []  # type: List[bytes]
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after_fd = _FileIdentity.from_stat(os.fstat(descriptor))
        try:
            after_path = _FileIdentity.from_stat(
                os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            )
        except OSError as exc:
            raise SealingError("%s changed after reading" % where) from exc
        if before != after_fd or before != after_path or len(payload) != before.size:
            raise SealingError("%s changed while reading" % where)
        return payload
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_json_at(root: Path, relative: str) -> Tuple[Any, bytes]:
    payload = _stable_regular_file(root, relative, relative)
    return _decode_json(payload, relative), payload


def _read_json(path: Path) -> Tuple[Any, bytes]:
    path = Path(path)
    if not path.name:
        raise SealingError("JSON path has no file name")
    return _read_json_at(path.parent, path.name)


def _read_jsonl_at(
    root: Path, relative: str
) -> Tuple[Tuple[Mapping[str, Any], ...], bytes]:
    payload = _stable_regular_file(root, relative, relative)
    path = Path(relative)
    if payload and not payload.endswith(b"\n"):
        raise SealingError("%s lacks its final newline" % path.name)
    records = []  # type: List[Mapping[str, Any]]
    for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
        value = _decode_json(line, "%s:%d" % (path.name, line_number))
        if not isinstance(value, Mapping):
            raise SealingError("%s contains a non-object record" % path.name)
        records.append(value)
    return tuple(records), payload


def _read_jsonl(path: Path) -> Tuple[Tuple[Mapping[str, Any], ...], bytes]:
    path = Path(path)
    if not path.name:
        raise SealingError("JSONL path has no file name")
    return _read_jsonl_at(path.parent, path.name)


def _inventory_seal_tree(root: Path) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return every regular file and directory; reject aliases and specials."""

    root_fd = _directory_fd(Path(root), "seal inventory")
    found_files = []  # type: List[str]
    found_directories = []  # type: List[str]

    def visit(directory_fd: int, prefix: str) -> None:
        try:
            with os.scandir(directory_fd) as iterator:
                entries = sorted(iterator, key=lambda row: row.name)
        except OSError as exc:
            raise SealingError("cannot enumerate sealed file tree") from exc
        for entry in entries:
            relative = entry.name if not prefix else "%s/%s" % (prefix, entry.name)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SealingError("cannot inspect sealed entry: %s" % relative) from exc
            if stat.S_ISLNK(info.st_mode):
                raise SealingError("sealed tree contains a symlink: %s" % relative)
            if stat.S_ISDIR(info.st_mode):
                found_directories.append(relative)
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
                    os, "O_NOFOLLOW", 0
                )
                try:
                    child = os.open(entry.name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise SealingError(
                        "sealed directory changed while inventorying: %s" % relative
                    ) from exc
                try:
                    if _FileIdentity.from_stat(os.fstat(child)) != _FileIdentity.from_stat(
                        info
                    ):
                        raise SealingError(
                            "sealed directory changed while inventorying: %s" % relative
                        )
                    visit(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise SealingError(
                        "sealed tree contains a hard-linked file: %s" % relative
                    )
                found_files.append(relative)
            else:
                raise SealingError("sealed tree contains a special file: %s" % relative)

    try:
        visit(root_fd, "")
    finally:
        os.close(root_fd)
    return tuple(found_files), tuple(found_directories)


def _indexed_directory_prefixes(paths: Iterable[str]) -> Tuple[str, ...]:
    expected = set()  # type: Set[str]
    for relative in paths:
        parts = _safe_relative(relative, "campaign file index")
        for length in range(1, len(parts)):
            expected.add("/".join(parts[:length]))
    return tuple(sorted(expected))


def _read_indexed_json(
    root: Path,
    relative: str,
    files: Mapping[str, Any],
    where: str,
) -> Tuple[Any, bytes]:
    entry = _require_mapping(files.get(relative), "%s file index entry" % where)
    value, payload = _read_json_at(root, relative)
    if (
        _sha256(payload) != _require_sha(entry.get("sha256"), "%s file hash" % where)
        or len(payload) != _require_int(
            entry.get("size_bytes"), "%s file size" % where
        )
    ):
        raise SealingError("%s changed after file observation" % where)
    return value, payload


def _require_index_shape(
    files: Mapping[str, Any], relative: str, kind: str, count: int
) -> None:
    entry = _require_mapping(files.get(relative), "required file index entry")
    if (
        entry.get("kind") != kind
        or _require_int(entry.get("record_count"), "%s record_count" % relative)
        != count
    ):
        raise SealingError("sealed file kind or record count differs: %s" % relative)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _to_mapping(value: Any, where: str) -> Mapping[str, Any]:
    try:
        mapping = value.to_mapping()
    except AttributeError as exc:
        raise SealingError("%s lacks a canonical mapping" % where) from exc
    if not isinstance(mapping, Mapping):
        raise SealingError("%s mapping is not an object" % where)
    return mapping


def _observe_file(
    root: Path,
    relative: str,
    *,
    kind: str,
    record_count: int,
) -> Mapping[str, Any]:
    record_count = _require_int(record_count, "%s record_count" % relative)
    value, payload = _read_json_at(root, relative)
    if kind == "array":
        if not isinstance(value, list) or len(value) != record_count:
            raise SealingError("%s array count differs" % relative)
    elif kind == "object":
        if not isinstance(value, Mapping) or record_count != 1:
            raise SealingError("%s object shape differs" % relative)
    elif kind == _DELAYED_DIAGNOSTIC_KIND:
        if not isinstance(value, Mapping) or record_count != 1:
            raise SealingError("%s diagnostic shape differs" % relative)
        diagnostic = _validate_delayed_diagnostic_mapping(value, relative)
    else:
        raise SealingError("unknown sealed file kind")
    observed = {
        "sha256": _sha256(payload),
        "size_bytes": len(payload),
        "kind": kind,
        "record_count": record_count,
    }
    if kind == _DELAYED_DIAGNOSTIC_KIND:
        observed.update(diagnostic)
    return observed


def _validate_delayed_diagnostic_mapping(
    value: Mapping[str, Any], where: str
) -> Mapping[str, Any]:
    """Reconstruct and replay a diagnostic from independently read bytes."""

    if frozenset(value) != _DIAGNOSTIC_FIELDS:
        raise SealingError("%s has the wrong diagnostic field set" % where)
    if value.get("schema") != _DIAGNOSTIC_SCHEMA:
        raise SealingError("%s has the wrong diagnostic schema" % where)
    if value.get("arm") != Arm.DELAYED_EXACT.value:
        raise SealingError("%s is not delayed_exact evidence" % where)
    if value.get("arm_semantic_label") != "DIAGNOSTIC_UPPER_BOUND":
        raise SealingError("%s has the wrong arm semantic label" % where)
    if _require_bool(value.get("synthetic_test_mode"), "diagnostic mode"):
        raise SealingError("official diagnostic used synthetic test mode")
    config = _require_mapping(value.get("config"), "diagnostic config")
    if dict(config) != _DIAGNOSTIC_CONFIG:
        raise SealingError("%s has the wrong diagnostic config" % where)
    config_identity_sha256 = _require_sha(
        value.get("config_identity_sha256"), "diagnostic config hash"
    )
    if canonical_sha256(config) != config_identity_sha256:
        raise SealingError("%s config hash differs" % where)

    input_count = _require_int(value.get("input_count"), "diagnostic input count")
    input_pose_count = _require_int(
        value.get("input_pose_count"), "diagnostic input pose count"
    )
    retired_count = _require_int(
        value.get("retired_count"), "diagnostic retired count"
    )
    simulation_iterations = _require_int(
        value.get("simulation_iterations"), "diagnostic simulation iterations"
    )
    termination_iteration_bound = _require_int(
        value.get("termination_iteration_bound"),
        "diagnostic termination iteration bound",
    )
    peak_fifo_depth = _require_int(
        value.get("peak_fifo_depth"), "diagnostic peak FIFO depth"
    )
    if (
        not _require_bool(value.get("termination_proven"), "termination proof")
        or not _require_bool(
            value.get("queue_never_exceeded_input_count"), "queue bound proof"
        )
        or not _require_bool(
            value.get("exact_once_ordered_conservation"), "conservation proof"
        )
        or not _require_bool(
            value.get("no_full_pressure_reasons"), "no-pressure proof"
        )
        or simulation_iterations > termination_iteration_bound
        or peak_fifo_depth > input_count
    ):
        raise SealingError("%s progress or queue proof differs" % where)

    supplied_evidence_sha256 = _require_sha(
        value.get("evidence_sha256"), "diagnostic evidence hash"
    )
    body = dict(value)
    del body["evidence_sha256"]
    if canonical_sha256(body) != supplied_evidence_sha256:
        raise SealingError("%s evidence hash differs" % where)

    event_values = _require_array(value.get("input_events"), "diagnostic events")
    pose_values = _require_array(value.get("input_poses"), "diagnostic poses")
    if len(event_values) != input_count or len(pose_values) != input_pose_count:
        raise SealingError("%s complete-input counts differ" % where)
    events = []  # type: List[Event]
    poses = []  # type: List[PosePacket]
    try:
        for index, raw in enumerate(event_values):
            row = _require_mapping(raw, "diagnostic event[%d]" % index)
            if frozenset(row) != _DIAGNOSTIC_EVENT_FIELDS:
                raise SealingError("diagnostic event field set differs")
            pose_index = row["causal_pose_index"]
            if pose_index is not None:
                pose_index = _require_int(
                    pose_index, "diagnostic event causal_pose_index"
                )
            events.append(Event(
                _require_int(row["event_id"], "diagnostic event_id"),
                _require_int(row["timestamp_ns"], "diagnostic timestamp"),
                _require_bool(
                    row["transform_guard_valid"],
                    "diagnostic transform_guard_valid",
                ),
                pose_index,
            ))
        for index, raw in enumerate(pose_values):
            row = _require_mapping(raw, "diagnostic pose[%d]" % index)
            if frozenset(row) != _DIAGNOSTIC_POSE_FIELDS:
                raise SealingError("diagnostic pose field set differs")
            source_value = row["source"]
            if type(source_value) is not str:
                raise SealingError("diagnostic pose source must be text")
            poses.append(PosePacket(
                _require_int(row["pose_id"], "diagnostic pose_id"),
                _require_int(row["timestamp_ns"], "diagnostic pose timestamp"),
                _require_int(
                    row["commit_cycle"], "diagnostic pose commit_cycle", -(1 << 63)
                ),
                PoseSource(source_value),
                _require_sha(row["pose_sha256"], "diagnostic pose hash"),
                _require_bool(row["value_valid"], "diagnostic pose value_valid"),
                _require_bool(
                    row["arithmetic_valid"],
                    "diagnostic pose arithmetic_valid",
                ),
            ))
        replay = run_delayed_unbounded_diagnostic(
            window_id=str(value.get("window_id")),
            window_start_ns=_require_int(
                value.get("window_start_ns"), "diagnostic window_start_ns"
            ),
            events=tuple(events),
            poses=tuple(poses),
            synthetic_test_mode=False,
        )
    except (CycleModelError, ValueError) as exc:
        raise SealingError("%s cannot be independently replayed" % where) from exc
    if replay.to_mapping() != dict(value):
        raise SealingError("%s differs from independent replay" % where)
    return {
        "schema": _DIAGNOSTIC_SCHEMA,
        "evidence_sha256": supplied_evidence_sha256,
        "config_schema": config["schema"],
        "config_identity_sha256": config_identity_sha256,
        "termination_guard_rule": config["termination_guard_rule"],
        "queue_bound_rule": config["queue_bound_rule"],
        "input_events_sha256": _require_sha(
            value.get("input_events_sha256"), "diagnostic input event hash"
        ),
        "input_poses_sha256": _require_sha(
            value.get("input_poses_sha256"), "diagnostic input pose hash"
        ),
        "input_event_ids_sha256": _require_sha(
            value.get("input_event_ids_sha256"), "diagnostic input ID hash"
        ),
        "retired_event_ids_sha256": _require_sha(
            value.get("retired_event_ids_sha256"), "diagnostic retired ID hash"
        ),
        "input_count": input_count,
        "input_pose_count": input_pose_count,
        "retired_count": retired_count,
        "exact_once_ordered_conservation": True,
        "no_full_pressure_reasons": True,
        "termination_proven": True,
        "queue_never_exceeded_input_count": True,
        "simulation_iterations": simulation_iterations,
        "termination_iteration_bound": termination_iteration_bound,
        "decision_records_sha256": _require_sha(
            value.get("decision_records_sha256"), "diagnostic decision hash"
        ),
        "cycle_receipts_sha256": _require_sha(
            value.get("cycle_receipts_sha256"), "diagnostic receipt hash"
        ),
        "peak_fifo_depth": peak_fifo_depth,
        "peak_ingress_staging_occupancy": _require_int(
            value.get("peak_ingress_staging_occupancy"),
            "diagnostic peak ingress staging occupancy",
        ),
        "pose_ring_accounting_sha256": _require_sha(
            value.get("pose_ring_accounting_sha256"),
            "diagnostic pose-ring accounting hash",
        ),
        "window_id": value.get("window_id"),
        "window_start_ns": value.get("window_start_ns"),
    }


def _packet_hashes(
    records: Sequence[Mapping[str, Any]], where: str
) -> Tuple[str, ...]:
    hashes = []  # type: List[str]
    for index, record in enumerate(records):
        body = dict(record)
        supplied = _require_sha(
            body.pop("packet_sha256", None), "%s[%d]" % (where, index)
        )
        if canonical_sha256(body) != supplied:
            raise SealingError("%s packet hash differs" % where)
        hashes.append(supplied)
    return tuple(hashes)


def _snapshot_hashes(
    records: Sequence[Mapping[str, Any]]
) -> Tuple[str, ...]:
    hashes = []  # type: List[str]
    for index, record in enumerate(records):
        body = dict(record)
        supplied = _require_sha(
            body.pop("pose_snapshot_sha256", None), "snapshot[%d]" % index
        )
        if canonical_sha256(body) != supplied:
            raise SealingError("snapshot hash differs")
        hashes.append(supplied)
    return tuple(hashes)


def _observe_assay(
    assay_dir: Path, expected_manifest_sha256: str
) -> Tuple[Mapping[str, Any], Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    expected = _require_sha(expected_manifest_sha256, "expected assay manifest")
    manifest_value, manifest_payload = _read_json_at(assay_dir, _ASSAY_MANIFEST)
    if not isinstance(manifest_value, Mapping):
        raise SealingError("assay manifest must be an object")
    manifest = manifest_value
    manifest_sha256 = _sha256(manifest_payload)
    if manifest_sha256 != expected:
        raise SealingError("assay manifest differs from the caller-supplied root")
    if (
        manifest.get("schema") != "redred.mc_wtb.stage4_score_free_inputs/v2"
        or manifest.get("provenance_scope")
        != "OFFICIAL_HASH_PINNED_DEVELOPMENT_INPUT"
        or manifest.get("fixture_label") is not None
    ):
        raise SealingError("assay manifest is not an official score-free input")

    contract = load_comparison_contract()
    registry = manifest.get("registry")
    if not isinstance(registry, Mapping) or registry != {
        "window_count": contract.registry["window_count"],
        "sha256": contract.registry["sha256"],
        "query_event_count": contract.registry["query_event_count"],
        "forbidden_interval_ns": contract.registry["forbidden_interval_ns"],
        "forbidden_interval_selected_records": 0,
    }:
        raise SealingError("official assay registry binding differs")
    if manifest.get("comparison_contract_sha256") != contract.canonical_sha256:
        raise SealingError("official assay contract binding differs")

    frozen_windows = tuple(window_registry())
    windows = manifest.get("windows")
    if not isinstance(windows, list) or len(windows) != len(frozen_windows):
        raise SealingError("official assay must contain exactly 24 windows")
    for summary, frozen in zip(windows, frozen_windows):
        if not isinstance(summary, Mapping):
            raise SealingError("assay window summary is not an object")
        for field in (
            "window_id",
            "warmup_start_ns_inclusive",
            "query_start_ns_inclusive",
            "query_end_ns_exclusive",
        ):
            if summary.get(field) != frozen[field]:
                raise SealingError("assay window registry projection differs")
    if sum(
        _require_int(row.get("query_event_count"), "assay window query_event_count")
        for row in windows
    ) != _require_int(
        contract.registry["query_event_count"], "contract query_event_count"
    ):
        raise SealingError("assay window query counts do not conserve")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(_ASSAY_STREAMS):
        raise SealingError("assay artifact set differs")
    records = {}  # type: Dict[str, Tuple[Mapping[str, Any], ...]]
    stream_seals = {}  # type: Dict[str, Mapping[str, Any]]
    for name in _ASSAY_STREAMS:
        rows, payload = _read_jsonl_at(assay_dir, name)
        artifact = artifacts.get(name)
        if isinstance(artifact, Mapping):
            _require_int(artifact.get("record_count"), "%s record_count" % name)
            _require_int(artifact.get("size_bytes"), "%s size_bytes" % name)
        if not isinstance(artifact, Mapping) or artifact != {
            "path": name,
            "sha256": _sha256(payload),
            "record_count": len(rows),
            "size_bytes": len(payload),
        }:
            raise SealingError("assay stream authority differs: %s" % name)
        records[name] = rows
        stream_seals[name] = dict(artifact)

    authority = manifest.get("authoritative_input_binding")
    if not isinstance(authority, Mapping):
        raise SealingError("assay authority is absent")
    authority_body = dict(authority)
    authority_sha256 = _require_sha(
        authority_body.pop("binding_sha256", None), "assay authority"
    )
    if canonical_sha256(authority_body) != authority_sha256:
        raise SealingError("assay authority canonical hash differs")

    calibration = authority.get("calibration_model")
    if not isinstance(calibration, Mapping):
        raise SealingError("calibration authority is absent")
    calibration_body = dict(calibration)
    calibration_sha256 = _require_sha(
        calibration_body.pop("authority_sha256", None), "calibration authority"
    )
    if canonical_sha256(calibration_body) != calibration_sha256:
        raise SealingError("calibration authority canonical hash differs")
    raw_sources = authority.get("raw_source_streams")
    source = manifest.get("source")
    if (
        not isinstance(raw_sources, Mapping)
        or not isinstance(source, Mapping)
        or calibration_body.get("source_sha256")
        != raw_sources.get("calib.txt_sha256")
        or calibration_body.get("source_sha256") != source.get("calibration_sha256")
    ):
        raise SealingError("calibration source binding differs")

    events = records["stage4_events.jsonl"]
    if sum(row.get("is_query") is True for row in events) != _require_int(
        contract.registry["query_event_count"], "contract query_event_count"
    ):
        raise SealingError("assay query event count differs")
    ray_projection = {
        "calibration_authority_sha256": calibration_sha256,
        "events": [
            {
                field: row.get(field)
                for field in (
                    "window_id",
                    "event_id",
                    "payload_hex",
                    "x",
                    "y",
                    "sensor_ray",
                )
            }
            for row in events
        ],
    }
    dataset_hashes = _packet_hashes(
        records["stage4_dataset_pose_packets.jsonl"], "dataset packet"
    )
    oracle_hashes = _packet_hashes(
        records["oracle_resampled_groundtruth_1khz.jsonl"], "oracle packet"
    )
    snapshot_hashes = _snapshot_hashes(
        records["stage4_occurrence_pose_snapshots.jsonl"]
    )
    schedule_hashes = tuple(
        _require_sha(row.get("packet_sha256"), "oracle schedule packet")
        for row in records["stage4_oracle_window_schedule.jsonl"]
    )
    closure = {
        "schema": "redred.mc_wtb.stage4_score_free_assay_closure/v1",
        "assay_manifest_sha256": manifest_sha256,
        "assay_authority_sha256": authority_sha256,
        "comparison_contract_sha256": contract.canonical_sha256,
        "registry_sha256": contract.registry["sha256"],
        "streams": stream_seals,
        "calibration": {
            "calib_txt_sha256": calibration_body["source_sha256"],
            "authority_sha256": calibration_sha256,
            "model_sha256": canonical_sha256(calibration_body.get("model")),
            "sensor_ray_generator_rule": calibration_body.get(
                "sensor_ray_generator_rule"
            ),
            "payload_bound_ray_projection_sha256": canonical_sha256(
                ray_projection
            ),
        },
        "packet_and_snapshot_roots": {
            "dataset_packet_sha256": canonical_sha256(list(dataset_hashes)),
            "oracle_packet_sha256": canonical_sha256(list(oracle_hashes)),
            "oracle_schedule_packet_sha256": canonical_sha256(
                list(schedule_hashes)
            ),
            "occurrence_snapshot_sha256": canonical_sha256(
                list(snapshot_hashes)
            ),
        },
    }
    return manifest, closure, tuple(windows)


def _full_cycle_mapping(sealed: Any) -> Mapping[str, Any]:
    value = integration_adapter._full_cycle_evidence(sealed.simulation)
    if not isinstance(value, Mapping):
        raise SealingError("full cycle evidence is not an object")
    return value


_AUTHORITATIVE_INPUT_FIELDS = frozenset((
    "schema",
    "window_id",
    "window_start_ns",
    "input_events_sha256",
    "input_poses_sha256",
    "input_event_ids_sha256",
    "input_count",
    "input_pose_count",
))
_MINIMUM_DEPTH_FIELDS = frozenset((
    "basis",
    "bounded_peak_buffer_entries",
    "fifo_full_forced_bypass_event_ids",
    "minimum_zero_loss_buffer_entries",
    "bounded_decision_records_sha256",
    "bounded_cycle_receipts_sha256",
    "unbounded_diagnostic_evidence_sha256",
    "unbounded_diagnostic_config_sha256",
    "unbounded_diagnostic_decision_records_sha256",
    "unbounded_diagnostic_cycle_receipts_sha256",
))


def _event_input_mapping(event: Event) -> Mapping[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp_ns": event.timestamp_ns,
        "transform_guard_valid": event.transform_guard_valid,
        "causal_pose_index": event.causal_pose_index,
    }


def _pose_input_mapping(pose: PosePacket) -> Mapping[str, Any]:
    return {
        "pose_id": pose.pose_id,
        "timestamp_ns": pose.timestamp_ns,
        "commit_cycle": pose.commit_cycle,
        "source": pose.source.value,
        "pose_sha256": pose.pose_sha256,
        "value_valid": pose.value_valid,
        "arithmetic_valid": pose.arithmetic_valid,
    }


def _authoritative_window_inputs(inputs: Any) -> Mapping[str, Any]:
    events = tuple(inputs.events)
    poses = tuple(inputs.dataset_poses)
    return {
        "schema": "redred.mc_wtb.stage4_authoritative_window_cycle_inputs/v1",
        "window_id": inputs.window_id,
        "window_start_ns": inputs.window_start_ns,
        "input_events_sha256": canonical_sha256(
            [_event_input_mapping(event) for event in events]
        ),
        "input_poses_sha256": canonical_sha256(
            [_pose_input_mapping(pose) for pose in poses]
        ),
        "input_event_ids_sha256": canonical_sha256(
            [event.event_id for event in events]
        ),
        "input_count": len(events),
        "input_pose_count": len(poses),
    }


def _validate_authoritative_window_inputs(
    value: Any, window_id: str, window_start_ns: int
) -> Mapping[str, Any]:
    binding = _require_mapping(value, "authoritative window cycle inputs")
    if frozenset(binding) != _AUTHORITATIVE_INPUT_FIELDS:
        raise SealingError("authoritative window input field set differs")
    expected_identity = {
        "schema": "redred.mc_wtb.stage4_authoritative_window_cycle_inputs/v1",
        "window_id": window_id,
        "window_start_ns": window_start_ns,
    }
    if any(binding.get(name) != expected for name, expected in expected_identity.items()):
        raise SealingError("authoritative window input identity differs")
    for name in (
        "input_events_sha256",
        "input_poses_sha256",
        "input_event_ids_sha256",
    ):
        _require_sha(binding.get(name), "authoritative %s" % name)
    _require_int(binding.get("input_count"), "authoritative input_count")
    _require_int(binding.get("input_pose_count"), "authoritative input_pose_count")
    _require_int(binding.get("window_start_ns"), "authoritative window_start_ns")
    return binding


def _derive_bounded_depth_evidence(
    full_cycle: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    contract_document = load_comparison_contract().as_dict()
    score_free_accounting = _require_mapping(
        contract_document.get("score_free_accounting"),
        "comparison contract score-free accounting",
    )
    delayed_fifo = _require_mapping(
        score_free_accounting.get("delayed_fifo"),
        "comparison contract delayed FIFO",
    )
    bounded_entries = _require_int(
        delayed_fifo.get("bounded_entries"),
        "comparison contract delayed FIFO bounded_entries",
    )
    if (
        _require_int(full_cycle.get("buffer_entries"), "bounded buffer_entries")
        != bounded_entries
    ):
        raise SealingError("bounded buffer_entries differs from frozen contract")
    records = _require_array(
        full_cycle.get("decision_records"), "bounded full-cycle decision records"
    )
    embedded_receipts = _require_array(
        full_cycle.get("cycle_receipts"), "bounded full-cycle receipts"
    )
    receipt_values = [dict(row) for row in receipts]
    if embedded_receipts != receipt_values or len(records) != len(receipt_values):
        raise SealingError("bounded full-cycle arrays do not conserve")
    decision_sha256 = _require_sha(
        full_cycle.get("decision_records_sha256"), "bounded decision records hash"
    )
    receipt_sha256 = _require_sha(
        full_cycle.get("cycle_receipts_sha256"), "bounded cycle receipts hash"
    )
    if (
        canonical_sha256(records) != decision_sha256
        or canonical_sha256(receipt_values) != receipt_sha256
    ):
        raise SealingError("bounded decision or receipt stream hash differs")

    pressure_ids = []  # type: List[int]
    peak = 0
    seen_ids = set()
    for index, (record_value, receipt) in enumerate(zip(records, receipt_values)):
        record = _require_mapping(record_value, "bounded record[%d]" % index)
        event_id = _require_int(receipt.get("event_id"), "bounded receipt event_id")
        if event_id in seen_ids or record.get("event_id") != event_id:
            raise SealingError("bounded record/receipt event IDs differ")
        seen_ids.add(event_id)
        if (
            receipt.get("disposition") != record.get("disposition")
            or receipt.get("disposition_reason") != record.get("disposition_reason")
            or receipt.get("decision_record_sha256") != canonical_sha256(record)
        ):
            raise SealingError("bounded record/receipt disposition binding differs")
        occupancies = tuple(
            _require_int(receipt.get(name), "bounded receipt %s" % name)
            for name in (
                "fifo_occupancy_before_admission",
                "fifo_occupancy_after_admission",
                "fifo_occupancy_before_retire",
                "fifo_occupancy_after_retire",
            )
        )
        if any(occupancy > bounded_entries for occupancy in occupancies):
            raise SealingError("bounded receipt occupancy exceeds FIFO capacity")
        peak = max((peak,) + occupancies)
        if receipt.get("disposition_reason") == "fifo_full_forced_bypass":
            pressure_ids.append(event_id)
    bounded_peak = _require_int(
        full_cycle.get("peak_buffer_occupancy"), "bounded peak buffer occupancy"
    )
    if bounded_peak > bounded_entries:
        raise SealingError("bounded receipt-derived peak exceeds FIFO capacity")
    if peak != bounded_peak:
        raise SealingError("bounded receipt-derived peak differs")
    return {
        "bounded_peak_buffer_entries": bounded_peak,
        "fifo_full_forced_bypass_event_ids": tuple(pressure_ids),
        "bounded_decision_records_sha256": decision_sha256,
        "bounded_cycle_receipts_sha256": receipt_sha256,
    }


def _validate_minimum_depth_accounting(
    accounting_evidence: Mapping[str, Any],
    bounded: Mapping[str, Any],
    diagnostic: Any,
) -> None:
    minimum = _require_mapping(
        accounting_evidence.get("minimum_depth_evidence"),
        "accounting minimum-depth evidence",
    )
    if frozenset(minimum) != _MINIMUM_DEPTH_FIELDS:
        raise SealingError("minimum-depth accounting field set differs")
    if accounting_evidence.get("minimum_depth_evidence_sha256") != canonical_sha256(
        minimum
    ):
        raise SealingError("minimum-depth accounting hash differs")
    pressure_ids = list(bounded["fifo_full_forced_bypass_event_ids"])
    expected = {
        "bounded_peak_buffer_entries": bounded["bounded_peak_buffer_entries"],
        "fifo_full_forced_bypass_event_ids": pressure_ids,
        "bounded_decision_records_sha256": bounded[
            "bounded_decision_records_sha256"
        ],
        "bounded_cycle_receipts_sha256": bounded[
            "bounded_cycle_receipts_sha256"
        ],
    }
    if diagnostic is None:
        expected.update({
            "basis": "bounded_peak_no_full_pressure",
            "minimum_zero_loss_buffer_entries": bounded[
                "bounded_peak_buffer_entries"
            ],
            "unbounded_diagnostic_evidence_sha256": None,
            "unbounded_diagnostic_config_sha256": None,
            "unbounded_diagnostic_decision_records_sha256": None,
            "unbounded_diagnostic_cycle_receipts_sha256": None,
        })
        if pressure_ids:
            raise SealingError("bounded pressure lacks its unbounded diagnostic")
    else:
        entry = _require_mapping(diagnostic, "delayed diagnostic file entry")
        expected.update({
            "basis": "independent_no_pressure_replay_peak",
            "minimum_zero_loss_buffer_entries": entry["peak_fifo_depth"],
            "unbounded_diagnostic_evidence_sha256": entry["evidence_sha256"],
            "unbounded_diagnostic_config_sha256": entry[
                "config_identity_sha256"
            ],
            "unbounded_diagnostic_decision_records_sha256": entry[
                "decision_records_sha256"
            ],
            "unbounded_diagnostic_cycle_receipts_sha256": entry[
                "cycle_receipts_sha256"
            ],
        })
        if not pressure_ids:
            raise SealingError("unbounded diagnostic is attached without bounded pressure")
    if dict(minimum) != expected:
        raise SealingError("minimum-depth accounting binding differs")


def _validate_accounting_totals(
    accounting: Mapping[str, Any],
    accounting_evidence: Mapping[str, Any],
    bounded: Mapping[str, Any],
    diagnostic: Any,
    window_id: str,
    arm_name: str,
) -> None:
    if (
        accounting.get("window_id") != window_id
        or accounting.get("arm") != arm_name
        or accounting_evidence.get("window_id") != window_id
        or accounting_evidence.get("arm") != arm_name
    ):
        raise SealingError("score-free accounting leaf identity differs")
    expected_minimum = (
        bounded["bounded_peak_buffer_entries"]
        if diagnostic is None
        else diagnostic["peak_fifo_depth"]
    )
    if (
        _require_int(accounting.get("peak_buffer_entries"), "accounting bounded peak")
        != bounded["bounded_peak_buffer_entries"]
        or _require_int(
            accounting.get("minimum_zero_loss_buffer_entries"),
            "accounting minimum depth",
        )
        != expected_minimum
    ):
        raise SealingError("score-free accounting bounded or minimum depth differs")
    _validate_minimum_depth_accounting(
        accounting_evidence, bounded, diagnostic
    )


def _write_leaf_inputs(
    output_root: Path,
    relative_root: str,
    sealed: Any,
) -> None:
    _write_json(
        output_root / relative_root / "full-cycle-result.json",
        _full_cycle_mapping(sealed),
    )
    _write_json(
        output_root / relative_root / "cycle-receipts.json",
        [_to_mapping(row, "cycle receipt") for row in sealed.simulation.cycle_receipts],
    )
    _write_json(
        output_root / relative_root / "query-decision-records.json",
        [_to_mapping(row, "query decision") for row in sealed.query_records],
    )
    _write_json(
        output_root / relative_root / "decision-receipt.json",
        _to_mapping(sealed.receipt, "decision receipt"),
    )
    _write_json(
        output_root / relative_root / "score-free-accounting.json",
        _to_mapping(sealed.accounting, "score-free accounting"),
    )
    _write_json(
        output_root / relative_root / "score-free-accounting-evidence.json",
        _to_mapping(sealed.accounting_evidence, "accounting evidence"),
    )
    diagnostic = sealed.delayed_unbounded_diagnostic
    if diagnostic is not None:
        if sealed.arm is not Arm.DELAYED_EXACT:
            raise SealingError("unbounded diagnostic is attached to a non-delayed arm")
        _write_json(
            output_root / relative_root / _DELAYED_DIAGNOSTIC_FILE,
            _to_mapping(diagnostic, "delayed unbounded diagnostic"),
        )


def _admission_projection(receipts: Sequence[Mapping[str, Any]]) -> Tuple[Any, ...]:
    return tuple(
        (
            row.get("event_id"),
            row.get("occurrence_cycle"),
            row.get("admission_cycle"),
            row.get("admission_lane"),
        )
        for row in receipts
    )


def _observe_delayed_diagnostic(
    output_root: Path,
    relative_root: str,
    sealed: Any,
    assay_manifest_sha256: str,
    observed: Mapping[str, Mapping[str, Any]],
    bounded_full_cycle: Mapping[str, Any],
    bounded_receipts: Sequence[Mapping[str, Any]],
    accounting_evidence: Mapping[str, Any],
    authoritative_inputs: Mapping[str, Any],
    files: Dict[str, Mapping[str, Any]],
) -> Any:
    diagnostic = sealed.delayed_unbounded_diagnostic
    bounded = _derive_bounded_depth_evidence(
        bounded_full_cycle, bounded_receipts
    )
    bounded_full = bool(bounded["fifo_full_forced_bypass_event_ids"])
    if diagnostic is None:
        _validate_minimum_depth_accounting(accounting_evidence, bounded, None)
        return None
    if sealed.arm is not Arm.DELAYED_EXACT or not bounded_full:
        raise SealingError("unbounded diagnostic is outside a pressured delayed leaf")

    relative = "%s/%s" % (relative_root, _DELAYED_DIAGNOSTIC_FILE)
    entry = _observe_file(
        output_root,
        relative,
        kind=_DELAYED_DIAGNOSTIC_KIND,
        record_count=1,
    )
    files[relative] = entry
    diagnostic_value, _ = _read_indexed_json(
        output_root, relative, files, "delayed diagnostic"
    )
    diagnostic_mapping = _require_mapping(
        diagnostic_value, "delayed diagnostic leaf"
    )
    diagnostic_receipts = _require_array(
        diagnostic_mapping.get("cycle_receipts"), "diagnostic cycle receipts"
    )
    if _admission_projection(bounded_receipts) != _admission_projection(
        tuple(_require_mapping(row, "diagnostic receipt") for row in diagnostic_receipts)
    ):
        raise SealingError("bounded and unbounded admission schedules differ")
    for diagnostic_name, authoritative_name in (
        ("input_events_sha256", "input_events_sha256"),
        ("input_poses_sha256", "input_poses_sha256"),
        ("input_event_ids_sha256", "input_event_ids_sha256"),
        ("input_count", "input_count"),
        ("input_pose_count", "input_pose_count"),
        ("window_id", "window_id"),
        ("window_start_ns", "window_start_ns"),
    ):
        if entry[diagnostic_name] != authoritative_inputs[authoritative_name]:
            raise SealingError(
                "diagnostic differs from authoritative window inputs"
            )
    if entry["retired_count"] != authoritative_inputs["input_count"]:
        raise SealingError("diagnostic retired count differs from authoritative input")
    if entry["peak_fifo_depth"] <= bounded["bounded_peak_buffer_entries"]:
        raise SealingError("diagnostic peak is not pressure-revealing")
    if observed["cycle-receipts.json"]["sha256"] != bounded[
        "bounded_cycle_receipts_sha256"
    ]:
        raise SealingError("bounded receipt file hash differs from receipt stream")
    _validate_minimum_depth_accounting(accounting_evidence, bounded, entry)
    return {
        "path": relative,
        "sha256": entry["sha256"],
        "schema": entry["schema"],
        "evidence_sha256": entry["evidence_sha256"],
        "config_schema": entry["config_schema"],
        "config_identity_sha256": entry["config_identity_sha256"],
        "termination_guard_rule": entry["termination_guard_rule"],
        "queue_bound_rule": entry["queue_bound_rule"],
        "input_events_sha256": entry["input_events_sha256"],
        "input_poses_sha256": entry["input_poses_sha256"],
        "input_event_ids_sha256": entry["input_event_ids_sha256"],
        "retired_event_ids_sha256": entry["retired_event_ids_sha256"],
        "input_count": entry["input_count"],
        "input_pose_count": entry["input_pose_count"],
        "retired_count": entry["retired_count"],
        "exact_once_ordered_conservation": entry[
            "exact_once_ordered_conservation"
        ],
        "no_full_pressure_reasons": entry["no_full_pressure_reasons"],
        "termination_proven": entry["termination_proven"],
        "queue_never_exceeded_input_count": entry[
            "queue_never_exceeded_input_count"
        ],
        "simulation_iterations": entry["simulation_iterations"],
        "termination_iteration_bound": entry["termination_iteration_bound"],
        "decision_records_sha256": entry["decision_records_sha256"],
        "cycle_receipts_sha256": entry["cycle_receipts_sha256"],
        "peak_fifo_depth": entry["peak_fifo_depth"],
        "peak_ingress_staging_occupancy": entry[
            "peak_ingress_staging_occupancy"
        ],
        "pose_ring_accounting_sha256": entry[
            "pose_ring_accounting_sha256"
        ],
        "window_id": entry["window_id"],
        "window_start_ns": entry["window_start_ns"],
        "assay_authoritative_input_manifest_sha256": assay_manifest_sha256,
        "bounded_full_cycle_result_sha256": observed["full-cycle-result.json"][
            "sha256"
        ],
        "bounded_cycle_receipts_sha256": observed["cycle-receipts.json"][
            "sha256"
        ],
    }


def _observe_leaf(
    output_root: Path,
    relative_root: str,
    sealed: Any,
    assay_manifest_sha256: str,
    ray_events_sha256: str,
    expected_query_count: int,
    authoritative_inputs: Mapping[str, Any],
    files: Dict[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    if len(sealed.query_records) != expected_query_count:
        raise SealingError("query projection count differs from its window summary")
    counts = {
        "full-cycle-result.json": 1,
        "cycle-receipts.json": len(sealed.simulation.cycle_receipts),
        "query-decision-records.json": len(sealed.query_records),
        "decision-receipt.json": 1,
        "score-free-accounting.json": 1,
        "score-free-accounting-evidence.json": 1,
    }
    kinds = dict((name, "object") for name in counts)
    kinds["cycle-receipts.json"] = "array"
    kinds["query-decision-records.json"] = "array"
    observed = {}  # type: Dict[str, Mapping[str, Any]]
    for name in counts:
        relative = "%s/%s" % (relative_root, name)
        entry = _observe_file(
            output_root,
            relative,
            kind=kinds[name],
            record_count=counts[name],
        )
        files[relative] = entry
        observed[name] = entry

    full_value, _ = _read_indexed_json(
        output_root,
        "%s/full-cycle-result.json" % relative_root,
        files,
        "bounded full-cycle result",
    )
    cycle_value, _ = _read_indexed_json(
        output_root,
        "%s/cycle-receipts.json" % relative_root,
        files,
        "bounded cycle receipts",
    )
    query_value, _ = _read_indexed_json(
        output_root,
        "%s/query-decision-records.json" % relative_root,
        files,
        "query decisions",
    )
    receipt_value, _ = _read_indexed_json(
        output_root,
        "%s/decision-receipt.json" % relative_root,
        files,
        "decision receipt",
    )
    accounting_value, _ = _read_indexed_json(
        output_root,
        "%s/score-free-accounting.json" % relative_root,
        files,
        "score-free accounting",
    )
    accounting_evidence_value, _ = _read_indexed_json(
        output_root,
        "%s/score-free-accounting-evidence.json" % relative_root,
        files,
        "score-free accounting evidence",
    )
    manifest_mapping = dict(_to_mapping(sealed.manifest, "score input manifest"))
    boundary = {
        "schema": "redred.mc_wtb.stage4_score_boundary_evidence/v1",
        "assay_authoritative_input_manifest_sha256": assay_manifest_sha256,
        "full_cycle_result_sha256": observed["full-cycle-result.json"]["sha256"],
        "cycle_receipts_sha256": observed["cycle-receipts.json"]["sha256"],
        "query_projection_sha256": observed["query-decision-records.json"]["sha256"],
    }
    expected_manifest_fields = {
        "assay_authoritative_input_manifest_sha256": boundary[
            "assay_authoritative_input_manifest_sha256"
        ],
        "full_cycle_result_sha256": boundary["full_cycle_result_sha256"],
        "cycle_receipts_sha256": boundary["cycle_receipts_sha256"],
        "query_projection_sha256": boundary["query_projection_sha256"],
        "decision_receipt_sha256": observed["decision-receipt.json"]["sha256"],
        "score_free_accounting_sha256": observed[
            "score-free-accounting.json"
        ]["sha256"],
        "ray_events_sha256": ray_events_sha256,
    }
    if any(
        manifest_mapping.get(field) != value
        for field, value in expected_manifest_fields.items()
    ):
        raise SealingError("score input manifest differs from observed leaf bytes")
    if (
        not isinstance(full_value, Mapping)
        or full_value.get("cycle_receipts_sha256")
        != observed["cycle-receipts.json"]["sha256"]
        or not isinstance(cycle_value, list)
        or not isinstance(query_value, list)
        or not isinstance(receipt_value, Mapping)
        or receipt_value.get("decision_records_sha256")
        != observed["query-decision-records.json"]["sha256"]
        or _require_int(
            receipt_value.get("expected_events"), "receipt expected_events"
        )
        != len(query_value)
        or _require_int(
            receipt_value.get("retired_records"), "receipt retired_records"
        )
        != len(query_value)
    ):
        raise SealingError("receipt or cycle evidence differs from observed arrays")

    _write_json(output_root / relative_root / "score-boundary-evidence.json", boundary)
    _write_json(
        output_root / relative_root / "score-input-manifest.json", manifest_mapping
    )
    for name in ("score-boundary-evidence.json", "score-input-manifest.json"):
        relative = "%s/%s" % (relative_root, name)
        entry = _observe_file(
            output_root, relative, kind="object", record_count=1
        )
        files[relative] = entry
        observed[name] = entry
    diagnostic_binding = _observe_delayed_diagnostic(
        output_root,
        relative_root,
        sealed,
        assay_manifest_sha256,
        observed,
        _require_mapping(full_value, "bounded full-cycle result"),
        tuple(_require_mapping(row, "bounded cycle receipt") for row in cycle_value),
        _require_mapping(accounting_evidence_value, "accounting evidence"),
        authoritative_inputs,
        files,
    )
    bounded = _derive_bounded_depth_evidence(
        _require_mapping(full_value, "bounded full-cycle result"),
        tuple(_require_mapping(row, "bounded cycle receipt") for row in cycle_value),
    )
    diagnostic_entry = None
    if diagnostic_binding is not None:
        diagnostic_entry = files[diagnostic_binding["path"]]
    _validate_accounting_totals(
        _require_mapping(accounting_value, "score-free accounting"),
        _require_mapping(accounting_evidence_value, "accounting evidence"),
        bounded,
        diagnostic_entry,
        sealed.simulation.window_id,
        sealed.arm.value,
    )
    return {
        "score_input_manifest_path": "%s/score-input-manifest.json" % relative_root,
        "score_input_manifest_sha256": observed["score-input-manifest.json"][
            "sha256"
        ],
        "score_boundary_evidence_path": "%s/score-boundary-evidence.json"
        % relative_root,
        "score_boundary_evidence_sha256": observed[
            "score-boundary-evidence.json"
        ]["sha256"],
        "delayed_unbounded_depth_diagnostic": diagnostic_binding,
    }


_CAMPAIGN_FIELDS = frozenset((
    "schema",
    "content_class",
    "assay_manifest_sha256",
    "assay_authority_sha256",
    "assay_closure_sha256",
    "comparison_contract_sha256",
    "registry_sha256",
    "window_count",
    "arm_count",
    "arm_window_count",
    "window_order",
    "arm_order",
    "windows",
    "files",
))
_WINDOW_FIELDS = frozenset((
    "schema",
    "window_id",
    "warmup_start_ns_inclusive",
    "query_start_ns_inclusive",
    "query_end_ns_exclusive",
    "selected_event_count",
    "query_event_count",
    "ordered_query_event_ids_sha256",
    "ray_events_path",
    "ray_events_sha256",
    "authoritative_cycle_inputs",
    "arms",
))
_ARM_BINDING_FIELDS = frozenset((
    "score_input_manifest_path",
    "score_input_manifest_sha256",
    "score_boundary_evidence_path",
    "score_boundary_evidence_sha256",
    "delayed_unbounded_depth_diagnostic",
))
_DIAGNOSTIC_BINDING_FIELDS = frozenset((
    "path", "sha256", "schema", "evidence_sha256", "config_schema",
    "config_identity_sha256", "termination_guard_rule", "queue_bound_rule",
    "input_events_sha256", "input_poses_sha256", "input_event_ids_sha256",
    "retired_event_ids_sha256", "input_count", "input_pose_count",
    "retired_count", "exact_once_ordered_conservation",
    "no_full_pressure_reasons", "termination_proven",
    "queue_never_exceeded_input_count", "simulation_iterations",
    "termination_iteration_bound", "decision_records_sha256",
    "cycle_receipts_sha256", "peak_fifo_depth",
    "peak_ingress_staging_occupancy", "pose_ring_accounting_sha256",
    "window_id", "window_start_ns",
    "assay_authoritative_input_manifest_sha256",
    "bounded_full_cycle_result_sha256", "bounded_cycle_receipts_sha256",
))


def _verify_reopened_leaf(
    root: Path,
    files: Mapping[str, Any],
    window: Mapping[str, Any],
    arm_name: str,
    arm_binding: Mapping[str, Any],
    authoritative_inputs: Mapping[str, Any],
    assay_sha256: str,
) -> Tuple[str, ...]:
    if frozenset(arm_binding) != _ARM_BINDING_FIELDS:
        raise SealingError("window arm binding field set differs")
    window_root = "windows/%s" % window["window_id"]
    leaf_root = "%s/arms/%s" % (window_root, arm_name)
    required = tuple("%s/%s" % (leaf_root, name) for name in _LEAF_FILES)
    for relative in required:
        _require_mapping(files.get(relative), "required leaf file index entry")

    values = {}  # type: Dict[str, Any]
    for name in _LEAF_FILES:
        relative = "%s/%s" % (leaf_root, name)
        values[name] = _read_indexed_json(
            root, relative, files, "%s %s" % (arm_name, name)
        )[0]
    full = _require_mapping(values["full-cycle-result.json"], "bounded full cycle")
    receipts = tuple(
        _require_mapping(row, "bounded receipt")
        for row in _require_array(values["cycle-receipts.json"], "bounded receipts")
    )
    query = _require_array(values["query-decision-records.json"], "query decisions")
    receipt = _require_mapping(values["decision-receipt.json"], "decision receipt")
    accounting_totals = _require_mapping(
        values["score-free-accounting.json"], "score-free accounting"
    )
    accounting_evidence = _require_mapping(
        values["score-free-accounting-evidence.json"], "accounting evidence"
    )
    for name in _LEAF_FILES:
        relative = "%s/%s" % (leaf_root, name)
        if name in ("cycle-receipts.json", "query-decision-records.json"):
            expected_count = len(receipts) if name == "cycle-receipts.json" else len(query)
            _require_index_shape(files, relative, "array", expected_count)
        else:
            _require_index_shape(files, relative, "object", 1)
    bounded = _derive_bounded_depth_evidence(full, receipts)
    if full.get("window_id") != window["window_id"] or full.get("arm") != arm_name:
        raise SealingError("bounded full-cycle leaf identity differs")
    query_sha256 = canonical_sha256(query)
    if (
        receipt.get("decision_records_sha256") != query_sha256
        or _require_int(receipt.get("expected_events"), "receipt expected_events")
        != len(query)
        or _require_int(receipt.get("retired_records"), "receipt retired_records")
        != len(query)
        or len(query)
        != _require_int(window.get("query_event_count"), "window query_event_count")
    ):
        raise SealingError("decision receipt does not conserve query projection")

    boundary_path = "%s/score-boundary-evidence.json" % leaf_root
    manifest_path = "%s/score-input-manifest.json" % leaf_root
    if (
        arm_binding.get("score_boundary_evidence_path") != boundary_path
        or arm_binding.get("score_boundary_evidence_sha256")
        != files[boundary_path]["sha256"]
        or arm_binding.get("score_input_manifest_path") != manifest_path
        or arm_binding.get("score_input_manifest_sha256")
        != files[manifest_path]["sha256"]
    ):
        raise SealingError("window arm leaf pointer differs")
    expected_boundary = {
        "schema": "redred.mc_wtb.stage4_score_boundary_evidence/v1",
        "assay_authoritative_input_manifest_sha256": assay_sha256,
        "full_cycle_result_sha256": files[
            "%s/full-cycle-result.json" % leaf_root
        ]["sha256"],
        "cycle_receipts_sha256": files[
            "%s/cycle-receipts.json" % leaf_root
        ]["sha256"],
        "query_projection_sha256": query_sha256,
    }
    if values["score-boundary-evidence.json"] != expected_boundary:
        raise SealingError("bounded score-boundary bytes differ")
    manifest = _require_mapping(values["score-input-manifest.json"], "score manifest")
    expected_manifest_links = {
        "assay_authoritative_input_manifest_sha256": assay_sha256,
        "full_cycle_result_sha256": expected_boundary["full_cycle_result_sha256"],
        "cycle_receipts_sha256": expected_boundary["cycle_receipts_sha256"],
        "query_projection_sha256": query_sha256,
        "decision_receipt_sha256": files[
            "%s/decision-receipt.json" % leaf_root
        ]["sha256"],
        "score_free_accounting_sha256": files[
            "%s/score-free-accounting.json" % leaf_root
        ]["sha256"],
        "ray_events_sha256": window["ray_events_sha256"],
    }
    if any(manifest.get(name) != expected for name, expected in expected_manifest_links.items()):
        raise SealingError("score manifest differs from independently observed files")

    diagnostic_binding = arm_binding["delayed_unbounded_depth_diagnostic"]
    pressure_ids = bounded["fifo_full_forced_bypass_event_ids"]
    if arm_name != Arm.DELAYED_EXACT.value:
        if diagnostic_binding is not None or pressure_ids:
            raise SealingError("diagnostic or FIFO pressure appears on a non-delayed arm")
        _validate_accounting_totals(
            accounting_totals,
            accounting_evidence,
            bounded,
            None,
            window["window_id"],
            arm_name,
        )
        return required
    if diagnostic_binding is None:
        _validate_accounting_totals(
            accounting_totals,
            accounting_evidence,
            bounded,
            None,
            window["window_id"],
            arm_name,
        )
        return required

    binding = _require_mapping(diagnostic_binding, "delayed diagnostic binding")
    if frozenset(binding) != _DIAGNOSTIC_BINDING_FIELDS or not pressure_ids:
        raise SealingError("delayed diagnostic binding shape or pressure differs")
    diagnostic_path = "%s/%s" % (leaf_root, _DELAYED_DIAGNOSTIC_FILE)
    if binding.get("path") != diagnostic_path:
        raise SealingError("delayed diagnostic path differs")
    entry = _require_mapping(files.get(diagnostic_path), "diagnostic file index entry")
    for name in _DIAGNOSTIC_BINDING_FIELDS - frozenset((
        "path", "assay_authoritative_input_manifest_sha256",
        "bounded_full_cycle_result_sha256", "bounded_cycle_receipts_sha256",
    )):
        if binding.get(name) != entry.get(name):
            raise SealingError("diagnostic outer binding differs")
    if (
        binding.get("assay_authoritative_input_manifest_sha256") != assay_sha256
        or binding.get("bounded_full_cycle_result_sha256")
        != expected_boundary["full_cycle_result_sha256"]
        or binding.get("bounded_cycle_receipts_sha256")
        != expected_boundary["cycle_receipts_sha256"]
    ):
        raise SealingError("diagnostic bounded-evidence binding differs")
    for name in (
        "input_events_sha256", "input_poses_sha256", "input_event_ids_sha256",
        "input_count", "input_pose_count", "window_id", "window_start_ns",
    ):
        if binding.get(name) != authoritative_inputs.get(name):
            raise SealingError("diagnostic authoritative input binding differs")
    if (
        binding.get("retired_count") != authoritative_inputs["input_count"]
        or binding.get("peak_fifo_depth") <= bounded["bounded_peak_buffer_entries"]
    ):
        raise SealingError("diagnostic conservation or pressure depth differs")
    diagnostic_value, _ = _read_indexed_json(
        root, diagnostic_path, files, "delayed diagnostic"
    )
    diagnostic_receipts = tuple(
        _require_mapping(row, "diagnostic receipt")
        for row in _require_array(
            _require_mapping(diagnostic_value, "diagnostic evidence").get(
                "cycle_receipts"
            ),
            "diagnostic receipts",
        )
    )
    if _admission_projection(receipts) != _admission_projection(diagnostic_receipts):
        raise SealingError("sealed diagnostic admission binding differs")
    _require_index_shape(files, diagnostic_path, _DELAYED_DIAGNOSTIC_KIND, 1)
    _validate_accounting_totals(
        accounting_totals,
        accounting_evidence,
        bounded,
        entry,
        window["window_id"],
        arm_name,
    )
    return required + (diagnostic_path,)


def _verify_delayed_diagnostic_links(
    root: Path, campaign: Mapping[str, Any]
) -> Tuple[str, ...]:
    """Verify all distinct leaves and the separated diagnostic links."""

    files = _require_mapping(campaign.get("files"), "campaign file index")
    pointers = _require_array(campaign.get("windows"), "campaign windows")
    frozen_windows = tuple(window_registry())
    assay_sha256 = _require_sha(
        campaign.get("assay_manifest_sha256"), "campaign assay manifest hash"
    )
    required = {"assay-closure.json"}
    seen_paths = set()
    for pointer_value, frozen in zip(pointers, frozen_windows):
        pointer = _require_mapping(pointer_value, "campaign window pointer")
        expected_relative = "windows/%s/window-seal.json" % frozen["window_id"]
        if frozenset(pointer) != frozenset(("window_id", "path", "sha256")) or (
            pointer.get("window_id") != frozen["window_id"]
            or pointer.get("path") != expected_relative
            or expected_relative in seen_paths
        ):
            raise SealingError("campaign window pointers are not exact and unique")
        seen_paths.add(expected_relative)
        indexed = _require_mapping(files.get(expected_relative), "window file index")
        if pointer.get("sha256") != indexed.get("sha256"):
            raise SealingError("window pointer differs from file index")
        window_value, _ = _read_indexed_json(
            root, expected_relative, files, "window seal"
        )
        window = _require_mapping(window_value, "window seal")
        if frozenset(window) != _WINDOW_FIELDS:
            raise SealingError("window seal field set differs")
        if (
            window.get("schema") != "redred.mc_wtb.stage4_score_free_window_seal/v1"
            or window.get("window_id") != frozen["window_id"]
            or any(
                window.get(name) != frozen[name]
                for name in (
                    "warmup_start_ns_inclusive",
                    "query_start_ns_inclusive",
                    "query_end_ns_exclusive",
                )
            )
        ):
            raise SealingError("window seal differs from frozen registry")
        selected_count = _require_int(
            window.get("selected_event_count"), "window selected_event_count"
        )
        _require_int(window.get("query_event_count"), "window query_event_count")
        _require_sha(
            window.get("ordered_query_event_ids_sha256"),
            "window ordered query event IDs",
        )
        authoritative = _validate_authoritative_window_inputs(
            window.get("authoritative_cycle_inputs"),
            frozen["window_id"],
            frozen["warmup_start_ns_inclusive"],
        )
        if authoritative["input_count"] != selected_count:
            raise SealingError("authoritative event count differs from window seal")
        ray_path = "windows/%s/ray-events.json" % frozen["window_id"]
        if (
            window.get("ray_events_path") != ray_path
            or window.get("ray_events_sha256")
            != _require_mapping(files.get(ray_path), "ray file index").get("sha256")
            or files[ray_path].get("record_count") != selected_count
        ):
            raise SealingError("window ray binding differs")
        _require_index_shape(files, expected_relative, "object", 1)
        _require_index_shape(files, ray_path, "array", selected_count)
        arms = _require_mapping(window.get("arms"), "window arms")
        if frozenset(arms) != frozenset(_ARM_ORDER):
            raise SealingError("window does not contain exactly four arms")
        required.update((expected_relative, ray_path))
        for arm_name in _ARM_ORDER:
            required.update(_verify_reopened_leaf(
                root,
                files,
                window,
                arm_name,
                _require_mapping(arms[arm_name], "window arm binding"),
                authoritative,
                assay_sha256,
            ))
    if len(seen_paths) != len(frozen_windows):
        raise SealingError("campaign window pointers are incomplete")
    return tuple(sorted(required))


def _verify_seal_tree(root: Path, expected_manifest_sha256: str) -> Mapping[str, Any]:
    expected = _require_sha(expected_manifest_sha256, "expected campaign seal")
    manifest_name = "stage4-score-free-seal-manifest.json"
    value, payload = _read_json_at(root, manifest_name)
    campaign = _require_mapping(value, "campaign seal")
    if _sha256(payload) != expected:
        raise SealingError("campaign seal differs from its expected root")
    if frozenset(campaign) != _CAMPAIGN_FIELDS:
        raise SealingError("campaign seal field set differs")
    contract = load_comparison_contract()
    frozen_windows = tuple(window_registry())
    frozen_ids = [row["window_id"] for row in frozen_windows]
    if (
        campaign.get("schema") != "redred.mc_wtb.stage4_score_free_campaign_seal/v1"
        or campaign.get("content_class") != "SCORE_FREE_OBSERVER_EVIDENCE_ONLY"
        or campaign.get("comparison_contract_sha256") != contract.canonical_sha256
        or campaign.get("registry_sha256") != contract.registry["sha256"]
        or _require_int(campaign.get("window_count"), "campaign window_count")
        != len(frozen_windows)
        or _require_int(campaign.get("arm_count"), "campaign arm_count")
        != len(_ARM_ORDER)
        or _require_int(
            campaign.get("arm_window_count"), "campaign arm_window_count"
        )
        != len(frozen_windows) * len(_ARM_ORDER)
        or campaign.get("window_order") != frozen_ids
        or campaign.get("arm_order") != list(_ARM_ORDER)
    ):
        raise SealingError("campaign does not close the frozen 24-by-4 matrix")
    windows = _require_array(campaign.get("windows"), "campaign windows")
    if len(windows) != len(frozen_windows):
        raise SealingError("campaign window pointer count differs")
    files = _require_mapping(campaign.get("files"), "campaign file index")
    expected_inventory = set(files)
    expected_inventory.add(manifest_name)
    expected_directories = set(_indexed_directory_prefixes(files))
    before_inventory, before_directories = _inventory_seal_tree(root)
    if set(before_inventory) != expected_inventory:
        raise SealingError("sealed tree contains an unindexed or missing file")
    if set(before_directories) != expected_directories:
        raise SealingError("sealed tree contains an unindexed or missing directory")
    for relative, expected_entry_value in files.items():
        _safe_relative(relative, "campaign file index")
        expected_entry = _require_mapping(
            expected_entry_value, "campaign file index entry"
        )
        kind = expected_entry.get("kind")
        if type(kind) is not str:
            raise SealingError("sealed artifact kind must be text")
        record_count = _require_int(
            expected_entry.get("record_count"), "sealed artifact record_count"
        )
        observed = _observe_file(
            root, relative, kind=kind, record_count=record_count
        )
        if observed != expected_entry:
            raise SealingError("sealed artifact differs: %s" % relative)

    closure_value, _ = _read_indexed_json(
        root, "assay-closure.json", files, "assay closure"
    )
    _require_index_shape(files, "assay-closure.json", "object", 1)
    closure = _require_mapping(closure_value, "assay closure")
    if (
        closure.get("schema") != "redred.mc_wtb.stage4_score_free_assay_closure/v1"
        or closure.get("assay_manifest_sha256")
        != _require_sha(campaign.get("assay_manifest_sha256"), "campaign assay hash")
        or closure.get("assay_authority_sha256")
        != _require_sha(campaign.get("assay_authority_sha256"), "campaign authority hash")
        or closure.get("comparison_contract_sha256") != contract.canonical_sha256
        or closure.get("registry_sha256") != contract.registry["sha256"]
        or campaign.get("assay_closure_sha256")
        != files["assay-closure.json"]["sha256"]
    ):
        raise SealingError("campaign assay closure binding differs")
    required_files = set(_verify_delayed_diagnostic_links(root, campaign))
    if set(files) != required_files:
        raise SealingError("campaign file roster does not exactly close all leaves")
    after_inventory, after_directories = _inventory_seal_tree(root)
    if (
        set(after_inventory) != expected_inventory
        or set(after_directories) != expected_directories
    ):
        raise SealingError("sealed tree changed during verification")
    for relative, entry_value in files.items():
        entry = _require_mapping(entry_value, "final sealed file index entry")
        final_payload = _stable_regular_file(root, relative, "final %s" % relative)
        if (
            _sha256(final_payload) != entry["sha256"]
            or len(final_payload) != entry["size_bytes"]
        ):
            raise SealingError("sealed file changed during verification: %s" % relative)
    final_value, final_payload = _read_json_at(root, manifest_name)
    if final_value != campaign or final_payload != payload:
        raise SealingError("campaign manifest changed during verification")
    return campaign


def verify_score_free_seal(
    output_dir: Path, *, expected_seal_manifest_sha256: str
) -> Mapping[str, Any]:
    """Reopen and verify every byte under a previously frozen campaign root."""

    return _verify_seal_tree(Path(output_dir), expected_seal_manifest_sha256)


def _build_window_with_required_diagnostic(bundle: Any, window_id: str) -> Mapping[Any, Any]:
    """Build once normally, adding the independent replay only when required."""

    try:
        return integration_adapter.build_all_arm_window(bundle, window_id)
    except integration_adapter.IntegrationError as exc:
        if _REPLAY_REQUIRED not in str(exc):
            raise
    inputs = integration_adapter.build_window_cycle_inputs(bundle, window_id)
    try:
        diagnostic = run_delayed_unbounded_diagnostic(
            window_id=inputs.window_id,
            window_start_ns=inputs.window_start_ns,
            events=inputs.events,
            poses=inputs.dataset_poses,
            synthetic_test_mode=False,
        )
    except CycleModelError as exc:
        raise SealingError(
            "required delayed unbounded diagnostic did not terminate validly"
        ) from exc
    return integration_adapter.build_all_arm_window(
        bundle,
        window_id,
        delayed_unbounded_diagnostic=diagnostic,
    )


def seal_official_score_free(
    assay_dir: Path,
    output_dir: Path,
    *,
    expected_assay_manifest_sha256: str,
) -> SealResult:
    """Write and independently observe all 24-by-4 score-free seal leaves."""

    assay_root = Path(assay_dir)
    output = Path(output_dir)
    if output.exists():
        raise SealingError("output directory must not already exist")
    manifest, assay_closure, window_summaries = _observe_assay(
        assay_root, expected_assay_manifest_sha256
    )
    bundle = integration_adapter.load_assay_bundle(
        assay_root, expected_manifest_sha256=expected_assay_manifest_sha256
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".%s." % output.name, dir=str(output.parent))
    )
    try:
        files = {}  # type: Dict[str, Mapping[str, Any]]
        _write_json(temporary / "assay-closure.json", assay_closure)
        files["assay-closure.json"] = _observe_file(
            temporary, "assay-closure.json", kind="object", record_count=1
        )
        window_roots = []  # type: List[Mapping[str, Any]]
        for summary in window_summaries:
            window_id = str(summary["window_id"])
            authoritative_inputs = _authoritative_window_inputs(
                integration_adapter.build_window_cycle_inputs(bundle, window_id)
            )
            _validate_authoritative_window_inputs(
                authoritative_inputs,
                window_id,
                int(summary["warmup_start_ns_inclusive"]),
            )
            if authoritative_inputs["input_count"] != int(
                summary["selected_event_count"]
            ):
                raise SealingError(
                    "authoritative event count differs from the window summary"
                )
            integrated = _build_window_with_required_diagnostic(bundle, window_id)
            if set(integrated) != set(Arm):
                raise SealingError("window does not contain all four arms")
            first = integrated[Arm.ZOH_FRESHNESS]
            ray_values = [
                _to_mapping(row, "ray event") for row in first.ray_events
            ]
            if len(ray_values) != int(summary["selected_event_count"]):
                raise SealingError("ray event count differs from its window summary")
            for arm in Arm:
                other = [
                    _to_mapping(row, "ray event")
                    for row in integrated[arm].ray_events
                ]
                if other != ray_values:
                    raise SealingError("arm ray projections differ within a window")
            window_root = "windows/%s" % window_id
            ray_relative = "%s/ray-events.json" % window_root
            _write_json(temporary / ray_relative, ray_values)
            files[ray_relative] = _observe_file(
                temporary,
                ray_relative,
                kind="array",
                record_count=len(ray_values),
            )
            ray_sha256 = files[ray_relative]["sha256"]
            arms = {}  # type: Dict[str, Mapping[str, Any]]
            for arm in Arm:
                leaf_root = "%s/arms/%s" % (window_root, arm.value)
                _write_leaf_inputs(temporary, leaf_root, integrated[arm])
                arms[arm.value] = _observe_leaf(
                    temporary,
                    leaf_root,
                    integrated[arm],
                    expected_assay_manifest_sha256,
                    str(ray_sha256),
                    int(summary["query_event_count"]),
                    authoritative_inputs,
                    files,
                )
            window_seal = {
                "schema": "redred.mc_wtb.stage4_score_free_window_seal/v1",
                "window_id": window_id,
                "warmup_start_ns_inclusive": summary[
                    "warmup_start_ns_inclusive"
                ],
                "query_start_ns_inclusive": summary["query_start_ns_inclusive"],
                "query_end_ns_exclusive": summary["query_end_ns_exclusive"],
                "selected_event_count": summary["selected_event_count"],
                "query_event_count": summary["query_event_count"],
                "ordered_query_event_ids_sha256": summary[
                    "ordered_query_event_ids_sha256"
                ],
                "ray_events_path": ray_relative,
                "ray_events_sha256": ray_sha256,
                "authoritative_cycle_inputs": authoritative_inputs,
                "arms": arms,
            }
            window_seal_relative = "%s/window-seal.json" % window_root
            _write_json(temporary / window_seal_relative, window_seal)
            files[window_seal_relative] = _observe_file(
                temporary,
                window_seal_relative,
                kind="object",
                record_count=1,
            )
            window_roots.append({
                "window_id": window_id,
                "path": window_seal_relative,
                "sha256": files[window_seal_relative]["sha256"],
            })
        campaign = {
            "schema": "redred.mc_wtb.stage4_score_free_campaign_seal/v1",
            "content_class": "SCORE_FREE_OBSERVER_EVIDENCE_ONLY",
            "assay_manifest_sha256": expected_assay_manifest_sha256,
            "assay_authority_sha256": assay_closure["assay_authority_sha256"],
            "assay_closure_sha256": files["assay-closure.json"]["sha256"],
            "comparison_contract_sha256": manifest[
                "comparison_contract_sha256"
            ],
            "registry_sha256": manifest["registry"]["sha256"],
            "window_count": len(window_roots),
            "arm_count": len(_ARM_ORDER),
            "arm_window_count": len(window_roots) * len(_ARM_ORDER),
            "window_order": [row["window_id"] for row in window_roots],
            "arm_order": list(_ARM_ORDER),
            "windows": window_roots,
            "files": dict(sorted(files.items())),
        }
        campaign_path = temporary / "stage4-score-free-seal-manifest.json"
        _write_json(campaign_path, campaign)
        observed_campaign, campaign_payload = _read_json(campaign_path)
        campaign_sha256 = _sha256(campaign_payload)
        _verify_seal_tree(temporary, campaign_sha256)
        os.replace(str(temporary), str(output))
        return SealResult(output, observed_campaign, campaign_sha256)
    except Exception:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise
