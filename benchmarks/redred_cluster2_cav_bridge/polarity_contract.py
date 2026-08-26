"""Versioned hardware-polarity transport validation beside the sealed v1 contract."""

from __future__ import annotations

from typing import Iterable, Mapping, Tuple

from .contract import (
    TRANSPORT_OUTCOME_SCHEMA,
    BridgeValidationError,
    validate_transport_outcome as validate_v1_transport_outcome,
)


TRANSPORT_OUTCOME_POLARITY_SCHEMA = (
    "redred.cluster2_cav_bridge.transport_outcome/v2"
)

_POLARITY_OUTCOME_FIELDS = frozenset((
    "schema", "event_id", "source_index", "occurrence_cycle", "outcome",
    "retire_cycle", "retire_native_lane", "retire_row", "retire_col",
    "retire_polarity",
))


def validate_polarity_transport_outcome(
    value: object,
) -> Mapping[str, object]:
    """Validate one v2 row whose retire polarity came from native RTL."""

    if not isinstance(value, Mapping) or frozenset(value) != _POLARITY_OUTCOME_FIELDS:
        raise BridgeValidationError("polarity transport outcome field schema differs")
    if value["schema"] != TRANSPORT_OUTCOME_POLARITY_SCHEMA:
        raise BridgeValidationError("polarity transport outcome schema differs")

    v1_compatible = dict(value)
    retire_polarity = v1_compatible.pop("retire_polarity")
    v1_compatible["schema"] = TRANSPORT_OUTCOME_SCHEMA
    validate_v1_transport_outcome(v1_compatible)

    if value["outcome"] == "DELIVERED":
        if type(retire_polarity) is not int or retire_polarity not in (0, 1):
            raise BridgeValidationError(
                "polarity transport outcome retire_polarity must be 0 or 1"
            )
    elif retire_polarity is not None:
        raise BridgeValidationError(
            "polarity transport OVERRUN retire fields must all be null"
        )
    return value


def validate_versioned_transport_outcome(
    value: object,
) -> Mapping[str, object]:
    """Dispatch strict v1 or hardware-polarity v2 without weakening either."""

    if not isinstance(value, Mapping):
        raise BridgeValidationError("versioned transport outcome must be an object")
    if value.get("schema") == TRANSPORT_OUTCOME_SCHEMA:
        return validate_v1_transport_outcome(value)
    if value.get("schema") == TRANSPORT_OUTCOME_POLARITY_SCHEMA:
        return validate_polarity_transport_outcome(value)
    raise BridgeValidationError("versioned transport outcome schema differs")


def validate_transport_outcome_stream(
    rows: Iterable[object],
) -> Tuple[Mapping[str, object], ...]:
    """Validate a non-empty stream and forbid partial v1/v2 polarity claims."""

    validated = tuple(validate_versioned_transport_outcome(row) for row in rows)
    if not validated:
        raise BridgeValidationError("transport outcome stream must not be empty")
    if len({row["schema"] for row in validated}) != 1:
        raise BridgeValidationError("transport outcome stream mixes schema versions")
    return validated
