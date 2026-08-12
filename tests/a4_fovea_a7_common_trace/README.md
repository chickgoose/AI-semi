# A4 Fovea+A7 common-trace integration

Status: **LOCAL_RTL**, not frozen-common-TB or physical qualification.

This candidate-owned harness extracts the exact W6 owner hierarchy from commit
`b5201254bceb39b3563370567355efe17a3b5e16`, verifies the canonical three-file
fovea hashes, and replays prepared generator-v4 address-only traces. It uses the
owner's 16 ns reference clock and quarter-phase sample clock: sample rise is 4 ns
after a reference rise and sample fall is 4 ns before the next reference rise.
Reset releases on sample fall. A real pre-NBA synchronous consumer must observe
every accepted address exactly two reference cycles later.

The harness reproduces the common one-pending-event/source ingress rule and
emits the current `trace.events.csv` and `trace.csv` schemas. Reference arrays
are TB-only scoreboards. No queue, retry, reorder, payload, or synthesized
adapter is inserted in the owner hierarchy.

```sh
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite smoke --output /tmp/a4-fovea-a7-smoke
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite capacity22 --output /tmp/a4-fovea-a7-cap22
python3 tests/a4_fovea_a7_common_trace/run_common_trace.py \
  --suite full50 --output /tmp/a4-fovea-a7-full50
```

`smoke` selects `core_simultaneous_identity` from the exact full50 generation.
The other modes are full infrastructure and intentionally fail closed on any
tool, manifest, trace, owner RTL, canonical fovea, conservation, address/order,
latency, phase, or output-reuse mismatch.
