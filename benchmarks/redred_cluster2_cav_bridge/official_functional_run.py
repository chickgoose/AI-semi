"""Official, score-free Cluster2 CAV functional assay runner.

The output is a small canonical JSON receipt.  Dataset locations are CLI-only
inputs and are deliberately never serialized; every recorded path is a stable
repository- or dataset-relative authority label.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_assay.source import OFFICIAL_SOURCE_PINS

from .contract import canonical_json_bytes
from .functional_assay import (
    VIEW_ORDER,
    run_functional_assay,
    validate_functional_assay_result,
)
from .functional_source import build_official_uzh_functional_source
from .native_outcome_bundle import (
    SEALED_BUNDLE_RELATIVE_PATH,
    SEALED_BUNDLE_SHA256,
    SEALED_RECEIPT_RELATIVE_PATH,
    SEALED_RECEIPT_SHA256,
    load_abaa094_native_outcomes,
)
from .transport_time import TRANSPORT_TIME_SEMANTICS
from .world_grid import COORDINATE_CONVENTION


SCHEMA = "redred.cluster2_cav_bridge.official_uzh_functional_result/v1"
SEAL_ALGORITHM = "SHA256_CANONICAL_JSON_EXCLUDING_SEAL"
EXPECTED_EVENT_COUNT = 8_503
EXPECTED_POSE_COUNT = 11_883
EXPECTED_CAUSAL_CAV_COUNT = 8_420
EXPECTED_ZOH_COUNT = 0
EXPECTED_BYPASS_COUNT = 83
EXPECTED_GROUNDTRUTH_SIZE = 1_379_205
EXPECTED_CALIBRATION_SIZE = 128
CYCLEMASK_RELATIVE_PATH = (
    "common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt"
)
CYCLEMASK_LF_SHA256 = (
    "850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea"
)
CYCLEMASK_CRLF_SHA256 = (
    "a50866f95430e3fe8d8af775c2e9692353e1e6bc9a1ecfedfed620143be48313"
)
EXPECTED_RESULT_DIGESTS = {
    "join_identity_sha256": (
        "bfbd23b607cc7d68371133e7d67da43c2302641391b4cdeac572013eaab256b2"
    ),
    "geometry_sha256": (
        "3f6b09f3208582907b588ad679bd60871c694ec31eff46423b0240ceb2f15747"
    ),
    "retire_sidecar_sha256": (
        "c29d9b980674da62d48e3a4cb0dc26618d08a3658997a7a5e90eb15ef81b6897"
    ),
    "world_grid_sha256": (
        "f5cb124031b2a343b55a85f92902bd8b764bc865298d9de58ee86f60e49048e0"
    ),
}
CORE_CODE_PATHS = (
    "benchmarks/redred_cluster2_cav_bridge/official_functional_run.py",
    "benchmarks/redred_cluster2_cav_bridge/functional_source.py",
    "benchmarks/redred_cluster2_cav_bridge/native_outcome_bundle.py",
    "benchmarks/redred_cluster2_cav_bridge/functional_assay.py",
    "benchmarks/redred_cluster2_cav_bridge/world_grid.py",
    "benchmarks/redred_cluster2_cav_bridge/__init__.py",
    "benchmarks/redred_cluster2_cav_bridge/cav_adapter.py",
    "benchmarks/redred_cluster2_cav_bridge/contract.py",
    "benchmarks/redred_cluster2_cav_bridge/native_ledger.py",
    "benchmarks/redred_cluster2_cav_bridge/source_crosswalk.py",
    "benchmarks/redred_cluster2_cav_bridge/transport_time.py",
    "benchmarks/redred_mc_wtb_causal_reference/__init__.py",
    "benchmarks/redred_mc_wtb_causal_reference/development.py",
    "benchmarks/redred_mc_wtb_causal_reference/reference.py",
    "benchmarks/redred_mc_wtb_causal_reference/routing.py",
    "benchmarks/redred_mc_wtb_motion_qualification/__init__.py",
    "benchmarks/redred_mc_wtb_motion_qualification/controller.py",
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_stage4_assay/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_assay/generator.py",
    "benchmarks/redred_mc_wtb_stage4_assay/source.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOP_FIELDS = frozenset((
    "schema", "status", "input_authority", "population", "latency",
    "world_grid", "digests", "three_view_equality", "claim_scope", "seal",
))


class OfficialFunctionalRunError(ValueError):
    """An official input, replay result, authority, or seal is inconsistent."""


def _fail(message: str) -> None:
    raise OfficialFunctionalRunError(message)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_json_tree(value: object, where: str = "summary") -> None:
    """Reject subclasses and every scalar kind absent from this receipt."""

    if type(value) is dict:
        for key, child in value.items():  # type: ignore[union-attr]
            if type(key) is not str:
                _fail("%s object keys must be exact str" % where)
            _exact_json_tree(child, "%s.%s" % (where, key))
        return
    if type(value) is list:
        for index, child in enumerate(value):  # type: ignore[union-attr]
            _exact_json_tree(child, "%s[%d]" % (where, index))
        return
    if type(value) not in (str, int, bool):
        _fail("%s has a non-exact or unsupported JSON type" % where)


def _typed_equal(value: object, expected: object, where: str) -> None:
    """Compare fixed evidence without Python's bool/int or int/float equality."""

    if type(value) is not type(expected):
        _fail("%s type differs" % where)
    if type(expected) is dict:
        if frozenset(value) != frozenset(expected):  # type: ignore[arg-type]
            _fail("%s fields differ" % where)
        for key, expected_child in expected.items():  # type: ignore[union-attr]
            _typed_equal(value[key], expected_child, "%s.%s" % (where, key))  # type: ignore[index]
    elif type(expected) is list:
        if len(value) != len(expected):  # type: ignore[arg-type]
            _fail("%s length differs" % where)
        for index, expected_child in enumerate(expected):  # type: ignore[union-attr]
            _typed_equal(value[index], expected_child, "%s[%d]" % (where, index))  # type: ignore[index]
    elif value != expected:
        _fail("%s value differs" % where)


