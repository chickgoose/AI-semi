"""Canonical Stage-3 candidate identity, configuration, and source authority.

This module is the dependency-closed hand-off boundary for output adapters and
campaign tooling.  It does not execute a candidate.  It binds each candidate's
adapter-exported native model identity and exact configuration bytes, the
physical executable artifact whose digest is published in candidate output,
every adapter-declared executable source, and the mandatory shared framework,
pose geometry, cycle-model, and canonical-sealer sources.

The public builder is suitable for an adapter before it emits output.  The
public verifier is suitable for a campaign after reopening a stored manifest.
Both reject missing or mutated sources, duplicate entries, non-canonical path
spellings, symlinks, hard-link aliases, and dependency-order changes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import List, Mapping, Optional, Sequence, Set, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


AUTHORITY_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_authority/v2"
CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign_authority/v1"
CANDIDATE_NAMES = ("RG3", "DSPB", "PLL")

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_MANIFEST_FIELDS = frozenset((
    "schema", "candidate", "native_candidate_id", "config_encoding",
    "config_bytes_hex", "config_sha256", "executable_encoding",
    "executable_bytes_hex", "executable_sha256", "dependencies",
    "dependency_aggregate_sha256", "manifest_sha256",
))
_DEPENDENCY_FIELDS = frozenset(("role", "path", "sha256"))
_CAMPAIGN_FIELDS = frozenset((
    "schema", "candidate_order", "candidates", "aggregate_sha256",
))

_OUTPUT_ADAPTER_PATHS = {
    "RG3": "benchmarks/redred_mc_wtb_predictor_stage3/rg3_output.py",
    "DSPB": "benchmarks/redred_mc_wtb_predictor_stage3/dspb_output.py",
    "PLL": "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py",
}
_MODEL_PATHS = {
    "RG3": "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
    "DSPB": "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
    "PLL": "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
}
_COMMON_AUTHORITY_SEEDS = (
    (
        "candidate_framework",
        "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    ),
    (
        "pose_recovery_api",
        "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    ),
    (
        "pose_recovery_geometry",
        "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    ),
    (
        "cycle_model_api",
        "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    ),
    (
        "cycle_model",
        "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
    ),
    (
        "canonical_sealer_api",
        "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    ),
    (
        "canonical_json_sealer",
        "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    ),
    (
        "canonical_receipt_sealer",
        "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
    ),
)


class CandidateAuthorityError(ValueError):
    """A candidate identity, configuration, dependency, or seal failed."""


@dataclass(frozen=True)
class DependencySeal:
    """One canonical repository-relative source identity."""

    role: str
    path: str
    sha256: str

    def to_mapping(self) -> Mapping[str, object]:
        return {"role": self.role, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class CandidateAuthority:
    """Immutable native/config/artifact/dependency authority for a candidate."""

    candidate: str
    native_candidate_id: str
    config_bytes: bytes
    config_sha256: str
    executable_artifact_bytes: bytes
    executable_sha256: str
    dependencies: Tuple[DependencySeal, ...]
    dependency_aggregate_sha256: str
    manifest_sha256: str

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "schema": AUTHORITY_SCHEMA,
            "candidate": self.candidate,
            "native_candidate_id": self.native_candidate_id,
            "config_encoding": "adapter-export-bytes-hex/v1",
            "config_bytes_hex": self.config_bytes.hex(),
            "config_sha256": self.config_sha256,
            "executable_encoding": "canonical-json-ascii-hex/v1",
            "executable_bytes_hex": self.executable_artifact_bytes.hex(),
            "executable_sha256": self.executable_sha256,
            "dependencies": [row.to_mapping() for row in self.dependencies],
            "dependency_aggregate_sha256": self.dependency_aggregate_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_name(value: object) -> str:
    if type(value) is not str or value not in CANDIDATE_NAMES:
        raise CandidateAuthorityError("candidate must be one of RG3, DSPB, PLL")
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CandidateAuthorityError("%s must be lowercase SHA-256" % where)
    return value


def _exact_mapping(
    value: object, fields: frozenset, where: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise CandidateAuthorityError("%s field schema differs" % where)
    return value


@dataclass(frozen=True)
class _AdapterExports:
    native_candidate_id: str
    config_bytes: bytes
    config_sha256: str
    executable_artifact_bytes: bytes
    executable_sha256: str
    native_dependency_paths: Tuple[str, ...]
    native_dependency_digests: Tuple[Tuple[str, str], ...]


def _validated_adapter_exports(candidate: str) -> _AdapterExports:
    """Read the exact public authority exported by one hardened adapter."""

    name = _candidate_name(candidate)
    if name == "RG3":
        from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_output

        native_id = rg3_output.RG3_OUTPUT_CANDIDATE_ID
        config_bytes = bytes(rg3_output.RG3_CONFIG_BYTES)
        exported_config_sha = rg3_output.RG3_CONFIG_SHA256
        executable_bytes = bytes(rg3_output.RG3_EXECUTABLE_MANIFEST_BYTES)
        exported_executable_sha = rg3_output.RG3_EXECUTABLE_SHA256
        manifest = rg3_output.RG3_EXECUTABLE_MANIFEST
    elif name == "DSPB":
        from benchmarks.redred_mc_wtb_predictor_stage3 import dspb_output

        sealed_manifest = dict(dspb_output.locked_dspb_executable_manifest())
        exported_executable_sha = sealed_manifest.pop("manifest_sha256", None)
        executable_bytes = canonical_json_bytes(sealed_manifest)
        if exported_executable_sha != dspb_output.locked_dspb_executable_sha256():
            raise CandidateAuthorityError("DSPB executable export digest differs")
        native_id = sealed_manifest.get("candidate_id")
        config_bytes = dspb_output.locked_dspb_config_bytes()
        exported_config_sha = dspb_output.locked_dspb_config_sha256()
        manifest = sealed_manifest
    else:
        from benchmarks.redred_mc_wtb_predictor_stage3 import pll_output

        native_id = pll_output.CANDIDATE_ID
        config_bytes = pll_output.locked_config_bytes()
        exported_config_sha = pll_output.locked_config_sha256()
        manifest = pll_output.executable_dependency_manifest()
        executable_bytes = canonical_json_bytes(manifest)
        exported_executable_sha = pll_output.generator_executable_sha256()

    if type(native_id) is not str or not native_id:
        raise CandidateAuthorityError("adapter native candidate identity is invalid")
    if type(config_bytes) is not bytes or not config_bytes:
        raise CandidateAuthorityError("adapter config export is invalid")
    config_sha = _sha256(exported_config_sha, "adapter config digest")
    if hashlib.sha256(config_bytes).hexdigest() != config_sha:
        raise CandidateAuthorityError("adapter config export digest differs")
    executable_sha = _sha256(
        exported_executable_sha, "adapter executable digest"
    )
    if hashlib.sha256(executable_bytes).hexdigest() != executable_sha:
        raise CandidateAuthorityError("adapter executable artifact digest differs")
    try:
        files = manifest["files"]
        dependency_rows = tuple(
            (row["path"], _sha256(row["sha256"], "adapter dependency digest"))
            for row in files
        )
    except (KeyError, TypeError) as exc:
        raise CandidateAuthorityError("adapter executable manifest differs") from exc
    dependency_paths = tuple(path for path, _ in dependency_rows)
    if not dependency_paths or len(set(dependency_paths)) != len(dependency_paths):
        raise CandidateAuthorityError("adapter executable dependencies differ")
    for index, path in enumerate(dependency_paths):
        _canonical_relative_path(path, "adapter dependency %d" % index)
    return _AdapterExports(
        native_id,
        config_bytes,
        config_sha,
        executable_bytes,
        executable_sha,
        dependency_paths,
        dependency_rows,
    )


def candidate_native_id(candidate: str) -> str:
    """Return the adapter-exported, model-native candidate identity."""

    return _validated_adapter_exports(candidate).native_candidate_id


def candidate_config_mapping(candidate: str) -> Mapping[str, object]:
    """Decode the adapter's native config without imposing a common schema."""

    try:
        decoded = json.loads(candidate_config_bytes(candidate).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateAuthorityError("adapter config is not ASCII JSON") from exc
    if not isinstance(decoded, Mapping):
        raise CandidateAuthorityError("adapter config is not a JSON object")
    return decoded


def candidate_config_bytes(candidate: str) -> bytes:
    """Return the exact byte stream exported and hashed by the adapter."""

    return _validated_adapter_exports(candidate).config_bytes


def candidate_config_sha256(candidate: str) -> str:
    return _validated_adapter_exports(candidate).config_sha256


def candidate_executable_artifact_bytes(candidate: str) -> bytes:
    """Return bytes of the artifact whose raw hash candidate output publishes."""

    return _validated_adapter_exports(candidate).executable_artifact_bytes


def candidate_executable_sha256(candidate: str) -> str:
    return _validated_adapter_exports(candidate).executable_sha256


def _canonical_relative_path(value: object, where: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise CandidateAuthorityError("%s is a path alias" % where)
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in ("", ".", "..") for part in pure.parts)
        or pure.suffix != ".py"
    ):
        raise CandidateAuthorityError("%s is a path alias" % where)
    return value


