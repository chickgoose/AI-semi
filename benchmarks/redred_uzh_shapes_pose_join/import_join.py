#!/usr/bin/env python3
"""Import the pinned UZH shapes_rotation archive into a source-preserving pose join."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import zipfile
from bisect import bisect_right
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator


SPEC_SCHEMA = "redred.uzh_shapes_pose_join.spec/v1"
CALIBRATION_SCHEMA = "redred.uzh_shapes_pose_join.calibration/v1"
POSE_STREAM_SCHEMA = "redred.uzh_shapes_pose_join.pose_stream/v1"
POSE_SCHEMA = "redred.uzh_shapes_pose_join.pose/v1"
EVENT_STREAM_SCHEMA = "redred.uzh_shapes_pose_join.event_stream/v1"
EVENT_SCHEMA = "redred.uzh_shapes_pose_join.event/v1"
RECEIPT_SCHEMA = "redred.uzh_shapes_pose_join.receipt/v1"
COMPLETION_SCHEMA = "redred.uzh_shapes_pose_join.completion/v1"

STATUS = "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED"
EVIDENCE_CLASS = "DATASET_SOURCE_PRESERVING_POSE_JOIN"
PROMOTION_STATUS = "HOLD_MC_WTB_ADAPTER"

LICENSE_NAME = "LICENSE.txt"
CALIBRATION_NAME = "calibration.json"
POSES_NAME = "poses.jsonl"
EVENTS_NAME = "events_pose_join.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETION_NAME = "COMPLETE.json"
SOURCE_SPOOL_NAME = ".source.zip"
PRIMARY_NAMES = (LICENSE_NAME, CALIBRATION_NAME, POSES_NAME, EVENTS_NAME)
FINAL_NAMES = frozenset((*PRIMARY_NAMES, RECEIPT_NAME, COMPLETION_NAME))

LANDING_URL = "https://rpg.ifi.uzh.ch/davis_data.html"
DOWNLOAD_URL = "https://rpg.ifi.uzh.ch/datasets/davis/shapes_rotation.zip"
CITATION_URL = "https://arxiv.org/abs/1610.08336"
LICENSE_DEED_URL = "https://creativecommons.org/licenses/by-nc-sa/3.0/"
LICENSE_BYTES_URL = "https://creativecommons.org/licenses/by-nc-sa/3.0/legalcode.txt"
OFFICIAL_DOWNLOAD_BASENAME = "shapes_rotation.zip"
PROFILE_ID = "uzh-rpg-davis240c-shapes_rotation-text-zip-v1"
AUTHORITY_SCOPE = "official_requested_url_plus_exact_local_archive_member_and_license_bytes"

SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
CRC_RE = re.compile(r"[0-9a-f]{8}\Z")
TIMESTAMP_RE = re.compile(rb"(0|[1-9][0-9]{0,9})\.([0-9]{9})\Z")
INTEGER_RE = re.compile(rb"0|[1-9][0-9]{0,9}\Z")
DECIMAL_RE = re.compile(rb"-?(?:0|[1-9][0-9]*)\.[0-9]+\Z")

_PRODUCTION_ARCHIVE = (157_446_920, "56aade6bf53dcf73e8fe40905ccac8385cd7606bc9a85103bf2c9f9045117551", 1363)
_PRODUCTION_LICENSE = (22_306, "8812f83442fd0eca14eb0208988e190fdcbfebec58fa5459d3218edfdfdc5a32")
_PRODUCTION_MEMBERS = {
    "events": ("events.txt", 509_907_771, 130_659_807, "c54a9cef", "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda", 23_126_288),
    "poses": ("groundtruth.txt", 1_379_205, 597_809, "28836890", "bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb", 11_883),
    "calibration": ("calib.txt", 128, 82, "0a2da176", "ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd", 1),
}


class JoinFailure(ValueError):
    """The source, source lock, join, package, or inspection is invalid."""


@dataclass(frozen=True)
class PoseRecord:
    index: int
    timestamp_text: str
    timestamp_ns: int
    position: tuple[str, str, str]
    quaternion: tuple[str, str, str, str]


@dataclass(frozen=True)
class EventRecord:
    dataset_index: int
    timestamp_text: str
    timestamp_ns: int
    x: int
    y: int
    polarity: int
    raw: bytes


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_keys(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JoinFailure(f"{where} must be an object")
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing or extra:
        raise JoinFailure(f"{where} keys mismatch: missing={missing} extra={extra}")
    return value


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JoinFailure(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_number(text: str) -> Any:
    raise JoinFailure(f"JSON floating point or non-finite number is forbidden: {text}")


def _json_bytes(data: bytes, where: str) -> Any:
    try:
        return json.loads(
            data.decode("ascii"),
            object_pairs_hook=_json_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except JoinFailure:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise JoinFailure(f"invalid JSON in {where}: {error}") from error


def _positive_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise JoinFailure(f"{where} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JoinFailure(f"{where} must be a nonnegative integer")
    return value


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise JoinFailure(f"{where} must be a lowercase SHA-256")
    return value


def _basename(value: Any, where: str) -> str:
    if not isinstance(value, str) or value in ("", ".", "..") or Path(value).name != value:
        raise JoinFailure(f"{where} must be one ordinary basename")
    return value


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise JoinFailure(f"cannot inspect path component {current}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise JoinFailure(f"symlink path component is forbidden: {current}")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _stable_open(path: Path) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    path = Path(path)
    _reject_symlink_components(path)
    required = ("O_CLOEXEC", "O_NOFOLLOW")
    if not all(hasattr(os, name) for name in required):
        raise JoinFailure("stable no-follow input reads are unsupported on this platform")
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as error:
        raise JoinFailure(f"cannot stat input {path}: {error}") from error
    if not stat.S_ISREG(before.st_mode):
        raise JoinFailure(f"input is not a regular file: {path}")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise JoinFailure(f"cannot open input {path}: {error}") from error
    stream = os.fdopen(descriptor, "rb", closefd=False)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise JoinFailure(f"input changed before open: {path}")
        yield stream, opened
        after = os.fstat(descriptor)
        final = path.stat(follow_symlinks=False)
        if _identity(after) != _identity(before) or _identity(final) != _identity(before):
            raise JoinFailure(f"input changed during stable read: {path}")
    except OSError as error:
        raise JoinFailure(f"stable read failed for {path}: {error}") from error
    finally:
        stream.close()
        os.close(descriptor)


def _read_stable(path: Path, maximum: int, label: str) -> bytes:
    with _stable_open(path) as (stream, info):
        if info.st_size > maximum:
            raise JoinFailure(f"{label} exceeds {maximum} bytes")
        data = stream.read(maximum + 1)
        if len(data) != info.st_size or len(data) > maximum:
            raise JoinFailure(f"{label} byte accounting mismatch")
        return data


def _member_spec(value: Any, where: str, expected_name: str) -> dict[str, Any]:
    row = _strict_keys(value, {"name", "size_bytes", "compressed_size_bytes", "crc32", "sha256", "line_count"}, where)
    if row["name"] != expected_name:
        raise JoinFailure(f"{where}.name must be {expected_name!r}")
    for key in ("size_bytes", "compressed_size_bytes", "line_count"):
        _positive_integer(row[key], f"{where}.{key}")
    if not isinstance(row["crc32"], str) or CRC_RE.fullmatch(row["crc32"]) is None:
        raise JoinFailure(f"{where}.crc32 is invalid")
    _digest(row["sha256"], f"{where}.sha256")
    return row


def validate_spec(value: Any) -> dict[str, Any]:
    spec = _strict_keys(value, {
        "schema", "source_lock", "dataset", "source_archive", "license", "required_members",
        "sensor", "source_formats", "timebase", "selection", "join_policy", "resource_limits", "claim_scope",
    }, "spec")
    if spec["schema"] != SPEC_SCHEMA:
        raise JoinFailure("spec schema mismatch")
    lock = _strict_keys(spec["source_lock"], {"profile_id", "authority_scope", "remote_server_authenticated"}, "source_lock")
    if lock != {"profile_id": PROFILE_ID, "authority_scope": AUTHORITY_SCOPE, "remote_server_authenticated": False}:
        raise JoinFailure("source-lock authority contract mismatch")
    dataset = _strict_keys(spec["dataset"], {
        "provider", "collection", "sequence", "sensor", "camera_id", "landing_url", "download_url", "citation_url", "license_spdx",
    }, "dataset")
    if dataset != {
        "provider": "University of Zurich Robotics and Perception Group",
        "collection": "Event-Camera Dataset", "sequence": "shapes_rotation", "sensor": "DAVIS240C",
        "camera_id": "uzh-davis240c-shapes_rotation", "landing_url": LANDING_URL,
        "download_url": DOWNLOAD_URL, "citation_url": CITATION_URL, "license_spdx": "CC-BY-NC-SA-3.0",
    }:
        raise JoinFailure("dataset authority identity mismatch")
    archive = _strict_keys(spec["source_archive"], {"official_download_basename", "basename", "size_bytes", "sha256", "expected_entry_count"}, "source_archive")
    if archive["official_download_basename"] != OFFICIAL_DOWNLOAD_BASENAME:
        raise JoinFailure("official archive basename mismatch")
    _basename(archive["basename"], "source_archive.basename")
    _positive_integer(archive["size_bytes"], "source_archive.size_bytes")
    _digest(archive["sha256"], "source_archive.sha256")
    _positive_integer(archive["expected_entry_count"], "source_archive.expected_entry_count")
    license_row = _strict_keys(spec["license"], {"basename", "size_bytes", "sha256", "source_url", "deed_url"}, "license")
    _basename(license_row["basename"], "license.basename")
    _positive_integer(license_row["size_bytes"], "license.size_bytes")
    _digest(license_row["sha256"], "license.sha256")
    if license_row["source_url"] != LICENSE_BYTES_URL or license_row["deed_url"] != LICENSE_DEED_URL:
        raise JoinFailure("license exact-bytes/deed URL binding mismatch")
    members = _strict_keys(spec["required_members"], {"events", "poses", "calibration"}, "required_members")
    _member_spec(members["events"], "required_members.events", "events.txt")
    _member_spec(members["poses"], "required_members.poses", "groundtruth.txt")
    _member_spec(members["calibration"], "required_members.calibration", "calib.txt")
    if spec["sensor"] != {"width": 240, "height": 180, "pixel_origin": "top_left_0_0", "source_polarity_values": [0, 1]}:
        raise JoinFailure("sensor contract mismatch")
    if spec["source_formats"] != {
        "separator": "single_ascii_space", "line_termination": "LF", "timestamp_fractional_digits": 9,
        "events_fields": ["timestamp", "x", "y", "polarity"],
        "poses_fields": ["timestamp", "px", "py", "pz", "qx", "qy", "qz", "qw"],
        "calibration_fields": ["fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"],
    }:
        raise JoinFailure("source format contract mismatch")
    if spec["timebase"] != {"unit": "ns", "epoch": "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction"}:
        raise JoinFailure("timebase contract mismatch")
    selection = _strict_keys(spec["selection"], {
        "start_timestamp_ns_inclusive", "end_timestamp_ns_exclusive", "expected_event_count",
        "expected_first_dataset_event_index", "expected_last_dataset_event_index",
        "expected_first_timestamp_ns", "expected_last_timestamp_ns", "selected_raw_lines_sha256",
    }, "selection")
    for key in selection:
        if key == "selected_raw_lines_sha256":
            _digest(selection[key], f"selection.{key}")
        else:
            _nonnegative_integer(selection[key], f"selection.{key}")
    if selection["start_timestamp_ns_inclusive"] >= selection["end_timestamp_ns_exclusive"] or selection["expected_event_count"] <= 0:
        raise JoinFailure("selection bounds/count are invalid")
    if spec["join_policy"] != {
        "ordering": "preserve_source_order", "causal_pose": "latest_at_or_before_event_timestamp",
        "future_bracket": "first_pose_strictly_after_event_timestamp", "interpolation_output": "none_bracket_identity_only",
        "max_causal_pose_age_ns_inclusive": 5_000_000, "missing_stale_or_unbracketed": "fail_package_no_partial_success",
    }:
        raise JoinFailure("join policy mismatch")
    limits = _strict_keys(spec["resource_limits"], {
        "max_archive_bytes", "max_zip_entries", "max_event_line_bytes", "max_pose_line_bytes",
        "max_calibration_line_bytes", "max_pose_records", "max_selected_events", "max_license_bytes", "copy_chunk_bytes",
    }, "resource_limits")
    for key, item in limits.items():
        _positive_integer(item, f"resource_limits.{key}")
    if limits != {
        "max_archive_bytes": 157_446_920, "max_zip_entries": 1363,
        "max_event_line_bytes": 96, "max_pose_line_bytes": 256,
        "max_calibration_line_bytes": 512, "max_pose_records": 20_000,
        "max_selected_events": 2_000, "max_license_bytes": 65_536,
        "copy_chunk_bytes": 1_048_576,
    }:
        raise JoinFailure("resource-limit source lock mismatch")
    if archive["size_bytes"] > limits["max_archive_bytes"] or archive["expected_entry_count"] > limits["max_zip_entries"]:
        raise JoinFailure("source identity exceeds declared resource limits")
    if spec["claim_scope"] != {
        "official_uzh_source": True, "official_redred_traffic": False, "canonical_redred_traffic": False,
        "transport_replay_performed": False, "warp_performed": False, "pure_rotation_claimed": False,
        "distortion_applied": False, "translation_preserved_not_applied": True,
    }:
        raise JoinFailure("spec claim scope was broadened")
    return spec


def _load_spec(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_stable(path, 1024 * 1024, "specification")
    return validate_spec(_json_bytes(raw, "specification")), raw


def _production_lock_matches(spec: dict[str, Any]) -> bool:
    archive = spec["source_archive"]
    if (archive["size_bytes"], archive["sha256"], archive["expected_entry_count"]) != _PRODUCTION_ARCHIVE:
        return False
    license_row = spec["license"]
    if (license_row["size_bytes"], license_row["sha256"]) != _PRODUCTION_LICENSE:
        return False
    for role, expected in _PRODUCTION_MEMBERS.items():
        row = spec["required_members"][role]
        if (row["name"], row["size_bytes"], row["compressed_size_bytes"], row["crc32"], row["sha256"], row["line_count"]) != expected:
            return False
    return True


def _claim_scope(official_source: bool) -> dict[str, Any]:
    return {
        "official_uzh_source": official_source,
        "generated_artifact_official_uzh": False,
        "official_redred_traffic": False,
        "canonical_redred_traffic": False,
        "transport_replay_performed": False,
        "warp_performed": False,
        "pure_rotation_claimed": False,
        "distortion_applied": False,
        "translation_preserved_not_applied": True,
    }


_DIRFD_SUPPORTED = (
    all(hasattr(os, name) for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"))
    and all(function in os.supports_dir_fd for function in (os.open, os.stat, os.mkdir, os.rmdir, os.unlink))
    and os.stat in os.supports_follow_symlinks
)


def _rename_noreplace(old_fd: int, old_name: str, new_fd: int, new_name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise JoinFailure("renameat2(RENAME_NOREPLACE) is unsupported on this platform")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(old_fd, os.fsencode(old_name), new_fd, os.fsencode(new_name), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), new_name)


class _PackageBuilder:
    """Private same-parent staging directory published by no-replace renameat2."""

    def __init__(self, result_dir: Path) -> None:
        if not _DIRFD_SUPPORTED:
            raise JoinFailure("hardened POSIX dirfd package publication is unsupported")
        self.result_dir = Path(os.path.abspath(result_dir))
        self.parent = self.result_dir.parent
        self.target = self.result_dir.name
        _basename(self.target, "result directory name")
        _reject_symlink_components(self.parent)
        self.parent_fd: int | None = None
        self.stage_fd: int | None = None
        self.stage_name: str | None = None
        self.created: set[str] = set()
        self.published = False

    def __enter__(self) -> "_PackageBuilder":
        try:
            before = self.parent.stat(follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise JoinFailure("result parent is not a directory")
            self.parent_fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            opened = os.fstat(self.parent_fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise JoinFailure("result parent changed before open")
            try:
                os.stat(self.target, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise JoinFailure(f"result path already exists: {self.result_dir}")
            for _ in range(128):
                candidate = f".{self.target}.{secrets.token_hex(12)}.tmp"
                try:
                    os.mkdir(candidate, 0o700, dir_fd=self.parent_fd)
                except FileExistsError:
                    continue
                self.stage_name = candidate
                break
            if self.stage_name is None:
                raise JoinFailure("cannot allocate private staging directory")
            self.stage_fd = os.open(self.stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.parent_fd)
            return self
        except JoinFailure:
            self._cleanup()
            raise
        except OSError as error:
            self._cleanup()
            raise JoinFailure(f"cannot create hardened result staging directory: {error}") from error

    def _new_fd(self, name: str, mode: int = 0o644) -> int:
        _basename(name, "package artifact name")
        if self.stage_fd is None:
            raise JoinFailure("staging directory is not open")
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, mode, dir_fd=self.stage_fd)
        except OSError as error:
            raise JoinFailure(f"cannot create staged artifact {name}: {error}") from error
        self.created.add(name)
        return descriptor

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            try:
                count = os.write(descriptor, memoryview(payload)[offset:])
            except InterruptedError:
                continue
            if count <= 0:
                raise JoinFailure("staged artifact write made no progress")
            offset += count

    def write(self, name: str, payload: bytes, mode: int = 0o644) -> None:
        descriptor = self._new_fd(name, mode)
        try:
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
        except OSError as error:
            raise JoinFailure(f"cannot write staged artifact {name}: {error}") from error
        finally:
            os.close(descriptor)

    def capture_archive(self, source: Path, expected: dict[str, Any], chunk_bytes: int) -> dict[str, Any]:
        descriptor = self._new_fd(SOURCE_SPOOL_NAME, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with _stable_open(source) as (stream, info):
                if source.name != expected["basename"]:
                    raise JoinFailure("provided archive basename differs from local-path source lock")
                while True:
                    chunk = stream.read(chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected["size_bytes"]:
                        raise JoinFailure("archive exceeds pinned size")
                    digest.update(chunk)
                    self._write_all(descriptor, chunk)
                if size != info.st_size:
                    raise JoinFailure("archive capture byte accounting mismatch")
            os.fsync(descriptor)
        except OSError as error:
            raise JoinFailure(f"archive capture failed: {error}") from error
        finally:
            os.close(descriptor)
        actual_sha = digest.hexdigest()
        if size != expected["size_bytes"] or actual_sha != expected["sha256"]:
            raise JoinFailure("archive size/SHA mismatch")
        return {"basename": source.name, "size_bytes": size, "sha256": actual_sha}

    def open_spool(self) -> BinaryIO:
        if self.stage_fd is None:
            raise JoinFailure("staging directory is not open")
        try:
            descriptor = os.open(SOURCE_SPOOL_NAME, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.stage_fd)
        except OSError as error:
            raise JoinFailure(f"cannot reopen captured archive: {error}") from error
        return os.fdopen(descriptor, "rb")

    def remove(self, name: str) -> None:
        if self.stage_fd is None:
            raise JoinFailure("staging directory is not open")
        try:
            os.unlink(name, dir_fd=self.stage_fd)
        except OSError as error:
            raise JoinFailure(f"cannot remove private staged source {name}: {error}") from error
        self.created.discard(name)

    def publish(self) -> None:
        if self.parent_fd is None or self.stage_fd is None or self.stage_name is None:
            raise JoinFailure("staging publication state is incomplete")
        if SOURCE_SPOOL_NAME in self.created:
            raise JoinFailure("private source spool must not enter the completed package")
        if self.created != FINAL_NAMES:
            raise JoinFailure(f"staged artifact inventory mismatch: {sorted(self.created)}")
        try:
            os.fsync(self.stage_fd)
            pinned_parent = os.fstat(self.parent_fd)
            current_parent = self.parent.stat(follow_symlinks=False)
            if (current_parent.st_dev, current_parent.st_ino) != (pinned_parent.st_dev, pinned_parent.st_ino):
                raise JoinFailure("result parent path no longer names the pinned directory")
            try:
                os.stat(self.target, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise JoinFailure("result target appeared before publication")
            stage_identity = os.fstat(self.stage_fd)
            _rename_noreplace(self.parent_fd, self.stage_name, self.parent_fd, self.target)
            self.stage_name = None
            # From this point cleanup must never unlink through stage_fd: the
            # directory may already be visible under the final target name.
            self.published = True
            os.fsync(self.parent_fd)
            published = os.stat(self.target, dir_fd=self.parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(published.st_mode) or (published.st_dev, published.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                raise JoinFailure("published target identity differs from staged directory")
            current_parent = self.parent.stat(follow_symlinks=False)
            if (current_parent.st_dev, current_parent.st_ino) != (pinned_parent.st_dev, pinned_parent.st_ino):
                raise JoinFailure("result parent changed after publication; result may exist in pinned directory")
        except JoinFailure:
            raise
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise JoinFailure("result target already exists; no overwrite performed") from error
            raise JoinFailure(f"atomic package publication failed: {error}") from error

    def _cleanup(self) -> None:
        if self.stage_fd is not None and not self.published:
            for name in sorted(self.created):
                try:
                    os.unlink(name, dir_fd=self.stage_fd)
                except OSError:
                    pass
            self.created.clear()
        if self.stage_fd is not None:
            try:
                os.close(self.stage_fd)
            except OSError:
                pass
            self.stage_fd = None
        if self.stage_name is not None and self.parent_fd is not None:
            try:
                os.rmdir(self.stage_name, dir_fd=self.parent_fd)
            except OSError:
                pass
            self.stage_name = None
        if self.parent_fd is not None:
            try:
                os.close(self.parent_fd)
            except OSError:
                pass
            self.parent_fd = None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._cleanup()


def _timestamp(raw: bytes, where: str) -> tuple[str, int]:
    match = TIMESTAMP_RE.fullmatch(raw)
    if match is None:
        raise JoinFailure(f"{where} timestamp must be an exact 9-digit decimal")
    return raw.decode("ascii"), int(match.group(1)) * 1_000_000_000 + int(match.group(2))


def _decimal(raw: bytes, where: str) -> str:
    if DECIMAL_RE.fullmatch(raw) is None:
        raise JoinFailure(f"{where} must be a canonical decimal lexeme")
    try:
        Decimal(raw.decode("ascii"))
    except InvalidOperation as error:
        raise JoinFailure(f"{where} is not finite decimal data") from error
    return raw.decode("ascii")


def _line(stream: BinaryIO, maximum: int, where: str) -> bytes | None:
    raw = stream.readline(maximum + 1)
    if not raw:
        return None
    if len(raw) > maximum:
        raise JoinFailure(f"{where} exceeds the line-size resource limit")
    if not raw.endswith(b"\n") or raw.endswith(b"\r\n") or b"\t" in raw:
        raise JoinFailure(f"{where} must use one LF terminator and no tabs/CR")
    return raw


def _member_identity(info: zipfile.ZipInfo, digest: str, lines: int) -> dict[str, Any]:
    return {
        "name": info.filename, "size_bytes": info.file_size, "compressed_size_bytes": info.compress_size,
        "crc32": f"{info.CRC:08x}", "sha256": digest, "line_count": lines,
    }


def _check_member_info(info: zipfile.ZipInfo, expected: dict[str, Any], where: str) -> None:
    mode = info.external_attr >> 16
    if info.is_dir() or stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
        raise JoinFailure(f"{where} must be a regular ZIP member")
    if info.flag_bits & 1:
        raise JoinFailure(f"{where} must not be encrypted")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise JoinFailure(f"{where} uses an unsupported compression method")
    if (info.file_size != expected["size_bytes"] or info.compress_size != expected["compressed_size_bytes"] or f"{info.CRC:08x}" != expected["crc32"]):
        raise JoinFailure(f"{where} central-directory identity mismatch")


def _parse_calibration(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: dict[str, Any], limit: int) -> tuple[dict[str, Any], dict[str, Any]]:
    digest = hashlib.sha256()
    with archive.open(info, "r") as stream:
        raw = _line(stream, limit, "calib.txt:1")
        if raw is None:
            raise JoinFailure("calib.txt is empty")
        digest.update(raw)
        if _line(stream, limit, "calib.txt:2") is not None:
            raise JoinFailure("calib.txt must contain exactly one record")
    fields = raw[:-1].split(b" ")
    if len(fields) != 9 or raw[:-1].count(b" ") != 8:
        raise JoinFailure("calib.txt must contain nine single-space decimal fields")
    names = ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3")
    parameters = {name: _decimal(item, f"calib.txt {name}") for name, item in zip(names, fields)}
    if Decimal(parameters["fx"]) <= 0 or Decimal(parameters["fy"]) <= 0:
        raise JoinFailure("calibration focal lengths must be positive")
    actual = digest.hexdigest()
    if len(raw) != expected["size_bytes"] or actual != expected["sha256"] or expected["line_count"] != 1:
        raise JoinFailure("calibration size/SHA/line-count mismatch")
    record = {
        "schema": CALIBRATION_SCHEMA,
        "calibration_id": f"calib-sha256:{actual}",
        "camera_id": "uzh-davis240c-shapes_rotation",
        "sensor": {"width": 240, "height": 180},
        "model": "opencv_pinhole_radtan",
        "parameter_order": list(names),
        "parameters_exact_decimal": parameters,
        "source_member": "calib.txt",
        "source_record_index": 0,
    }
    return record, _member_identity(info, actual, 1)


def _parse_poses(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: dict[str, Any], line_limit: int, record_limit: int) -> tuple[list[PoseRecord], dict[str, Any], int]:
    poses: list[PoseRecord] = []
    digest = hashlib.sha256()
    total = 0
    previous = -1
    negative_dots = 0
    previous_q: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
    with archive.open(info, "r") as stream:
        while True:
            raw = _line(stream, line_limit, f"groundtruth.txt:{len(poses) + 1}")
            if raw is None:
                break
            total += len(raw)
            digest.update(raw)
            if len(poses) >= record_limit:
                raise JoinFailure("pose record count exceeds resource limit")
            fields = raw[:-1].split(b" ")
            if len(fields) != 8 or raw[:-1].count(b" ") != 7:
                raise JoinFailure(f"groundtruth.txt:{len(poses) + 1} must contain eight single-space fields")
            timestamp_text, timestamp_ns = _timestamp(fields[0], f"groundtruth.txt:{len(poses) + 1}")
            if timestamp_ns <= previous:
                raise JoinFailure("pose timestamps must be strictly increasing")
            previous = timestamp_ns
            values = tuple(_decimal(item, f"groundtruth.txt:{len(poses) + 1}") for item in fields[1:])
            position = (values[0], values[1], values[2])
            quaternion = (values[3], values[4], values[5], values[6])
            q_decimal = tuple(Decimal(item) for item in quaternion)
            norm = sum(item * item for item in q_decimal)
            if not Decimal("0.99") <= norm <= Decimal("1.01"):
                raise JoinFailure(f"groundtruth.txt:{len(poses) + 1} quaternion norm is invalid")
            if previous_q is not None and sum(a * b for a, b in zip(previous_q, q_decimal)) < 0:
                negative_dots += 1
            previous_q = q_decimal
            poses.append(PoseRecord(len(poses), timestamp_text, timestamp_ns, position, quaternion))
    actual = digest.hexdigest()
    if not poses or total != expected["size_bytes"] or actual != expected["sha256"] or len(poses) != expected["line_count"]:
        raise JoinFailure("pose size/SHA/line-count mismatch")
    return poses, _member_identity(info, actual, len(poses)), negative_dots


def _parse_events(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: dict[str, Any], spec: dict[str, Any]) -> tuple[list[EventRecord], dict[str, Any], dict[str, Any]]:
    selection = spec["selection"]
    start = selection["start_timestamp_ns_inclusive"]
    end = selection["end_timestamp_ns_exclusive"]
    line_limit = spec["resource_limits"]["max_event_line_bytes"]
    selected_limit = spec["resource_limits"]["max_selected_events"]
    digest = hashlib.sha256()
    selected_digest = hashlib.sha256()
    selected: list[EventRecord] = []
    total = before = after = total_size = 0
    previous = -1
    with archive.open(info, "r") as stream:
        while True:
            raw = _line(stream, line_limit, f"events.txt:{total + 1}")
            if raw is None:
                break
            total_size += len(raw)
            digest.update(raw)
            fields = raw[:-1].split(b" ")
            if len(fields) != 4 or raw[:-1].count(b" ") != 3:
                raise JoinFailure(f"events.txt:{total + 1} must contain four single-space fields")
            timestamp_text, timestamp_ns = _timestamp(fields[0], f"events.txt:{total + 1}")
            if timestamp_ns < previous:
                raise JoinFailure("event timestamps must be monotonic nondecreasing")
            previous = timestamp_ns
            if INTEGER_RE.fullmatch(fields[1]) is None or INTEGER_RE.fullmatch(fields[2]) is None or fields[3] not in (b"0", b"1"):
                raise JoinFailure(f"events.txt:{total + 1} coordinate/polarity is noncanonical")
            x, y, polarity = int(fields[1]), int(fields[2]), int(fields[3])
            if not 0 <= x < 240 or not 0 <= y < 180:
                raise JoinFailure(f"events.txt:{total + 1} is outside the DAVIS240C lattice")
            if timestamp_ns < start:
                before += 1
            elif timestamp_ns >= end:
                after += 1
            else:
                if len(selected) >= selected_limit:
                    raise JoinFailure("selected event count exceeds resource limit")
                selected_digest.update(raw)
                selected.append(EventRecord(total, timestamp_text, timestamp_ns, x, y, polarity, raw))
            total += 1
    actual = digest.hexdigest()
    if total_size != expected["size_bytes"] or actual != expected["sha256"] or total != expected["line_count"]:
        raise JoinFailure("events size/SHA/line-count mismatch")
    if (
        len(selected) != selection["expected_event_count"] or not selected
        or selected[0].dataset_index != selection["expected_first_dataset_event_index"]
        or selected[-1].dataset_index != selection["expected_last_dataset_event_index"]
        or selected[0].timestamp_ns != selection["expected_first_timestamp_ns"]
        or selected[-1].timestamp_ns != selection["expected_last_timestamp_ns"]
        or selected_digest.hexdigest() != selection["selected_raw_lines_sha256"]
    ):
        raise JoinFailure("selected event window anchors/raw SHA mismatch")
    identity = _member_identity(info, actual, total)
    counts = {"source_event_records": total, "before_window_records": before, "after_window_records": after}
    return selected, identity, counts


def _validate_zip_inventory(archive: zipfile.ZipFile, spec: dict[str, Any]) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) != spec["source_archive"]["expected_entry_count"] or len(infos) > spec["resource_limits"]["max_zip_entries"]:
        raise JoinFailure("ZIP entry count differs from the source lock")
    names = [row.filename for row in infos]
    if len(names) != len(set(names)):
        raise JoinFailure("duplicate ZIP member name is forbidden")
    for info in infos:
        name = info.filename
        pure = PurePosixPath(name)
        if not name or "\\" in name or name.startswith("/") or ".." in pure.parts or "\x00" in name:
            raise JoinFailure(f"unsafe ZIP member path: {name!r}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise JoinFailure(f"ZIP symlink member is forbidden: {name}")
        if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise JoinFailure(f"ZIP special-file member is forbidden: {name}")
    by_name = {row.filename: row for row in infos}
    result: dict[str, zipfile.ZipInfo] = {}
    for role in ("events", "poses", "calibration"):
        expected = spec["required_members"][role]
        info = by_name.get(expected["name"])
        if info is None:
            raise JoinFailure(f"required ZIP member is absent: {expected['name']}")
        _check_member_info(info, expected, f"required member {expected['name']}")
        result[role] = info
    return result


def _pose_row(pose: PoseRecord) -> dict[str, Any]:
    return {
        "schema": POSE_SCHEMA, "record_type": "pose", "source_pose_index": pose.index,
        "timestamp_ns": pose.timestamp_ns, "timestamp_seconds_lexeme": pose.timestamp_text,
        "position_m_exact_decimal": dict(zip(("px", "py", "pz"), pose.position)),
        "quaternion_exact_decimal": dict(zip(("qx", "qy", "qz", "qw"), pose.quaternion)),
    }


def _jsonl(rows: Iterator[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(row) for row in rows)


def _output_identity(payload: bytes, kind: str, records: int | None) -> dict[str, Any]:
    return {"kind": kind, "size_bytes": len(payload), "sha256": _sha(payload), "record_count": records}


def import_join(archive_path: Path, license_path: Path, spec_path: Path, result_dir: Path) -> dict[str, Any]:
    """Capture one exact archive, preserve raw source records, join, and atomically publish."""

    archive_path, license_path, spec_path, result_dir = map(Path, (archive_path, license_path, spec_path, result_dir))
    spec, spec_raw = _load_spec(spec_path)
    license_raw = _read_stable(license_path, spec["resource_limits"]["max_license_bytes"], "license")
    license_expected = spec["license"]
    if license_path.name != license_expected["basename"]:
        raise JoinFailure("provided license basename differs from source lock")
    if len(license_raw) != license_expected["size_bytes"] or _sha(license_raw) != license_expected["sha256"]:
        raise JoinFailure("license size/SHA mismatch")
    if not license_raw.startswith(b"Creative Commons Legal Code\n\nAttribution-NonCommercial-ShareAlike 3.0 Unported\n"):
        raise JoinFailure("license content header mismatch")

    with _PackageBuilder(result_dir) as package:
        archive_identity = package.capture_archive(archive_path, spec["source_archive"], spec["resource_limits"]["copy_chunk_bytes"])
        try:
            with package.open_spool() as spool, zipfile.ZipFile(spool) as archive:
                infos = _validate_zip_inventory(archive, spec)
                calibration, calibration_identity = _parse_calibration(
                    archive, infos["calibration"], spec["required_members"]["calibration"], spec["resource_limits"]["max_calibration_line_bytes"],
                )
                poses, pose_identity, negative_dots = _parse_poses(
                    archive, infos["poses"], spec["required_members"]["poses"],
                    spec["resource_limits"]["max_pose_line_bytes"], spec["resource_limits"]["max_pose_records"],
                )
                selected, events_identity, partition = _parse_events(archive, infos["events"], spec["required_members"]["events"], spec)
        except JoinFailure:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as error:
            raise JoinFailure(f"invalid captured ZIP archive: {error}") from error

        pose_timestamps = [row.timestamp_ns for row in poses]
        joined: list[dict[str, Any]] = []
        ages: list[int] = []
        futures: list[int] = []
        spans: list[int] = []
        for sequence_index, event in enumerate(selected):
            left = bisect_right(pose_timestamps, event.timestamp_ns) - 1
            right = left + 1
            if left < 0 or right >= len(poses):
                raise JoinFailure("selected event lacks a causal/future pose bracket")
            left_pose, right_pose = poses[left], poses[right]
            age = event.timestamp_ns - left_pose.timestamp_ns
            future = right_pose.timestamp_ns - event.timestamp_ns
            span = right_pose.timestamp_ns - left_pose.timestamp_ns
            if age < 0 or future <= 0 or age > spec["join_policy"]["max_causal_pose_age_ns_inclusive"]:
                raise JoinFailure("selected event pose bracket is stale or noncausal")
            ages.append(age); futures.append(future); spans.append(span)
            joined.append({
                "schema": EVENT_SCHEMA, "record_type": "event",
                "dataset_event_index": event.dataset_index, "join_sequence_index": sequence_index,
                "timestamp_ns": event.timestamp_ns, "timestamp_seconds_lexeme": event.timestamp_text,
                "x": event.x, "y": event.y, "polarity_01": event.polarity,
                "calibration_id": calibration["calibration_id"],
                "causal_pose": {"source_pose_index": left, "pose_timestamp_ns": left_pose.timestamp_ns, "age_ns": age},
                "bracket": {
                    "left_source_pose_index": left, "right_source_pose_index": right,
                    "left_timestamp_ns": left_pose.timestamp_ns, "right_timestamp_ns": right_pose.timestamp_ns,
                    "alpha_numerator_ns": age, "alpha_denominator_ns": span,
                },
            })

        official_source = _production_lock_matches(spec)
        claims = _claim_scope(official_source)
        calibration_payload = _canonical(calibration)
        pose_header = {
            "schema": POSE_STREAM_SCHEMA, "record_type": "header",
            "pose_stream_id": f"pose-sha256:{pose_identity['sha256']}", "camera_id": spec["dataset"]["camera_id"],
            "timebase": spec["timebase"], "record_count": len(poses), "source_member": "groundtruth.txt",
            "source_pose_meaning": "event_camera_pose_with_respect_to_arbitrary_motion_capture_origin",
            "source_world_z_axis": "gravity_aligned_pointing_up", "source_quaternion_order": ["qx", "qy", "qz", "qw"],
            "source_quaternion_convention": "JPL_as_documented_by_UZH_source_paper",
            "matrix_direction_conversion": "UNRESOLVED_NOT_PERFORMED", "translation_policy": "preserved_not_applied",
        }
        poses_payload = _jsonl(iter((pose_header, *(_pose_row(row) for row in poses))))
        event_header = {
            "schema": EVENT_STREAM_SCHEMA, "record_type": "header",
            "event_stream_id": f"selected-sha256:{spec['selection']['selected_raw_lines_sha256']}",
            "camera_id": spec["dataset"]["camera_id"], "calibration_id": calibration["calibration_id"],
            "pose_stream_id": pose_header["pose_stream_id"], "timebase": spec["timebase"],
            "coordinate_frame": "original_davis240c_sensor_image", "source_member": "events.txt",
            "record_count": len(joined), "selection": spec["selection"], "join_policy": spec["join_policy"], "claim_scope": claims,
        }
        events_payload = _jsonl(iter((event_header, *joined)))

        primary_payloads = {
            LICENSE_NAME: (license_raw, "CC_BY_NC_SA_3_0_LEGAL_CODE", None),
            CALIBRATION_NAME: (calibration_payload, "SOURCE_PRESERVED_CALIBRATION_JSON", 1),
            POSES_NAME: (poses_payload, "SOURCE_PRESERVED_POSE_JSONL", len(poses)),
            EVENTS_NAME: (events_payload, "SOURCE_EVENT_POSE_BRACKET_JSONL", len(joined)),
        }
        artifact_inventory = {
            name: _output_identity(payload, kind, records)
            for name, (payload, kind, records) in primary_payloads.items()
        }
        timestamp_ties = sum(count - 1 for count in Counter(row.timestamp_ns for row in selected).values())
        duplicates = sum(count - 1 for count in Counter((row.timestamp_text, row.x, row.y, row.polarity) for row in selected).values())
        polarity = Counter(row.polarity for row in selected)
        receipt = {
            "schema": RECEIPT_SCHEMA, "status": STATUS, "evidence_class": EVIDENCE_CLASS, "promotion_status": PROMOTION_STATUS,
            "source_dataset_identity": {
                **spec["dataset"], "official_download_basename": spec["source_archive"]["official_download_basename"],
                "provided_local_archive_basename": archive_path.name,
            },
            "source_provenance": {
                "capture_strategy": "one_pinned_source_descriptor_to_private_exact_byte_spool_then_parse_spool",
                "archive": archive_identity,
                "members": {"events": events_identity, "poses": pose_identity, "calibration": calibration_identity},
                "license": {"basename": license_path.name, "size_bytes": len(license_raw), "sha256": _sha(license_raw)},
            },
            "specification_identity": {
                "basename": spec_path.name, "raw_sha256": _sha(spec_raw),
                "semantic_sha256": _sha(_canonical(spec)),
            },
            "input_contract": {
                "source_lock": spec["source_lock"], "sensor": spec["sensor"], "source_formats": spec["source_formats"],
                "timebase": spec["timebase"], "license_exact_bytes_url": LICENSE_BYTES_URL,
            },
            "selection": spec["selection"], "join_policy": spec["join_policy"],
            "conservation": {
                **partition, "admitted_window_records": len(selected), "joined_event_records": len(joined),
                "join_rejected_or_dropped_records": 0,
                "source_partition_equation": "source_event_records == before_window_records + admitted_window_records + after_window_records",
                "zero_drop_equation": "admitted_window_records == joined_event_records + join_rejected_or_dropped_records",
                "join_failure_policy": "FAIL_PACKAGE_NO_PARTIAL_SUCCESS",
                "timestamp_tie_extras": timestamp_ties, "exact_duplicate_input_extras": duplicates,
                "polarity_0_records": polarity[0], "polarity_1_records": polarity[1],
            },
            "pose_coverage_metrics": {
                "pose_record_count": len(poses), "first_pose_timestamp_ns": poses[0].timestamp_ns,
                "last_pose_timestamp_ns": poses[-1].timestamp_ns, "minimum_causal_pose_age_ns": min(ages),
                "maximum_causal_pose_age_ns": max(ages), "minimum_future_pose_distance_ns": min(futures),
                "maximum_future_pose_distance_ns": max(futures), "minimum_bracket_span_ns": min(spans),
                "maximum_bracket_span_ns": max(spans),
                "maximum_allowed_causal_pose_age_ns_inclusive": spec["join_policy"]["max_causal_pose_age_ns_inclusive"],
                "negative_adjacent_quaternion_dot_boundaries": negative_dots, "quaternion_interpolation_performed": False,
            },
            "artifact_inventory": artifact_inventory, "claim_scope": claims,
        }
        receipt_payload = _canonical(receipt)
        completion_artifacts = {
            name: {"size_bytes": row["size_bytes"], "sha256": row["sha256"]}
            for name, row in artifact_inventory.items()
        }
        completion_artifacts[RECEIPT_NAME] = {"size_bytes": len(receipt_payload), "sha256": _sha(receipt_payload)}
        completion = {"schema": COMPLETION_SCHEMA, "status": STATUS, "promotion_status": PROMOTION_STATUS, "artifacts": completion_artifacts}

        package.remove(SOURCE_SPOOL_NAME)
        for name, (payload, _kind, _records) in primary_payloads.items():
            package.write(name, payload)
        package.write(RECEIPT_NAME, receipt_payload)
        package.write(COMPLETION_NAME, _canonical(completion))
        package.publish()
    return receipt


def _read_result_file(path: Path, maximum: int) -> bytes:
    return _read_stable(path, maximum, f"result artifact {path.name}")


def _jsonl_rows(data: bytes, where: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(data.splitlines(keepends=True), 1):
        if not raw.endswith(b"\n"):
            raise JoinFailure(f"{where}:{number} lacks LF")
        value = _json_bytes(raw[:-1], f"{where}:{number}")
        if not isinstance(value, dict):
            raise JoinFailure(f"{where}:{number} must be an object")
        rows.append(value)
    if not rows:
        raise JoinFailure(f"{where} is empty")
    return rows


def _inspect_claims(value: Any, expected_official: bool) -> dict[str, Any]:
    expected = _claim_scope(expected_official)
    if value != expected:
        raise JoinFailure("claim scope is broadened or inconsistent with exact source lock")
    return value


def _provenance_is_production(receipt: dict[str, Any]) -> bool:
    source = receipt["source_provenance"]
    archive = source["archive"]
    if (archive.get("size_bytes"), archive.get("sha256")) != _PRODUCTION_ARCHIVE[:2]:
        return False
    license_row = source["license"]
    if (license_row.get("size_bytes"), license_row.get("sha256")) != _PRODUCTION_LICENSE:
        return False
    for role, expected in _PRODUCTION_MEMBERS.items():
        row = source["members"].get(role, {})
        if (row.get("name"), row.get("size_bytes"), row.get("compressed_size_bytes"), row.get("crc32"), row.get("sha256"), row.get("line_count")) != expected:
            return False
    return True


def inspect(result_dir: Path, spec_path: Path | None = None) -> dict[str, Any]:
    """Inspect a completed package without claiming remote-server authentication."""

    result_dir = Path(result_dir)
    _reject_symlink_components(result_dir)
    try:
        info = result_dir.stat(follow_symlinks=False)
    except OSError as error:
        raise JoinFailure(f"result directory is absent: {error}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise JoinFailure("result path is not a directory")
    actual_names = {path.name for path in result_dir.iterdir()}
    if actual_names != FINAL_NAMES:
        raise JoinFailure(f"result artifact inventory mismatch: {sorted(actual_names)}")
    completion = _strict_keys(_json_bytes(_read_result_file(result_dir / COMPLETION_NAME, 4 * 1024 * 1024), COMPLETION_NAME), {"schema", "status", "promotion_status", "artifacts"}, "completion")
    if completion["schema"] != COMPLETION_SCHEMA or completion["status"] != STATUS or completion["promotion_status"] != PROMOTION_STATUS:
        raise JoinFailure("completion status was changed or promoted")
    artifacts = _strict_keys(completion["artifacts"], set((*PRIMARY_NAMES, RECEIPT_NAME)), "completion.artifacts")
    payloads: dict[str, bytes] = {}
    maximums = {LICENSE_NAME: 128 * 1024, CALIBRATION_NAME: 1024 * 1024, POSES_NAME: 64 * 1024 * 1024, EVENTS_NAME: 32 * 1024 * 1024, RECEIPT_NAME: 8 * 1024 * 1024}
    for name, maximum in maximums.items():
        row = _strict_keys(artifacts[name], {"size_bytes", "sha256"}, f"completion.artifacts.{name}")
        _positive_integer(row["size_bytes"], f"completion.artifacts.{name}.size_bytes")
        _digest(row["sha256"], f"completion.artifacts.{name}.sha256")
        payload = _read_result_file(result_dir / name, maximum)
        if len(payload) != row["size_bytes"] or _sha(payload) != row["sha256"]:
            raise JoinFailure(f"artifact size/SHA mismatch: {name}")
        payloads[name] = payload
    receipt = _strict_keys(_json_bytes(payloads[RECEIPT_NAME], RECEIPT_NAME), {
        "schema", "status", "evidence_class", "promotion_status", "source_dataset_identity", "source_provenance",
        "specification_identity", "input_contract", "selection", "join_policy", "conservation",
        "pose_coverage_metrics", "artifact_inventory", "claim_scope",
    }, "receipt")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["status"] != STATUS or receipt["evidence_class"] != EVIDENCE_CLASS or receipt["promotion_status"] != PROMOTION_STATUS:
        raise JoinFailure("receipt status/evidence class was changed or promoted")
    production = _provenance_is_production(receipt)
    _inspect_claims(receipt["claim_scope"], production)
    source_identity = _strict_keys(receipt["source_dataset_identity"], {
        "provider", "collection", "sequence", "sensor", "camera_id", "landing_url", "download_url", "citation_url", "license_spdx",
        "official_download_basename", "provided_local_archive_basename",
    }, "source_dataset_identity")
    if source_identity != {
        "provider": "University of Zurich Robotics and Perception Group", "collection": "Event-Camera Dataset",
        "sequence": "shapes_rotation", "sensor": "DAVIS240C", "camera_id": "uzh-davis240c-shapes_rotation",
        "landing_url": LANDING_URL, "download_url": DOWNLOAD_URL, "citation_url": CITATION_URL,
        "license_spdx": "CC-BY-NC-SA-3.0", "official_download_basename": OFFICIAL_DOWNLOAD_BASENAME,
        "provided_local_archive_basename": "uzh-shapes_rotation.zip",
    }:
        raise JoinFailure("source dataset identity/basename boundary mismatch")
    provenance = _strict_keys(receipt["source_provenance"], {"capture_strategy", "archive", "members", "license"}, "source_provenance")
    if provenance["capture_strategy"] != "one_pinned_source_descriptor_to_private_exact_byte_spool_then_parse_spool":
        raise JoinFailure("source capture strategy mismatch")
    archive_provenance = _strict_keys(provenance["archive"], {"basename", "size_bytes", "sha256"}, "source_provenance.archive")
    _basename(archive_provenance["basename"], "source_provenance.archive.basename")
    _positive_integer(archive_provenance["size_bytes"], "source_provenance.archive.size_bytes")
    _digest(archive_provenance["sha256"], "source_provenance.archive.sha256")
    members = _strict_keys(provenance["members"], {"events", "poses", "calibration"}, "source_provenance.members")
    for role, name in (("events", "events.txt"), ("poses", "groundtruth.txt"), ("calibration", "calib.txt")):
        row = _strict_keys(members[role], {"name", "size_bytes", "compressed_size_bytes", "crc32", "sha256", "line_count"}, f"source_provenance.members.{role}")
        if row["name"] != name:
            raise JoinFailure("source member name mismatch")
        for key in ("size_bytes", "compressed_size_bytes", "line_count"):
            _positive_integer(row[key], f"source_provenance.members.{role}.{key}")
        _digest(row["sha256"], f"source_provenance.members.{role}.sha256")
        if not isinstance(row["crc32"], str) or CRC_RE.fullmatch(row["crc32"]) is None:
            raise JoinFailure("source member CRC is invalid")
    license_identity = _strict_keys(provenance["license"], {"basename", "size_bytes", "sha256"}, "source_provenance.license")
    _positive_integer(license_identity["size_bytes"], "source_provenance.license.size_bytes")
    _digest(license_identity["sha256"], "source_provenance.license.sha256")
    if license_identity["basename"] != "CC-BY-NC-SA-3.0.txt" or len(payloads[LICENSE_NAME]) != license_identity["size_bytes"] or _sha(payloads[LICENSE_NAME]) != license_identity["sha256"]:
        raise JoinFailure("packaged license differs from source provenance")
    if not payloads[LICENSE_NAME].startswith(b"Creative Commons Legal Code\n\nAttribution-NonCommercial-ShareAlike 3.0 Unported\n"):
        raise JoinFailure("packaged license header mismatch")
    input_contract = _strict_keys(receipt["input_contract"], {"source_lock", "sensor", "source_formats", "timebase", "license_exact_bytes_url"}, "input_contract")
    if input_contract["source_lock"] != {"profile_id": PROFILE_ID, "authority_scope": AUTHORITY_SCOPE, "remote_server_authenticated": False} or input_contract["license_exact_bytes_url"] != LICENSE_BYTES_URL:
        raise JoinFailure("receipt source lock/license URL mismatch")
    if input_contract["sensor"] != {"width": 240, "height": 180, "pixel_origin": "top_left_0_0", "source_polarity_values": [0, 1]}:
        raise JoinFailure("receipt sensor contract mismatch")
    expected_source_formats = {
        "separator": "single_ascii_space", "line_termination": "LF", "timestamp_fractional_digits": 9,
        "events_fields": ["timestamp", "x", "y", "polarity"],
        "poses_fields": ["timestamp", "px", "py", "pz", "qx", "qy", "qz", "qw"],
        "calibration_fields": ["fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"],
    }
    if input_contract["source_formats"] != expected_source_formats:
        raise JoinFailure("receipt source format contract mismatch")
    timebase = {"unit": "ns", "epoch": "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction"}
    if input_contract["timebase"] != timebase:
        raise JoinFailure("receipt timebase mismatch")
    selection = _strict_keys(receipt["selection"], {
        "start_timestamp_ns_inclusive", "end_timestamp_ns_exclusive", "expected_event_count",
        "expected_first_dataset_event_index", "expected_last_dataset_event_index", "expected_first_timestamp_ns",
        "expected_last_timestamp_ns", "selected_raw_lines_sha256",
    }, "receipt.selection")
    _digest(selection["selected_raw_lines_sha256"], "receipt.selection.selected_raw_lines_sha256")
    for key in selection:
        if key != "selected_raw_lines_sha256":
            _nonnegative_integer(selection[key], f"receipt.selection.{key}")
    if selection["expected_event_count"] <= 0 or selection["start_timestamp_ns_inclusive"] >= selection["end_timestamp_ns_exclusive"]:
        raise JoinFailure("receipt selection bounds/count mismatch")
    join_policy = receipt["join_policy"]
    if join_policy != {
        "ordering": "preserve_source_order", "causal_pose": "latest_at_or_before_event_timestamp",
        "future_bracket": "first_pose_strictly_after_event_timestamp", "interpolation_output": "none_bracket_identity_only",
        "max_causal_pose_age_ns_inclusive": 5_000_000, "missing_stale_or_unbracketed": "fail_package_no_partial_success",
    }:
        raise JoinFailure("receipt join policy mismatch")

    calibration = _strict_keys(_json_bytes(payloads[CALIBRATION_NAME], CALIBRATION_NAME), {
        "schema", "calibration_id", "camera_id", "sensor", "model", "parameter_order",
        "parameters_exact_decimal", "source_member", "source_record_index",
    }, "calibration")
    order = ["fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"]
    if calibration["schema"] != CALIBRATION_SCHEMA or calibration["camera_id"] != "uzh-davis240c-shapes_rotation" or calibration["sensor"] != {"width": 240, "height": 180} or calibration["model"] != "opencv_pinhole_radtan" or calibration["parameter_order"] != order or calibration["source_member"] != "calib.txt" or calibration["source_record_index"] != 0:
        raise JoinFailure("calibration record contract mismatch")
    parameters = _strict_keys(calibration["parameters_exact_decimal"], set(order), "calibration.parameters")
    for key in order:
        _decimal(parameters[key].encode("ascii") if isinstance(parameters[key], str) else b"", f"calibration.{key}")
    calibration_raw = (" ".join(parameters[key] for key in order) + "\n").encode("ascii")
    if len(calibration_raw) != members["calibration"]["size_bytes"] or _sha(calibration_raw) != members["calibration"]["sha256"]:
        raise JoinFailure("calibration output cannot reconstruct pinned member bytes")
    if calibration["calibration_id"] != f"calib-sha256:{members['calibration']['sha256']}":
        raise JoinFailure("calibration ID is not content-bound")

    pose_rows = _jsonl_rows(payloads[POSES_NAME], POSES_NAME)
    pose_header = _strict_keys(pose_rows[0], {
        "schema", "record_type", "pose_stream_id", "camera_id", "timebase", "record_count", "source_member",
        "source_pose_meaning", "source_world_z_axis", "source_quaternion_order", "source_quaternion_convention",
        "matrix_direction_conversion", "translation_policy",
    }, "pose header")
    if pose_header != {
        "schema": POSE_STREAM_SCHEMA, "record_type": "header", "pose_stream_id": f"pose-sha256:{members['poses']['sha256']}",
        "camera_id": "uzh-davis240c-shapes_rotation", "timebase": timebase, "record_count": len(pose_rows) - 1,
        "source_member": "groundtruth.txt", "source_pose_meaning": "event_camera_pose_with_respect_to_arbitrary_motion_capture_origin",
        "source_world_z_axis": "gravity_aligned_pointing_up", "source_quaternion_order": ["qx", "qy", "qz", "qw"],
        "source_quaternion_convention": "JPL_as_documented_by_UZH_source_paper", "matrix_direction_conversion": "UNRESOLVED_NOT_PERFORMED",
        "translation_policy": "preserved_not_applied",
    }:
        raise JoinFailure("pose header contract mismatch")
    pose_digest = hashlib.sha256()
    pose_reconstructed_size = 0
    pose_times: list[int] = []
    negative_dots = 0
    prior_q: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
    for index, row_value in enumerate(pose_rows[1:]):
        row = _strict_keys(row_value, {"schema", "record_type", "source_pose_index", "timestamp_ns", "timestamp_seconds_lexeme", "position_m_exact_decimal", "quaternion_exact_decimal"}, f"pose {index}")
        if row["schema"] != POSE_SCHEMA or row["record_type"] != "pose" or row["source_pose_index"] != index:
            raise JoinFailure("pose record identity/order mismatch")
        timestamp_text = row["timestamp_seconds_lexeme"]
        if not isinstance(timestamp_text, str):
            raise JoinFailure("pose timestamp lexeme is not a string")
        _, timestamp_ns = _timestamp(timestamp_text.encode("ascii"), f"pose {index}")
        if row["timestamp_ns"] != timestamp_ns or (pose_times and timestamp_ns <= pose_times[-1]):
            raise JoinFailure("pose timestamp/order mismatch")
        pose_times.append(timestamp_ns)
        position = _strict_keys(row["position_m_exact_decimal"], {"px", "py", "pz"}, f"pose {index} position")
        quaternion = _strict_keys(row["quaternion_exact_decimal"], {"qx", "qy", "qz", "qw"}, f"pose {index} quaternion")
        values: list[str] = []
        for key in ("px", "py", "pz"):
            if not isinstance(position[key], str): raise JoinFailure("pose decimal must be a string")
            _decimal(position[key].encode("ascii"), f"pose {index}.{key}"); values.append(position[key])
        q_values: list[Decimal] = []
        for key in ("qx", "qy", "qz", "qw"):
            if not isinstance(quaternion[key], str): raise JoinFailure("pose decimal must be a string")
            _decimal(quaternion[key].encode("ascii"), f"pose {index}.{key}"); values.append(quaternion[key]); q_values.append(Decimal(quaternion[key]))
        q_tuple = tuple(q_values)
        if not Decimal("0.99") <= sum(item * item for item in q_tuple) <= Decimal("1.01"):
            raise JoinFailure("pose quaternion norm mismatch")
        if prior_q is not None and sum(a * b for a, b in zip(prior_q, q_tuple)) < 0: negative_dots += 1
        prior_q = q_tuple
        reconstructed = (timestamp_text + " " + " ".join(values) + "\n").encode("ascii")
        pose_digest.update(reconstructed)
        pose_reconstructed_size += len(reconstructed)
    if len(pose_rows) - 1 != members["poses"]["line_count"] or pose_reconstructed_size != members["poses"]["size_bytes"] or pose_digest.hexdigest() != members["poses"]["sha256"]:
        raise JoinFailure("pose output cannot reconstruct pinned member bytes")

    event_rows = _jsonl_rows(payloads[EVENTS_NAME], EVENTS_NAME)
    event_header = _strict_keys(event_rows[0], {
        "schema", "record_type", "event_stream_id", "camera_id", "calibration_id", "pose_stream_id", "timebase",
        "coordinate_frame", "source_member", "record_count", "selection", "join_policy", "claim_scope",
    }, "event header")
    if event_header["schema"] != EVENT_STREAM_SCHEMA or event_header["record_type"] != "header" or event_header["camera_id"] != "uzh-davis240c-shapes_rotation" or event_header["calibration_id"] != calibration["calibration_id"] or event_header["pose_stream_id"] != pose_header["pose_stream_id"] or event_header["timebase"] != timebase or event_header["coordinate_frame"] != "original_davis240c_sensor_image" or event_header["source_member"] != "events.txt" or event_header["record_count"] != len(event_rows) - 1 or event_header["selection"] != selection or event_header["join_policy"] != join_policy:
        raise JoinFailure("event header contract mismatch")
    _inspect_claims(event_header["claim_scope"], production)
    if event_header["event_stream_id"] != f"selected-sha256:{selection['selected_raw_lines_sha256']}":
        raise JoinFailure("event stream ID is not selected-byte-bound")
    selected_digest = hashlib.sha256()
    event_times: list[int] = []
    dataset_indices: list[int] = []
    ages: list[int] = []; futures: list[int] = []; spans: list[int] = []
    tie_counter: Counter[int] = Counter(); duplicate_counter: Counter[tuple[str, int, int, int]] = Counter(); polarity_counter: Counter[int] = Counter()
    for index, row_value in enumerate(event_rows[1:]):
        row = _strict_keys(row_value, {"schema", "record_type", "dataset_event_index", "join_sequence_index", "timestamp_ns", "timestamp_seconds_lexeme", "x", "y", "polarity_01", "calibration_id", "causal_pose", "bracket"}, f"event {index}")
        if row["schema"] != EVENT_SCHEMA or row["record_type"] != "event" or row["join_sequence_index"] != index or row["calibration_id"] != calibration["calibration_id"]:
            raise JoinFailure("event record identity/order mismatch")
        dataset_index = _nonnegative_integer(row["dataset_event_index"], f"event {index}.dataset_event_index")
        if dataset_indices and dataset_index != dataset_indices[-1] + 1: raise JoinFailure("selected dataset event indices are not contiguous")
        dataset_indices.append(dataset_index)
        timestamp_text = row["timestamp_seconds_lexeme"]
        if not isinstance(timestamp_text, str): raise JoinFailure("event timestamp lexeme is not a string")
        _, timestamp_ns = _timestamp(timestamp_text.encode("ascii"), f"event {index}")
        if row["timestamp_ns"] != timestamp_ns or not selection["start_timestamp_ns_inclusive"] <= timestamp_ns < selection["end_timestamp_ns_exclusive"]: raise JoinFailure("event timestamp/window mismatch")
        if event_times and timestamp_ns < event_times[-1]: raise JoinFailure("selected event timestamps are not monotonic")
        event_times.append(timestamp_ns)
        x = _nonnegative_integer(row["x"], f"event {index}.x"); y = _nonnegative_integer(row["y"], f"event {index}.y")
        polarity = row["polarity_01"]
        if x >= 240 or y >= 180 or isinstance(polarity, bool) or polarity not in (0, 1): raise JoinFailure("event coordinate/polarity mismatch")
        causal = _strict_keys(row["causal_pose"], {"source_pose_index", "pose_timestamp_ns", "age_ns"}, f"event {index}.causal_pose")
        bracket = _strict_keys(row["bracket"], {"left_source_pose_index", "right_source_pose_index", "left_timestamp_ns", "right_timestamp_ns", "alpha_numerator_ns", "alpha_denominator_ns"}, f"event {index}.bracket")
        left = causal["source_pose_index"]; right = bracket["right_source_pose_index"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (*causal.values(), *bracket.values())): raise JoinFailure("event pose bracket fields must be integers")
        if left < 0 or right != left + 1 or right >= len(pose_times) or bracket["left_source_pose_index"] != left: raise JoinFailure("event pose bracket indices mismatch")
        age = timestamp_ns - pose_times[left]; future = pose_times[right] - timestamp_ns; span = pose_times[right] - pose_times[left]
        if causal != {"source_pose_index": left, "pose_timestamp_ns": pose_times[left], "age_ns": age} or bracket != {"left_source_pose_index": left, "right_source_pose_index": right, "left_timestamp_ns": pose_times[left], "right_timestamp_ns": pose_times[right], "alpha_numerator_ns": age, "alpha_denominator_ns": span}: raise JoinFailure("event pose bracket values mismatch")
        if age < 0 or age > join_policy["max_causal_pose_age_ns_inclusive"] or future <= 0: raise JoinFailure("event pose bracket is stale/noncausal")
        raw = f"{timestamp_text} {x} {y} {polarity}\n".encode("ascii"); selected_digest.update(raw)
        ages.append(age); futures.append(future); spans.append(span); tie_counter[timestamp_ns] += 1; duplicate_counter[(timestamp_text, x, y, polarity)] += 1; polarity_counter[polarity] += 1
    if len(event_rows) - 1 != selection["expected_event_count"] or selected_digest.hexdigest() != selection["selected_raw_lines_sha256"] or dataset_indices[0] != selection["expected_first_dataset_event_index"] or dataset_indices[-1] != selection["expected_last_dataset_event_index"] or event_times[0] != selection["expected_first_timestamp_ns"] or event_times[-1] != selection["expected_last_timestamp_ns"]:
        raise JoinFailure("event output cannot reconstruct selected source bytes/anchors")

    conservation = _strict_keys(receipt["conservation"], {"source_event_records", "before_window_records", "admitted_window_records", "after_window_records", "joined_event_records", "join_rejected_or_dropped_records", "source_partition_equation", "zero_drop_equation", "join_failure_policy", "timestamp_tie_extras", "exact_duplicate_input_extras", "polarity_0_records", "polarity_1_records"}, "conservation")
    for key in ("source_event_records", "before_window_records", "admitted_window_records", "after_window_records", "joined_event_records", "join_rejected_or_dropped_records", "timestamp_tie_extras", "exact_duplicate_input_extras", "polarity_0_records", "polarity_1_records"):
        _nonnegative_integer(conservation[key], f"conservation.{key}")
    if conservation["source_event_records"] != members["events"]["line_count"] or conservation["source_event_records"] != conservation["before_window_records"] + conservation["admitted_window_records"] + conservation["after_window_records"] or conservation["admitted_window_records"] != conservation["joined_event_records"] + conservation["join_rejected_or_dropped_records"] or conservation["admitted_window_records"] != len(event_rows) - 1 or conservation["join_rejected_or_dropped_records"] != 0 or conservation["timestamp_tie_extras"] != sum(value - 1 for value in tie_counter.values()) or conservation["exact_duplicate_input_extras"] != sum(value - 1 for value in duplicate_counter.values()) or conservation["polarity_0_records"] != polarity_counter[0] or conservation["polarity_1_records"] != polarity_counter[1] or conservation["source_partition_equation"] != "source_event_records == before_window_records + admitted_window_records + after_window_records" or conservation["zero_drop_equation"] != "admitted_window_records == joined_event_records + join_rejected_or_dropped_records" or conservation["join_failure_policy"] != "FAIL_PACKAGE_NO_PARTIAL_SUCCESS":
        raise JoinFailure("conservation accounting mismatch")
    metrics = receipt["pose_coverage_metrics"]
    expected_metrics = {
        "pose_record_count": len(pose_times), "first_pose_timestamp_ns": pose_times[0], "last_pose_timestamp_ns": pose_times[-1],
        "minimum_causal_pose_age_ns": min(ages), "maximum_causal_pose_age_ns": max(ages),
        "minimum_future_pose_distance_ns": min(futures), "maximum_future_pose_distance_ns": max(futures),
        "minimum_bracket_span_ns": min(spans), "maximum_bracket_span_ns": max(spans),
        "maximum_allowed_causal_pose_age_ns_inclusive": join_policy["max_causal_pose_age_ns_inclusive"],
        "negative_adjacent_quaternion_dot_boundaries": negative_dots, "quaternion_interpolation_performed": False,
    }
    if metrics != expected_metrics: raise JoinFailure("pose coverage metrics mismatch")

    inventory = _strict_keys(receipt["artifact_inventory"], set(PRIMARY_NAMES), "artifact_inventory")
    expected_kinds = {LICENSE_NAME: ("CC_BY_NC_SA_3_0_LEGAL_CODE", None), CALIBRATION_NAME: ("SOURCE_PRESERVED_CALIBRATION_JSON", 1), POSES_NAME: ("SOURCE_PRESERVED_POSE_JSONL", len(pose_rows) - 1), EVENTS_NAME: ("SOURCE_EVENT_POSE_BRACKET_JSONL", len(event_rows) - 1)}
    for name in PRIMARY_NAMES:
        row = _strict_keys(inventory[name], {"kind", "size_bytes", "sha256", "record_count"}, f"artifact_inventory.{name}")
        kind, records = expected_kinds[name]
        if row != {"kind": kind, "size_bytes": len(payloads[name]), "sha256": _sha(payloads[name]), "record_count": records}: raise JoinFailure(f"receipt artifact inventory mismatch: {name}")
        if artifacts[name] != {"size_bytes": row["size_bytes"], "sha256": row["sha256"]}: raise JoinFailure(f"completion/receipt inventory mismatch: {name}")
    specification = _strict_keys(receipt["specification_identity"], {"basename", "raw_sha256", "semantic_sha256"}, "specification_identity")
    _basename(specification["basename"], "specification_identity.basename"); _digest(specification["raw_sha256"], "specification_identity.raw_sha256"); _digest(specification["semantic_sha256"], "specification_identity.semantic_sha256")
    if spec_path is not None:
        spec, raw = _load_spec(Path(spec_path))
        if specification != {
            "basename": Path(spec_path).name,
            "raw_sha256": _sha(raw),
            "semantic_sha256": _sha(_canonical(spec)),
        }:
            raise JoinFailure("provided specification differs from package binding")

        expected_production = _production_lock_matches(spec)
        expected_source_identity = {
            **spec["dataset"],
            "official_download_basename": spec["source_archive"]["official_download_basename"],
            "provided_local_archive_basename": spec["source_archive"]["basename"],
        }
        expected_provenance = {
            "capture_strategy": "one_pinned_source_descriptor_to_private_exact_byte_spool_then_parse_spool",
            "archive": {
                "basename": spec["source_archive"]["basename"],
                "size_bytes": spec["source_archive"]["size_bytes"],
                "sha256": spec["source_archive"]["sha256"],
            },
            "members": spec["required_members"],
            "license": {
                "basename": spec["license"]["basename"],
                "size_bytes": spec["license"]["size_bytes"],
                "sha256": spec["license"]["sha256"],
            },
        }
        expected_input_contract = {
            "source_lock": spec["source_lock"],
            "sensor": spec["sensor"],
            "source_formats": spec["source_formats"],
            "timebase": spec["timebase"],
            "license_exact_bytes_url": LICENSE_BYTES_URL,
        }
        if source_identity != expected_source_identity:
            raise JoinFailure("source dataset identity differs from provided specification")
        if provenance != expected_provenance:
            raise JoinFailure("source provenance differs from provided specification")
        if input_contract != expected_input_contract:
            raise JoinFailure("input contract differs from provided specification")
        if selection != spec["selection"] or join_policy != spec["join_policy"]:
            raise JoinFailure("selection/join policy differs from provided specification")
        if receipt["claim_scope"] != _claim_scope(expected_production):
            raise JoinFailure("claim scope differs from provided specification source lock")
        if production != expected_production:
            raise JoinFailure("production-source classification differs from provided specification")
    return {"status": STATUS, "promotion_status": PROMOTION_STATUS, "official_uzh_source": production, "generated_artifact_official_uzh": False, "receipt_sha256": _sha(payloads[RECEIPT_NAME])}


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--spec", type=Path, default=Path(__file__).with_name("join_spec.json"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--license", dest="license_path", type=Path)
    parser.add_argument("--inspect", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(sys.argv[1:] if argv is None else argv)
    try:
        if args.inspect:
            if args.archive is not None or args.license_path is not None:
                raise JoinFailure("inspection accepts no archive/license input")
            print(json.dumps(inspect(args.result_dir, args.spec), sort_keys=True))
            return 0
        if args.archive is None or args.license_path is None:
            raise JoinFailure("import requires --archive and --license")
        receipt = import_join(args.archive, args.license_path, args.spec, args.result_dir)
        print(json.dumps({"status": receipt["status"], "promotion_status": receipt["promotion_status"], "joined_events": receipt["conservation"]["joined_event_records"]}, sort_keys=True))
        return 0
    except JoinFailure as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
