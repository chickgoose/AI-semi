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


CAMPAIGN_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_receipt/v2"
ATTEMPT_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_attempt/v2"
GENERATOR_EVIDENCE_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign108_adapter_dispatch/v2"
)

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
    },
}


class Campaign108Error(ValueError):
    """A frozen authority, artifact, or single-attempt invariant failed."""


Adapter = Callable[
    [New108AdapterBundle, CAVRegistryEvaluation], Mapping[str, object]
]


@dataclass(frozen=True)
class FrozenCandidate:
    candidate_id: str
    model_candidate_id: str
    output_adapter_path: Path
    output_adapter_sha256: str
    candidate_executable_path: Path
    candidate_executable_sha256: str
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


def _dispatch_rg3(
    bundle: New108AdapterBundle, baseline: CAVRegistryEvaluation
) -> Mapping[str, object]:
    del baseline
    return rg3_output.generate_locked_rg3_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_dspb(
    bundle: New108AdapterBundle, baseline: CAVRegistryEvaluation
) -> Mapping[str, object]:
    del baseline
    return dspb_output.generate_dspb_candidate_output(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        str(bundle.provenance_seal["aggregate_sha256"]),
    )


def _dispatch_pll(
    bundle: New108AdapterBundle, baseline: CAVRegistryEvaluation
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
) -> Tuple[bytes, bytes]:
    expected = _EXPECTED_AUTHORITIES[spec.candidate_id]
    if (
        _relative_authority_path(spec.output_adapter_path)
        != expected["output_adapter_path"]
        or _relative_authority_path(spec.candidate_executable_path)
        != expected["candidate_executable_path"]
    ):
        raise Campaign108Error("candidate authority path differs from the frozen ID")
    adapter_bytes = _read_bytes(spec.output_adapter_path, "output adapter")
    executable_bytes = _read_bytes(
        spec.candidate_executable_path, "candidate executable"
    )
    if _sha256_bytes(adapter_bytes) != spec.output_adapter_sha256:
        raise Campaign108Error("output adapter differs from the frozen ID")
    if _sha256_bytes(executable_bytes) != spec.candidate_executable_sha256:
        raise Campaign108Error("candidate executable differs from the frozen ID")
    if _sha256_bytes(spec.config_bytes) != spec.config_sha256:
        raise Campaign108Error("adapter-owned config differs from the frozen ID")
    return adapter_bytes, executable_bytes


