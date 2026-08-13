# A4 Paired-Cortical-Column-K2

This is an isolated N=16, K=2 scheduler candidate. It does not modify or
instantiate frozen common, manifest, team, A5, or A8 RTL.

## Contract

The scheduler boundary is one atomic bundle:

- `grant_count` is 0, 1, or 2; `grant_addr[3:0]` is first and
  `grant_addr[7:4]` is second.
- `bundle_ready=0` holds the count, ordered addresses, request snapshot, and
  every policy register stable. `source_ready` remains zero.
- `bundle_ready=1` commits every valid address together. Policy advances only
  for the committed offer, by exactly `grant_count` successful microsteps.
- reset forces count, addresses, and source-ready to zero even with live input
  pins, aborts an uncommitted offer, and clears policy; `drain_idle` is one.

The optional `a4_pcck2_ordered_link_adapter.sv` is separate transport. It
accepts a scheduler bundle atomically into a two-entry FIFO, presents two
ordered retirement lanes, gates lane-1 valid with lane-0 readiness so `10`
cannot handshake a younger record, and does not mutate scheduler policy in
response to a partial downstream stall.

## Policy

The six-phase committed row-pair calendar is:

```text
(row1,row2), (row1,row2), (row1,row2),
(row1,row2), (row1,row2), (row0,row3)
```

Thus 12 continuously backlogged committed events have row aggregate
`[1,5,5,1]`. Each row owns one four-column rotating arbiter; only four compact
row winner summaries enter the pair/debt selection logic.

If a scheduled row is empty, a work-conserving fallback row can borrow the
token. The scheduled row records bounded positive debt. Debt service precedes
new calendar tokens. At debt saturation fallback can still issue, but the
scheduled token does not advance. Debt therefore cannot wrap, be silently
dropped, or flatten weights by continued calendar advancement.

This is an **aggregate-preserving calendar**, not scalar-prefix equivalence.
Its first full epoch row order is `1,2,1,2,1,2,1,2,1,2,0,3`; it intentionally
does not claim the A5 scalar wheel or A8 paired-row oracle sequence/debt law.

## Reproduction

Required tools are resolved before work starts; missing Verilator or Yosys is
a hard failure. Output paths must not already exist.

```bash
python3 rtl/candidates/a4_paired_cortical_column_k2/run_qualification.py \
  --common-root /home/chickgoose/projects/a1 \
  --verilator /tmp/a7-sim-bin/verilator \
  --yosys /tmp/a7-yosys/usr/bin/yosys \
  --work-dir /tmp/a4-pcck2-qualification \
  --output /tmp/a4-pcck2-qualification.json
```

The run performs warning-free lint, six independent-model tests, seven directed
RTL/model locksteps, four ordered-link subcases, five RTL mutation kills, exact
hash-locked generator-v4 replay (`full50` plus the `capacity22` subset) through
both model and RTL, generic Yosys structural measurement, and immutable A5/A8
contract cross-checks.

See `docs/architecture.md` for semantics and physical limits. Tracked result
JSON is a reproducible snapshot; `/tmp` logs and generated traces are not
committed.
