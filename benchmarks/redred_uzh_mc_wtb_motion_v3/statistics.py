"""Deterministic paired statistics for the MC-WTB motion V3 assay.

The statistical unit at the event level is an *equal timestamp cluster*.  A
cluster is never split by the circular moving-block bootstrap.  Multiple
windows are not pooled as if their events were independent: each window first
produces an effect, windows in a declared dependence group are averaged, and
dependence groups are then equally weighted.

This module intentionally owns no evaluator or preregistration I/O.  Callers
must pass already-frozen values and an explicit gate specification.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Hashable, Mapping, Sequence
from typing import Any


class StatisticsFailure(ValueError):
    """Raised when an input or resample cannot support the declared inference."""


_WINDOW_KEYS = {
    "window_id",
    "dependence_group",
    "timestamps_ns",
    "sample_ids",
    "baseline",
    "candidate",
}
_BOOTSTRAP_KEYS = {"resamples", "block_length_clusters", "seed_text"}
_GATE_KEYS = {
    "gate_id",
    "familywise_alpha",
    "familywise_hypotheses",
    "minimum_relative_reduction_strictly_greater_than",
    "predeclared_window_ids",
    "predeclared_dependence_groups",
}


def _strict_mapping(value: object, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise StatisticsFailure(f"{where} keys differ from the statistics contract")
    return value


def _integer(value: object, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StatisticsFailure(f"{where} must be an integer >= {minimum}")
    return value


def _finite(value: object, where: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatisticsFailure(f"{where} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise StatisticsFailure(f"{where} must be finite and >= {minimum}")
    return result


def _identifier(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise StatisticsFailure(f"{where} must be a non-empty ASCII string")
    return value


def _numeric_vector(value: object, where: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StatisticsFailure(f"{where} must be a sequence")
    result = tuple(_finite(item, f"{where}[{index}]", minimum=0.0)
                   for index, item in enumerate(value))
    if not result:
        raise StatisticsFailure(f"{where} must not be empty")
    return result


def _timestamps(value: object, where: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StatisticsFailure(f"{where} must be a sequence")
    output: list[int] = []
    for index, item in enumerate(value):
        output.append(_integer(item, f"{where}[{index}]", 0))
    if not output:
        raise StatisticsFailure(f"{where} must not be empty")
    if any(left > right for left, right in zip(output, output[1:])):
        raise StatisticsFailure(f"{where} must be nondecreasing")
    return tuple(output)


def equal_timestamp_clusters(timestamps_ns: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """Return contiguous index clusters, rejecting malformed timestamp order."""

    timestamps = _timestamps(timestamps_ns, "timestamps_ns")
    clusters: list[list[int]] = []
    for index, timestamp in enumerate(timestamps):
        if not clusters or timestamp != timestamps[clusters[-1][0]]:
            clusters.append([])
        clusters[-1].append(index)
    return tuple(tuple(cluster) for cluster in clusters)


def _bootstrap_spec(value: object) -> tuple[int, int, str]:
    spec = _strict_mapping(value, _BOOTSTRAP_KEYS, "bootstrap")
    resamples = _integer(spec["resamples"], "bootstrap.resamples", 2)
    block = _integer(spec["block_length_clusters"],
                     "bootstrap.block_length_clusters", 1)
    seed = _identifier(spec["seed_text"], "bootstrap.seed_text")
    return resamples, block, seed


def _uniform_below(seed: bytes, replicate: int, block_index: int, upper: int) -> int:
    """Version-independent SHA-256 counter PRNG with rejection of modulo bias."""

    if upper < 1:
        raise StatisticsFailure("bootstrap draw upper bound must be positive")
    modulus = 1 << 256
    limit = modulus - modulus % upper
    nonce = 0
    while True:
        payload = (seed + replicate.to_bytes(8, "big")
                   + block_index.to_bytes(8, "big") + nonce.to_bytes(8, "big"))
        value = int.from_bytes(hashlib.sha256(payload).digest(), "big")
        if value < limit:
            return value % upper
        nonce += 1


def moving_block_cluster_draws(
    timestamps_ns: Sequence[int],
    *,
    block_length_clusters: int,
    resamples: int,
    seed_text: str,
    stream_id: str = "default",
) -> tuple[tuple[int, ...], ...]:
    """Return deterministic circular moving-block draws as original indices.

    Every occurrence of an equal-timestamp cluster is emitted in full.  The
    number of sampled clusters equals the original cluster count; event count
    may vary because tied clusters can have different cardinalities.
    """

    clusters = equal_timestamp_clusters(timestamps_ns)
    block = _integer(block_length_clusters, "block_length_clusters", 1)
    count = _integer(resamples, "resamples", 2)
    seed = _identifier(seed_text, "seed_text")
    stream = _identifier(stream_id, "stream_id")
    if len(clusters) < 2:
        raise StatisticsFailure("at least two timestamp clusters are required")
    if block > len(clusters):
        raise StatisticsFailure("block length exceeds timestamp-cluster count")
    blocks_per_draw = math.ceil(len(clusters) / block)
    seed_bytes = hashlib.sha256(
        b"redred.mcwtb.motion.v3.bootstrap\0" + seed.encode("ascii")
        + b"\0" + stream.encode("ascii")
    ).digest()
    output: list[tuple[int, ...]] = []
    for replicate in range(count):
        sampled: list[tuple[int, ...]] = []
        for block_index in range(blocks_per_draw):
            start = _uniform_below(seed_bytes, replicate, block_index, len(clusters))
            sampled.extend(
                clusters[(start + offset) % len(clusters)] for offset in range(block)
            )
        indices = tuple(index for cluster in sampled[:len(clusters)] for index in cluster)
        output.append(indices)
    return tuple(output)


def paired_effect_sizes(
    baseline: Sequence[float], candidate: Sequence[float]
) -> dict[str, Any]:
    """Compute lower-is-better paired effects without non-finite sentinels."""

    base = _numeric_vector(baseline, "baseline")
    cand = _numeric_vector(candidate, "candidate")
    if len(base) != len(cand):
        raise StatisticsFailure("baseline/candidate lengths differ")
    mean_base = math.fsum(base) / len(base)
    mean_cand = math.fsum(cand) / len(cand)
    if mean_base <= 0.0:
        raise StatisticsFailure("relative effect is undefined for zero baseline mean")
    differences = tuple(left - right for left, right in zip(base, cand))
    mean_difference = math.fsum(differences) / len(differences)
    positive = sum(value > 0.0 for value in differences)
    negative = sum(value < 0.0 for value in differences)
    ties = len(differences) - positive - negative
    non_ties = positive + negative
    standardized: float | None = None
    standardized_reason: str | None = None
    if len(differences) < 2:
        standardized_reason = "FEWER_THAN_TWO_PAIRS"
    else:
        variance = math.fsum((value - mean_difference) ** 2 for value in differences)
        variance /= len(differences) - 1
        if variance == 0.0:
            standardized_reason = "ZERO_PAIRED_DIFFERENCE_VARIANCE"
        else:
            standardized = mean_difference / math.sqrt(variance)
    return {
        "pairs": len(base),
        "baseline_mean": mean_base,
        "candidate_mean": mean_cand,
        "absolute_mean_reduction": mean_difference,
        "relative_mean_reduction": 1.0 - mean_cand / mean_base,
        "paired_standardized_mean_difference": standardized,
        "paired_standardized_unavailable_reason": standardized_reason,
        "matched_rank_biserial": (
            None if non_ties == 0 else (positive - negative) / non_ties
        ),
        "candidate_better_pairs": positive,
        "ties": ties,
        "candidate_worse_pairs": negative,
    }


def _lower_quantile(samples: Sequence[float], alpha: float) -> tuple[float, int]:
    if not samples or not 0.0 < alpha < 1.0:
        raise StatisticsFailure("invalid lower-quantile request")
    rank = math.ceil(alpha * len(samples))
    if rank < 1:
        raise StatisticsFailure("bootstrap resolution cannot represent confidence gate")
    ordered = sorted(samples)
    return ordered[rank - 1], rank


def _window(value: object) -> dict[str, Any]:
    row = _strict_mapping(value, _WINDOW_KEYS, "window")
    window_id = _identifier(row["window_id"], "window.window_id")
    group = _identifier(row["dependence_group"], "window.dependence_group")
    timestamps = _timestamps(row["timestamps_ns"], f"window {window_id}.timestamps_ns")
    baseline = _numeric_vector(row["baseline"], f"window {window_id}.baseline")
    candidate = _numeric_vector(row["candidate"], f"window {window_id}.candidate")
    sample_ids_value = row["sample_ids"]
    if isinstance(sample_ids_value, (str, bytes)) or not isinstance(sample_ids_value, Sequence):
        raise StatisticsFailure(f"window {window_id}.sample_ids must be a sequence")
    sample_ids: list[Hashable] = []
    for index, item in enumerate(sample_ids_value):
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise StatisticsFailure(f"window {window_id}.sample_ids[{index}] is invalid")
        sample_ids.append(item)
    lengths = {len(timestamps), len(sample_ids), len(baseline), len(candidate)}
    if len(lengths) != 1:
        raise StatisticsFailure(f"window {window_id} vector lengths differ")
    if len(set(sample_ids)) != len(sample_ids):
        raise StatisticsFailure(f"window {window_id} sample IDs are not unique")
    # Validate the point effect before any resampling.
    paired_effect_sizes(baseline, candidate)
    return {
        "window_id": window_id,
        "dependence_group": group,
        "timestamps_ns": timestamps,
        "sample_ids": tuple(sample_ids),
        "baseline": baseline,
        "candidate": candidate,
    }


def _gate(value: object, window_ids: tuple[str, ...], groups: Mapping[str, str],
          resamples: int) -> dict[str, Any]:
    spec = _strict_mapping(value, _GATE_KEYS, "gate")
    gate_id = _identifier(spec["gate_id"], "gate.gate_id")
    alpha = _finite(spec["familywise_alpha"], "gate.familywise_alpha")
    if not 0.0 < alpha < 1.0:
        raise StatisticsFailure("gate.familywise_alpha must be in (0,1)")
    hypotheses = _integer(spec["familywise_hypotheses"],
                          "gate.familywise_hypotheses", 1)
    threshold = _finite(
        spec["minimum_relative_reduction_strictly_greater_than"],
        "gate.minimum_relative_reduction_strictly_greater_than",
    )
    declared_ids = spec["predeclared_window_ids"]
    if not isinstance(declared_ids, list) or any(not isinstance(item, str) for item in declared_ids):
        raise StatisticsFailure("gate.predeclared_window_ids must be a string list")
    if tuple(declared_ids) != window_ids:
        raise StatisticsFailure("runtime windows differ from predeclared order")
    declared_groups = spec["predeclared_dependence_groups"]
    if not isinstance(declared_groups, Mapping) or declared_groups != groups:
        raise StatisticsFailure("runtime dependence groups differ from preregistration")
    # There is one simultaneous window assertion plus one aggregate assertion.
    if hypotheses < len(window_ids) + 1:
        raise StatisticsFailure("familywise hypothesis count omits a tested gate")
    adjusted_alpha = alpha / hypotheses
    if adjusted_alpha * resamples < 1.0:
        raise StatisticsFailure("resample count cannot resolve adjusted confidence tail")
    return {
        "gate_id": gate_id,
        "familywise_alpha": alpha,
        "familywise_hypotheses": hypotheses,
        "adjusted_one_sided_alpha": adjusted_alpha,
        "one_sided_confidence": 1.0 - adjusted_alpha,
        "threshold": threshold,
    }


def analyze_multiple_windows(
    windows: Sequence[Mapping[str, object]],
    *,
    bootstrap: Mapping[str, object],
    gate: Mapping[str, object],
) -> dict[str, Any]:
    """Analyze a predeclared window family without event-level pooling.

    Overlapping sample IDs are legal only inside one declared dependence group.
    The gate is an intersection rule: every window lower bound and the grouped
    aggregate lower bound must strictly exceed the frozen threshold.
    """

    if isinstance(windows, (str, bytes)) or not isinstance(windows, Sequence) or not windows:
        raise StatisticsFailure("windows must be a non-empty sequence")
    parsed = tuple(_window(value) for value in windows)
    window_ids = tuple(row["window_id"] for row in parsed)
    if len(set(window_ids)) != len(window_ids):
        raise StatisticsFailure("window IDs are not unique")
    groups = {row["window_id"]: row["dependence_group"] for row in parsed}
    resamples, block, seed = _bootstrap_spec(bootstrap)
    gate_spec = _gate(gate, window_ids, groups, resamples)

    owners: dict[Hashable, str] = {}
    for row in parsed:
        for sample_id in row["sample_ids"]:
            prior = owners.get(sample_id)
            if prior is not None and prior != row["dependence_group"]:
                raise StatisticsFailure(
                    "overlapping sample IDs cross declared dependence groups"
                )
            owners[sample_id] = row["dependence_group"]

    window_results: dict[str, Any] = {}
    window_samples: dict[str, tuple[float, ...]] = {}
    for row in parsed:
        clusters = equal_timestamp_clusters(row["timestamps_ns"])
        if block > len(clusters):
            raise StatisticsFailure(
                f"window {row['window_id']} has fewer clusters than block length"
            )
        # Windows in one dependence group share the PRNG stream.  This does not
        # assert independence between nested windows; group aggregation below
        # counts them as one unit.
        draws = moving_block_cluster_draws(
            row["timestamps_ns"],
            block_length_clusters=block,
            resamples=resamples,
            seed_text=seed,
            stream_id=row["dependence_group"],
        )
        reductions: list[float] = []
        for draw_index, indices in enumerate(draws):
            sampled_base = tuple(row["baseline"][index] for index in indices)
            sampled_candidate = tuple(row["candidate"][index] for index in indices)
            try:
                reduction = paired_effect_sizes(
                    sampled_base, sampled_candidate
                )["relative_mean_reduction"]
            except StatisticsFailure as error:
                raise StatisticsFailure(
                    f"window {row['window_id']} bootstrap replicate {draw_index} invalid: {error}"
                ) from error
            if not isinstance(reduction, float) or not math.isfinite(reduction):
                raise StatisticsFailure(
                    f"window {row['window_id']} produced a non-finite bootstrap effect"
                )
            reductions.append(reduction)
        lower, rank = _lower_quantile(reductions, gate_spec["adjusted_one_sided_alpha"])
        point = paired_effect_sizes(row["baseline"], row["candidate"])
        window_results[row["window_id"]] = {
            "dependence_group": row["dependence_group"],
            "records": len(row["baseline"]),
            "timestamp_clusters": len(clusters),
            "effect_sizes": point,
            "bootstrap_lower_bound": lower,
            "bootstrap_lower_rank_one_based": rank,
            "passes_strict_gate": lower > gate_spec["threshold"],
        }
        window_samples[row["window_id"]] = tuple(reductions)

    group_members: dict[str, list[str]] = {}
    for row in parsed:
        group_members.setdefault(row["dependence_group"], []).append(row["window_id"])
    group_points: dict[str, float] = {}
    group_samples: dict[str, tuple[float, ...]] = {}
    for group, members in group_members.items():
        group_points[group] = math.fsum(
            window_results[window_id]["effect_sizes"]["relative_mean_reduction"]
            for window_id in members
        ) / len(members)
        group_samples[group] = tuple(
            math.fsum(window_samples[window_id][replicate] for window_id in members)
            / len(members)
            for replicate in range(resamples)
        )
    aggregate_point = math.fsum(group_points.values()) / len(group_points)
    aggregate_samples = tuple(
        math.fsum(samples[replicate] for samples in group_samples.values())
        / len(group_samples)
        for replicate in range(resamples)
    )
    aggregate_lower, aggregate_rank = _lower_quantile(
        aggregate_samples, gate_spec["adjusted_one_sided_alpha"]
    )
    all_windows_pass = all(
        result["passes_strict_gate"] for result in window_results.values()
    )
    aggregate_pass = aggregate_lower > gate_spec["threshold"]
    passed = all_windows_pass and aggregate_pass
    return {
        "status": "PASS_PREDECLARED_CONFIDENCE_GATES" if passed
        else "FAIL_PREDECLARED_CONFIDENCE_GATES",
        "gate_id": gate_spec["gate_id"],
        "bootstrap": {
            "method": "CIRCULAR_MOVING_BLOCK_EQUAL_TIMESTAMP_CLUSTER",
            "resamples": resamples,
            "block_length_clusters": block,
            "seed_text": seed,
            "prng": "SHA256_COUNTER_REJECTION_V1",
        },
        "confidence_gate": {
            "familywise_alpha": gate_spec["familywise_alpha"],
            "familywise_hypotheses": gate_spec["familywise_hypotheses"],
            "adjusted_one_sided_alpha": gate_spec["adjusted_one_sided_alpha"],
            "one_sided_confidence": gate_spec["one_sided_confidence"],
            "minimum_relative_reduction_strictly_greater_than": gate_spec["threshold"],
            "all_windows_pass": all_windows_pass,
            "aggregate_pass": aggregate_pass,
            "strict_comparison": True,
        },
        "windows": window_results,
        "aggregation": {
            "method": "EQUAL_WINDOW_WITHIN_DEPENDENCE_GROUP_THEN_EQUAL_GROUP",
            "window_count": len(parsed),
            "independent_unit_count": len(group_members),
            "dependence_groups": {group: members for group, members in group_members.items()},
            "group_relative_reductions": group_points,
            "aggregate_relative_reduction": aggregate_point,
            "aggregate_bootstrap_lower_bound": aggregate_lower,
            "aggregate_bootstrap_lower_rank_one_based": aggregate_rank,
            "events_pooled_as_independent": False,
            "nested_windows_counted_as_independent": False,
            "between_window_generalization_claimed": False,
        },
    }


__all__ = [
    "StatisticsFailure",
    "analyze_multiple_windows",
    "equal_timestamp_clusters",
    "moving_block_cluster_draws",
    "paired_effect_sizes",
]
