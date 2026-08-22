"""Canonical Stage-3 candidate identity, configuration, and source authority.

This module is the dependency-closed hand-off boundary for output adapters and
campaign tooling.  It does not execute a candidate.  It binds each candidate's
native model identity, exact canonical configuration bytes, and every local
Python source reachable from the ordered authority seeds.

The public builder is suitable for an adapter before it emits output.  The
public verifier is suitable for a campaign after reopening a stored manifest.
Both reject missing or mutated sources, duplicate entries, non-canonical path
spellings, symlinks, hard-link aliases, and dependency-order changes.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


AUTHORITY_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_authority/v1"
CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign_authority/v1"
CONFIG_SCHEMA = "redred.mc_wtb_predictor_stage3.native_config/v1"
CANDIDATE_NAMES = ("RG3", "DSPB", "PLL")

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_RG3_NATIVE_ID = (
    "redred.mc_wtb_predictor_stage3.rg3_cav/"
    "body_transport3_cadence10ms_nearpi1em6_"
    "residual0p5_dircos0_accel0p25/v1"
)
_RG3_PARAMETERS = (
    ("maximum_pose_interval_ns", 10_000_000),
    ("near_pi_margin_rad", 1.0e-6),
    ("maximum_rate_change_ratio", 0.5),
    ("minimum_direction_cosine", 0.0),
    ("maximum_acceleration_contribution_ratio", 0.25),
)

_DSPB_NATIVE_ID = "DSPB-A4-E0E1E2E3-V1"
_DSPB_PARAMETERS = (
    ("max_horizon_ns", 5_000_000),
    ("zoh_max_age_ns", 1_000_000),
    ("ewma_rate_alpha", 0.25),
    ("credit_ewma_alpha", 0.25),
    ("minimum_credit_samples", 2),
    ("credit_tie_tolerance_rad", 1.0e-12),
    ("winner_switch_margin_rad", 1.0e-4),
    ("disagreement_probe_ns", 5_000_000),
    ("maximum_expert_disagreement_rad", 0.5),
    ("maximum_rate_rad_s", 100.0),
    ("maximum_rg3_acceleration_rad_s2", 10_000.0),
    ("maximum_cadence_ratio", 2.0),
    ("rg3_minimum_direction_cosine", 0.0),
    ("rg3_maximum_prior_residual_rad", 0.25),
    ("axis_minimum_coherence", 0.90),
    ("minimum_signed_speed_rad_s", 1.0e-9),
    ("near_pi_margin_rad", 1.0e-6),
)

_PLL_PARAMETERS = (
    ("proportional_gain", 0.25),
    ("integral_gain", 0.02),
    ("lock_residual_max_rad", math.radians(2.0)),
    ("phase_jump_max_rad", math.radians(30.0)),
    ("near_pi_margin_rad", 1.0e-6),
    ("max_gap_ns", 20_000_000),
    ("max_prediction_horizon_ns", 5_000_000),
    ("cav_max_horizon_ns", 5_000_000),
    ("zoh_max_age_ns", 1_000_000),
    ("max_proportional_correction_rad_s", math.radians(2_000.0)),
    ("max_integral_correction_rad_s", math.radians(500.0)),
    ("max_angular_rate_rad_s", math.radians(4_000.0)),
    ("lock_count", 2),
    ("limit_cycle_min_residual_rad", math.radians(0.05)),
    ("limit_cycle_cosine_max", -0.95),
)
_PLL_PARAMETER_MAP = dict(_PLL_PARAMETERS)
_PLL_NATIVE_ID = "%s:%s:%s" % (
    "SO3_PLL_A5_V1",
    ",".join(format(_PLL_PARAMETER_MAP[name], ".17g") for name in (
        "proportional_gain",
        "integral_gain",
        "lock_residual_max_rad",
        "phase_jump_max_rad",
        "near_pi_margin_rad",
        "max_proportional_correction_rad_s",
        "max_integral_correction_rad_s",
        "max_angular_rate_rad_s",
        "limit_cycle_min_residual_rad",
        "limit_cycle_cosine_max",
    )),
    ",".join(str(_PLL_PARAMETER_MAP[name]) for name in (
        "max_gap_ns",
        "max_prediction_horizon_ns",
        "cav_max_horizon_ns",
        "zoh_max_age_ns",
        "lock_count",
    )),
)
_MANIFEST_FIELDS = frozenset((
    "schema", "candidate", "native_candidate_id", "config_encoding",
    "config_bytes_hex", "config_sha256", "dependencies",
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
        "pose_recovery_geometry",
        "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    ),
    (
        "cycle_model",
        "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
    ),
    (
        "shared_output_sealer",
        "benchmarks/redred_mc_wtb_predictor_stage3/output_common.py",
    ),
    (
        "screen_output_sealer",
        "benchmarks/redred_mc_wtb_predictor_stage3/screen108.py",
    ),
    (
        "canonical_json_sealer",
        "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    ),
    (
        "authority_manifest",
        "benchmarks/redred_mc_wtb_predictor_stage3/candidate_authority.py",
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
    """Immutable native/config/dependency authority for one candidate."""

    candidate: str
    native_candidate_id: str
    config_bytes: bytes
    config_sha256: str
    dependencies: Tuple[DependencySeal, ...]
    dependency_aggregate_sha256: str
    manifest_sha256: str

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "schema": AUTHORITY_SCHEMA,
            "candidate": self.candidate,
            "native_candidate_id": self.native_candidate_id,
            "config_encoding": "canonical-json-ascii-hex/v1",
            "config_bytes_hex": self.config_bytes.hex(),
            "config_sha256": self.config_sha256,
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


def candidate_native_id(candidate: str) -> str:
    """Return the model-native, parameter-bound candidate identity."""

    name = _candidate_name(candidate)
    if name == "RG3":
        return _RG3_NATIVE_ID
    if name == "DSPB":
        return _DSPB_NATIVE_ID
    return _PLL_NATIVE_ID


def _native_parameter_mapping(candidate: str) -> Mapping[str, object]:
    if candidate == "RG3":
        return dict(_RG3_PARAMETERS)
    if candidate == "DSPB":
        return dict(_DSPB_PARAMETERS)
    return dict(_PLL_PARAMETERS)


def candidate_config_mapping(candidate: str) -> Mapping[str, object]:
    """Return the uniform native configuration mapping for one candidate."""

    name = _candidate_name(candidate)
    return {
        "schema": CONFIG_SCHEMA,
        "candidate": name,
        "native_candidate_id": candidate_native_id(name),
        "parameters": dict(_native_parameter_mapping(name)),
    }


def candidate_config_bytes(candidate: str) -> bytes:
    """Return exact newline-terminated canonical JSON configuration bytes."""

    return canonical_json_bytes(candidate_config_mapping(candidate))


def candidate_config_sha256(candidate: str) -> str:
    return hashlib.sha256(candidate_config_bytes(candidate)).hexdigest()


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


def _module_name(relative: str) -> Tuple[str, bool]:
    pure = PurePosixPath(relative)
    if pure.name == "__init__.py":
        return ".".join(pure.parent.parts), True
    return ".".join(pure.with_suffix("").parts), False


def _module_sources(root: Path, module: str) -> Tuple[str, ...]:
    if module != "benchmarks" and not module.startswith("benchmarks."):
        return ()
    parts = tuple(module.split("."))
    found = []  # type: List[str]
    for length in range(2, len(parts) + 1):
        package = root.joinpath(*parts[:length]) / "__init__.py"
        if package.is_file():
            found.append(PurePosixPath(*parts[:length], "__init__.py").as_posix())
    file_path = root.joinpath(*parts).with_suffix(".py")
    package_path = root.joinpath(*parts) / "__init__.py"
    if file_path.is_file():
        relative = PurePosixPath(*parts).with_suffix(".py").as_posix()
        found.append(relative)
    elif package_path.is_file():
        relative = PurePosixPath(*parts, "__init__.py").as_posix()
        found.append(relative)
    return tuple(dict.fromkeys(found))


def _absolute_import_base(
    current_relative: str, module: Optional[str], level: int
) -> str:
    if level == 0:
        return "" if module is None else module
    current, is_package = _module_name(current_relative)
    package_parts = current.split(".") if is_package else current.split(".")[:-1]
    remove = level - 1
    if remove > len(package_parts):
        raise CandidateAuthorityError("relative import escapes source package")
    base = package_parts[:len(package_parts) - remove]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _imported_sources(root: Path, relative: str) -> Tuple[str, ...]:
    path = _source_path(root, relative)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise CandidateAuthorityError(
            "cannot parse dependency source: %s" % relative
        ) from exc
    modules = set()  # type: Set[str]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "benchmarks" or alias.name.startswith("benchmarks."):
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_import_base(relative, node.module, node.level)
            if base == "benchmarks" or base.startswith("benchmarks."):
                modules.add(base)
                for alias in node.names:
                    if alias.name != "*":
                        modules.add("%s.%s" % (base, alias.name))
        elif isinstance(node, ast.Call):
            function = node.func
            if (
                isinstance(function, ast.Name) and function.id == "__import__"
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr == "import_module"
            ):
                raise CandidateAuthorityError(
                    "dynamic import prevents dependency closure: %s" % relative
                )
    result = set()  # type: Set[str]
    for module in modules:
        result.update(_module_sources(root, module))
    return tuple(sorted(result))


def _authority_seeds(candidate: str) -> Tuple[Tuple[str, str], ...]:
    name = _candidate_name(candidate)
    return (
        ("output_adapter", _OUTPUT_ADAPTER_PATHS[name]),
        ("model", _MODEL_PATHS[name]),
    ) + _COMMON_AUTHORITY_SEEDS


def candidate_dependency_specs(
    candidate: str, repo_root: Optional[Path] = None
) -> Tuple[Tuple[str, str], ...]:
    """Return ordered role/path pairs closed over local Python imports."""

    name = _candidate_name(candidate)
    root = _root_path(repo_root)
    seeds = _authority_seeds(name)
    seed_paths = tuple(path for _, path in seeds)
    if len(set(seed_paths)) != len(seed_paths):
        raise CandidateAuthorityError("authority seed paths are duplicated")
    closure = set(seed_paths)  # type: Set[str]
    pending = list(seed_paths)
    while pending:
        relative = pending.pop()
        _source_path(root, relative)
        for dependency in _imported_sources(root, relative):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    transitive = tuple(sorted(closure.difference(seed_paths)))
    return seeds + tuple(("transitive_source", path) for path in transitive)


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


def _manifest_body(
    candidate: str,
    native_candidate_id: str,
    config_bytes: bytes,
    config_sha256: str,
    dependencies: Sequence[DependencySeal],
    dependency_aggregate_sha256: str,
) -> Mapping[str, object]:
    return {
        "schema": AUTHORITY_SCHEMA,
        "candidate": candidate,
        "native_candidate_id": native_candidate_id,
        "config_encoding": "canonical-json-ascii-hex/v1",
        "config_bytes_hex": config_bytes.hex(),
        "config_sha256": config_sha256,
        "dependencies": [row.to_mapping() for row in dependencies],
        "dependency_aggregate_sha256": dependency_aggregate_sha256,
    }


def build_candidate_authority(
    candidate: str, repo_root: Optional[Path] = None
) -> CandidateAuthority:
    """Build one source-bound authority manifest without executing a model."""

    name = _candidate_name(candidate)
    root = _root_path(repo_root)
    config_bytes = candidate_config_bytes(name)
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    dependencies = _sealed_dependencies(
        candidate_dependency_specs(name, root), root
    )
    dependency_aggregate = canonical_sha256([
        row.to_mapping() for row in dependencies
    ])
    body = _manifest_body(
        name,
        candidate_native_id(name),
        config_bytes,
        config_sha,
        dependencies,
        dependency_aggregate,
    )
    return CandidateAuthority(
        name,
        candidate_native_id(name),
        config_bytes,
        config_sha,
        dependencies,
        dependency_aggregate,
        canonical_sha256(body),
    )


def _decode_config_hex(value: object) -> bytes:
    if type(value) is not str or len(value) % 2 != 0:
        raise CandidateAuthorityError("config bytes are not canonical hex")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise CandidateAuthorityError("config bytes are not canonical hex") from exc
    if decoded.hex() != value:
        raise CandidateAuthorityError("config bytes are not canonical hex")
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
    if manifest["native_candidate_id"] != candidate_native_id(candidate):
        raise CandidateAuthorityError("native candidate identity differs")
    if manifest["config_encoding"] != "canonical-json-ascii-hex/v1":
        raise CandidateAuthorityError("config encoding differs")
    config_bytes = _decode_config_hex(manifest["config_bytes_hex"])
    expected_config = candidate_config_bytes(candidate)
    if config_bytes != expected_config:
        raise CandidateAuthorityError("exact candidate config bytes differ")
    config_sha = _sha256(manifest["config_sha256"], "config digest")
    if config_sha != hashlib.sha256(config_bytes).hexdigest():
        raise CandidateAuthorityError("candidate config digest differs")

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
    "CONFIG_SCHEMA",
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
    "candidate_native_id",
    "verify_campaign_authority",
    "verify_candidate_authority",
)