def _root_path(value: Optional[Path]) -> Path:
    supplied = _repo_root() if value is None else Path(value)
    try:
        root = supplied.resolve(strict=True)
    except OSError as exc:
        raise CandidateAuthorityError("repository root is missing") from exc
    if not root.is_dir():
        raise CandidateAuthorityError("repository root is not a directory")
    return root


def _source_path(root: Path, relative: str) -> Path:
    canonical = _canonical_relative_path(relative, "dependency path")
    lexical = root.joinpath(*PurePosixPath(canonical).parts)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise CandidateAuthorityError(
            "dependency source is missing: %s" % canonical
        ) from exc
    if resolved != lexical or lexical.is_symlink() or not lexical.is_file():
        raise CandidateAuthorityError("dependency source uses a path alias: %s" % canonical)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CandidateAuthorityError("dependency source escapes repository root") from exc
    return lexical


def _authority_seeds(candidate: str) -> Tuple[Tuple[str, str], ...]:
    name = _candidate_name(candidate)
    primary = (
        ("output_adapter", _OUTPUT_ADAPTER_PATHS[name]),
        ("model", _MODEL_PATHS[name]),
    ) + _COMMON_AUTHORITY_SEEDS
    observed = {path for _, path in primary}
    native = []
    for path in _validated_adapter_exports(name).native_dependency_paths:
        if path not in observed:
            native.append(("native_executable_source", path))
            observed.add(path)
    return primary + tuple(native)


