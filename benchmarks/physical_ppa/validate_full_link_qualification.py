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


def _stable_read_regular(path: Path, label: str, errors: list[str]) -> bytes | None:
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
    return b"".join(chunks)


class ArtifactReader:
    """Resolve and verify record-relative artifact references exactly once."""

    def __init__(self, base_dir: Path, errors: list[str]):
        self.base_dir = base_dir
        self.errors = errors
        self.cache: dict[tuple[str, str], bytes] = {}

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

        key = (raw_path, str(expected).lower())
        if key in self.cache:
            return self.cache[key]
        data = _stable_read_regular(current, f"{path}.path", self.errors)
        if data is None:
            return None
        actual = hashlib.sha256(data).hexdigest()
        if not isinstance(expected, str) or actual != expected.lower():
            self.errors.append(
                f"{path}.sha256 digest mismatch ({expected!r} != {actual})"
            )
            return None
        self.cache[key] = data
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
) -> list[str]:
    path = "$.candidate.bundle_inventory"
    inventory = reader.json(value, path)
    _strict_keys(inventory, {"schema_version", "files"}, f"{path}<content>", errors)
    if inventory.get("schema_version") != 1:
        errors.append(f"{path}<content>.schema_version must equal 1")
    files = _array(inventory.get("files"), f"{path}<content>.files", errors)
    ordered: list[str] = []
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
        reader.read(entry, item_path)
    if not ordered:
        errors.append(f"{path}<content>.files must not be empty")
    return ordered


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


