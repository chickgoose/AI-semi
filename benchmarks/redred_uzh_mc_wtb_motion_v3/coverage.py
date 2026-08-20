"""Loss-explicit coverage and disposition accounting for MC-WTB metric v3.

The ledger deliberately does not turn out-of-FOV, invalid geometry, or missing
records into one scalar penalty.  Geometry coverage and delivery disposition
are orthogonal partitions over the same frozen event-ID denominator.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Final


IN_FOV: Final = "in_fov"
VALID_REFERENCE_OOF_STATUSES: Final = frozenset(
    {"outside_reference_image", "behind_reference"}
)
INVALID_GEOMETRY_STATUSES: Final = frozenset(
    {"invalid_distortion", "invalid_geometry"}
)

REFERENCE_DISPOSITIONS: Final = frozenset(
    {"WORLD_REFERENCE_EVENT", "REFERENCE_EVENT"}
)
RAW_ESCAPE_DISPOSITIONS: Final = frozenset(
    {"RAW_ESCAPE_GEOMETRIC_OOF", "RAW_ESCAPE"}
)
INVALID_BYPASS_DISPOSITIONS: Final = frozenset(
    {"RAW_BYPASS_INVALID_GEOMETRY", "INVALID_GEOMETRY_BYPASS"}
)
DROPPED_DISPOSITIONS: Final = frozenset({"DROPPED", "DROP"})

GEOMETRY_CATEGORIES: Final = (
    "in_fov",
    "valid_reference_oof_world_valid",
    "invalid_geometry",
    "geometry_unavailable",
)
DISPOSITION_CATEGORIES: Final = (
    "reference_event",
    "raw_escape",
    "invalid_geometry_bypass",
    "dropped",
    "missing",
    "duplicate",
)
HALO_CATEGORIES: Final = (
    "covered_by_padded_tile_halo",
    "outside_padded_tile_halo",
    "coordinate_unavailable",
)


class CoverageError(ValueError):
    """Raised when the caller supplies an ambiguous coverage contract."""


def _positive_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoverageError(f"{where} must be a positive integer")
    return value


def _nonnegative_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageError(f"{where} must be a non-negative integer")
    return value


def _finite_coordinate(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoverageError(f"{where} must be a finite coordinate")
    result = float(value)
    if not math.isfinite(result):
        raise CoverageError(f"{where} must be a finite coordinate")
    return result


def padded_tile_halo_bounds(
    sensor_width: int,
    sensor_height: int,
    tile_width: int,
    tile_height: int,
    *,
    halo_tiles_x: int = 1,
    halo_tiles_y: int = 1,
) -> dict[str, Any]:
    """Return half-open sensor, tile, and tile-aligned padded-halo bounds.

    The padded pixel rectangle includes partial edge-tile padding.  It never
    changes the physical sensor FOV: coordinates in that rectangle but outside
    ``[0,width) x [0,height)`` remain reference-OOF.
    """

    width = _positive_int(sensor_width, "sensor_width")
    height = _positive_int(sensor_height, "sensor_height")
    tw = _positive_int(tile_width, "tile_width")
    th = _positive_int(tile_height, "tile_height")
    hx = _nonnegative_int(halo_tiles_x, "halo_tiles_x")
    hy = _nonnegative_int(halo_tiles_y, "halo_tiles_y")
    core_columns = (width + tw - 1) // tw
    core_rows = (height + th - 1) // th

    return {
        "boundary_convention": "continuous_half_open",
        "sensor_pixels": {
            "x_min_inclusive": 0,
            "x_max_exclusive": width,
            "y_min_inclusive": 0,
            "y_max_exclusive": height,
        },
        "tile_shape_pixels": {"width": tw, "height": th},
        "core_tiles": {
            "columns": core_columns,
            "rows": core_rows,
            "x_min_inclusive": 0,
            "x_max_exclusive": core_columns,
            "y_min_inclusive": 0,
            "y_max_exclusive": core_rows,
        },
        "halo_tiles": {"x_each_side": hx, "y_each_side": hy},
        "padded_tiles": {
            "x_min_inclusive": -hx,
            "x_max_exclusive": core_columns + hx,
            "y_min_inclusive": -hy,
            "y_max_exclusive": core_rows + hy,
        },
        "padded_pixels": {
            "x_min_inclusive": -hx * tw,
            "x_max_exclusive": (core_columns + hx) * tw,
            "y_min_inclusive": -hy * th,
            "y_max_exclusive": (core_rows + hy) * th,
        },
    }


def classify_reference_coordinate(
    x: object, y: object, bounds: Mapping[str, Any]
) -> str:
    """Classify a continuous coordinate without promoting halo to sensor FOV."""

    px = _finite_coordinate(x, "x")
    py = _finite_coordinate(y, "y")
    try:
        sensor = bounds["sensor_pixels"]
        padded = bounds["padded_pixels"]
        in_sensor = (
            sensor["x_min_inclusive"] <= px < sensor["x_max_exclusive"]
            and sensor["y_min_inclusive"] <= py < sensor["y_max_exclusive"]
        )
        in_padded = (
            padded["x_min_inclusive"] <= px < padded["x_max_exclusive"]
            and padded["y_min_inclusive"] <= py < padded["y_max_exclusive"]
        )
    except (KeyError, TypeError) as error:
        raise CoverageError("bounds are not padded_tile_halo_bounds output") from error
    if in_sensor:
        return "sensor_in_fov"
    if in_padded:
        return "padded_tile_halo"
    return "outside_padded_tile_halo"


def _geometry_category(status: object) -> str | None:
    if status is None:
        return None
    if not isinstance(status, str):
        raise CoverageError("geometry_status must be a string or null")
    if status == IN_FOV:
        return "in_fov"
    if status in VALID_REFERENCE_OOF_STATUSES:
        return "valid_reference_oof_world_valid"
    if status in INVALID_GEOMETRY_STATUSES:
        return "invalid_geometry"
    raise CoverageError(f"unknown geometry_status: {status!r}")


def _disposition_category(disposition: object) -> str:
    if not isinstance(disposition, str):
        raise CoverageError("disposition must be a string")
    if disposition in REFERENCE_DISPOSITIONS:
        return "reference_event"
    if disposition in RAW_ESCAPE_DISPOSITIONS:
        return "raw_escape"
    if disposition in INVALID_BYPASS_DISPOSITIONS:
        return "invalid_geometry_bypass"
    if disposition in DROPPED_DISPOSITIONS:
        return "dropped"
    raise CoverageError(f"unknown disposition: {disposition!r}")


def _empty_id_ledger(categories: Sequence[str]) -> dict[str, list[int]]:
    return {category: [] for category in categories}


def _counts(id_ledger: Mapping[str, Sequence[int]]) -> dict[str, int]:
    return {category: len(ids) for category, ids in id_ledger.items()}


def _coordinates(
    arm_value: Mapping[str, Any], geometry_category: str
) -> tuple[float, float] | None:
    x = arm_value.get("locality_x")
    y = arm_value.get("locality_y")
    if x is None and y is None:
        if geometry_category == "in_fov":
            raise CoverageError("in_fov geometry must expose both locality coordinates")
        return None
    if x is None or y is None:
        raise CoverageError("locality coordinates must be both present or both absent")
    coordinate = (
        _finite_coordinate(x, "locality_x"),
        _finite_coordinate(y, "locality_y"),
    )
    if geometry_category == "invalid_geometry":
        raise CoverageError("invalid geometry must not expose locality coordinates")
    return coordinate


def _validate_expected_ids(expected_event_ids: Sequence[int]) -> tuple[int, ...]:
    if isinstance(expected_event_ids, (str, bytes)):
        raise CoverageError("expected_event_ids must be an ordered integer sequence")
    result = tuple(expected_event_ids)
    if not result:
        raise CoverageError("expected_event_ids must not be empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in result):
        raise CoverageError("expected_event_ids must contain only integers")
    if len(set(result)) != len(result):
        raise CoverageError("expected_event_ids must be unique")
    return result


def _validate_arm_names(arm_names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(arm_names, (str, bytes)):
        raise CoverageError("arm_names must be an ordered string sequence")
    result = tuple(arm_names)
    if not result or any(not isinstance(name, str) or not name for name in result):
        raise CoverageError("arm_names must contain non-empty strings")
    if len(set(result)) != len(result):
        raise CoverageError("arm_names must be unique")
    return result


def build_coverage_ledger(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_event_ids: Sequence[int],
    arm_names: Sequence[str],
    sensor_width: int,
    sensor_height: int,
    tile_width: int,
    tile_height: int,
    halo_tiles_x: int = 1,
    halo_tiles_y: int = 1,
) -> dict[str, Any]:
    """Build an equal-denominator, loss-explicit ledger for normalized v3 rows.

    Each row must have ``dataset_event_index`` and an ``arms`` mapping.  Every
    present arm observation has ``geometry_status``, ``disposition``, and
    optional ``locality_x/locality_y``.  A duplicated event ID is treated as
    ambiguous for every arm rather than choosing one copy.  Missing/duplicate
    IDs stay in the frozen denominator.
    """

    expected = _validate_expected_ids(expected_event_ids)
    arms = _validate_arm_names(arm_names)
    bounds = padded_tile_halo_bounds(
        sensor_width,
        sensor_height,
        tile_width,
        tile_height,
        halo_tiles_x=halo_tiles_x,
        halo_tiles_y=halo_tiles_y,
    )
    expected_set = set(expected)
    by_id: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    observed_ids: list[int] = []
    for ordinal, row in enumerate(records):
        if not isinstance(row, Mapping):
            raise CoverageError(f"record {ordinal} must be a mapping")
        event_id = row.get("dataset_event_index")
        if isinstance(event_id, bool) or not isinstance(event_id, int):
            raise CoverageError(f"record {ordinal} dataset_event_index must be an integer")
        observed_ids.append(event_id)
        by_id[event_id].append(row)

    unexpected_ids = sorted(event_id for event_id in by_id if event_id not in expected_set)
    duplicate_expected_ids = [event_id for event_id in expected if len(by_id[event_id]) > 1]
    missing_expected_ids = [event_id for event_id in expected if not by_id[event_id]]
    duplicate_extra_records = sum(len(by_id[event_id]) - 1 for event_id in duplicate_expected_ids)
    # Preserve only the first occurrence for the order diagnostic.  Duplicates
    # themselves are still fatal to eligibility and never selected as data.
    seen_for_order: set[int] = set()
    first_expected_occurrences: list[int] = []
    for event_id in observed_ids:
        if event_id in expected_set and event_id not in seen_for_order:
            seen_for_order.add(event_id)
            first_expected_occurrences.append(event_id)
    order_matches = first_expected_occurrences == list(expected)

    arm_ledgers: dict[str, Any] = {}
    all_equal = True
    for arm in arms:
        geometry_ids = _empty_id_ledger(GEOMETRY_CATEGORIES)
        disposition_ids = _empty_id_ledger(DISPOSITION_CATEGORIES)
        halo_ids = _empty_id_ledger(HALO_CATEGORIES)
        cross_counts: Counter[str] = Counter()
        violations: list[dict[str, Any]] = []

        for event_id in expected:
            occurrences = by_id[event_id]
            if not occurrences:
                geometry = "geometry_unavailable"
                disposition = "missing"
            elif len(occurrences) > 1:
                geometry = "geometry_unavailable"
                disposition = "duplicate"
            else:
                row_arms = occurrences[0].get("arms")
                if not isinstance(row_arms, Mapping):
                    raise CoverageError(f"event {event_id} arms must be a mapping")
                extra_arms = sorted(set(row_arms) - set(arms))
                if extra_arms:
                    raise CoverageError(
                        f"event {event_id} contains undeclared arms: {extra_arms}"
                    )
                if arm not in row_arms:
                    geometry = "geometry_unavailable"
                    disposition = "missing"
                else:
                    arm_value = row_arms[arm]
                    if not isinstance(arm_value, Mapping):
                        raise CoverageError(f"event {event_id} arm {arm} must be a mapping")
                    disposition = _disposition_category(arm_value.get("disposition"))
                    geometry_or_none = _geometry_category(arm_value.get("geometry_status"))
                    if geometry_or_none is None:
                        if disposition != "dropped":
                            raise CoverageError(
                                f"event {event_id} arm {arm} lacks geometry for a non-drop disposition"
                            )
                        geometry = "geometry_unavailable"
                        coordinate = None
                    else:
                        geometry = geometry_or_none
                        coordinate = _coordinates(arm_value, geometry)
                        if (
                            arm_value.get("geometry_status") == "outside_reference_image"
                            and coordinate is None
                        ):
                            raise CoverageError(
                                f"event {event_id} arm {arm} outside_reference_image lacks coordinates"
                            )
                        if geometry == "in_fov":
                            assert coordinate is not None
                            if classify_reference_coordinate(*coordinate, bounds) != "sensor_in_fov":
                                raise CoverageError(
                                    f"event {event_id} arm {arm} marks an outside coordinate in_fov"
                                )
                        elif geometry == "valid_reference_oof_world_valid":
                            if coordinate is None:
                                halo_ids["coordinate_unavailable"].append(event_id)
                            else:
                                region = classify_reference_coordinate(*coordinate, bounds)
                                if region == "sensor_in_fov":
                                    raise CoverageError(
                                        f"event {event_id} arm {arm} marks an in-sensor coordinate OOF"
                                    )
                                halo_category = (
                                    "covered_by_padded_tile_halo"
                                    if region == "padded_tile_halo"
                                    else "outside_padded_tile_halo"
                                )
                                halo_ids[halo_category].append(event_id)

            geometry_ids[geometry].append(event_id)
            disposition_ids[disposition].append(event_id)
            cross_counts[f"{geometry}|{disposition}"] += 1
            expected_disposition = {
                "in_fov": "reference_event",
                "valid_reference_oof_world_valid": "raw_escape",
                "invalid_geometry": "invalid_geometry_bypass",
                "geometry_unavailable": None,
            }[geometry]
            if expected_disposition is not None and disposition != expected_disposition:
                violations.append({
                    "dataset_event_index": event_id,
                    "geometry": geometry,
                    "disposition": disposition,
                    "expected_disposition": expected_disposition,
                })

        geometry_counts = _counts(geometry_ids)
        disposition_counts = _counts(disposition_ids)
        halo_counts = _counts(halo_ids)
        geometry_total = sum(geometry_counts.values())
        disposition_total = sum(disposition_counts.values())
        cross_total = sum(cross_counts.values())
        halo_total = sum(halo_counts.values())
        equal = (
            geometry_total == disposition_total == cross_total == len(expected)
            and halo_total == geometry_counts["valid_reference_oof_world_valid"]
        )
        all_equal = all_equal and equal
        loss_ids = sorted(
            disposition_ids["dropped"]
            + disposition_ids["missing"]
            + disposition_ids["duplicate"]
        )
        no_loss = equal and not loss_ids
        cross_contract_valid = not violations
        complete = no_loss and cross_contract_valid
        arm_ledgers[arm] = {
            "denominator": len(expected),
            "geometry": {"counts": geometry_counts, "dataset_event_ids": geometry_ids},
            "disposition": {
                "counts": disposition_counts,
                "dataset_event_ids": disposition_ids,
            },
            "valid_reference_oof_halo": {
                "counts": halo_counts,
                "dataset_event_ids": halo_ids,
            },
            "geometry_disposition_cross_counts": dict(sorted(cross_counts.items())),
            "cross_contract_violations": violations,
            "loss_dataset_event_ids": loss_ids,
            "equal_denominator_invariant": equal,
            "no_drop_missing_duplicate": no_loss,
            "geometry_disposition_contract_valid": cross_contract_valid,
            "complete_coverage_contract": complete,
        }

    global_complete = all(
        arm_ledgers[arm]["complete_coverage_contract"] for arm in arms
    )
    identity_clean = (
        not unexpected_ids
        and not duplicate_expected_ids
        and not missing_expected_ids
        and order_matches
    )
    return {
        "schema": "redred.uzh_mc_wtb_motion_v3.coverage_ledger/v1",
        "accounting": "orthogonal_geometry_and_disposition_no_scalar_penalty",
        "expected_denominator": len(expected),
        "expected_dataset_event_ids": list(expected),
        "arm_names": list(arms),
        "padded_tile_halo_bounds": bounds,
        "input_identity": {
            "observed_record_count": len(records),
            "missing_expected_dataset_event_ids": missing_expected_ids,
            "duplicate_expected_dataset_event_ids": duplicate_expected_ids,
            "duplicate_extra_record_count": duplicate_extra_records,
            "unexpected_dataset_event_ids": unexpected_ids,
            "first_occurrence_order_matches_expected": order_matches,
        },
        "arms": arm_ledgers,
        "equal_denominator_invariant": all_equal,
        "eligible_complete_coverage": identity_clean and all_equal and global_complete,
    }


coverage_ledger = build_coverage_ledger


__all__ = [
    "CoverageError",
    "build_coverage_ledger",
    "classify_reference_coordinate",
    "coverage_ledger",
    "padded_tile_halo_bounds",
]
