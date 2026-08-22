"""Single-attempt dispatch of tested Stage-3 output adapters to screen108.

This module does not implement a predictor or replay pose/event edges.  A
closed frozen-ID registry dispatches exactly one tested output adapter.  The
adapter returns the sealed candidate-output envelope that is written
append-only before the locked screen is allowed to join selector labels.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    CAVRegistryEvaluation,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
    build_locked_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256

from . import dspb, dspb_output, pll_output, rg3, rg3_output, screen108, so3_pll


CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_receipt/v3"
ATTEMPT_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_attempt/v3"
GENERATOR_EVIDENCE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign108_adapter_dispatch/v3"
)
DEPENDENCY_MANIFEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign108_dependency_manifest/v1"
)
REPLAY_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_replay/v1"

RG3_ID = rg3_output.RG3_OUTPUT_CANDIDATE_ID
DSPB_ID = dspb.DSPBConfig().candidate_id
SO3_PLL_ID = pll_output.CANDIDATE_ID
FROZEN_CANDIDATE_IDS = (RG3_ID, DSPB_ID, SO3_PLL_ID)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_AUTHORITIES = {
    RG3_ID: {
        "output_adapter_path": "benchmarks/redred_mc_wtb_predictor_stage3/rg3_output.py",
        "output_adapter_sha256": (
            "ef3ebd6afa3bdb16c5744e22b837cbb42be0b0fb32d2a7fbad1f0d0c6fc9b989"
        ),
        "candidate_executable_sha256": (
            "ef3ebd6afa3bdb16c5744e22b837cbb42be0b0fb32d2a7fbad1f0d0c6fc9b989"
        ),
        "candidate_executable_path": (
            "benchmarks/redred_mc_wtb_predictor_stage3/rg3_output.py"
        ),
        "candidate_config_sha256": (
            "bd8f57020a8eff97112e933e86e87fa49e802d63545638913d34be6be0a20ee1"
        ),
        "model_path": "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
        "model_sha256": (
            "de1cb82ac902064dc5875f34807028554f62815ec626b91f1fac68e07a41d865"
        ),
    },
    DSPB_ID: {
        "output_adapter_path": "benchmarks/redred_mc_wtb_predictor_stage3/dspb_output.py",
        "output_adapter_sha256": (
            "0ec8500d610b7327d8ae74f5699b7256af0c96920169d027180cb6500133870a"
        ),
        "candidate_executable_sha256": (
            "c63f4d5f1ac6b28f5ba2665af133a2de5c5e39a51297bfbeceee240571026302"
        ),
        "candidate_executable_path": (
            "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py"
        ),
        "candidate_config_sha256": (
            "94cdfdbbb532baf02fc836b43677cbdacffeeeaedb42584718d6875e3358b9f5"
        ),
        "model_path": "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
        "model_sha256": (
            "c63f4d5f1ac6b28f5ba2665af133a2de5c5e39a51297bfbeceee240571026302"
        ),
    },
    SO3_PLL_ID: {
        "output_adapter_path": "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py",
        "output_adapter_sha256": (
            "9d1495753d8a166c86856c89f0dc0dde4005e027789f222960545ae2b79503c0"
        ),
        "candidate_executable_sha256": (
            "9d1495753d8a166c86856c89f0dc0dde4005e027789f222960545ae2b79503c0"
        ),
        "candidate_executable_path": (
            "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py"
        ),
        "candidate_config_sha256": (
            "971a74c52f65f4b68d828fc9b391f194e5f2e8245c8d43779c472e72a99a2ffd"
        ),
        "model_path": "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
        "model_sha256": (
            "dc1f0433f7ae4b16828ba51100a5587ae70c42f86c3ef1dc26960bc23058ed58"
        ),
    },
}

_DEPENDENCY_AUTHORITIES = (
    ("pose_recovery_package", "benchmarks/redred_mc_wtb_pose_recovery/__init__.py", "4a188a7d496b5b02db50d35d6b5b0b59a4bbb9db9e7b2d4c5bfed0262e564d7a"),
    ("pose_geometry", "benchmarks/redred_mc_wtb_pose_recovery/geometry.py", "4996aca6e9b85a777334a0239ea655a40bb8ba31a78e467bbbd4cbfa95dc8057"),
    ("contract_package", "benchmarks/redred_mc_wtb_stage4_contract/__init__.py", "851a179fb715c7f30160ccd28837e94795fb361e7ae203ee5fbbf5cbb8562a7c"),
    ("contract", "benchmarks/redred_mc_wtb_stage4_contract/contract.py", "65f5131fce3b30e956e30494e7ebaa2f3fad399a396f8760625a275f63378d38"),
    ("contract_receipt", "benchmarks/redred_mc_wtb_stage4_contract/receipt.py", "dd41d3591544da08c031f1c0d6d70428df3e495865c65dbbdeb31c0ae2a91c9b"),
    ("cyclemodel_package", "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py", "8357e0b4d5579dcd1d96ade00060ecce7c7e9a254aac54f094d286b851ea46af"),
    ("cyclemodel", "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py", "ac69d2e5e35f100cca1385be728814fec5e873ce0cf81a2ca4f38880100167ee"),
    ("axis_analyzer", "benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py", "093a80285f79f97f9a73090ed7a9c5cbbe3112a89a9a99a923d1e68556a573c3"),
    ("current_cav_evaluator", "benchmarks/redred_mc_wtb_so3_axis_audit/evaluator.py", "64cf6d9aff7c4a3dec791469b5e2f010fe80d8930650f8438d80f4659b3302fd"),
    ("new108_adapter", "benchmarks/redred_mc_wtb_so3_axis_audit/new108_adapter.py", "51ad0ac11f4334b9936fe7aa332d6e84401bfd0a369e8c1c85a59ec8b5579904"),
    ("locked_screen", "benchmarks/redred_mc_wtb_predictor_stage3/screen108.py", "1fd56ae7b96a6cb910a85834f830b48d4bad25be6f93998404be1e5383773513"),
    ("screen_schema", "benchmarks/redred_mc_wtb_predictor_stage3/screen108_result.schema.json", "d5028197abb43f5be6c336ad80f39f6fa3d778ced1b4f13bd8047baead0256f3"),
    ("predictor_package", "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py", "956d1dac6c59cd40eba6079ab7b0762e0a92b6c001594643f36a6440156defdc"),
    ("axis_audit_package", "benchmarks/redred_mc_wtb_so3_axis_audit/__init__.py", "a543dd82af6621ff0714975326f75e2e8a2dd150561a97907fa26a25dca3539b"),
    ("axis_selector", "benchmarks/redred_mc_wtb_so3_axis_audit/selector.py", "c6dfd995f095a9b5c3bb5319a004a44db9af095605ff62e536f49c782f4ff76b"),
    ("causal_reference_package", "benchmarks/redred_mc_wtb_causal_reference/__init__.py", "3e61e89dfa8bfd7727685bf54afb468c35eb3ae2e809688b7039607af4b89b9f"),
    ("causal_reference", "benchmarks/redred_mc_wtb_causal_reference/reference.py", "91476780b663991ab39f9f60d6afc900ae32ae58e4d790d105995a345f9bcd83"),
    ("causal_reference_routing", "benchmarks/redred_mc_wtb_causal_reference/routing.py", "665405747de889a100c21c145aad8847286e299ec0a8c1722eea04614aea1929"),
    ("causal_reference_development", "benchmarks/redred_mc_wtb_causal_reference/development.py", "dbd8cf95df3a5d6520aad047e5a0706fc3618866e700ef97478b13add5068d42"),
    ("motion_qualification_package", "benchmarks/redred_mc_wtb_motion_qualification/__init__.py", "823cc7684d51a41df0c0f33ce972cb565f8960e3c7cd767088f70cccfbd0622d"),
    ("motion_qualification", "benchmarks/redred_mc_wtb_motion_qualification/controller.py", "fee2819cb7205ced693cece540c0d50b0e3e96574af9f1d03bc5392d3f4447e1"),
    ("stage4_assay_package", "benchmarks/redred_mc_wtb_stage4_assay/__init__.py", "b5af60a5c5372749c927ed97f9b047c174312ecd382823aa2ae56974a924cdc6"),
    ("stage4_assay_generator", "benchmarks/redred_mc_wtb_stage4_assay/generator.py", "2e0f5bb2903150606c05916f688f3e03fca1b004fc1ac52dc0dee36c9428d719"),
    ("stage4_assay_source", "benchmarks/redred_mc_wtb_stage4_assay/source.py", "48e6786d35f46d7e8d51c268ee7f539718dd1d5e6ed8190296b349d5c66f1102"),
)


class Campaign108Error(ValueError):
    """A frozen authority, artifact, or single-attempt invariant failed."""


@dataclass(frozen=True)
class NeutralAdapterView:
    """Only the neutral projection and aggregate binding exposed to adapters."""

    neutral_registry: Tuple[object, ...]
    event_streams: Mapping[str, Tuple[object, ...]]
    pose_streams: Mapping[str, Tuple[object, ...]]
    provenance_seal: Mapping[str, str]


@dataclass(frozen=True)
class NeutralCycleView:
    records: Tuple[object, ...]


@dataclass(frozen=True)
class NeutralBaselineWindow:
    registry: object
    input_events: Tuple[object, ...]
    input_poses: Tuple[object, ...]
    simulation: NeutralCycleView


@dataclass(frozen=True)
class NeutralBaselineView:
    windows: Tuple[NeutralBaselineWindow, ...]
    neutral_input_sha256: str


Adapter = Callable[[NeutralAdapterView, NeutralBaselineView], Mapping[str, object]]


@dataclass(frozen=True)
class FrozenCandidate:
    candidate_id: str
    model_candidate_id: str
    output_adapter_path: Path
    output_adapter_sha256: str
    candidate_executable_path: Path
    candidate_executable_sha256: str
    model_path: Path
    model_sha256: str
    config_bytes: bytes
    config_sha256: str
    adapter: Adapter


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _relative_authority_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_repo_root()))
    except ValueError as exc:
        raise Campaign108Error("candidate authority lies outside the repository") from exc


def _read_bytes(path: Path, where: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Campaign108Error("cannot read %s" % where) from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Campaign108Error("%s must be lowercase SHA-256" % where)
    return value


def _read_json_bytes(payload: bytes, where: str) -> Mapping[str, object]:
    def reject_constant(value: str) -> None:
        raise Campaign108Error("%s contains a non-finite JSON value" % where)

    def reject_duplicates(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
        result = {}  # type: Dict[str, object]
        for key, value in pairs:
            if key in result:
                raise Campaign108Error("%s contains a duplicate JSON key" % where)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Campaign108Error("cannot parse %s" % where) from exc
    if not isinstance(value, Mapping):
        raise Campaign108Error("%s must be a JSON object" % where)
    return value


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Campaign108Error("campaign artifact is not finite JSON") from exc


def _exclusive_write(path: Path, payload: bytes, where: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise Campaign108Error("cannot append-only create %s" % where) from exc


def _neutral_view(bundle: New108AdapterBundle) -> NeutralAdapterView:
    """Snapshot a label-free, immutable view for both adapter executions."""

    if type(bundle) is not New108AdapterBundle:
        raise Campaign108Error("locked adapter returned the wrong bundle type")
    registries = tuple(bundle.neutral_registry)
    identifiers = tuple(row.window_id for row in registries)
    if (
        not registries
        or len(set(identifiers)) != len(identifiers)
        or set(bundle.event_streams) != set(identifiers)
        or set(bundle.pose_streams) != set(identifiers)
    ):
        raise Campaign108Error("neutral adapter projection identities differ")
    events = MappingProxyType({
        window_id: tuple(bundle.event_streams[window_id])
        for window_id in identifiers
    })
    poses = MappingProxyType({
        window_id: tuple(bundle.pose_streams[window_id])
        for window_id in identifiers
    })
    aggregate = bundle.provenance_seal.get("aggregate_sha256")
    _sha256(aggregate, "adapter aggregate digest")
    return NeutralAdapterView(
        registries,
        events,
        poses,
        MappingProxyType({"aggregate_sha256": str(aggregate)}),
    )


def _neutral_projection_sha256(view: NeutralAdapterView) -> str:
    return canonical_sha256({
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": [row.to_mapping() for row in view.neutral_registry],
        "windows": [
            {
                "window_id": row.window_id,
                "events": [
                    event.to_content_mapping()
                    for event in view.event_streams[row.window_id]
                ],
                "poses": [
                    pose.to_content_mapping()
                    for pose in view.pose_streams[row.window_id]
                ],
            }
            for row in view.neutral_registry
        ],
    })


def _neutral_baseline_view(
    baseline: CAVRegistryEvaluation,
    neutral: NeutralAdapterView,
) -> NeutralBaselineView:
    try:
        windows = tuple(baseline.windows)
        digest = baseline.neutral_input_sha256
    except AttributeError as exc:
        raise Campaign108Error("baseline neutral authority is missing") from exc
    if len(windows) != len(neutral.neutral_registry):
        raise Campaign108Error("baseline neutral window cardinality differs")
    projected = []
    for row, registry in zip(windows, neutral.neutral_registry):
        window_id = registry.window_id
        if (
            row.registry != registry
            or tuple(row.input_events) != neutral.event_streams[window_id]
            or tuple(row.input_poses) != neutral.pose_streams[window_id]
        ):
            raise Campaign108Error("baseline neutral window binding differs")
        projected.append(NeutralBaselineWindow(
            registry,
            neutral.event_streams[window_id],
            neutral.pose_streams[window_id],
            NeutralCycleView(tuple(row.simulation.records)),
        ))
    _sha256(digest, "baseline neutral input digest")
    return NeutralBaselineView(tuple(projected), str(digest))


def _execute_adapter(
    spec: FrozenCandidate,
    view: NeutralAdapterView,
    baseline: NeutralBaselineView,
    mode: str,
) -> Mapping[str, object]:
    if mode not in ("production", "verifier_replay"):
        raise Campaign108Error("adapter execution mode differs")
    return spec.adapter(view, baseline)


def _dispatch_rg3(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    del baseline
    return rg3_output.generate_locked_rg3_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_dspb(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    del baseline
    return dspb_output.generate_dspb_candidate_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_pll(
    bundle: NeutralAdapterView, baseline: NeutralBaselineView
) -> Mapping[str, object]:
    return pll_output.generate_locked_pll_output(bundle, baseline)


def _candidate_registry() -> Mapping[str, FrozenCandidate]:
    rg3_authority = _EXPECTED_AUTHORITIES[RG3_ID]
    dspb_authority = _EXPECTED_AUTHORITIES[DSPB_ID]
    pll_authority = _EXPECTED_AUTHORITIES[SO3_PLL_ID]
    return {
        RG3_ID: FrozenCandidate(
            RG3_ID,
            rg3.RG3_POLICY.candidate_id,
            Path(rg3_output.__file__).resolve(),
            rg3_authority["output_adapter_sha256"],
            Path(rg3_output.RG3_EXECUTABLE_PATH).resolve(),
            rg3_authority["candidate_executable_sha256"],
            Path(rg3.__file__).resolve(),
            rg3_authority["model_sha256"],
            bytes(rg3_output.RG3_CONFIG_BYTES),
            rg3_authority["candidate_config_sha256"],
            _dispatch_rg3,
        ),
        DSPB_ID: FrozenCandidate(
            DSPB_ID,
            dspb.DSPBConfig().candidate_id,
            Path(dspb_output.__file__).resolve(),
            dspb_authority["output_adapter_sha256"],
            Path(dspb.__file__).resolve(),
            dspb_authority["candidate_executable_sha256"],
            Path(dspb.__file__).resolve(),
            dspb_authority["model_sha256"],
            bytes(dspb_output.locked_dspb_config_bytes()),
            dspb_authority["candidate_config_sha256"],
            _dispatch_dspb,
        ),
        SO3_PLL_ID: FrozenCandidate(
            SO3_PLL_ID,
            so3_pll.SO3PLLConfig().candidate_id,
            Path(pll_output.__file__).resolve(),
            pll_authority["output_adapter_sha256"],
            Path(pll_output.__file__).resolve(),
            pll_authority["candidate_executable_sha256"],
            Path(so3_pll.__file__).resolve(),
            pll_authority["model_sha256"],
            bytes(pll_output.locked_config_bytes()),
            pll_authority["candidate_config_sha256"],
            _dispatch_pll,
        ),
    }


_CANDIDATES = _candidate_registry()


def _candidate(candidate_id: object) -> FrozenCandidate:
    if type(candidate_id) is not str or candidate_id not in _CANDIDATES:
        raise Campaign108Error("candidate ID is not in the frozen Stage3 registry")
    return _CANDIDATES[candidate_id]


def frozen_candidate_config_bytes(candidate_id: str) -> bytes:
    """Return the exact adapter-owned config bytes accepted for a frozen ID."""

    return bytes(_candidate(candidate_id).config_bytes)


def frozen_candidate_config(candidate_id: str) -> Mapping[str, object]:
    """Return the adapter-owned config as a detached JSON mapping."""

    return _read_json_bytes(
        frozen_candidate_config_bytes(candidate_id), "locked candidate config"
    )


def _verify_authorities(
    spec: FrozenCandidate,
) -> Tuple[bytes, bytes, bytes]:
    expected = _EXPECTED_AUTHORITIES[spec.candidate_id]
    if (
        _relative_authority_path(spec.output_adapter_path)
        != expected["output_adapter_path"]
        or _relative_authority_path(spec.candidate_executable_path)
        != expected["candidate_executable_path"]
        or _relative_authority_path(spec.model_path) != expected["model_path"]
    ):
        raise Campaign108Error("candidate authority path differs from the frozen ID")
    adapter_bytes = _read_bytes(spec.output_adapter_path, "output adapter")
    executable_bytes = _read_bytes(
        spec.candidate_executable_path, "candidate executable"
    )
    model_bytes = _read_bytes(spec.model_path, "candidate model")
    if _sha256_bytes(adapter_bytes) != spec.output_adapter_sha256:
        raise Campaign108Error("output adapter differs from the frozen ID")
    if _sha256_bytes(executable_bytes) != spec.candidate_executable_sha256:
        raise Campaign108Error("candidate executable differs from the frozen ID")
    if _sha256_bytes(model_bytes) != spec.model_sha256:
        raise Campaign108Error("candidate model differs from the frozen ID")
    if _sha256_bytes(spec.config_bytes) != spec.config_sha256:
        raise Campaign108Error("adapter-owned config differs from the frozen ID")
    return adapter_bytes, executable_bytes, model_bytes


def _dependency_manifest(
    candidate_id: str,
) -> Tuple[Mapping[str, object], Tuple[Tuple[Path, bytes], ...]]:
    dependencies = []
    snapshots = []
    for role, relative, expected_sha256 in _DEPENDENCY_AUTHORITIES:
        path = _repo_root() / relative
        payload = _read_bytes(path, "%s dependency" % role)
        if _sha256_bytes(payload) != expected_sha256:
            raise Campaign108Error("%s dependency differs from the frozen manifest" % role)
        dependencies.append({
            "role": role,
            "path": relative,
            "sha256": expected_sha256,
        })
        snapshots.append((path, payload))
    body = {
        "schema": DEPENDENCY_MANIFEST_SCHEMA,
        "candidate_id": candidate_id,
        "dependencies": dependencies,
    }
    manifest = dict(body, manifest_sha256=canonical_sha256(body))
    return manifest, tuple(snapshots)


def _check_dependencies_unchanged(
    snapshots: Sequence[Tuple[Path, bytes]],
) -> None:
    for path, payload in snapshots:
        _check_unchanged(path, payload, "%s dependency" % path.name)


def _validate_adapter_output(
    value: object,
    spec: FrozenCandidate,
    bundle: NeutralAdapterView,
    baseline: NeutralBaselineView,
) -> Mapping[str, object]:
    fields = {
        "schema", "candidate_id", "adapter_aggregate_sha256",
        "neutral_input_sha256", "candidate_executable_sha256",
        "candidate_config_sha256", "windows", "aggregate_sha256",
    }
    # Event rows remain opaque: occurrence/decision/state fields added by a
    # future v2 row pass through byte-for-byte once the screen adopts them.
    if not isinstance(value, Mapping) or set(value) != fields:
        raise Campaign108Error("output adapter returned the wrong field schema")
    output = dict(value)
    unsigned = dict(output)
    digest = unsigned.pop("aggregate_sha256", None)
    if (
        output.get("schema") != screen108.CANDIDATE_OUTPUT_SCHEMA
        or output.get("candidate_id") != spec.candidate_id
        or output.get("adapter_aggregate_sha256")
        != bundle.provenance_seal.get("aggregate_sha256")
        or output.get("neutral_input_sha256") != baseline.neutral_input_sha256
        or output.get("candidate_executable_sha256")
        != spec.candidate_executable_sha256
        or output.get("candidate_config_sha256") != spec.config_sha256
        or digest != canonical_sha256(unsigned)
    ):
        raise Campaign108Error("output adapter seal or frozen binding differs")
    supplied_windows = output.get("windows")
    expected_ids = [row.window_id for row in bundle.neutral_registry]
    if not isinstance(supplied_windows, list) or [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in supplied_windows
    ] != expected_ids:
        raise Campaign108Error("output adapter changed screen window IDs")
    return output


def _dispatch_evidence(
    spec: FrozenCandidate,
    candidate_output_sha256: str,
    window_ids: Sequence[str],
    dependency_manifest_sha256: str,
    campaign_runner_sha256: str,
) -> Mapping[str, object]:
    dispatch = {
        "output_adapter_path": _relative_authority_path(spec.output_adapter_path),
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": _relative_authority_path(
            spec.candidate_executable_path
        ),
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "model_path": _relative_authority_path(spec.model_path),
        "model_sha256": spec.model_sha256,
        "dependency_manifest_sha256": dependency_manifest_sha256,
        "candidate_config_sha256": spec.config_sha256,
        "campaign_runner_sha256": campaign_runner_sha256,
        "candidate_output_sha256": candidate_output_sha256,
        "ordered_window_ids": list(window_ids),
        "ordered_window_ids_sha256": canonical_sha256(list(window_ids)),
    }
    body = {
        "schema": GENERATOR_EVIDENCE_SCHEMA,
        "candidate_id": spec.candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "windows": [dispatch],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _replay_receipt(
    spec: FrozenCandidate,
    neutral_input_sha256: str,
    production_output_bytes: bytes,
    replay_output_bytes: bytes,
    production_evidence_bytes: bytes,
    replay_evidence_bytes: bytes,
) -> Mapping[str, object]:
    if production_output_bytes != replay_output_bytes:
        raise Campaign108Error("verifier replay candidate output is not byte-identical")
    if production_evidence_bytes != replay_evidence_bytes:
        raise Campaign108Error("verifier replay evidence is not byte-identical")
    body = {
        "schema": REPLAY_SCHEMA,
        "candidate_id": spec.candidate_id,
        "mode": "verifier_replay",
        "neutral_input_sha256": neutral_input_sha256,
        "adapter_execution_count": 2,
        "production_output_bytes_sha256": _sha256_bytes(production_output_bytes),
        "replay_output_bytes_sha256": _sha256_bytes(replay_output_bytes),
        "production_evidence_bytes_sha256": _sha256_bytes(production_evidence_bytes),
        "replay_evidence_bytes_sha256": _sha256_bytes(replay_evidence_bytes),
        "candidate_output_byte_identical": True,
        "evidence_byte_identical": True,
        "replay_used_for_tuning": False,
        "replay_output_used_for_screen": False,
    }
    return dict(body, replay_sha256=canonical_sha256(body))


def _artifact(path: Path, payload: bytes, semantic_sha256: str) -> Mapping[str, object]:
    return {
        "path": path.name,
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "semantic_sha256": semantic_sha256,
    }


def _check_unchanged(path: Path, expected: bytes, where: str) -> None:
    if _read_bytes(path, where) != expected:
        raise Campaign108Error("%s changed during the single attempt" % where)


def _artifact_paths(campaign_directory: Path, candidate_id: str) -> Mapping[str, Path]:
    prefix = candidate_id.lower()
    return {
        "attempt": campaign_directory / (prefix + ".attempt.json"),
        "generator_evidence": campaign_directory / (
            prefix + ".generator-evidence.json"
        ),
        "candidate_output": campaign_directory / (prefix + ".candidate-output.json"),
        "dependency_manifest": campaign_directory / (
            prefix + ".dependency-manifest.json"
        ),
        "replay": campaign_directory / (prefix + ".replay.json"),
        "screen_result": campaign_directory / (prefix + ".screen108-result.json"),
        "campaign_receipt": campaign_directory / (prefix + ".campaign-receipt.json"),
    }


def run_campaign108(
    candidate_id: str,
    dataset_directory: Path,
    config_path: Path,
    cncp_path: Path,
    campaign_directory: Path,
) -> Mapping[str, object]:
    """Run one production adapter execution plus one non-tuning replay."""

    spec = _candidate(candidate_id)
    config_file = Path(config_path)
    cncp_file = Path(cncp_path)
    config_bytes = _read_bytes(config_file, "candidate config")
    cncp_bytes = _read_bytes(cncp_file, "CNCP")
    if config_bytes != spec.config_bytes:
        raise Campaign108Error("candidate config bytes differ from the output adapter")
    config = _read_json_bytes(config_bytes, "candidate config")
    cncp = _read_json_bytes(cncp_bytes, "CNCP")
    screen108.validate_cncp(cncp)
    adapter_bytes, executable_bytes, model_bytes = _verify_authorities(spec)
    dependency_manifest, dependency_snapshots = _dependency_manifest(candidate_id)
    dependency_manifest_digest = str(dependency_manifest["manifest_sha256"])
    dependency_manifest_bytes = _json_bytes(dependency_manifest)
    campaign_bytes = _read_bytes(Path(__file__), "campaign runner")
    campaign_digest = _sha256_bytes(campaign_bytes)

    campaign_root = Path(campaign_directory)
    if campaign_root.exists() and (
        not campaign_root.is_dir() or campaign_root.is_symlink()
    ):
        raise Campaign108Error("campaign directory must be a real directory")
    try:
        campaign_root.mkdir(mode=0o755, parents=False, exist_ok=True)
    except OSError as exc:
        raise Campaign108Error("cannot create campaign directory") from exc
    paths = _artifact_paths(campaign_root, candidate_id)
    config_sha256 = _sha256_bytes(config_bytes)
    cncp_sha256 = _sha256_bytes(cncp_bytes)
    config_semantic_sha256 = canonical_sha256(config)
    cncp_semantic_sha256 = canonical_sha256(cncp)
    adapter_relative = _relative_authority_path(spec.output_adapter_path)
    executable_relative = _relative_authority_path(spec.candidate_executable_path)
    model_relative = _relative_authority_path(spec.model_path)
    attempt_body = {
        "schema": ATTEMPT_SCHEMA,
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_index": 1,
        "output_adapter_path": adapter_relative,
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": executable_relative,
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "model_path": model_relative,
        "model_sha256": spec.model_sha256,
        "dependency_manifest_sha256": dependency_manifest_digest,
        "candidate_config_sha256": config_sha256,
        "candidate_config_semantic_sha256": config_semantic_sha256,
        "cncp_sha256": cncp_sha256,
        "cncp_semantic_sha256": cncp_semantic_sha256,
        "campaign_runner_sha256": campaign_digest,
        "adapter_execution_count": 2,
        "verification_replay_count": 1,
        "verification_replay_is_tuning": False,
        "retry_allowed": False,
        "tuning_allowed": False,
    }
    attempt = dict(attempt_body, attempt_sha256=canonical_sha256(attempt_body))
    attempt_bytes = _json_bytes(attempt)
    _exclusive_write(paths["attempt"], attempt_bytes, "campaign attempt marker")
    _exclusive_write(
        paths["dependency_manifest"],
        dependency_manifest_bytes,
        "dependency manifest",
    )

    # The bundle contains a selector-label sidecar, but neither this projection
    # nor any output-adapter API reads or receives it.  screen108 is called only
    # after the returned candidate envelope has been sealed and persisted.
    bundle = build_locked_new108_adapter(Path(dataset_directory))
    neutral = _neutral_view(bundle)
    full_baseline = evaluate_current_cav_registry(
        neutral.neutral_registry, neutral.event_streams, neutral.pose_streams
    )
    baseline = _neutral_baseline_view(full_baseline, neutral)
    neutral_input_sha256 = _neutral_projection_sha256(neutral)
    if neutral_input_sha256 != baseline.neutral_input_sha256:
        raise Campaign108Error("neutral source binding differs from baseline")

    # Production and verifier modes call the same tested adapter.  All causal
    # scheduling remains inside that adapter; the campaign never interprets a
    # candidate row and never exposes the label sidecar.
    generated = _execute_adapter(spec, neutral, baseline, "production")
    candidate_output = _validate_adapter_output(generated, spec, neutral, baseline)
    output_digest = str(candidate_output["aggregate_sha256"])
    candidate_output_bytes = _json_bytes(candidate_output)
    _exclusive_write(paths["candidate_output"], candidate_output_bytes, "candidate output")

    window_ids = [row.window_id for row in neutral.neutral_registry]
    evidence = _dispatch_evidence(
        spec,
        output_digest,
        window_ids,
        dependency_manifest_digest,
        campaign_digest,
    )
    evidence_digest = str(evidence["aggregate_sha256"])
    evidence_bytes = _json_bytes(evidence)
    _exclusive_write(paths["generator_evidence"], evidence_bytes, "adapter dispatch evidence")

    # Freeze every authority before verifier replay.  The source-bound bundle
    # is independently reprojected to catch mapping substitution while the
    # immutable neutral snapshot remains the exact adapter input.
    _check_unchanged(config_file, config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_unchanged(spec.output_adapter_path, adapter_bytes, "output adapter")
    _check_unchanged(
        spec.candidate_executable_path, executable_bytes, "candidate executable"
    )
    _check_unchanged(spec.model_path, model_bytes, "candidate model")
    _check_dependencies_unchanged(dependency_snapshots)
    if _neutral_projection_sha256(_neutral_view(bundle)) != neutral_input_sha256:
        raise Campaign108Error("neutral source changed after production execution")

    replayed = _execute_adapter(spec, neutral, baseline, "verifier_replay")
    replay_output = _validate_adapter_output(replayed, spec, neutral, baseline)
    replay_output_bytes = _json_bytes(replay_output)
    replay_evidence = _dispatch_evidence(
        spec,
        str(replay_output["aggregate_sha256"]),
        window_ids,
        dependency_manifest_digest,
        campaign_digest,
    )
    replay_evidence_bytes = _json_bytes(replay_evidence)
    replay = _replay_receipt(
        spec,
        neutral_input_sha256,
        candidate_output_bytes,
        replay_output_bytes,
        evidence_bytes,
        replay_evidence_bytes,
    )
    replay_digest = str(replay["replay_sha256"])
    replay_bytes = _json_bytes(replay)
    _exclusive_write(paths["replay"], replay_bytes, "verifier replay receipt")

    _check_unchanged(config_file, config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_unchanged(spec.output_adapter_path, adapter_bytes, "output adapter")
    _check_unchanged(
        spec.candidate_executable_path, executable_bytes, "candidate executable"
    )
    _check_unchanged(spec.model_path, model_bytes, "candidate model")
    _check_dependencies_unchanged(dependency_snapshots)
    _check_unchanged(
        paths["candidate_output"], candidate_output_bytes, "candidate output"
    )
    _check_unchanged(
        paths["generator_evidence"], evidence_bytes, "adapter dispatch evidence"
    )
    if _neutral_projection_sha256(_neutral_view(bundle)) != neutral_input_sha256:
        raise Campaign108Error("neutral source changed during verifier replay")

    screen_result = screen108.run_locked_screen108(
        Path(dataset_directory),
        paths["candidate_output"],
        spec.candidate_executable_path,
        config_file,
        cncp,
    )
    screen_digest = screen108.verify_screen108_result_envelope(screen_result)
    screen_bytes = _json_bytes(screen_result)
    _exclusive_write(paths["screen_result"], screen_bytes, "screen108 result")

    _check_unchanged(config_file, config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(Path(__file__), campaign_bytes, "campaign runner")
    _check_unchanged(spec.output_adapter_path, adapter_bytes, "output adapter")
    _check_unchanged(
        spec.candidate_executable_path, executable_bytes, "candidate executable"
    )
    _check_unchanged(spec.model_path, model_bytes, "candidate model")
    _check_dependencies_unchanged(dependency_snapshots)
    for name, payload in (
        ("attempt", attempt_bytes),
        ("dependency_manifest", dependency_manifest_bytes),
        ("candidate_output", candidate_output_bytes),
        ("generator_evidence", evidence_bytes),
        ("replay", replay_bytes),
        ("screen_result", screen_bytes),
    ):
        _check_unchanged(paths[name], payload, "%s artifact" % name)

    artifacts = {
        "attempt": _artifact(paths["attempt"], attempt_bytes, attempt["attempt_sha256"]),
        "dependency_manifest": _artifact(
            paths["dependency_manifest"],
            dependency_manifest_bytes,
            dependency_manifest_digest,
        ),
        "generator_evidence": _artifact(
            paths["generator_evidence"], evidence_bytes, evidence_digest
        ),
        "candidate_output": _artifact(
            paths["candidate_output"], candidate_output_bytes, output_digest
        ),
        "replay": _artifact(paths["replay"], replay_bytes, replay_digest),
        "screen_result": _artifact(paths["screen_result"], screen_bytes, screen_digest),
    }
    bindings = {
        "output_adapter_path": adapter_relative,
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": executable_relative,
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "model_path": model_relative,
        "model_sha256": spec.model_sha256,
        "dependency_manifest_sha256": dependency_manifest_digest,
        "candidate_config_sha256": config_sha256,
        "candidate_config_semantic_sha256": config_semantic_sha256,
        "cncp_sha256": cncp_sha256,
        "cncp_semantic_sha256": cncp_semantic_sha256,
        "generator_evidence_sha256": evidence_digest,
        "candidate_output_sha256": output_digest,
        "campaign_runner_sha256": campaign_digest,
        "replay_sha256": replay_digest,
        "screen_result_sha256": screen_digest,
    }
    receipt_body = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED",
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_sha256": attempt["attempt_sha256"],
        "bindings": bindings,
        "artifacts": artifacts,
        "policy": {
            "attempt_count": 1,
            "adapter_execution_count": 2,
            "verification_replay_count": 1,
            "verification_replay_is_tuning": False,
            "verification_replay_output_scored": False,
            "retry_performed": False,
            "tuning_performed": False,
            "labels_accessed_before_candidate_output_seal": False,
            "source_selection_changed": False,
            "external_data_accessed": False,
            "rtl_or_ppa_evaluated": False,
        },
    }
    receipt = dict(receipt_body, receipt_sha256=canonical_sha256(receipt_body))
    _exclusive_write(
        paths["campaign_receipt"], _json_bytes(receipt), "campaign receipt"
    )
    return receipt


def verify_campaign108_receipt(
    receipt: Mapping[str, object], campaign_directory: Path
) -> str:
    """Verify frozen authorities and all campaign artifact cross-bindings."""

    required = {
        "schema", "status", "candidate_id", "model_candidate_id",
        "attempt_sha256", "bindings", "artifacts", "policy", "receipt_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise Campaign108Error("campaign receipt field schema differs")
    if (
        receipt["schema"] != CAMPAIGN_SCHEMA
        or receipt["status"] != "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED"
    ):
        raise Campaign108Error("campaign receipt schema or status differs")
    spec = _candidate(receipt["candidate_id"])
    if receipt["model_candidate_id"] != spec.model_candidate_id:
        raise Campaign108Error("campaign model identity differs")
    expected_policy = {
        "attempt_count": 1,
        "adapter_execution_count": 2,
        "verification_replay_count": 1,
        "verification_replay_is_tuning": False,
        "verification_replay_output_scored": False,
        "retry_performed": False,
        "tuning_performed": False,
        "labels_accessed_before_candidate_output_seal": False,
        "source_selection_changed": False,
        "external_data_accessed": False,
        "rtl_or_ppa_evaluated": False,
    }
    if receipt["policy"] != expected_policy:
        raise Campaign108Error("campaign receipt policy boundary differs")
    unsigned_receipt = dict(receipt)
    supplied = unsigned_receipt.pop("receipt_sha256", None)
    if supplied != canonical_sha256(unsigned_receipt):
        raise Campaign108Error("campaign receipt aggregate seal differs")

    bindings = receipt["bindings"]
    binding_fields = {
        "output_adapter_path", "output_adapter_sha256",
        "candidate_executable_path", "candidate_executable_sha256",
        "model_path", "model_sha256", "dependency_manifest_sha256",
        "candidate_config_sha256", "candidate_config_semantic_sha256",
        "cncp_sha256", "cncp_semantic_sha256", "generator_evidence_sha256",
        "candidate_output_sha256", "campaign_runner_sha256", "replay_sha256",
        "screen_result_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise Campaign108Error("campaign digest bindings differ")
    for field in binding_fields - {
        "output_adapter_path", "candidate_executable_path", "model_path"
    }:
        _sha256(bindings[field], "campaign %s" % field)
    if (
        bindings["output_adapter_path"]
        != _relative_authority_path(spec.output_adapter_path)
        or bindings["output_adapter_sha256"] != spec.output_adapter_sha256
        or bindings["candidate_executable_path"]
        != _relative_authority_path(spec.candidate_executable_path)
        or bindings["candidate_executable_sha256"]
        != spec.candidate_executable_sha256
        or bindings["model_path"] != _relative_authority_path(spec.model_path)
        or bindings["model_sha256"] != spec.model_sha256
        or bindings["candidate_config_sha256"] != spec.config_sha256
        or bindings["campaign_runner_sha256"]
        != _sha256_bytes(_read_bytes(Path(__file__), "campaign runner"))
    ):
        raise Campaign108Error("campaign frozen authority binding differs")
    _verify_authorities(spec)

    artifacts = receipt["artifacts"]
    artifact_names = {
        "attempt", "dependency_manifest", "generator_evidence",
        "candidate_output", "replay", "screen_result",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != artifact_names:
        raise Campaign108Error("campaign artifact index differs")
    root = Path(campaign_directory)
    expected_paths = _artifact_paths(root, str(receipt["candidate_id"]))
    decoded = {}
    for name, identity in artifacts.items():
        if not isinstance(identity, Mapping) or set(identity) != {
            "path", "size_bytes", "sha256", "semantic_sha256"
        }:
            raise Campaign108Error("campaign artifact identity differs")
        relative = identity["path"]
        if type(relative) is not str or relative != expected_paths[name].name:
            raise Campaign108Error("campaign artifact path differs")
        payload = _read_bytes(root / relative, "%s artifact" % name)
        if (
            len(payload) != identity["size_bytes"]
            or _sha256_bytes(payload) != identity["sha256"]
        ):
            raise Campaign108Error("campaign artifact bytes differ")
        decoded[name] = _read_json_bytes(payload, "%s artifact" % name)

    attempt = decoded["attempt"]
    attempt_unsigned = dict(attempt)
    attempt_digest = attempt_unsigned.pop("attempt_sha256", None)
    attempt_fields = {
        "schema", "candidate_id", "model_candidate_id", "attempt_index",
        "output_adapter_path", "output_adapter_sha256",
        "candidate_executable_path", "candidate_executable_sha256",
        "model_path", "model_sha256", "dependency_manifest_sha256",
        "candidate_config_sha256", "candidate_config_semantic_sha256",
        "cncp_sha256", "cncp_semantic_sha256", "campaign_runner_sha256",
        "adapter_execution_count", "verification_replay_count",
        "verification_replay_is_tuning",
        "retry_allowed", "tuning_allowed", "attempt_sha256",
    }
    if (
        set(attempt) != attempt_fields
        or attempt.get("schema") != ATTEMPT_SCHEMA
        or attempt.get("candidate_id") != receipt["candidate_id"]
        or attempt.get("model_candidate_id") != spec.model_candidate_id
        or type(attempt.get("attempt_index")) is not int
        or attempt.get("attempt_index") != 1
        or attempt.get("retry_allowed") is not False
        or attempt.get("tuning_allowed") is not False
        or attempt.get("adapter_execution_count") != 2
        or attempt.get("verification_replay_count") != 1
        or attempt.get("verification_replay_is_tuning") is not False
        or attempt_digest != canonical_sha256(attempt_unsigned)
        or attempt_digest != receipt["attempt_sha256"]
        or attempt_digest != artifacts["attempt"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign attempt seal differs")
    for field in binding_fields - {
        "generator_evidence_sha256", "candidate_output_sha256",
        "replay_sha256", "screen_result_sha256",
    }:
        if attempt.get(field) != bindings[field]:
            raise Campaign108Error("campaign attempt digest binding differs")

    candidate_output = decoded["candidate_output"]
    output_unsigned = dict(candidate_output)
    output_digest = output_unsigned.pop("aggregate_sha256", None)
    if (
        set(candidate_output) != {
            "schema", "candidate_id", "adapter_aggregate_sha256",
            "neutral_input_sha256", "candidate_executable_sha256",
            "candidate_config_sha256", "windows", "aggregate_sha256",
        }
        or candidate_output.get("schema") != screen108.CANDIDATE_OUTPUT_SCHEMA
        or candidate_output.get("candidate_id") != receipt["candidate_id"]
        or candidate_output.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or candidate_output.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or output_digest != canonical_sha256(output_unsigned)
        or output_digest != bindings["candidate_output_sha256"]
        or output_digest != artifacts["candidate_output"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign candidate output seal differs")

    evidence = decoded["generator_evidence"]
    evidence_unsigned = dict(evidence)
    evidence_digest = evidence_unsigned.pop("aggregate_sha256", None)
    windows = evidence.get("windows")
    dispatch = windows[0] if isinstance(windows, list) and len(windows) == 1 else None
    output_windows = candidate_output.get("windows")
    if not isinstance(output_windows, list):
        raise Campaign108Error("campaign candidate output windows differ")
    output_window_ids = [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in output_windows
    ]
    if (
        set(evidence) != {
            "schema", "candidate_id", "model_candidate_id", "windows",
            "aggregate_sha256",
        }
        or evidence.get("schema") != GENERATOR_EVIDENCE_SCHEMA
        or evidence.get("candidate_id") != receipt["candidate_id"]
        or evidence.get("model_candidate_id") != spec.model_candidate_id
        or not isinstance(dispatch, Mapping)
        or set(dispatch) != {
            "output_adapter_path", "output_adapter_sha256",
            "candidate_executable_path", "candidate_executable_sha256",
            "model_path", "model_sha256", "dependency_manifest_sha256",
            "candidate_config_sha256", "campaign_runner_sha256",
            "candidate_output_sha256",
            "ordered_window_ids", "ordered_window_ids_sha256",
        }
        or dispatch.get("output_adapter_path") != bindings["output_adapter_path"]
        or dispatch.get("output_adapter_sha256") != bindings["output_adapter_sha256"]
        or dispatch.get("candidate_executable_path")
        != bindings["candidate_executable_path"]
        or dispatch.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or dispatch.get("model_path") != bindings["model_path"]
        or dispatch.get("model_sha256") != bindings["model_sha256"]
        or dispatch.get("dependency_manifest_sha256")
        != bindings["dependency_manifest_sha256"]
        or dispatch.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or dispatch.get("campaign_runner_sha256")
        != bindings["campaign_runner_sha256"]
        or dispatch.get("candidate_output_sha256") != output_digest
        or dispatch.get("ordered_window_ids") != output_window_ids
        or dispatch.get("ordered_window_ids_sha256")
        != canonical_sha256(output_window_ids)
        or evidence_digest != canonical_sha256(evidence_unsigned)
        or evidence_digest != bindings["generator_evidence_sha256"]
        or evidence_digest != artifacts["generator_evidence"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign adapter dispatch seal differs")

    dependency_manifest = decoded["dependency_manifest"]
    expected_manifest, unused_snapshots = _dependency_manifest(
        str(receipt["candidate_id"])
    )
    del unused_snapshots
    if (
        dependency_manifest != expected_manifest
        or dependency_manifest.get("manifest_sha256")
        != bindings["dependency_manifest_sha256"]
        or dependency_manifest.get("manifest_sha256")
        != artifacts["dependency_manifest"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign dependency manifest differs")

    replay = decoded["replay"]
    replay_unsigned = dict(replay)
    replay_digest = replay_unsigned.pop("replay_sha256", None)
    if (
        set(replay) != {
            "schema", "candidate_id", "mode", "neutral_input_sha256",
            "adapter_execution_count", "production_output_bytes_sha256",
            "replay_output_bytes_sha256", "production_evidence_bytes_sha256",
            "replay_evidence_bytes_sha256", "candidate_output_byte_identical",
            "evidence_byte_identical", "replay_used_for_tuning",
            "replay_output_used_for_screen", "replay_sha256",
        }
        or replay.get("schema") != REPLAY_SCHEMA
        or replay.get("candidate_id") != receipt["candidate_id"]
        or replay.get("mode") != "verifier_replay"
        or replay.get("neutral_input_sha256")
        != candidate_output.get("neutral_input_sha256")
        or replay.get("adapter_execution_count") != 2
        or replay.get("candidate_output_byte_identical") is not True
        or replay.get("evidence_byte_identical") is not True
        or replay.get("replay_used_for_tuning") is not False
        or replay.get("replay_output_used_for_screen") is not False
        or replay.get("production_output_bytes_sha256")
        != artifacts["candidate_output"]["sha256"]
        or replay.get("replay_output_bytes_sha256")
        != artifacts["candidate_output"]["sha256"]
        or replay.get("production_evidence_bytes_sha256")
        != artifacts["generator_evidence"]["sha256"]
        or replay.get("replay_evidence_bytes_sha256")
        != artifacts["generator_evidence"]["sha256"]
        or replay_digest != canonical_sha256(replay_unsigned)
        or replay_digest != bindings["replay_sha256"]
        or replay_digest != artifacts["replay"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign verifier replay seal differs")

    screen_result = decoded["screen_result"]
    screen_digest = screen108.verify_screen108_result_envelope(screen_result)
    provenance = screen_result.get("provenance")
    if (
        screen_result.get("candidate_id") != receipt["candidate_id"]
        or canonical_sha256(screen_result.get("cncp"))
        != bindings["cncp_semantic_sha256"]
        or not isinstance(provenance, Mapping)
        or provenance.get("candidate_output_sha256") != output_digest
        or provenance.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or provenance.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or screen_digest != bindings["screen_result_sha256"]
        or screen_digest != artifacts["screen_result"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign screen result binding differs")
    return str(supplied)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch exactly one frozen Stage3 adapter to locked NEW108"
    )
    parser.add_argument("--candidate-id", choices=FROZEN_CANDIDATE_IDS, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cncp", type=Path, required=True)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = run_campaign108(
        args.candidate_id,
        args.dataset_dir,
        args.config,
        args.cncp,
        args.campaign_dir,
    )
    print("receipt_sha256=%s" % receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Campaign108Error as exc:
        print("campaign108: %s" % exc, file=sys.stderr)
        sys.exit(2)


__all__ = (
    "ATTEMPT_SCHEMA",
    "CAMPAIGN_SCHEMA",
    "DEPENDENCY_MANIFEST_SCHEMA",
    "DSPB_ID",
    "FROZEN_CANDIDATE_IDS",
    "GENERATOR_EVIDENCE_SCHEMA",
    "NeutralAdapterView",
    "NeutralBaselineView",
    "RG3_ID",
    "REPLAY_SCHEMA",
    "SO3_PLL_ID",
    "Campaign108Error",
    "frozen_candidate_config",
    "frozen_candidate_config_bytes",
    "run_campaign108",
    "verify_campaign108_receipt",
)