def candidate_dependency_specs(
    candidate: str, repo_root: Optional[Path] = None
) -> Tuple[Tuple[str, str], ...]:
    """Return the adapter-native closure plus mandatory shared sources."""

    name = _candidate_name(candidate)
    root = _root_path(repo_root)
    seeds = _authority_seeds(name)
    seed_paths = tuple(path for _, path in seeds)
    if len(set(seed_paths)) != len(seed_paths):
        raise CandidateAuthorityError("authority seed paths are duplicated")
    for relative in seed_paths:
        _source_path(root, relative)
    return seeds


def candidate_dependency_paths(
    candidate: str, repo_root: Optional[Path] = None
) -> Tuple[str, ...]:
    return tuple(path for _, path in candidate_dependency_specs(candidate, repo_root))


def _sealed_dependencies(
    specs: Sequence[Tuple[str, str]], root: Path
) -> Tuple[DependencySeal, ...]:
    paths = []  # type: List[str]
    identities = set()  # type: Set[Tuple[int, int]]
    result = []  # type: List[DependencySeal]
    for role, supplied_path in specs:
        if type(role) is not str or not role:
            raise CandidateAuthorityError("dependency role is invalid")
        relative = _canonical_relative_path(supplied_path, "dependency path")
        if relative in paths:
            raise CandidateAuthorityError("dependency paths are duplicated")
        path = _source_path(root, relative)
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in identities:
            raise CandidateAuthorityError("dependency sources use a path alias")
        paths.append(relative)
        identities.add(identity)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CandidateAuthorityError(
                "cannot read dependency source: %s" % relative
            ) from exc
        result.append(DependencySeal(
            role, relative, hashlib.sha256(payload).hexdigest()
        ))
    return tuple(result)


def _verify_native_dependency_digests(
    exports: _AdapterExports, dependencies: Sequence[DependencySeal]
) -> None:
    sealed = {row.path: row.sha256 for row in dependencies}
    for path, expected_sha in exports.native_dependency_digests:
        if sealed.get(path) != expected_sha:
            raise CandidateAuthorityError(
                "adapter executable dependency digest differs: %s" % path
            )


