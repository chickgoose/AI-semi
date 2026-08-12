# A4 Fovea+A7 common-trace integration

Status: **LOCAL_RTL_TRACE_REPLAY_PASS** with explicit **HOLD** for frozen-common-TB,
common analyzers/receipt, reset beyond initial release, and physical/PPA qualification.

This candidate-owned harness extracts the exact W6 owner hierarchy from hardened
owner commit `e9f27e6aed302491011a5deb803a7b42a0c712b3`, verifies every owner RTL blob
and the canonical three-file
fovea hashes, and replays prepared generator-v4 address-only traces. It uses the
owner's 16 ns reference clock and quarter-phase sample clock: sample rise is 4 ns
after a reference rise and sample fall is 4 ns before the next reference rise.
Initial reset releases on sample fall exactly 4 ns before the next reference rise.
A real pre-NBA synchronous consumer must observe every accepted address exactly
two reference cycles later. Midstream reset, unrelated clocks, and sink
backpressure are deliberately not claimed by this replay.

The harness reproduces the common one-pending-event/source ingress rule (16
TB-side pending slots for N=16) and
emits the current `trace.events.csv` and `trace.csv` schemas. Reference arrays
are TB-only scoreboards. The candidate hierarchy has zero event queue entries
and a scalar limit of one accepted/delivered event per reference cycle. An
occurrence while its source slot is already pending is `source_overrun`; this is
workload ingress accounting, not hidden candidate capacity. No free queue,
retry, reorder, payload, or synthesized adapter is inserted.

```sh
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite smoke --output /tmp/a4-fovea-a7-smoke
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite capacity22 --output /tmp/a4-fovea-a7-cap22
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite full50 --output /tmp/a4-fovea-a7-full50
```

`smoke` selects `core_simultaneous_identity` from an otherwise exact full50
generation. `full50` executes all 50 official stems and `capacity22` all 22
official stems, with exact official ordering, frozen trace hashes, and
address-only metadata. “capacity22” means the 22-run capacity suite, not a
22-entry hardware queue. These are actual RTL replays, but not the frozen common
TB or its four analyzers. The runner refuses output reuse and fails closed on
tool/source mutation, manifest or result provenance, trace cardinality,
conservation, address/order, exact +2 latency, exact reset phase, drain guards,
or missing/duplicated PASS markers. `receipt.json` records per-result hashes and
prints both the qualified local PASS and remaining HOLD scope.
