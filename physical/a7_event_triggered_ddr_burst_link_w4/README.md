# A7 W4 physical experiment bundle

This directory freezes the experiment that may later run with Genus, Innovus,
and a site CDC/RDC tool. It neither contains nor implies a server run. Repository
state remains `PHYSICAL_HOLD` until non-synthetic site and result receipts pass.

## Three fail-closed gates

1. `--contract-only` verifies immutable RTL hashes, activity schedule, timing
   tokens, reset-exception policy, boundary, and schema. Passing still prints
   `A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN`.
2. `--site-manifest` requires real executable hashes/versions, PVT/RC files,
   setup/hold Liberty, named ICG/ODDR/IDDR or characterized equivalent cells in
   both Liberty views, cell-query evidence, exact loads/transitions, and both
   activity files. This permits a controlled run; it is not physical evidence.
3. `--results-receipt` requires mapped cells and reports, both-edge and both
   half-cycle setup/hold, clock-gating checks, recovery/removal, pulse width,
   skew, route/extraction, zero unconstrained/DRC/antenna violations, CDC/RDC,
   annotated power coverage, and recomputed energy/event.

Synthetic fixtures require both `--allow-synthetic-fixture` and
`synthetic_fixture:true` in site and receipt. The flag rejects production-labeled
manifests, and synthetic manifests are rejected without the flag. Even a
complete fixture prints `A7_W4_PHYSICAL_HOLD_EDA_NOT_RUN` and can never qualify.

## Frozen boundary and activity

The boundary is complete W4 TX + technology ICG boundary + two data wires +
forwarded clock + RX. The downstream core synchronizer is excluded and must be
classified as an explicit asynchronous output boundary. Every run uses 0.01 pF
per output, 0.05 ns clock transition, and 0.10 ns data transition. Comparisons
must retain the same logical endpoints and per-pin load; three link pins remain
charged.

The address sequence is `(5*event_index+3) mod 16`. After 16 warm-up cycles,
power is measured for 512 cycles at sparse 1/8 and saturated 1/cycle offered
load. Reset and drain are excluded identically. Activity coverage must be at
least 95%; vectorless power cannot qualify.

```text
energy_pJ_per_event = total_power_mW * measurement_duration_ns /
                      completed_logical_events
```

Zero completed events are invalid. Power must include the post-route clock tree
and full frozen boundary.

## Commands

```sh
scripts/physical/a7_w4_physical_preflight.py --contract-only

scripts/physical/a7_w4_physical_preflight.py \
  --site-manifest /path/to/site-manifest.json

scripts/physical/a7_w4_physical_preflight.py \
  --site-manifest /path/to/site-manifest.json \
  --results-receipt /path/to/results-receipt.json
```

Start from the two templates. Do not edit the frozen contract after examining
EDA results; create a new versioned experiment instead.
