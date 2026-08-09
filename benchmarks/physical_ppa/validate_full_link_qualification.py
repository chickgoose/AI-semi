#!/usr/bin/env python3
"""Validate address-only AER full-link qualification records.

The JSON Schema documents the interchange format. This dependency-free
validator enforces the cross-field accounting invariants that JSON Schema
cannot express conveniently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

import generate_full_link_inventory as inventory_generator
import extract_full_link_evidence as evidence_extractor


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
FREE_OPERATIONS = {
    "rename",
    "bit_permutation",
    "slice",
    "concatenation",
    "constant_tie",
    "zero_extension",
}
EXCLUDED_PIN_ROLES = {"clock", "reset", "power", "ground"}
PIN_DIRECTIONS = {"input", "output", "bidirectional"}
SCHEMA_PATH = Path(__file__).with_name("full_link_qualification.schema.json")
FEATURE_KIND_COMPATIBILITY = {
    "codec": {"codec", "encoder", "decoder"},
    "serializer": {"serializer"},
    "deserializer": {"deserializer"},
    "buffer": {"buffer"},
    "cdc": {"cdc", "clocking"},
    "normalizer": {"normalizer", "adapter"},
}
FEATURE_BLOCK_KINDS = set().union(*FEATURE_KIND_COMPATIBILITY.values())


class QualificationError(ValueError):
    """Raised when a record is incomplete or violates an accounting invariant."""


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without treating booleans as integers."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _resolve_local_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise QualificationError(f"unsupported non-local schema reference: {ref}")
    value: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise QualificationError(f"unresolved schema reference: {ref}")
        value = value[part]
    if not isinstance(value, dict):
        raise QualificationError(f"schema reference does not name an object: {ref}")
    return value


def _validate_against_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Validate the JSON-Schema keywords used by the qualification schema."""

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str):
            errors.append(f"{path} schema $ref must be a string")
            return
        _validate_against_schema(
            value, _resolve_local_ref(root_schema, ref), root_schema, path, errors
        )
        return

    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and not any(
        _json_equal(value, choice) for choice in schema["enum"]
    ):
        errors.append(f"{path} is not one of the allowed values")

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        if not _schema_type_matches(value, expected_type):
            errors.append(f"{path} must be of type {expected_type}")
            return

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(f"{path}.{name} is required")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, child in properties.items():
                if name in value and isinstance(child, dict):
                    _validate_against_schema(
                        value[name], child, root_schema, f"{path}.{name}", errors
                    )
            if schema.get("additionalProperties") is False:
                for name in value:
                    if name not in properties:
                        errors.append(f"{path}.{name} is an additional property")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            errors.append(f"{path} must contain at least {minimum_items} items")
        if schema.get("uniqueItems") is True:
            encoded = [
                json.dumps(item, sort_keys=True, separators=(",", ":"))
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path} must contain unique items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_against_schema(
                    item, item_schema, root_schema, f"{path}[{index}]", errors
                )

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            errors.append(f"{path} is shorter than {minimum_length} characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path} does not match required pattern {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} must be at least {minimum}")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(f"{path} must be greater than {exclusive_minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} must be at most {maximum}")


def _schema_errors(record: Any) -> list[str]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read qualification schema: {exc}") from exc
    errors: list[str] = []
    _validate_against_schema(record, schema, schema, "$", errors)
    return errors


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _stable_read_regular_snapshot(
    path: Path, label: str, errors: list[str]
) -> tuple[bytes, tuple[int, int, int, int, int]] | None:
    """Read one regular file without following symlinks and detect mutation."""

    try:
        before_path = os.lstat(path)
    except OSError as exc:
        errors.append(f"{label} cannot be lstat'ed: {exc}")
        return None
    if stat.S_ISLNK(before_path.st_mode):
        errors.append(f"{label} must not be a symlink")
        return None
    if not stat.S_ISREG(before_path.st_mode):
        errors.append(f"{label} must be a regular file")
        return None

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        errors.append(f"{label} cannot be opened without symlink following: {exc}")
        return None
    try:
        before_fd = os.fstat(descriptor)
        if not stat.S_ISREG(before_fd.st_mode):
            errors.append(f"{label} opened object is not a regular file")
            return None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
    except OSError as exc:
        errors.append(f"{label} failed during stable read: {exc}")
        return None
    finally:
        os.close(descriptor)

    try:
        after_path = os.lstat(path)
    except OSError as exc:
        errors.append(f"{label} disappeared after stable read: {exc}")
        return None
    identities = {
        _stat_identity(before_path),
        _stat_identity(before_fd),
        _stat_identity(after_fd),
        _stat_identity(after_path),
    }
    if len(identities) != 1:
        errors.append(f"{label} changed during stable read")
        return None
    return b"".join(chunks), _stat_identity(after_fd)


def _stable_read_regular(path: Path, label: str, errors: list[str]) -> bytes | None:
    snapshot = _stable_read_regular_snapshot(path, label, errors)
    return None if snapshot is None else snapshot[0]


def _reject_ancestor_symlinks(path: Path, label: str, errors: list[str]) -> None:
    """Reject a symlink in any existing component from filesystem root to path."""

    absolute = path.absolute()
    chain = list(reversed(absolute.parents)) + [absolute]
    for component in chain:
        try:
            info = os.lstat(component)
        except OSError as exc:
            errors.append(f"{label} ancestor cannot be lstat'ed: {component}: {exc}")
            return
        if stat.S_ISLNK(info.st_mode):
            errors.append(f"{label} ancestor must not be a symlink: {component}")
            return