def _manifest_body(
    candidate: str,
    native_candidate_id: str,
    config_bytes: bytes,
    config_sha256: str,
    executable_artifact_bytes: bytes,
    executable_sha256: str,
    dependencies: Sequence[DependencySeal],
    dependency_aggregate_sha256: str,
) -> Mapping[str, object]:
    return {
        "schema": AUTHORITY_SCHEMA,
        "candidate": candidate,
        "native_candidate_id": native_candidate_id,
        "config_encoding": "adapter-export-bytes-hex/v1",
        "config_bytes_hex": config_bytes.hex(),
        "config_sha256": config_sha256,
        "executable_encoding": "canonical-json-ascii-hex/v1",
        "executable_bytes_hex": executable_artifact_bytes.hex(),
        "executable_sha256": executable_sha256,
        "dependencies": [row.to_mapping() for row in dependencies],
        "dependency_aggregate_sha256": dependency_aggregate_sha256,
    }


def build_candidate_authority(
    candidate: str, repo_root: Optional[Path] = None
) -> CandidateAuthority:
    """Build one source-bound authority manifest without executing a model."""

    name = _candidate_name(candidate)
    root = _root_path(repo_root)
    exports = _validated_adapter_exports(name)
    dependencies = _sealed_dependencies(
        candidate_dependency_specs(name, root), root
    )
    _verify_native_dependency_digests(exports, dependencies)
    dependency_aggregate = canonical_sha256([
        row.to_mapping() for row in dependencies
    ])
    body = _manifest_body(
        name,
        exports.native_candidate_id,
        exports.config_bytes,
        exports.config_sha256,
        exports.executable_artifact_bytes,
        exports.executable_sha256,
        dependencies,
        dependency_aggregate,
    )
    return CandidateAuthority(
        name,
        exports.native_candidate_id,
        exports.config_bytes,
        exports.config_sha256,
        exports.executable_artifact_bytes,
        exports.executable_sha256,
        dependencies,
        dependency_aggregate,
        canonical_sha256(body),
    )


def _decode_hex(value: object, where: str) -> bytes:
    if type(value) is not str or len(value) % 2 != 0:
        raise CandidateAuthorityError("%s bytes are not canonical hex" % where)
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise CandidateAuthorityError(
            "%s bytes are not canonical hex" % where
        ) from exc
    if decoded.hex() != value:
        raise CandidateAuthorityError("%s bytes are not canonical hex" % where)
    return decoded


def verify_candidate_authority(
    value: object, repo_root: Optional[Path] = None
) -> str:
    """Verify identity, exact config, complete dependency order, and all seals."""

    mapping = value.to_mapping() if type(value) is CandidateAuthority else value
    manifest = _exact_mapping(mapping, _MANIFEST_FIELDS, "candidate authority")
    if manifest["schema"] != AUTHORITY_SCHEMA:
        raise CandidateAuthorityError("candidate authority schema differs")
    candidate = _candidate_name(manifest["candidate"])
    exports = _validated_adapter_exports(candidate)
    if manifest["native_candidate_id"] != exports.native_candidate_id:
        raise CandidateAuthorityError("native candidate identity differs")
    if manifest["config_encoding"] != "adapter-export-bytes-hex/v1":
        raise CandidateAuthorityError("config encoding differs")
    config_bytes = _decode_hex(manifest["config_bytes_hex"], "config")
    if config_bytes != exports.config_bytes:
        raise CandidateAuthorityError("exact candidate config bytes differ")
    config_sha = _sha256(manifest["config_sha256"], "config digest")
    if config_sha != hashlib.sha256(config_bytes).hexdigest():
        raise CandidateAuthorityError("candidate config digest differs")
    if config_sha != exports.config_sha256:
        raise CandidateAuthorityError("candidate config export digest differs")
    if manifest["executable_encoding"] != "canonical-json-ascii-hex/v1":
        raise CandidateAuthorityError("executable artifact encoding differs")
    executable_bytes = _decode_hex(
        manifest["executable_bytes_hex"], "executable artifact"
    )
    if executable_bytes != exports.executable_artifact_bytes:
        raise CandidateAuthorityError("exact executable artifact bytes differ")
    executable_sha = _sha256(
        manifest["executable_sha256"], "executable artifact digest"
    )
    if executable_sha != hashlib.sha256(executable_bytes).hexdigest():
        raise CandidateAuthorityError("executable artifact digest differs")
    if executable_sha != exports.executable_sha256:
        raise CandidateAuthorityError("adapter executable export digest differs")

    supplied_rows = manifest["dependencies"]
    if not isinstance(supplied_rows, list) or not supplied_rows:
        raise CandidateAuthorityError("dependency rows must be a nonempty list")
    parsed = []  # type: List[Tuple[str, str, str]]
    observed_paths = set()  # type: Set[str]
    for index, supplied in enumerate(supplied_rows):
        row = _exact_mapping(
            supplied, _DEPENDENCY_FIELDS, "dependency row %d" % index
        )
        role = row["role"]
        if type(role) is not str or not role:
            raise CandidateAuthorityError("dependency role is invalid")
        path = _canonical_relative_path(row["path"], "dependency path")
        if path in observed_paths:
            raise CandidateAuthorityError("dependency paths are duplicated")
        observed_paths.add(path)
        parsed.append((role, path, _sha256(row["sha256"], "dependency digest")))

    root = _root_path(repo_root)
    expected_specs = candidate_dependency_specs(candidate, root)
    observed_specs = tuple((role, path) for role, path, _ in parsed)
    if observed_specs != expected_specs:
        raise CandidateAuthorityError("dependency order or closure differs")
    current = _sealed_dependencies(expected_specs, root)
    _verify_native_dependency_digests(exports, current)
    for supplied, observed in zip(parsed, current):
        if supplied[2] != observed.sha256:
            raise CandidateAuthorityError(
                "dependency source digest differs: %s" % observed.path
            )
    current_aggregate = canonical_sha256([row.to_mapping() for row in current])
    supplied_aggregate = _sha256(
        manifest["dependency_aggregate_sha256"], "dependency aggregate"
    )
    if supplied_aggregate != current_aggregate:
        raise CandidateAuthorityError("dependency aggregate differs")

    supplied_manifest = _sha256(manifest["manifest_sha256"], "manifest digest")
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    if supplied_manifest != canonical_sha256(unsigned):
        raise CandidateAuthorityError("candidate authority manifest seal differs")
    return supplied_manifest


