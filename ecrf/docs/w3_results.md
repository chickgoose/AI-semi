# ECRF Wave-3 results

Status: **HOLD; pre-RTL gate failed.**  No SystemVerilog or lockstep TB was
added, as required by the preregistered conditional implementation rule.

## Reproduction and provenance

Run:

```bash
ECRF_COMMON_ROOT=/path/to/read-only/common-v4 \
  ECRF_OUT="$PWD/ecrf/results" bash ecrf/run_w3.sh
```

The runner enforces the commit and three SHA-256 values below before creating
temporary traces.  Its decision exit contract is:

- default mode: exit 0 means the evaluation completed, with the printed
  `decision=GO|HOLD` remaining authoritative;
- `--require-go`: exit 0 only for GO and exit 3 for a valid HOLD;
- exit 2: malformed decision or provenance/input contract failure; and
- exit 64: runner argument error.

Qualification/shortlist automation must use `--require-go`.  Thus the recorded
HOLD cannot be mistaken for GO from exit status alone.

The recorded run used read-only common commit
`47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be` with these inputs:

- `generate_trace.py` SHA-256
  `59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`;
- `manifest.neutrality-n16.json` SHA-256
  `9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9`
  (50 runs); and
- `manifest.multilane-n16.json` SHA-256
  `99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62`
  (22 runs).

The runner generates traces only in a unique `/tmp/ecrf-w3.*` directory.
The committed result CSV records the SHA-256 of every generated trace.

## Exhaustive result

The search found 52 Hall-feasible `(K,B,d,seed)` representatives and rejected
25 parameter points for which none of the 64 seeds met truncated Hall.  Each
selected point covered every 16-bit active mask and every K-bit lane mask,
including zero availability:

| K | selected point | exhaustive cases | all invariant/error counters |
|---|---|---:|---:|
| 2 | B=2,d=2,seed=0 | 262,144 | 0 |
| 4 | B=4,d=4,seed=0 | 1,048,576 | 0 |

The zero counters cover illegal grants, source/cell/lane duplication,
`accepted > K`, snapshot P-invariant failure, reaction deadlock, and failure
to reach an oracle-certified target.  Maximum reaction rounds were 2 and 4.

The automatic negative searches still found the limits hidden by the selected
fully connected points:

- 25 Hall-rejected points have explicit deficient-neighborhood witnesses.
- Twelve Hall-feasible K=4 sparse representatives had 1,272 bounded-capacity
  failures.  For example `k4_b10_d2_s41`, active `0x0109`, lane mask `0x7`,
  reaches only sources `[0,3]` although the oracle proves a matching of 3.
- Every checked representative has a structural peeling
  siphon/stopping-set candidate.  On the selected points the minimum witness
  is sources `[0,1]` (`0x0003`) with every neighboring cell having active
  degree 2.  This is a structural witness, not an observed reaction deadlock;
  observed reaction-deadlock counters are zero.

All witnesses are machine-readable in `results/counterexamples.json`.  P is
checked directly for every case as
`popcount(pending_after) + accepted == popcount(active_before)`.

## Flat K-grant comparison

The only fully functional minimum-proxy choices degenerate to complete
source-to-cell connectivity and produce exactly the same trace decisions as
the fixed-priority flat K-grant reference.  Across all 72 traces there are
zero differences in accepted, delivered, fixed-window delivered, overrun,
average/p95/p99/max latency, or drain cycle.

| suite | K | generated | accepted/delivered | fixed-window delivered | overrun | max trace p95/p99 | max latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| full50 | 2 | 106,416 | 104,005 | 103,970 | 2,411 | 9/9 | 521 |
| full50 | 4 | 106,416 | 106,405 | 106,370 | 11 | 5/5 | 5 |
| capacity22 | 2 | 65,616 | 63,205 | 63,184 | 2,411 | 9/9 | 521 |
| capacity22 | 4 | 65,616 | 65,605 | 65,584 | 11 | 5/5 | 5 |

Each row applies equally to flat and ECRF.  Overrun is caused by the frozen
one-pending-source replay model and is not counted as accepted.  Conservation
is exact after drain.

The selected structural proxies fail all PPA gates:

| K | wire ECRF/flat | work ECRF/flat | depth ECRF/flat |
|---|---:|---:|---:|
| 2 | 36/32 (1.125x) | 140/32 (4.375x) | 14/8 (1.75x) |
| 4 | 80/64 (1.25x) | 592/64 (9.25x) | 36/16 (2.25x) |

The required wire gate is at most 0.85x; work and depth must be at most 1.0x.
Sparse K=4 alternatives can reduce wire to 1.0x, but fail bounded capacity,
while no explored point reaches the 0.85x wire threshold.  Therefore ECRF
provides no throughput or latency gain to repay its matching cost and remains
HOLD.