class ArtifactReader:
    """Resolve and verify record-relative artifact references exactly once."""

    def __init__(self, base_dir: Path, errors: list[str]):
        self.base_dir = base_dir
        self.errors = errors
        self.cache: dict[str, tuple[str, str, bytes]] = {}
        self.inode_roles: dict[tuple[int, int], str] = {}

    def read(self, value: Any, path: str) -> bytes | None:
        artifact = _mapping(value, path, self.errors)
        raw_path = artifact.get("path")
        expected = artifact.get("sha256")
        _check_sha(expected, f"{path}.sha256", self.errors)
        if not isinstance(raw_path, str) or not raw_path:
            self.errors.append(f"{path}.path must be a nonempty relative path")
            return None
        relative = Path(raw_path)
        if (
            relative.is_absolute()
            or "\\" in raw_path
            or relative.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            self.errors.append(
                f"{path}.path must be normalized, relative, and contain no dot components"
            )
            return None

        cached = self.cache.get(raw_path)
        normalized_sha = str(expected).lower()
        if cached is not None:
            cached_sha, cached_role, cached_data = cached
            if cached_role != path:
                self.errors.append(
                    f"artifact evidence-role reuse is forbidden: {raw_path!r} "
                    f"used by {cached_role} and {path}"
                )
                return None
            if cached_sha != normalized_sha:
                self.errors.append(
                    f"artifact {raw_path!r} has contradictory SHA-256 records"
                )
                return None
            return cached_data

        current = self.base_dir
        for part in relative.parts:
            current = current / part
            try:
                info = os.lstat(current)
            except OSError as exc:
                self.errors.append(f"{path}.path cannot be resolved: {exc}")
                return None
            if stat.S_ISLNK(info.st_mode):
                self.errors.append(f"{path}.path traverses symlink {current}")
                return None

        snapshot = _stable_read_regular_snapshot(
            current, f"{path}.path", self.errors
        )
        if snapshot is None:
            return None
        data, identity = snapshot
        actual = hashlib.sha256(data).hexdigest()
        if not isinstance(expected, str) or actual != normalized_sha:
            self.errors.append(
                f"{path}.sha256 digest mismatch ({expected!r} != {actual})"
            )
            return None
        inode = (identity[0], identity[1])
        prior_role = self.inode_roles.get(inode)
        if prior_role is not None and prior_role != path:
            self.errors.append(
                f"artifact inode evidence-role reuse is forbidden between "
                f"{prior_role} and {path}"
            )
            return None
        self.inode_roles[inode] = path
        self.cache[raw_path] = (normalized_sha, path, data)
        return data

    def json(self, value: Any, path: str) -> dict[str, Any]:
        data = self.read(value, path)
        if data is None:
            return {}
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.errors.append(f"{path} must contain UTF-8 JSON: {exc}")
            return {}
        return _mapping(parsed, f"{path}<content>", self.errors)


def _strict_keys(
    value: dict[str, Any], required: set[str], path: str, errors: list[str]
) -> None:
    missing = required - set(value)
    extra = set(value) - required
    for name in sorted(missing):
        errors.append(f"{path}.{name} is required")
    for name in sorted(extra):
        errors.append(f"{path}.{name} is an additional property")


def _inventory_path(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{path} must be a nonempty relative path")
        return None
    candidate = Path(value)
    if (
        candidate.is_absolute()
        or "\\" in value
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        errors.append(f"{path} must be normalized and relative")
        return None
    return value


def _mapping(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _array(value: Any, path: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    return value


def _require(obj: dict[str, Any], fields: Sequence[str], path: str,
             errors: list[str]) -> None:
    for field in fields:
        if field not in obj:
            errors.append(f"{path}.{field} is required")


def _positive_int(value: Any, path: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{path} must be a positive integer")
        return 0
    return value


def _finite_nonnegative(value: Any, path: str, errors: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a finite nonnegative number")
        return 0.0
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        errors.append(f"{path} must be a finite nonnegative number")
        return 0.0
    return result


def _check_sha(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"{path} must be a 64-digit hexadecimal SHA-256")


def _pin_count(value: Any, path: str, errors: list[str]) -> int:
    pins = _array(value, path, errors)
    if not pins:
        errors.append(f"{path} must not be empty")
        return 0
    names: set[str] = set()
    count = 0
    for index, item in enumerate(pins):
        pin_path = f"{path}[{index}]"
        pin = _mapping(item, pin_path, errors)
        _require(pin, ("name", "direction", "width", "role"), pin_path, errors)
        name = pin.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{pin_path}.name must be a nonempty string")
        elif name in names:
            errors.append(f"{path} contains duplicate pin name {name!r}")
        else:
            names.add(name)
        if pin.get("direction") not in PIN_DIRECTIONS:
            errors.append(f"{pin_path}.direction is invalid")
        width = _positive_int(pin.get("width"), f"{pin_path}.width", errors)
        role = pin.get("role")
        if role not in EXCLUDED_PIN_ROLES | {"functional"}:
            errors.append(f"{pin_path}.role is invalid")
        if role == "functional":
            count += width
    return count


def _same_number(actual: Any, expected: float, path: str,
                 errors: list[str]) -> None:
    value = _finite_nonnegative(actual, path, errors)
    if not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12):
        errors.append(f"{path}={value:.12g}, expected {expected:.12g}")


def _load_bundle_inventory(
    reader: ArtifactReader, value: Any, errors: list[str]
) -> tuple[list[str], dict[str, bytes]]:
    path = "$.candidate.bundle_inventory"
    inventory = reader.json(value, path)
    _strict_keys(inventory, {"schema_version", "files"}, f"{path}<content>", errors)
    if inventory.get("schema_version") != 1:
        errors.append(f"{path}<content>.schema_version must equal 1")
    files = _array(inventory.get("files"), f"{path}<content>.files", errors)
    ordered: list[str] = []
    source_data: dict[str, bytes] = {}
    seen: set[str] = set()
    for index, item in enumerate(files):
        item_path = f"{path}<content>.files[{index}]"
        entry = _mapping(item, item_path, errors)
        _strict_keys(entry, {"path", "sha256"}, item_path, errors)
        source_path = _inventory_path(entry.get("path"), f"{item_path}.path", errors)
        if source_path is None:
            continue
        if source_path in seen:
            errors.append(f"{path}<content>.files contains duplicate {source_path!r}")
        seen.add(source_path)
        ordered.append(source_path)
        data = reader.read(entry, item_path)
        if data is not None:
            source_data[source_path] = data
    if not ordered:
        errors.append(f"{path}<content>.files must not be empty")
    return ordered, source_data


def _load_ordered_filelist(
    reader: ArtifactReader, value: Any, errors: list[str]
) -> list[str]:
    path = "$.candidate.filelist"
    data = reader.read(value, path)
    if data is None:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{path} must contain UTF-8 paths: {exc}")
        return []
    result: list[str] = []
    seen: set[str] = set()
    for index, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line or raw_line != raw_line.strip():
            errors.append(f"{path}<content> line {index} must be one normalized path")
            continue
        source_path = _inventory_path(raw_line, f"{path}<content> line {index}", errors)
        if source_path is None:
            continue
        if source_path in seen:
            errors.append(f"{path}<content> contains duplicate {source_path!r}")
        seen.add(source_path)
        result.append(source_path)
    if not result:
        errors.append(f"{path}<content> must not be empty")
    return result


def _load_canonical_evidence(
    reader: ArtifactReader,
    value: Any,
    path: str,
    evidence_type: str,
    value_fields: set[str],
    expected_inputs: list[tuple[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    """Regenerate one canonical result from its raw report and frozen inputs."""

    report = reader.json(value, path)
    producer = _mapping(report.get("producer"), f"{path}<content>.producer", errors)
    inputs = _array(
        producer.get("inputs"), f"{path}<content>.producer.inputs", errors
    )
    raw_reference = inputs[0] if inputs else {}
    raw_data = reader.read(
        raw_reference, f"{path}<content>.producer.raw_report"
    )
    context: list[tuple[str, dict[str, str]]] = []
    for role, reference in expected_inputs:
        artifact_ref = _mapping(reference, f"{path}<expected:{role}>", errors)
        context.append((role, {
            "path": str(artifact_ref.get("path")),
            "sha256": str(artifact_ref.get("sha256")).lower(),
        }))
    output_ref = _mapping(value, path, errors)
    extractor_errors: list[str] = []
    extractor_data = _stable_read_regular(
        Path(evidence_extractor.__file__), "trusted evidence extractor",
        extractor_errors,
    )
    errors.extend(extractor_errors)
    try:
        expected_report = evidence_extractor.produce_evidence(
            evidence_type=evidence_type,
            raw_data=raw_data or b"",
            raw_path=str(_mapping(raw_reference, f"{path}<raw>", errors).get("path")),
            context_inputs=context,
            output_path=str(output_ref.get("path")),
            extractor_sha256=hashlib.sha256(extractor_data or b"").hexdigest(),
        )
    except evidence_extractor.EvidenceError as exc:
        errors.append(f"{path} trusted raw-report extraction failed: {exc}")
        expected_report = {}
    if report != expected_report:
        errors.append(
            f"{path}<content> does not match trusted regenerated canonical evidence"
        )
    values = _mapping(expected_report.get("values"), f"{path}<derived-values>", errors)
    _strict_keys(values, value_fields, f"{path}<derived-values>", errors)
    return values


def _parse_json_bytes(data: bytes | None, path: str, errors: list[str]) -> dict[str, Any]:
    if data is None:
        return {}
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path} must contain UTF-8 JSON: {exc}")
        return {}
    return _mapping(value, path, errors)


def _load_synthesis_assets(
    reader: ArtifactReader,
    synthesis_data: bytes | None,
    candidate_filelist: Any,
    primary_library: Any,
    primary_tool_config: Any,
    primary_sdc: Any,
    mapped_netlist: Any,
    hierarchy_source: Any,
    filelist_data: bytes | None,
    library_data: bytes | None,
    tool_config_data: bytes | None,
    sdc_data: bytes | None,
    mapped_netlist_data: bytes | None,
    hierarchy_source_data: bytes | None,
    errors: list[str],
) -> dict[str, bytes]:
    """Read every command-bound include, generated-IP, and library artifact."""

    path = "$.flow.synthesis_command<content>"
    manifest = _parse_json_bytes(synthesis_data, path, errors)
    _strict_keys(
        manifest,
        {
            "schema_version", "synthesis_top", "command", "filelist",
            "tool_config", "sdc", "mapped_netlist", "hierarchy_source",
            "include_files", "generated_ip", "libraries",
        },
        path,
        errors,
    )
    if manifest.get("schema_version") != 1:
        errors.append(f"{path}.schema_version must equal 1")
    command = _array(manifest.get("command"), f"{path}.command", errors)
    if not command or not all(isinstance(token, str) and token for token in command):
        errors.append(f"{path}.command must contain nonempty string tokens")
    if manifest.get("filelist") != candidate_filelist:
        errors.append(f"{path}.filelist must equal candidate.filelist artifact binding")

    assets: dict[str, bytes] = {}
    filelist_ref = _mapping(candidate_filelist, "$.candidate.filelist", errors)
    if filelist_data is not None and isinstance(filelist_ref.get("path"), str):
        assets[filelist_ref["path"]] = filelist_data
    library_ref = _mapping(primary_library, "$.flow.library", errors)
    if library_data is not None and isinstance(library_ref.get("path"), str):
        assets[library_ref["path"]] = library_data
    for name, reference, data in (
        ("tool_config", primary_tool_config, tool_config_data),
        ("sdc", primary_sdc, sdc_data),
        ("mapped_netlist", mapped_netlist, mapped_netlist_data),
        ("hierarchy_source", hierarchy_source, hierarchy_source_data),
    ):
        if manifest.get(name) != reference:
            errors.append(f"{path}.{name} must equal flow.{name} artifact binding")
        artifact_ref = _mapping(reference, f"$.flow.{name}", errors)
        if data is not None and isinstance(artifact_ref.get("path"), str):
            assets[artifact_ref["path"]] = data

    for group in ("include_files", "generated_ip", "libraries"):
        for index, raw in enumerate(
            _array(manifest.get(group), f"{path}.{group}", errors)
        ):
            item_path = f"{path}.{group}[{index}]"
            reference = _mapping(raw, item_path, errors)
            artifact_path = reference.get("path")
            if artifact_path not in command:
                errors.append(f"{item_path}.path must appear as an exact command token")
            if group == "libraries" and reference == primary_library:
                continue
            data = reader.read(reference, item_path)
            if data is not None and isinstance(artifact_path, str):
                assets[artifact_path] = data
    if primary_library not in _array(manifest.get("libraries"), f"{path}.libraries", errors):
        errors.append(f"{path}.libraries must bind the primary flow.library artifact")
    return assets


def validate_record(
    record: Any, base_dir: Path | None = None
) -> dict[str, float | int | str]:
    """Validate one record, its artifacts, and physical accounting closure."""

    errors = _schema_errors(record)
    root = _mapping(record, "$", errors)
    if root.get("schema_version") != 4:
        errors.append("$.schema_version must equal 4")
    if root.get("status") not in {"freeze_candidate", "frozen"}:
        errors.append("$.status must be freeze_candidate or frozen")

    if base_dir is None:
        errors.append("artifact base_dir is required for actual digest validation")
        artifact_base = Path(".")
    else:
        artifact_base = Path(base_dir)
        _reject_ancestor_symlinks(artifact_base, "artifact base_dir", errors)
        try:
            base_info = os.lstat(artifact_base)
            if stat.S_ISLNK(base_info.st_mode):
                errors.append("artifact base_dir must not be a symlink")
            elif not stat.S_ISDIR(base_info.st_mode):
                errors.append("artifact base_dir must be a directory")
        except OSError as exc:
            errors.append(f"artifact base_dir cannot be inspected: {exc}")
    reader = ArtifactReader(artifact_base, errors)

    candidate = _mapping(root.get("candidate"), "$.candidate", errors)
    commit = candidate.get("commit_sha")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        errors.append("$.candidate.commit_sha must be a 7-64 digit hexadecimal commit")
    synthesis_top = candidate.get("synthesis_top")
    if not isinstance(synthesis_top, str) or not synthesis_top:
        errors.append("$.candidate.synthesis_top must be a nonempty string")
        synthesis_top = ""
    bundle_data = reader.read(
        candidate.get("bundle_inventory"), "$.candidate.bundle_inventory"
    )
    bundle_files, source_data = _load_bundle_inventory(
        reader, candidate.get("bundle_inventory"), errors
    )
    filelist_data = reader.read(candidate.get("filelist"), "$.candidate.filelist")
    filelist_files = _load_ordered_filelist(reader, candidate.get("filelist"), errors)
    if bundle_files != filelist_files:
        errors.append(
            "candidate bundle inventory and ordered filelist must contain the exact "
            "same source paths in the same order"
        )

    logical = _mapping(root.get("logical_contract"), "$.logical_contract", errors)
    if logical.get("event_identity_mode") != "address_only":
        errors.append("$.logical_contract.event_identity_mode must be address_only")
    source_count = _positive_int(
        logical.get("source_count"), "$.logical_contract.source_count", errors
    )
    if logical.get("one_pending_latch_per_source") is not True:
        errors.append("$.logical_contract.one_pending_latch_per_source must be true")
    source_mapping = _mapping(
        logical.get("source_mapping"), "$.logical_contract.source_mapping", errors
    )
    if source_mapping.get("bijective") is not True:
        errors.append("$.logical_contract.source_mapping.bijective must be true")
    reader.read(
        source_mapping.get("artifact"),
        "$.logical_contract.source_mapping.artifact",
    )

    seam = _mapping(root.get("tb_seam"), "$.tb_seam", errors)
    if seam.get("ppa_excluded") is not True:
        errors.append("$.tb_seam.ppa_excluded must be true")
    if seam.get("arbitrary_payload") is not False:
        errors.append("$.tb_seam.arbitrary_payload must be false")
    if seam.get("tb_only_event_id_in_dut") is not False:
        errors.append("$.tb_seam.tb_only_event_id_in_dut must be false")
    addr_width = _positive_int(
        seam.get("normalized_addr_width"), "$.tb_seam.normalized_addr_width", errors
    )
    source_width = _positive_int(
        seam.get("normalized_source_width"),
        "$.tb_seam.normalized_source_width",
        errors,
    )
    if source_width < max(1, (source_count - 1).bit_length()):
        errors.append("$.tb_seam.normalized_source_width cannot represent every source")
    if addr_width < source_width:
        errors.append("$.tb_seam.normalized_addr_width is narrower than source identity")

    boundary = _mapping(root.get("physical_boundary"), "$.physical_boundary", errors)
    if boundary.get("scope") != "full_link_tx_link_rx":
        errors.append("$.physical_boundary.scope must be full_link_tx_link_rx")
    for field in ("includes_tx", "includes_link", "includes_rx"):
        if boundary.get(field) is not True:
            errors.append(f"$.physical_boundary.{field} must be true")
    native_bits = _pin_count(
        boundary.get("native_boundary_pins"),
        "$.physical_boundary.native_boundary_pins",
        errors,
    )
    if boundary.get("native_functional_pin_bits") != native_bits:
        errors.append(
            "$.physical_boundary.native_functional_pin_bits does not match pin list "
            f"({boundary.get('native_functional_pin_bits')!r} != {native_bits})"
        )
    encoding = _mapping(
        boundary.get("link_encoding"), "$.physical_boundary.link_encoding", errors
    )
    link_cut = _mapping(boundary.get("link_cut"), "$.physical_boundary.link_cut", errors)
    if link_cut.get("count_each_signal_once") is not True:
        errors.append("$.physical_boundary.link_cut.count_each_signal_once must be true")
    link_bits = _pin_count(
        link_cut.get("pins"), "$.physical_boundary.link_cut.pins", errors
    )
    if link_cut.get("functional_pin_bits") != link_bits:
        errors.append(
            "$.physical_boundary.link_cut.functional_pin_bits does not match pin list "
            f"({link_cut.get('functional_pin_bits')!r} != {link_bits})"
        )

    normalization = _mapping(root.get("normalization"), "$.normalization", errors)
    if normalization.get("runtime_decode_in_tb") is not False:
        errors.append("$.normalization.runtime_decode_in_tb must be false")
    if normalization.get("uses_pending_to_disambiguate") is not False:
        errors.append("$.normalization.uses_pending_to_disambiguate must be false")
    if normalization.get("zero_feature_tb_binding_excluded") is not True:
        errors.append("$.normalization.zero_feature_tb_binding_excluded must be true")
    for index, item in enumerate(
        _array(normalization.get("free_wiring"), "$.normalization.free_wiring", errors)
    ):
        mapping = _mapping(item, f"$.normalization.free_wiring[{index}]", errors)
        if mapping.get("operation") not in FREE_OPERATIONS:
            errors.append(
                f"$.normalization.free_wiring[{index}].operation is not free wiring"
            )

    block_kinds: set[str] = set()
    block_by_name: dict[str, dict[str, Any]] = {}
    hierarchy_paths: set[str] = set()
    charged_source_union: set[str] = set()
    blocks = _array(root.get("charged_blocks"), "$.charged_blocks", errors)
    for index, item in enumerate(blocks):
        path = f"$.charged_blocks[{index}]"
        block = _mapping(item, path, errors)
        kind = block.get("kind")
        block_kinds.add(str(kind))
        name = block.get("name")
        if isinstance(name, str) and name:
            if name in block_by_name:
                errors.append(f"$.charged_blocks contains duplicate name {name!r}")
            else:
                block_by_name[name] = block
        hierarchy_path = block.get("hierarchy_path")
        if isinstance(hierarchy_path, str) and hierarchy_path:
            if hierarchy_path in hierarchy_paths:
                errors.append(
                    "$.charged_blocks contains duplicate hierarchy_path "
                    f"{hierarchy_path!r}"
                )
            hierarchy_paths.add(hierarchy_path)
            if (
                synthesis_top
                and hierarchy_path != synthesis_top
                and not hierarchy_path.startswith(f"{synthesis_top}.")
            ):
                errors.append(
                    f"{path}.hierarchy_path is outside synthesis_top {synthesis_top!r}"
                )
        reader.read(block.get("hierarchy_evidence"), f"{path}.hierarchy_evidence")
        sources = _array(block.get("source_files"), f"{path}.source_files", errors)
        for source_index, source in enumerate(sources):
            normalized = _inventory_path(
                source, f"{path}.source_files[{source_index}]", errors
            )
            if normalized is not None:
                charged_source_union.add(normalized)
        for field in (
            "included_in_area", "included_in_timing", "included_in_activity",
            "included_in_power",
        ):
            if block.get(field) is not True:
                errors.append(f"{path}.{field} must be true")
    for required_kind in ("tx", "link", "rx"):
        if required_kind not in block_kinds:
            errors.append(f"$.charged_blocks must include a charged {required_kind} block")
    if encoding.get("requires_runtime_decode") is True:
        for required_kind in ("encoder", "decoder"):
            if required_kind not in block_kinds:
                errors.append(
                    "runtime link encoding requires charged encoder and decoder blocks"
                )

    declarations = _mapping(
        root.get("feature_declarations"), "$.feature_declarations", errors
    )
    declared_blocks: dict[str, str] = {}
    all_declaration_names: dict[str, str] = {}
    declaration_inventory: set[tuple[str, str, str, str]] = set()
    for category, compatible_kinds in FEATURE_KIND_COMPATIBILITY.items():
        items = _array(
            declarations.get(category), f"$.feature_declarations.{category}", errors
        )
        for index, item in enumerate(items):
            path = f"$.feature_declarations.{category}[{index}]"
            declaration = _mapping(item, path, errors)
            declaration_name = declaration.get("name")
            if isinstance(declaration_name, str) and declaration_name:
                if declaration_name in all_declaration_names:
                    errors.append(
                        f"feature declaration name {declaration_name!r} is reused in "
                        f"{all_declaration_names[declaration_name]} and {category}"
                    )
                else:
                    all_declaration_names[declaration_name] = category
            block_name = declaration.get("charged_block")
            if not isinstance(block_name, str) or not block_name:
                continue
            if block_name in declared_blocks:
                errors.append(
                    f"charged block {block_name!r} is declared more than once "
                    f"({declared_blocks[block_name]} and {category})"
                )
                continue
            declared_blocks[block_name] = category
            block = block_by_name.get(block_name)
            if block is None:
                errors.append(
                    f"{path}.charged_block references unknown charged block {block_name!r}"
                )
                continue
            if block.get("kind") not in compatible_kinds:
                errors.append(
                    f"{path} category {category} cannot declare charged block "
                    f"kind {block.get('kind')!r}"
                )
            if declaration.get("hierarchy_path") != block.get("hierarchy_path"):
                errors.append(
                    f"{path}.hierarchy_path must match charged block {block_name!r}"
                )
            if isinstance(declaration_name, str) and declaration_name:
                declaration_inventory.add((
                    declaration_name,
                    category,
                    block_name,
                    str(declaration.get("hierarchy_path")),
                ))
    for block_name, block in block_by_name.items():
        if block.get("kind") in FEATURE_BLOCK_KINDS and block_name not in declared_blocks:
            errors.append(
                f"feature charged block {block_name!r} has no 1:1 feature declaration"
            )
    if bool(declarations.get("serializer")) != bool(declarations.get("deserializer")):
        errors.append(
            "serializer and deserializer feature declarations must both be present "
            "or both be empty"
        )

    flow = _mapping(root.get("flow"), "$.flow", errors)
    flow_results = _mapping(flow.get("results"), "$.flow.results", errors)
    tool_data = reader.read(flow.get("tool_config"), "$.flow.tool_config")
    sdc_data = reader.read(flow.get("sdc"), "$.flow.sdc")
    library_data = reader.read(flow.get("library"), "$.flow.library")
    hierarchy_report_data = reader.read(
        flow.get("synthesis_hierarchy_report"), "$.flow.synthesis_hierarchy_report"
    )
    synthesis_evidence_data = reader.read(
        flow.get("synthesis_evidence"), "$.flow.synthesis_evidence"
    )
    mapped_netlist_data = reader.read(
        flow.get("mapped_netlist"), "$.flow.mapped_netlist"
    )
    synthesis_command_data = reader.read(
        flow.get("synthesis_command"), "$.flow.synthesis_command"
    )
    hierarchy_source_data = reader.read(
        flow.get("hierarchy_source"), "$.flow.hierarchy_source"
    )
    del hierarchy_report_data, synthesis_evidence_data

    assets = _load_synthesis_assets(
        reader,
        synthesis_command_data,
        candidate.get("filelist"),
        flow.get("library"),
        flow.get("tool_config"),
        flow.get("sdc"),
        flow.get("mapped_netlist"),
        flow.get("hierarchy_source"),
        filelist_data,
        library_data,
        tool_data,
        sdc_data,
        mapped_netlist_data,
        hierarchy_source_data,
        errors,
    )
    assets.update(source_data)
    if filelist_data is not None:
        assets[str(_mapping(candidate.get("filelist"), "$.candidate.filelist", errors).get("path"))] = filelist_data
    try:
        generator_path = Path(inventory_generator.__file__)
        generator_errors: list[str] = []
        generator_data = _stable_read_regular(
            generator_path, "trusted inventory generator", generator_errors
        )
        errors.extend(generator_errors)
        inventory_ref = _mapping(flow.get("inventory"), "$.flow.inventory", errors)
        input_paths = {
            "bundle_inventory": str(_mapping(candidate.get("bundle_inventory"), "$.candidate.bundle_inventory", errors).get("path")),
            "filelist": str(_mapping(candidate.get("filelist"), "$.candidate.filelist", errors).get("path")),
            "mapped_netlist": str(_mapping(flow.get("mapped_netlist"), "$.flow.mapped_netlist", errors).get("path")),
            "hierarchy_source": str(_mapping(flow.get("hierarchy_source"), "$.flow.hierarchy_source", errors).get("path")),
            "synthesis_command": str(_mapping(flow.get("synthesis_command"), "$.flow.synthesis_command", errors).get("path")),
        }
        expected_inventory = inventory_generator.produce_inventory(
            bundle_data=bundle_data or b"",
            filelist_data=filelist_data or b"",
            mapped_netlist_data=mapped_netlist_data or b"",
            hierarchy_source_data=hierarchy_source_data or b"",
            synthesis_command_data=synthesis_command_data or b"",
            input_paths=input_paths,
            source_loader=lambda path: assets[path],
            output_path=str(inventory_ref.get("path")),
            generator_sha256=hashlib.sha256(generator_data or b"").hexdigest(),
        )
    except (inventory_generator.InventoryError, KeyError) as exc:
        errors.append(f"trusted inventory production failed: {exc}")
        expected_inventory = {}
    actual_inventory = reader.json(flow.get("inventory"), "$.flow.inventory")
    if actual_inventory != expected_inventory:
        errors.append(
            "$.flow.inventory does not byte-semantically match trusted regenerated inventory"
        )

    inventory_top = expected_inventory.get("synthesis_top")
    if inventory_top != synthesis_top:
        errors.append("trusted inventory synthesis_top must equal candidate synthesis_top")
    inventory_blocks = {
        item.get("name"): item
        for item in expected_inventory.get("blocks", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(inventory_blocks) != set(block_by_name):
        errors.append(
            "trusted inventory block set must exactly equal charged_blocks "
            f"(produced={sorted(inventory_blocks)!r}, charged={sorted(block_by_name)!r})"
        )
    for name in sorted(set(inventory_blocks) & set(block_by_name)):
        produced = inventory_blocks[name]
        charged = block_by_name[name]
        for field in ("kind", "top", "hierarchy_path", "source_files"):
            if produced.get(field) != charged.get(field):
                errors.append(
                    f"trusted inventory block {name!r} {field} does not match charged block"
                )
    produced_sources = {
        source
        for block in inventory_blocks.values()
        for source in block.get("source_files", [])
    }
    if produced_sources != charged_source_union:
        errors.append(
            "charged source hierarchy closure differs from trusted inventory "
            f"(produced={sorted(produced_sources)!r}, charged={sorted(charged_source_union)!r})"
        )
    produced_features = {
        (
            item.get("name"), item.get("category"), item.get("charged_block"),
            item.get("hierarchy_path"),
        )
        for item in expected_inventory.get("features", [])
        if isinstance(item, dict)
    }
    if produced_features != declaration_inventory:
        errors.append(
            "trusted generated feature inventory must exactly equal declaration set "
            f"(hidden={sorted(produced_features - declaration_inventory)!r}, "
            f"invented={sorted(declaration_inventory - produced_features)!r})"
        )

    area_inputs = [
        ("mapped_netlist", flow.get("mapped_netlist")),
        ("tool_config", flow.get("tool_config")),
        ("library", flow.get("library")),
    ]
    timing_inputs = [
        ("mapped_netlist", flow.get("mapped_netlist")),
        ("sdc", flow.get("sdc")),
        ("library", flow.get("library")),
    ]
    area_values = _load_canonical_evidence(
        reader, flow.get("area_report"), "$.flow.area_report", "area",
        {"mapped_cell_count", "area_um2"}, area_inputs, errors,
    )
    stage_values = _load_canonical_evidence(
        reader, flow.get("stage_report"), "$.flow.stage_report", "stage",
        {"pipeline_stage_count"}, area_inputs, errors,
    )
    setup_values = _load_canonical_evidence(
        reader, flow.get("setup_report"), "$.flow.setup_report", "setup",
        {"setup_wns_ns"}, timing_inputs, errors,
    )
    hold_values = _load_canonical_evidence(
        reader, flow.get("hold_report"), "$.flow.hold_report", "hold",
        {"hold_wns_ns"}, timing_inputs, errors,
    )
    route_values = _load_canonical_evidence(
        reader, flow.get("route_report"), "$.flow.route_report", "route",
        {"detailed_route_completed"}, timing_inputs, errors,
    )
    unresolved_values = _load_canonical_evidence(
        reader, flow.get("post_elaboration_report"),
        "$.flow.post_elaboration_report", "elaboration",
        {"unresolved_references"}, [("synthesis_command", flow.get("synthesis_command"))], errors,
    )
    unconstrained_values = _load_canonical_evidence(
        reader, flow.get("unconstrained_report"), "$.flow.unconstrained_report",
        "unconstrained", {"unconstrained_paths"}, timing_inputs, errors,
    )
    drc_values = _load_canonical_evidence(
        reader, flow.get("drc_report"), "$.flow.drc_report", "drc",
        {"drc_violations"}, timing_inputs, errors,
    )
    derived_flow = {
        **area_values, **stage_values, **setup_values, **hold_values, **route_values,
        **unresolved_values, **unconstrained_values, **drc_values,
    }
    for field, derived in derived_flow.items():
        if not _json_equal(flow_results.get(field), derived):
            errors.append(
                f"$.flow.results.{field} does not match parsed canonical evidence "
                f"({flow_results.get(field)!r} != {derived!r})"
            )
    for field in ("unresolved_references", "unconstrained_paths", "drc_violations"):
        if flow_results.get(field) != 0:
            errors.append(f"$.flow.results.{field} must equal 0")
    if flow_results.get("detailed_route_completed") is not True:
        errors.append("$.flow.results.detailed_route_completed must be true")
    for field in ("setup_wns_ns", "hold_wns_ns"):
        _finite_nonnegative(flow_results.get(field), f"$.flow.results.{field}", errors)

    activity = _mapping(root.get("activity"), "$.activity", errors)
    reader.read(activity.get("trace"), "$.activity.trace")
    reader.read(activity.get("prepared_input"), "$.activity.prepared_input")
    activity_values = _load_canonical_evidence(
        reader, activity.get("activity_artifact"), "$.activity.activity_artifact",
        "activity",
        {
            "candidate_id", "test_id", "seed", "hierarchy_root", "format",
            "coverage_percent", "window_start_cycle", "window_end_cycle_exclusive",
            "measurement_cycles",
        },
        [
            ("trace", activity.get("trace")),
            ("prepared_input", activity.get("prepared_input")),
            ("bundle_inventory", candidate.get("bundle_inventory")),
        ],
        errors,
    )
    power_values = _load_canonical_evidence(
        reader, activity.get("power_report"), "$.activity.power_report", "power",
        {
            "candidate_id", "test_id", "seed", "measurement_cycles",
            "average_power_mw", "errors",
        },
        [
            ("activity", activity.get("activity_artifact")),
            ("mapped_netlist", flow.get("mapped_netlist")),
            ("library", flow.get("library")),
        ], errors,
    )
    common_values = _load_canonical_evidence(
        reader, activity.get("common_result"), "$.activity.common_result", "common_result",
        {
            "candidate_id", "test_id", "seed", "measurement_cycles",
            "delivered_events", "errors",
        },
        [
            ("trace", activity.get("trace")),
            ("prepared_input", activity.get("prepared_input")),
            ("bundle_inventory", candidate.get("bundle_inventory")),
        ],
        errors,
    )
    candidate_id = candidate.get("id")
    for field in (
        "test_id", "seed", "hierarchy_root", "format", "coverage_percent",
        "window_start_cycle", "window_end_cycle_exclusive", "measurement_cycles",
    ):
        if not _json_equal(activity.get(field), activity_values.get(field)):
            errors.append(
                f"$.activity.{field} does not match parsed activity evidence"
            )
    for evidence_name, values, fields in (
        ("power", power_values, ("test_id", "seed", "measurement_cycles", "average_power_mw", "errors")),
        ("common_result", common_values, ("test_id", "seed", "measurement_cycles", "delivered_events", "errors")),
    ):
        if values.get("candidate_id") != candidate_id:
            errors.append(f"parsed {evidence_name} candidate_id does not match candidate")
        for field in fields:
            if not _json_equal(activity.get(field), values.get(field)):
                errors.append(
                    f"$.activity.{field} does not match parsed {evidence_name} evidence"
                )
    if activity_values.get("candidate_id") != candidate_id:
        errors.append("parsed activity candidate_id does not match candidate")
    if activity.get("hierarchy_root") != synthesis_top:
        errors.append("$.activity.hierarchy_root must equal candidate synthesis_top")
    coverage = _finite_nonnegative(
        activity.get("coverage_percent"), "$.activity.coverage_percent", errors
    )
    threshold = _finite_nonnegative(
        activity.get("coverage_threshold_percent"),
        "$.activity.coverage_threshold_percent",
        errors,
    )
    if threshold <= 0.0:
        errors.append("$.activity.coverage_threshold_percent must be positive")
    if coverage < threshold:
        errors.append("$.activity.coverage_percent is below the frozen threshold")
    start = activity.get("window_start_cycle")
    end = activity.get("window_end_cycle_exclusive")
    cycles = _positive_int(
        activity.get("measurement_cycles"), "$.activity.measurement_cycles", errors
    )
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        errors.append("$.activity.window_start_cycle must be a nonnegative integer")
    if not isinstance(end, int) or isinstance(end, bool) or end <= 0:
        errors.append("$.activity.window_end_cycle_exclusive must be positive")
    if isinstance(start, int) and isinstance(end, int) and end - start != cycles:
        errors.append("$.activity.measurement_cycles must equal end_cycle - start_cycle")
    clock_mhz = _finite_nonnegative(
        activity.get("clock_mhz"), "$.activity.clock_mhz", errors
    )
    if clock_mhz <= 0.0:
        errors.append("$.activity.clock_mhz must be positive")
    delivered = _positive_int(
        activity.get("delivered_events"), "$.activity.delivered_events", errors
    )
    power_mw = _finite_nonnegative(
        activity.get("average_power_mw"), "$.activity.average_power_mw", errors
    )
    if root.get("status") == "frozen":
        if activity.get("power_evidence") != "activity_annotated":
            errors.append("a frozen ranked record requires activity_annotated power evidence")
        if coverage <= 0.0 or threshold <= 0.0:
            errors.append("a frozen record requires positive annotation coverage and threshold")

    computed_events_per_cycle = delivered / cycles if cycles else 0.0
    computed_native = delivered / (cycles * native_bits) if cycles and native_bits else 0.0
    computed_link = delivered / (cycles * link_bits) if cycles and link_bits else 0.0
    computed_energy = (
        power_mw / (clock_mhz * computed_events_per_cycle)
        if clock_mhz and computed_events_per_cycle
        else 0.0
    )
    metrics = _mapping(root.get("metrics"), "$.metrics", errors)
    for field, expected in (
        ("events_per_cycle", computed_events_per_cycle),
        ("events_per_native_pin_cycle", computed_native),
        ("events_per_link_pin_cycle", computed_link),
        ("energy_nj_per_delivered_event", computed_energy),
    ):
        _same_number(metrics.get(field), expected, f"$.metrics.{field}", errors)

    if errors:
        raise QualificationError("\n".join(errors))
    return {
        "qualification_id": str(root.get("qualification_id")),
        "native_functional_pin_bits": native_bits,
        "link_functional_pin_bits": link_bits,
        "events_per_cycle": computed_events_per_cycle,
        "events_per_native_pin_cycle": computed_native,
        "events_per_link_pin_cycle": computed_link,
        "energy_nj_per_delivered_event": computed_energy,
    }


def read_record(path: Path) -> Any:
    errors: list[str] = []
    data = _stable_read_regular(path, str(path), errors)
    if errors or data is None:
        raise QualificationError("\n".join(errors))
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read {path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit computed JSON")
    args = parser.parse_args(argv)

    output: list[dict[str, float | int | str]] = []
    failed = False
    for path in args.records:
        try:
            output.append(validate_record(read_record(path), path.parent))
        except QualificationError as exc:
            failed = True
            print(f"{path}: NOT_QUALIFIED\n{exc}", file=sys.stderr)
    if failed:
        return 2
    if args.json:
        json.dump(output, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        for row in output:
            print(f"{row['qualification_id']}: QUALIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