def build_campaign_authority(
    repo_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Build the ordered three-candidate campaign authority envelope."""

    candidates = [
        build_candidate_authority(name, repo_root).to_mapping()
        for name in CANDIDATE_NAMES
    ]
    body = {
        "schema": CAMPAIGN_SCHEMA,
        "candidate_order": list(CANDIDATE_NAMES),
        "candidates": candidates,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def verify_campaign_authority(
    value: object, repo_root: Optional[Path] = None
) -> str:
    """Verify candidate order, every candidate manifest, and campaign seal."""

    campaign = _exact_mapping(value, _CAMPAIGN_FIELDS, "campaign authority")
    if campaign["schema"] != CAMPAIGN_SCHEMA:
        raise CandidateAuthorityError("campaign authority schema differs")
    if campaign["candidate_order"] != list(CANDIDATE_NAMES):
        raise CandidateAuthorityError("campaign candidate order differs")
    candidates = campaign["candidates"]
    if not isinstance(candidates, list) or len(candidates) != len(CANDIDATE_NAMES):
        raise CandidateAuthorityError("campaign candidate manifests differ")
    observed_names = []
    for manifest in candidates:
        verify_candidate_authority(manifest, repo_root)
        if not isinstance(manifest, Mapping):
            raise CandidateAuthorityError("campaign candidate manifest differs")
        observed_names.append(manifest.get("candidate"))
    if observed_names != list(CANDIDATE_NAMES):
        raise CandidateAuthorityError("campaign candidate manifest order differs")
    supplied = _sha256(campaign["aggregate_sha256"], "campaign aggregate")
    unsigned = dict(campaign)
    unsigned.pop("aggregate_sha256")
    if supplied != canonical_sha256(unsigned):
        raise CandidateAuthorityError("campaign authority aggregate differs")
    return supplied


__all__ = (
    "AUTHORITY_SCHEMA",
    "CAMPAIGN_SCHEMA",
    "CANDIDATE_NAMES",
    "CandidateAuthority",
    "CandidateAuthorityError",
    "DependencySeal",
    "build_campaign_authority",
    "build_candidate_authority",
    "candidate_config_bytes",
    "candidate_config_mapping",
    "candidate_config_sha256",
    "candidate_dependency_paths",
    "candidate_dependency_specs",
    "candidate_executable_artifact_bytes",
    "candidate_executable_sha256",
    "candidate_native_id",
    "verify_campaign_authority",
    "verify_candidate_authority",
)
