"""Score-free warmup initialization around the frozen causal reference bank."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Optional, Tuple

from benchmarks.redred_mc_wtb_causal_reference.reference import (
    CausalReferenceBank,
    CausalReferenceError,
    ReferenceObservation,
)


@dataclass(frozen=True)
class PrimeReceipt:
    schema: str
    config_sha256: str
    observation_count: int
    first_timestamp_ns: Optional[int]
    last_timestamp_ns: Optional[int]
    occupancy: Tuple[int, int]
    observations_sha256: str
    seal_sha256: str


class ScoreFreeCausalReferenceBank(CausalReferenceBank):
    """Reference bank with an explicit non-scoring warmup boundary."""

    def prime(self, warmup_observations: Iterable[ReferenceObservation]) -> PrimeReceipt:
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

        # Commit only after insertion and receipt construction both succeed.
        # This keeps the public bank unchanged even if sealing raises.
        shadow = ScoreFreeCausalReferenceBank(self.config)
        shadow._banks = (self._banks[0].copy(), self._banks[1].copy())
        shadow._last_timestamp = self._last_timestamp
        shadow._seen_ids = set(self._seen_ids)

        index = 0
        while index < len(source):
            timestamp = source[index].timestamp_ns
            end = index + 1
            while end < len(source) and source[end].timestamp_ns == timestamp:
                end += 1
            shadow._expire(timestamp)
            for observation in source[index:end]:
                bank = shadow._banks[observation.polarity]
                bank.append(observation)
                while len(bank) > shadow.config.capacity_per_polarity:
                    bank.popleft()
            shadow._last_timestamp = timestamp
            index = end
        shadow._seen_ids.update(observation.event_id for observation in source)

        schema = "redred.mc_wtb_causal_reference.prime_receipt/v1"
        config_sha256 = _canonical_sha256({
            "capacity_per_polarity": shadow.config.capacity_per_polarity,
            "max_age_ns": shadow.config.max_age_ns,
            "schema": "redred.mc_wtb_causal_reference.prime_config/v1",
        })
        observations_payload = [
            {
                "event_id": observation.event_id,
                "polarity": observation.polarity,
                "ray_hex": [float(value).hex() for value in observation.ray],
                "timestamp_ns": observation.timestamp_ns,
            }
            for observation in source
        ]
        observations_sha256 = _canonical_sha256(observations_payload)
        receipt_payload = {
            "config_sha256": config_sha256,
            "first_timestamp_ns": source[0].timestamp_ns if source else None,
            "last_timestamp_ns": source[-1].timestamp_ns if source else None,
            "observation_count": len(source),
            "observations_sha256": observations_sha256,
            "occupancy": list(shadow.occupancy()),
            "schema": schema,
        }
        receipt = PrimeReceipt(
            schema=schema,
            config_sha256=config_sha256,
            observation_count=len(source),
            first_timestamp_ns=source[0].timestamp_ns if source else None,
            last_timestamp_ns=source[-1].timestamp_ns if source else None,
            occupancy=shadow.occupancy(),
            observations_sha256=observations_sha256,
            seal_sha256=_canonical_sha256(receipt_payload),
        )
        self._banks = shadow._banks
        self._last_timestamp = shadow._last_timestamp
        self._seen_ids = shadow._seen_ids
        return receipt


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


__all__ = ("PrimeReceipt", "ScoreFreeCausalReferenceBank")
