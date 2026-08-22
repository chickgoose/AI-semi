"""Fixed-budget, past-only causal world-reference bank.

Events at one timestamp are scored against state from strictly earlier
timestamps and are inserted only after the whole timestamp cluster is scored.
That rule prevents within-timestamp and future leakage.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Deque, Iterable, Optional, Sequence, Tuple


class CausalReferenceError(ValueError):
    """The causal reference contract was violated."""


@dataclass(frozen=True)
class CausalReferenceConfig:
    capacity_per_polarity: int = 256
    max_age_ns: int = 2_000_000

    def __post_init__(self) -> None:
        if isinstance(self.capacity_per_polarity, bool) or not isinstance(self.capacity_per_polarity, int):
            raise CausalReferenceError("capacity_per_polarity must be an integer")
        if isinstance(self.max_age_ns, bool) or not isinstance(self.max_age_ns, int):
            raise CausalReferenceError("max_age_ns must be an integer")
        if self.capacity_per_polarity < 1 or self.max_age_ns < 1:
            raise CausalReferenceError("capacity and max age must be positive")


@dataclass(frozen=True)
class ReferenceObservation:
    event_id: int
    timestamp_ns: int
    polarity: int
    ray: Tuple[float, float, float]

    def __post_init__(self) -> None:
        if (
            isinstance(self.event_id, bool) or not isinstance(self.event_id, int)
            or isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int)
            or isinstance(self.polarity, bool) or not isinstance(self.polarity, int)
            or self.event_id < 0 or self.timestamp_ns < 0 or self.polarity not in (0, 1)
        ):
            raise CausalReferenceError("invalid observation identity")
        if len(self.ray) != 3 or not all(math.isfinite(float(value)) for value in self.ray):
            raise CausalReferenceError("ray must contain three finite values")
        norm = math.sqrt(sum(float(value) ** 2 for value in self.ray))
        if abs(norm - 1.0) > 1.0e-9:
            raise CausalReferenceError("ray must be normalized")


@dataclass(frozen=True)
class ReferenceScore:
    event_id: int
    timestamp_ns: int
    polarity: int
    angular_cost_rad: Optional[float]
    reference_available: bool
    reference_event_id: Optional[int]
    reference_timestamp_ns: Optional[int]
    reference_age_ns: Optional[int]


@dataclass(frozen=True)
class PrimeReceipt:
    """Sealed summary of score-free observations inserted into one bank."""

    schema: str
    observation_count: int
    first_timestamp_ns: Optional[int]
    last_timestamp_ns: Optional[int]
    occupancy: Tuple[int, int]
    observations_sha256: str
    seal_sha256: str


def angular_distance(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    return math.acos(min(1.0, max(-1.0, dot)))


class CausalReferenceBank:
    """Two deterministic polarity banks with finite capacity and maximum age."""

    def __init__(self, config: CausalReferenceConfig = CausalReferenceConfig()) -> None:
        self.config = config
        self._banks = (deque(), deque())  # type: Tuple[Deque[ReferenceObservation], Deque[ReferenceObservation]]
        self._last_timestamp = None  # type: Optional[int]
        self._seen_ids = set()  # type: set[int]

    def _expire(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - self.config.max_age_ns
        for bank in self._banks:
            while bank and bank[0].timestamp_ns < cutoff:
                bank.popleft()

    def prime(self, warmup_observations: Iterable[ReferenceObservation]) -> PrimeReceipt:
        """Insert warmup state without computing or returning any angular score.

        Validation, expiry, capacity, and equal-timestamp cluster rules are the
        same as :meth:`process`.  In particular, a cluster may not be split
        between ``prime`` and ``process`` calls.
        """

        source = tuple(warmup_observations)
        local_ids = set()
        for index, observation in enumerate(source):
            if not isinstance(observation, ReferenceObservation):
                raise CausalReferenceError("observations must be ReferenceObservation values")
            if index and observation.timestamp_ns < source[index - 1].timestamp_ns:
                raise CausalReferenceError("timestamps are not nondecreasing")
            if self._last_timestamp is not None and observation.timestamp_ns < self._last_timestamp:
                raise CausalReferenceError("timestamp moved backwards across calls")
            if observation.event_id in self._seen_ids or observation.event_id in local_ids:
                raise CausalReferenceError("duplicate event ID")
            local_ids.add(observation.event_id)
        if source and self._last_timestamp is not None and source[0].timestamp_ns == self._last_timestamp:
            raise CausalReferenceError("equal timestamp cluster was split across calls")

        index = 0
        while index < len(source):
            timestamp = source[index].timestamp_ns
            end = index + 1
            while end < len(source) and source[end].timestamp_ns == timestamp:
                end += 1
            cluster = source[index:end]
            self._expire(timestamp)
            for observation in cluster:
                bank = self._banks[observation.polarity]
                bank.append(observation)
                while len(bank) > self.config.capacity_per_polarity:
                    bank.popleft()
            self._last_timestamp = timestamp
            index = end
        self._seen_ids.update(observation.event_id for observation in source)

        schema = "redred.mc_wtb_causal_reference.prime_receipt/v1"
        observations_payload = [
            {
                "event_id": observation.event_id,
                "polarity": observation.polarity,
                "ray_hex": [float(value).hex() for value in observation.ray],
                "timestamp_ns": observation.timestamp_ns,
            }
            for observation in source
        ]
        observations_sha256 = hashlib.sha256(
            json.dumps(
                observations_payload,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        receipt_payload = {
            "first_timestamp_ns": source[0].timestamp_ns if source else None,
            "last_timestamp_ns": source[-1].timestamp_ns if source else None,
            "observation_count": len(source),
            "observations_sha256": observations_sha256,
            "occupancy": list(self.occupancy()),
            "schema": schema,
        }
        seal_sha256 = hashlib.sha256(
            json.dumps(
                receipt_payload,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return PrimeReceipt(
            schema=schema,
            observation_count=len(source),
            first_timestamp_ns=source[0].timestamp_ns if source else None,
            last_timestamp_ns=source[-1].timestamp_ns if source else None,
            occupancy=self.occupancy(),
            observations_sha256=observations_sha256,
            seal_sha256=seal_sha256,
        )

    def process(self, observations: Iterable[ReferenceObservation]) -> Tuple[ReferenceScore, ...]:
        source = tuple(observations)
        if not source:
            return ()
        local_ids = set()
        for index, observation in enumerate(source):
            if not isinstance(observation, ReferenceObservation):
                raise CausalReferenceError("observations must be ReferenceObservation values")
            if index and observation.timestamp_ns < source[index - 1].timestamp_ns:
                raise CausalReferenceError("timestamps are not nondecreasing")
            if self._last_timestamp is not None and observation.timestamp_ns < self._last_timestamp:
                raise CausalReferenceError("timestamp moved backwards across calls")
            if observation.event_id in self._seen_ids or observation.event_id in local_ids:
                raise CausalReferenceError("duplicate event ID")
            local_ids.add(observation.event_id)
        if self._last_timestamp is not None and source[0].timestamp_ns == self._last_timestamp:
            raise CausalReferenceError("equal timestamp cluster was split across calls")

        scores = []  # type: list[ReferenceScore]
        index = 0
        while index < len(source):
            timestamp = source[index].timestamp_ns
            end = index + 1
            while end < len(source) and source[end].timestamp_ns == timestamp:
                end += 1
            cluster = source[index:end]
            self._expire(timestamp)
            # Score the complete cluster before inserting any member.
            for observation in cluster:
                bank = self._banks[observation.polarity]
                if bank:
                    cost, _, _, selected = min(
                        (angular_distance(observation.ray, item.ray), item.timestamp_ns, item.event_id, item)
                        for item in bank
                    )
                    scores.append(ReferenceScore(
                        observation.event_id, timestamp, observation.polarity, cost, True,
                        selected.event_id, selected.timestamp_ns, timestamp - selected.timestamp_ns,
                    ))
                else:
                    scores.append(ReferenceScore(
                        observation.event_id, timestamp, observation.polarity, None, False,
                        None, None, None,
                    ))
            for observation in cluster:
                bank = self._banks[observation.polarity]
                bank.append(observation)
                while len(bank) > self.config.capacity_per_polarity:
                    bank.popleft()
            self._last_timestamp = timestamp
            index = end
        self._seen_ids.update(observation.event_id for observation in source)
        return tuple(scores)

    def occupancy(self) -> Tuple[int, int]:
        return len(self._banks[0]), len(self._banks[1])