def _validate_adapter_output(
    value: object,
    spec: FrozenCandidate,
    bundle: New108AdapterBundle,
    baseline: CAVRegistryEvaluation,
) -> Mapping[str, object]:
    fields = {
        "schema", "candidate_id", "adapter_aggregate_sha256",
        "neutral_input_sha256", "candidate_executable_sha256",
        "candidate_config_sha256", "windows", "aggregate_sha256",
    }
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
) -> Mapping[str, object]:
    dispatch = {
        "output_adapter_path": _relative_authority_path(spec.output_adapter_path),
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": _relative_authority_path(
            spec.candidate_executable_path
        ),
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "candidate_config_sha256": spec.config_sha256,
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
    """Dispatch one frozen output adapter exactly once and screen its seal."""

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
    adapter_bytes, executable_bytes = _verify_authorities(spec)

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
    attempt_body = {
        "schema": ATTEMPT_SCHEMA,
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_index": 1,
        "output_adapter_path": adapter_relative,
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": executable_relative,
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "candidate_config_sha256": config_sha256,
        "candidate_config_semantic_sha256": config_semantic_sha256,
        "cncp_sha256": cncp_sha256,
        "cncp_semantic_sha256": cncp_semantic_sha256,
        "campaign_runner_sha256": _sha256_bytes(
            _read_bytes(Path(__file__), "campaign runner")
        ),
        "retry_allowed": False,
        "tuning_allowed": False,
    }
    attempt = dict(attempt_body, attempt_sha256=canonical_sha256(attempt_body))
    attempt_bytes = _json_bytes(attempt)
    _exclusive_write(paths["attempt"], attempt_bytes, "campaign attempt marker")

    # The bundle contains a selector-label sidecar, but neither this projection
    # nor any output-adapter API reads or receives it.  screen108 is called only
    # after the returned candidate envelope has been sealed and persisted.
    bundle = build_locked_new108_adapter(Path(dataset_directory))
    if type(bundle) is not New108AdapterBundle:
        raise Campaign108Error("locked adapter returned the wrong bundle type")
    baseline = evaluate_current_cav_registry(
        bundle.neutral_registry, bundle.event_streams, bundle.pose_streams
    )

    # Exactly one adapter call.  All event-before-pose ordering and causal
    # replay semantics are owned by the independently tested adapter.
    generated = spec.adapter(bundle, baseline)
    candidate_output = _validate_adapter_output(generated, spec, bundle, baseline)
    output_digest = str(candidate_output["aggregate_sha256"])
    candidate_output_bytes = _json_bytes(candidate_output)
    _exclusive_write(paths["candidate_output"], candidate_output_bytes, "candidate output")

    window_ids = [row.window_id for row in bundle.neutral_registry]
    evidence = _dispatch_evidence(spec, output_digest, window_ids)
    evidence_digest = str(evidence["aggregate_sha256"])
    evidence_bytes = _json_bytes(evidence)
    _exclusive_write(paths["generator_evidence"], evidence_bytes, "adapter dispatch evidence")

    _check_unchanged(config_file, config_bytes, "candidate config")
    _check_unchanged(cncp_file, cncp_bytes, "CNCP")
    _check_unchanged(spec.output_adapter_path, adapter_bytes, "output adapter")
    _check_unchanged(
        spec.candidate_executable_path, executable_bytes, "candidate executable"
    )
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

    artifacts = {
        "attempt": _artifact(paths["attempt"], attempt_bytes, attempt["attempt_sha256"]),
        "generator_evidence": _artifact(
            paths["generator_evidence"], evidence_bytes, evidence_digest
        ),
        "candidate_output": _artifact(
            paths["candidate_output"], candidate_output_bytes, output_digest
        ),
        "screen_result": _artifact(paths["screen_result"], screen_bytes, screen_digest),
    }
    bindings = {
        "output_adapter_path": adapter_relative,
        "output_adapter_sha256": spec.output_adapter_sha256,
        "candidate_executable_path": executable_relative,
        "candidate_executable_sha256": spec.candidate_executable_sha256,
        "candidate_config_sha256": config_sha256,
        "candidate_config_semantic_sha256": config_semantic_sha256,
        "cncp_sha256": cncp_sha256,
        "cncp_semantic_sha256": cncp_semantic_sha256,
        "generator_evidence_sha256": evidence_digest,
        "candidate_output_sha256": output_digest,
        "screen_result_sha256": screen_digest,
    }
    receipt_body = {
        "schema": CAMPAIGN_SCHEMA,
        "status": "SCREEN108_SINGLE_ATTEMPT_COMPLETE",
        "candidate_id": candidate_id,
        "model_candidate_id": spec.model_candidate_id,
        "attempt_sha256": attempt["attempt_sha256"],
        "bindings": bindings,
        "artifacts": artifacts,
        "policy": {
            "attempt_count": 1,
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
        or receipt["status"] != "SCREEN108_SINGLE_ATTEMPT_COMPLETE"
    ):
        raise Campaign108Error("campaign receipt schema or status differs")
    spec = _candidate(receipt["candidate_id"])
    if receipt["model_candidate_id"] != spec.model_candidate_id:
        raise Campaign108Error("campaign model identity differs")
    expected_policy = {
        "attempt_count": 1,
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
        "candidate_config_sha256", "candidate_config_semantic_sha256",
        "cncp_sha256", "cncp_semantic_sha256", "generator_evidence_sha256",
        "candidate_output_sha256", "screen_result_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise Campaign108Error("campaign digest bindings differ")
    for field in binding_fields - {"output_adapter_path", "candidate_executable_path"}:
        _sha256(bindings[field], "campaign %s" % field)
    if (
        bindings["output_adapter_path"]
        != _relative_authority_path(spec.output_adapter_path)
        or bindings["output_adapter_sha256"] != spec.output_adapter_sha256
        or bindings["candidate_executable_path"]
        != _relative_authority_path(spec.candidate_executable_path)
        or bindings["candidate_executable_sha256"]
        != spec.candidate_executable_sha256
        or bindings["candidate_config_sha256"] != spec.config_sha256
    ):
        raise Campaign108Error("campaign frozen authority binding differs")
    _verify_authorities(spec)

    artifacts = receipt["artifacts"]
    artifact_names = {
        "attempt", "generator_evidence", "candidate_output", "screen_result"
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
        "candidate_config_sha256", "candidate_config_semantic_sha256",
        "cncp_sha256", "cncp_semantic_sha256", "campaign_runner_sha256",
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
        or attempt_digest != canonical_sha256(attempt_unsigned)
        or attempt_digest != receipt["attempt_sha256"]
        or attempt_digest != artifacts["attempt"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign attempt seal differs")
    for field in binding_fields - {
        "generator_evidence_sha256", "candidate_output_sha256",
        "screen_result_sha256",
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
            "candidate_config_sha256", "candidate_output_sha256",
            "ordered_window_ids", "ordered_window_ids_sha256",
        }
        or dispatch.get("output_adapter_path") != bindings["output_adapter_path"]
        or dispatch.get("output_adapter_sha256") != bindings["output_adapter_sha256"]
        or dispatch.get("candidate_executable_path")
        != bindings["candidate_executable_path"]
        or dispatch.get("candidate_executable_sha256")
        != bindings["candidate_executable_sha256"]
        or dispatch.get("candidate_config_sha256")
        != bindings["candidate_config_sha256"]
        or dispatch.get("candidate_output_sha256") != output_digest
        or dispatch.get("ordered_window_ids") != output_window_ids
        or dispatch.get("ordered_window_ids_sha256")
        != canonical_sha256(output_window_ids)
        or evidence_digest != canonical_sha256(evidence_unsigned)
        or evidence_digest != bindings["generator_evidence_sha256"]
        or evidence_digest != artifacts["generator_evidence"]["semantic_sha256"]
    ):
        raise Campaign108Error("campaign adapter dispatch seal differs")

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
    "DSPB_ID",
    "FROZEN_CANDIDATE_IDS",
    "GENERATOR_EVIDENCE_SCHEMA",
    "RG3_ID",
    "SO3_PLL_ID",
    "Campaign108Error",
    "frozen_candidate_config",
    "frozen_candidate_config_bytes",
    "run_campaign108",
    "verify_campaign108_receipt",
)