def _load_mapped_hierarchy(
    reader: ArtifactReader, value: Any, errors: list[str]
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    path = "$.flow.mapped_hierarchy_inventory"
    inventory = reader.json(value, path)
    _strict_keys(
        inventory,
        {"schema_version", "synthesis_top", "blocks"},
        f"{path}<content>",
        errors,
    )
    if inventory.get("schema_version") != 1:
        errors.append(f"{path}<content>.schema_version must equal 1")
    synthesis_top = inventory.get("synthesis_top")
    if not isinstance(synthesis_top, str) or not synthesis_top:
        errors.append(f"{path}<content>.synthesis_top must be a nonempty string")
        synthesis_top = None
    blocks: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(
        _array(inventory.get("blocks"), f"{path}<content>.blocks", errors)
    ):
        item_path = f"{path}<content>.blocks[{index}]"
        entry = _mapping(item, item_path, errors)
        _strict_keys(
            entry,
            {"name", "kind", "top", "hierarchy_path", "source_files"},
            item_path,
            errors,
        )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{item_path}.name must be a nonempty string")
            continue
        if name in blocks:
            errors.append(f"{path}<content>.blocks contains duplicate name {name!r}")
        sources = _array(entry.get("source_files"), f"{item_path}.source_files", errors)
        normalized_sources: list[str] = []
        for source_index, source in enumerate(sources):
            normalized = _inventory_path(
                source, f"{item_path}.source_files[{source_index}]", errors
            )
            if normalized is not None:
                normalized_sources.append(normalized)
        entry = dict(entry)
        entry["source_files"] = normalized_sources
        blocks[name] = entry
    return synthesis_top, blocks


def _load_generated_features(
    reader: ArtifactReader, value: Any, errors: list[str]
) -> tuple[str | None, set[tuple[str, str, str, str]]]:
    path = "$.flow.generated_feature_inventory"
    inventory = reader.json(value, path)
    _strict_keys(
        inventory,
        {"schema_version", "synthesis_top", "features"},
        f"{path}<content>",
        errors,
    )
    if inventory.get("schema_version") != 1:
        errors.append(f"{path}<content>.schema_version must equal 1")
    synthesis_top = inventory.get("synthesis_top")
    if not isinstance(synthesis_top, str) or not synthesis_top:
        errors.append(f"{path}<content>.synthesis_top must be a nonempty string")
        synthesis_top = None
    result: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(
        _array(inventory.get("features"), f"{path}<content>.features", errors)
    ):
        item_path = f"{path}<content>.features[{index}]"
        entry = _mapping(item, item_path, errors)
        _strict_keys(
            entry,
            {"name", "category", "charged_block", "hierarchy_path"},
            item_path,
            errors,
        )
        values = tuple(entry.get(field) for field in (
            "name", "category", "charged_block", "hierarchy_path"
        ))
        if not all(isinstance(item_value, str) and item_value for item_value in values):
            errors.append(f"{item_path} fields must be nonempty strings")
            continue
        typed_values = (values[0], values[1], values[2], values[3])
        if typed_values in result:
            errors.append(f"{path}<content>.features contains duplicate {typed_values!r}")
        result.add(typed_values)
    return synthesis_top, result


def validate_record(
    record: Any, base_dir: Path | None = None
) -> dict[str, float | int | str]:
    """Validate one record, its artifacts, and physical accounting closure."""

    errors = _schema_errors(record)
    root = _mapping(record, "$", errors)
    if root.get("schema_version") != 3:
        errors.append("$.schema_version must equal 3")
    if root.get("status") not in {"freeze_candidate", "frozen"}:
        errors.append("$.status must be freeze_candidate or frozen")

    if base_dir is None:
        errors.append("artifact base_dir is required for actual digest validation")
        artifact_base = Path(".")
    else:
        artifact_base = Path(base_dir)
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
    bundle_files = _load_bundle_inventory(
        reader, candidate.get("bundle_inventory"), errors
    )
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
                if normalized not in filelist_files:
                    errors.append(
                        f"{path}.source_files contains {normalized!r} outside candidate filelist"
                    )
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
    if set(filelist_files) != charged_source_union:
        missing = sorted(set(filelist_files) - charged_source_union)
        extra = sorted(charged_source_union - set(filelist_files))
        errors.append(
            "charged source closure mismatch "
            f"(uncharged={missing!r}, outside_filelist={extra!r})"
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
            reader.read(declaration.get("evidence"), f"{path}.evidence")
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
            if declaration.get("evidence") != block.get("hierarchy_evidence"):
                errors.append(
                    f"{path}.evidence must match charged block {block_name!r} "
                    "hierarchy_evidence"
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
    evidence_fields = (
        "tool_config", "sdc", "library", "post_elaboration_report",
        "synthesis_hierarchy_report", "synthesis_evidence", "mapped_netlist",
        "area_report", "stage_report", "setup_report", "hold_report",
        "route_report", "unconstrained_report", "drc_report",
    )
    for field in evidence_fields:
        reader.read(flow.get(field), f"$.flow.{field}")
    mapped_top, mapped_blocks = _load_mapped_hierarchy(
        reader, flow.get("mapped_hierarchy_inventory"), errors
    )
    if mapped_top != synthesis_top:
        errors.append("mapped hierarchy synthesis_top must equal candidate synthesis_top")
    if set(mapped_blocks) != set(block_by_name):
        errors.append(
            "mapped hierarchy block set must exactly equal charged_blocks "
            f"(mapped={sorted(mapped_blocks)!r}, charged={sorted(block_by_name)!r})"
        )
    for name in sorted(set(mapped_blocks) & set(block_by_name)):
        mapped = mapped_blocks[name]
        charged = block_by_name[name]
        for field in ("kind", "top", "hierarchy_path"):
            if mapped.get(field) != charged.get(field):
                errors.append(
                    f"mapped hierarchy block {name!r} {field} does not match charged block"
                )
        if mapped.get("source_files") != charged.get("source_files"):
            errors.append(
                f"mapped hierarchy block {name!r} source_files do not match charged block"
            )
    generated_top, generated_features = _load_generated_features(
        reader, flow.get("generated_feature_inventory"), errors
    )
    if generated_top != synthesis_top:
        errors.append("generated feature synthesis_top must equal candidate synthesis_top")
    if generated_features != declaration_inventory:
        hidden = sorted(generated_features - declaration_inventory)
        invented = sorted(declaration_inventory - generated_features)
        errors.append(
            "generated feature inventory must exactly equal declaration set "
            f"(hidden={hidden!r}, undevidenced_declarations={invented!r})"
        )

    flow_results = _mapping(flow.get("results"), "$.flow.results", errors)
    for field in ("unresolved_references", "unconstrained_paths", "drc_violations"):
        if flow_results.get(field) != 0:
            errors.append(f"$.flow.results.{field} must equal 0")
    if flow_results.get("detailed_route_completed") is not True:
        errors.append("$.flow.results.detailed_route_completed must be true")
    for field in ("setup_wns_ns", "hold_wns_ns"):
        _finite_nonnegative(flow_results.get(field), f"$.flow.results.{field}", errors)

    activity = _mapping(root.get("activity"), "$.activity", errors)
    for field in (
        "trace", "prepared_input", "activity_artifact", "power_report",
        "common_result",
    ):
        reader.read(activity.get(field), f"$.activity.{field}")
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
