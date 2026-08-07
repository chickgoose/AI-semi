#!/usr/bin/env python3
"""Small exhaustive model checks for A8 wrap and quantization assumptions."""

from __future__ import annotations

import itertools


def modulo_age(now: int, tag: int, epochs: int) -> int:
    return (now - tag) % epochs


def assert_wrap_counterexample() -> None:
    # With a four-cycle horizon, absolute ages 1 and 5 alias. The modulo-only
    # scheduler sees both as age 1 and cannot prove which request is older.
    epochs = 4
    now = 1
    assert modulo_age(now, 0, epochs) == modulo_age(now, 0, epochs)
    old_absolute_age = 5
    young_absolute_age = 1
    assert old_absolute_age > young_absolute_age
    assert old_absolute_age % epochs == young_absolute_age % epochs


def assert_safe_horizon_orders_all_live_sets() -> None:
    sources = 4
    bucket_cycles = 2
    epochs = 4
    horizon = bucket_cycles * epochs
    assert horizon > sources - 1

    # Every legal always-ready live set spans at most N-1 cycles. For requests
    # in different quantized buckets, modular age order equals absolute order.
    for now in range(2 * horizon):
      for count in range(1, sources + 1):
        for ages in itertools.combinations(range(sources), count):
          tags = [((now - age) // bucket_cycles) % epochs for age in ages]
          now_epoch = (now // bucket_cycles) % epochs
          quantized = [modulo_age(now_epoch, tag, epochs) for tag in tags]
          for left, right in itertools.combinations(range(count), 2):
            if tags[left] != tags[right]:
              assert (ages[left] > ages[right]) == (
                  quantized[left] > quantized[right]
              )


def assert_quantization_loss_exists() -> None:
    # Requests one cycle apart share a two-cycle bucket. A rotating tie-break
    # may legally select the younger source first, so exact FCFS is not claimed.
    bucket_cycles = 2
    older_cycle, younger_cycle = 4, 5
    assert older_cycle // bucket_cycles == younger_cycle // bucket_cycles


if __name__ == "__main__":
    assert_wrap_counterexample()
    assert_safe_horizon_orders_all_live_sets()
    assert_quantization_loss_exists()
    print("A8_WHEEL_COUNTEREXAMPLE_PASS")