def _relative_path(value: object, where: str) -> str:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        _fail("%s must be a non-empty relative POSIX path" % where)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        _fail("%s must be a normalized relative POSIX path" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("%s must be a lowercase full SHA-256" % where)
    return value


def _file_identity(value: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _stable_file_authority(path: Path, label: str) -> Dict[str, object]:
    """Hash one non-symlink regular file and detect replacement during read."""

    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("%s is not a regular non-symlink file" % label)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(opened) != _file_identity(before):
                _fail("%s changed while opening" % label)
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                size += len(block)
            after_open = os.fstat(stream.fileno())
        after = path.lstat()
    except OfficialFunctionalRunError:
        raise
    except OSError as error:
        raise OfficialFunctionalRunError("cannot read %s" % label) from error
    if (
        _file_identity(before) != _file_identity(after_open)
        or _file_identity(before) != _file_identity(after)
        or size != before.st_size
    ):
        _fail("%s changed during authority hashing" % label)
    return {"path": label, "sha256": digest.hexdigest(), "size_bytes": size}


def _source_authorities(dataset_directory: Path) -> Sequence[Mapping[str, object]]:
    specifications = (
        ("events.txt", "official_uzh/shapes_rotation/events.txt",
         OFFICIAL_SOURCE_PINS.events_sha256, OFFICIAL_SOURCE_PINS.events_size_bytes),
        ("groundtruth.txt", "official_uzh/shapes_rotation/groundtruth.txt",
         OFFICIAL_SOURCE_PINS.groundtruth_sha256, EXPECTED_GROUNDTRUTH_SIZE),
        ("calib.txt", "official_uzh/shapes_rotation/calib.txt",
         OFFICIAL_SOURCE_PINS.calibration_sha256, EXPECTED_CALIBRATION_SIZE),
    )
    rows = []
    for filename, label, expected_sha, expected_size in specifications:
        row = _stable_file_authority(dataset_directory / filename, label)
        if row["sha256"] != expected_sha or row["size_bytes"] != expected_size:
            _fail("%s differs from official SHA/size authority" % filename)
        rows.append(row)
    return rows


def _cyclemask_authority(cyclemask_path: Path) -> Mapping[str, object]:
    row = _stable_file_authority(cyclemask_path, CYCLEMASK_RELATIVE_PATH)
    try:
        payload = cyclemask_path.read_bytes()
    except OSError as error:
        raise OfficialFunctionalRunError("cannot re-read cyclemask") from error
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        _fail("cyclemask changed during semantic normalization")
    if b"\r" in payload.replace(b"\r\n", b""):
        _fail("cyclemask contains a bare or mixed carriage return")
    if b"\r\n" in payload:
        if b"\n" in payload.replace(b"\r\n", b""):
            _fail("cyclemask mixes LF and CRLF")
        line_endings = "CRLF"
        expected_raw = CYCLEMASK_CRLF_SHA256
        semantic = payload.replace(b"\r\n", b"\n")
    else:
        line_endings = "LF"
        expected_raw = CYCLEMASK_LF_SHA256
        semantic = payload
    if row["sha256"] != expected_raw:
        _fail("cyclemask raw encoding differs from the official authority")
    semantic_sha = hashlib.sha256(semantic).hexdigest()
    if semantic_sha != CYCLEMASK_LF_SHA256:
        _fail("cyclemask semantic LF digest differs")
    return {
        "path": CYCLEMASK_RELATIVE_PATH,
        "observed_line_endings": line_endings,
        "observed_raw_sha256": row["sha256"],
        "observed_size_bytes": row["size_bytes"],
        "canonical_semantic_lf_sha256": semantic_sha,
        "accepted_raw_encodings": [
            {"line_endings": "LF", "sha256": CYCLEMASK_LF_SHA256},
            {"line_endings": "CRLF", "sha256": CYCLEMASK_CRLF_SHA256},
        ],
    }


def _repository_authorities(repository_root: Path) -> Mapping[str, object]:
    receipt = _stable_file_authority(
        repository_root / SEALED_RECEIPT_RELATIVE_PATH,
        SEALED_RECEIPT_RELATIVE_PATH,
    )
    bundle = _stable_file_authority(
        repository_root / SEALED_BUNDLE_RELATIVE_PATH,
        SEALED_BUNDLE_RELATIVE_PATH,
    )
    if receipt["sha256"] != SEALED_RECEIPT_SHA256:
        _fail("sealed native receipt SHA-256 differs")
    if bundle["sha256"] != SEALED_BUNDLE_SHA256:
        _fail("sealed native bundle SHA-256 differs")
    code = []
    for relative in CORE_CODE_PATHS:
        code.append(_stable_file_authority(repository_root / relative, relative))
    return {"sealed_native_receipt": receipt, "sealed_native_bundle": bundle,
            "core_code": code}


def _summary_body(
    result: object,
    sources: Sequence[Mapping[str, object]],
    cyclemask: Mapping[str, object],
    repository: Mapping[str, object],
) -> Dict[str, object]:
    statistics = result.statistics  # type: ignore[attr-defined]
    views = result.views  # type: ignore[attr-defined]
    return {
        "schema": SCHEMA,
        "status": "COMPLETE_SCOPED_OBSERVATIONAL_AND_SOFTWARE_RESULT_WITH_HOLDS",
        "input_authority": {
            "official_sources": list(sources),
            "cyclemask": dict(cyclemask),
            "sealed_native_receipt": repository["sealed_native_receipt"],
            "sealed_native_bundle": repository["sealed_native_bundle"],
            "core_code": repository["core_code"],
        },
        "population": {
            "events": statistics.event_count,
            "poses": statistics.pose_count,
            "exact_native_join": statistics.exact_join_count,
            "decisions": statistics.decision_count,
            "causal_cav": dict(statistics.mode_counts)["causal_cav"],
            "zoh_fallback": dict(statistics.mode_counts)["zoh_fallback"],
            "sensor_fixed_bypass": dict(statistics.mode_counts)["sensor_fixed_bypass"],
            "native_overrun": 0,
        },
        "latency": {
            "semantics": statistics.transport_time_semantics,
            "native_clock_period_ps": 2_000,
            "histogram_cycles": [list(row) for row in statistics.latency_histogram],
            "event_count": statistics.event_count,
        },
        "world_grid": {
            "width": statistics.grid_width,
            "height": statistics.grid_height,
            "input_frame": "WORLD",
            "excluded_frame": "SENSOR_FIXED",
            "quantized_count": statistics.grid_quantized_count,
            "excluded_sensor_fixed_count": dict(statistics.frame_counts)["SENSOR_FIXED"],
            "unique_cell_count": statistics.grid_unique_count,
            "x_range_inclusive": [statistics.grid_x_min, statistics.grid_x_max],
            "y_range_inclusive": [statistics.grid_y_min, statistics.grid_y_max],
            "index_range_inclusive": [statistics.grid_index_min,
                                      statistics.grid_index_max],
            "coordinate_convention": statistics.coordinate_convention,
        },
        "digests": {
            "join_identity_sha256": statistics.join_identity_sha256,
            "geometry_sha256": statistics.geometry_sha256,
            "retire_sidecar_sha256": statistics.retire_sidecar_sha256,
            "world_grid_sha256": statistics.grid_sha256,
        },
        "three_view_equality": {
            "view_order": list(VIEW_ORDER),
            "geometry_sha256_by_view": [
                {"view": view.view_name, "sha256": view.geometry_sha256}
                for view in views
            ],
            "all_geometry_digests_equal": len({
                view.geometry_sha256 for view in views
            }) == 1,
            "shared_geometry_object": all(
                view.geometry is views[0].geometry for view in views
            ),
        },
        "claim_scope": {
            "actual_native_rtl_observation": (
                "PASS_SEALED_XCELIUM_NATIVE_OBSERVATION_ONLY"
            ),
            "software_cav_replay": (
                "PASS_SOFTWARE_CAV_FUNCTIONAL_REPLAY_ONLY_NOT_RTL"
            ),
            "world_functional_mapping": (
                "PASS_SOFTWARE_WORLD_RAY_GRID_MAPPING_ONLY_NOT_RTL"
            ),
            "latency_quality": (
                "HOLD_OBSERVATIONAL_LATENCY_SIDECAR_ONLY_NOT_PHYSICAL_REPLAY_OR_QUALITY"
            ),
            "wire_complete_cav_rtl": (
                "HOLD_WIRE_COMPLETE_CAV_RTL_NOT_IMPLEMENTED_OR_OBSERVED"
            ),
            "rtl_ppa": (
                "HOLD_CAV_WORLD_RTL_PPA_NOT_IMPLEMENTED_OR_EVALUATED"
            ),
        },
    }


def _sealed(body: Mapping[str, object]) -> Mapping[str, object]:
    result = dict(body)
    result["seal"] = {
        "algorithm": SEAL_ALGORITHM,
        "sha256": _canonical_sha256(body),
    }
    return result


def validate_official_functional_summary(
    value: object, repository_root: Optional[Path] = None
) -> Mapping[str, object]:
    """Validate the exact contract, seal, and current or supplied repo files."""

    _exact_json_tree(value)
    if type(value) is not dict or frozenset(value) != _TOP_FIELDS:
        _fail("summary top-level fields differ")
    summary = value
    if (
        summary["schema"] != SCHEMA
        or summary["status"]
        != "COMPLETE_SCOPED_OBSERVATIONAL_AND_SOFTWARE_RESULT_WITH_HOLDS"
    ):
        _fail("summary schema/status differs")
    population = summary["population"]
    expected_population = {
        "events": 8_503, "poses": 11_883, "exact_native_join": 8_503,
        "decisions": 8_503, "causal_cav": 8_420, "zoh_fallback": 0,
        "sensor_fixed_bypass": 83, "native_overrun": 0,
    }
    _typed_equal(population, expected_population, "official population")
    latency = summary["latency"]
    if type(latency) is not dict or frozenset(latency) != frozenset((
        "semantics", "native_clock_period_ps", "histogram_cycles", "event_count"
    )):
        _fail("latency fields differ")
    _typed_equal(latency, {
        "semantics": TRANSPORT_TIME_SEMANTICS,
        "native_clock_period_ps": 2_000,
        "histogram_cycles": [[1, 6_393], [2, 2_077], [3, 33]],
        "event_count": 8_503,
    }, "official latency evidence")
    grid = summary["world_grid"]
    if type(grid) is not dict or frozenset(grid) != frozenset((
        "width", "height", "input_frame", "excluded_frame", "quantized_count",
        "excluded_sensor_fixed_count", "unique_cell_count", "x_range_inclusive",
        "y_range_inclusive", "index_range_inclusive", "coordinate_convention",
    )):
        _fail("world-grid fields differ")
    _typed_equal(grid, {
        "width": 512,
        "height": 256,
        "input_frame": "WORLD",
        "excluded_frame": "SENSOR_FIXED",
        "quantized_count": 8_420,
        "excluded_sensor_fixed_count": 83,
        "unique_cell_count": 821,
        "x_range_inclusive": [238, 298],
        "y_range_inclusive": [93, 165],
        "index_range_inclusive": [47_876, 84_754],
        "coordinate_convention": COORDINATE_CONVENTION,
    }, "official world-grid evidence")
    digests = summary["digests"]
    if type(digests) is not dict or frozenset(digests) != frozenset((
        "join_identity_sha256", "geometry_sha256", "retire_sidecar_sha256",
        "world_grid_sha256",
    )):
        _fail("digest fields differ")
    for name, digest in digests.items():
        _sha256(digest, name)
    _typed_equal(digests, EXPECTED_RESULT_DIGESTS, "official result digests")
    equality = summary["three_view_equality"]
    if type(equality) is not dict or equality.get("view_order") != list(VIEW_ORDER):
        _fail("three-view order differs")
    rows = equality.get("geometry_sha256_by_view")
    if type(rows) is not list or len(rows) != 3:
        _fail("three-view digest rows differ")
    observed = []
    for index, row in enumerate(rows):
        if type(row) is not dict or frozenset(row) != frozenset(("view", "sha256")):
            _fail("three-view digest row fields differ")
        if row["view"] != VIEW_ORDER[index]:
            _fail("three-view digest order differs")
        observed.append(_sha256(row["sha256"], "view digest"))
    if (
        equality.get("all_geometry_digests_equal") is not True
        or equality.get("shared_geometry_object") is not True
        or len(set(observed)) != 1
        or observed[0] != digests["geometry_sha256"]
        or frozenset(equality) != frozenset((
            "view_order", "geometry_sha256_by_view",
            "all_geometry_digests_equal", "shared_geometry_object",
        ))
    ):
        _fail("three-view geometry equality differs")
    claims = summary["claim_scope"]
    _typed_equal(claims, {
        "actual_native_rtl_observation": (
            "PASS_SEALED_XCELIUM_NATIVE_OBSERVATION_ONLY"
        ),
        "software_cav_replay": (
            "PASS_SOFTWARE_CAV_FUNCTIONAL_REPLAY_ONLY_NOT_RTL"
        ),
        "world_functional_mapping": (
            "PASS_SOFTWARE_WORLD_RAY_GRID_MAPPING_ONLY_NOT_RTL"
        ),
        "latency_quality": (
            "HOLD_OBSERVATIONAL_LATENCY_SIDECAR_ONLY_NOT_PHYSICAL_REPLAY_OR_QUALITY"
        ),
        "wire_complete_cav_rtl": (
            "HOLD_WIRE_COMPLETE_CAV_RTL_NOT_IMPLEMENTED_OR_OBSERVED"
        ),
        "rtl_ppa": "HOLD_CAV_WORLD_RTL_PPA_NOT_IMPLEMENTED_OR_EVALUATED",
    }, "claim scope")
    authority = summary["input_authority"]
    if type(authority) is not dict or frozenset(authority) != frozenset((
        "official_sources", "cyclemask", "sealed_native_receipt",
        "sealed_native_bundle", "core_code",
    )):
        _fail("input authority fields differ")
    sources = authority["official_sources"]
    if type(sources) is not list or len(sources) != 3:
        _fail("official source authorities differ")
    expected_sources = (
        ("official_uzh/shapes_rotation/events.txt",
         OFFICIAL_SOURCE_PINS.events_sha256, OFFICIAL_SOURCE_PINS.events_size_bytes),
        ("official_uzh/shapes_rotation/groundtruth.txt",
         OFFICIAL_SOURCE_PINS.groundtruth_sha256, EXPECTED_GROUNDTRUTH_SIZE),
        ("official_uzh/shapes_rotation/calib.txt",
         OFFICIAL_SOURCE_PINS.calibration_sha256, EXPECTED_CALIBRATION_SIZE),
    )
    for row, expected in zip(sources, expected_sources):
        if type(row) is not dict or frozenset(row) != frozenset((
            "path", "sha256", "size_bytes"
        )):
            _fail("official source authority row differs")
        if (_relative_path(row["path"], "source path"),
                _sha256(row["sha256"], "source SHA"), row["size_bytes"]) != expected:
            _fail("official source authority differs")
    cyclemask = authority["cyclemask"]
    if type(cyclemask) is not dict or _relative_path(
        cyclemask.get("path"), "cyclemask path"
    ) != CYCLEMASK_RELATIVE_PATH:
        _fail("cyclemask authority differs")
    accepted = cyclemask.get("accepted_raw_encodings")
    if accepted != [
        {"line_endings": "LF", "sha256": CYCLEMASK_LF_SHA256},
        {"line_endings": "CRLF", "sha256": CYCLEMASK_CRLF_SHA256},
    ]:
        _fail("cyclemask accepted encodings differ")
    observed_ending = cyclemask.get("observed_line_endings")
    observed_sha = cyclemask.get("observed_raw_sha256")
    if (
        observed_ending not in ("LF", "CRLF")
        or observed_sha != (CYCLEMASK_LF_SHA256 if observed_ending == "LF"
                            else CYCLEMASK_CRLF_SHA256)
        or cyclemask.get("canonical_semantic_lf_sha256") != CYCLEMASK_LF_SHA256
        or type(cyclemask.get("observed_size_bytes")) is not int
        or cyclemask.get("observed_size_bytes") <= 0
        or frozenset(cyclemask) != frozenset((
            "path", "observed_line_endings", "observed_raw_sha256",
            "observed_size_bytes", "canonical_semantic_lf_sha256",
            "accepted_raw_encodings",
        ))
    ):
        _fail("cyclemask observed authority differs")
    for name, expected_path, expected_sha in (
        ("sealed_native_receipt", SEALED_RECEIPT_RELATIVE_PATH,
         SEALED_RECEIPT_SHA256),
        ("sealed_native_bundle", SEALED_BUNDLE_RELATIVE_PATH,
         SEALED_BUNDLE_SHA256),
    ):
        row = authority[name]
        if type(row) is not dict or frozenset(row) != frozenset((
            "path", "sha256", "size_bytes"
        )) or _relative_path(row["path"], name) != expected_path or _sha256(
            row["sha256"], name
        ) != expected_sha or type(row["size_bytes"]) is not int or row["size_bytes"] <= 0:
            _fail("%s authority differs" % name)
    code = authority["core_code"]
    if type(code) is not list or len(code) != len(CORE_CODE_PATHS):
        _fail("core code authority count differs")
    for row, expected_path in zip(code, CORE_CODE_PATHS):
        if type(row) is not dict or frozenset(row) != frozenset((
            "path", "sha256", "size_bytes"
        )) or _relative_path(row["path"], "code path") != expected_path or type(
            row["size_bytes"]
        ) is not int or row["size_bytes"] <= 0:
            _fail("core code authority differs")
        _sha256(row["sha256"], "core code SHA")
    seal = summary["seal"]
    if type(seal) is not dict or seal.get("algorithm") != SEAL_ALGORITHM or frozenset(
        seal
    ) != frozenset(("algorithm", "sha256")):
        _fail("summary seal fields differ")
    supplied = _sha256(seal.get("sha256"), "summary seal")
    body = dict(summary)
    body.pop("seal")
    if not hmac.compare_digest(supplied, _canonical_sha256(body)):
        _fail("summary seal differs")
    root = Path(__file__).parents[2] if repository_root is None else repository_root
    if not isinstance(root, Path):
        _fail("repository root must be a pathlib.Path")
    actual_repository = _repository_authorities(root)
    expected_repository = {
        "sealed_native_receipt": authority["sealed_native_receipt"],
        "sealed_native_bundle": authority["sealed_native_bundle"],
        "core_code": authority["core_code"],
    }
    if actual_repository != expected_repository:
        _fail("recorded repository authorities differ from actual files")
    return summary


def build_official_functional_summary(
    dataset_directory: Path,
    cyclemask_path: Path,
    repository_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Run the mandatory four-stage pipeline and return its sealed summary."""

    if not isinstance(dataset_directory, Path) or not isinstance(cyclemask_path, Path):
        _fail("dataset and cyclemask inputs must be pathlib.Path values")
    root = Path(__file__).parents[2] if repository_root is None else repository_root
    if not isinstance(root, Path):
        _fail("repository root must be a pathlib.Path")

    # This order is intentional and is covered by a mutation-sensitive test.
    source = build_official_uzh_functional_source(dataset_directory, cyclemask_path)
    outcomes = load_abaa094_native_outcomes(root)
    result = run_functional_assay(source, outcomes)
    checked = validate_functional_assay_result(result, source, outcomes)
    if checked is not result:
        _fail("functional result validator did not retain the exact result")

    sources = _source_authorities(dataset_directory)
    cyclemask = _cyclemask_authority(cyclemask_path)
    repository = _repository_authorities(root)
    return validate_official_functional_summary(_sealed(
        _summary_body(result, sources, cyclemask, repository)
    ))


def write_official_functional_summary(
    dataset_directory: Path,
    cyclemask_path: Path,
    output_path: Path,
    repository_root: Optional[Path] = None,
) -> Mapping[str, object]:
    """Run, atomically write canonical JSON, and re-read/revalidate it."""

    if not isinstance(output_path, Path):
        _fail("output must be a pathlib.Path")
    summary = build_official_functional_summary(
        dataset_directory, cyclemask_path, repository_root
    )
    payload = canonical_json_bytes(summary)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % output_path.name, dir=str(output_path.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, str(output_path))
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        raw = output_path.read_bytes()
    except OSError as error:
        raise OfficialFunctionalRunError("cannot write/re-read output") from error
    if raw != payload:
        _fail("written output differs from canonical bytes")
    try:
        decoded = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise OfficialFunctionalRunError("written output is not canonical JSON") from error
    if canonical_json_bytes(decoded) != raw:
        _fail("written output is not exact canonical JSON")
    validation_root = Path(__file__).parents[2] if repository_root is None else repository_root
    validate_official_functional_summary(decoded, validation_root)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--cyclemask", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    write_official_functional_summary(
        arguments.dataset, arguments.cyclemask, arguments.output
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "CORE_CODE_PATHS",
    "OfficialFunctionalRunError",
    "SCHEMA",
    "build_official_functional_summary",
    "validate_official_functional_summary",
    "write_official_functional_summary",
)
