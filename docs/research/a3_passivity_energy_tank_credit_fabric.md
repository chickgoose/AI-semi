# A3 W3 Passivity Energy-Tank Credit Fabric

Status: **model gate REJECT; no RTL permitted**, 2026-08-11

Scope is restricted to the new files under
`tests/a3_passivity_energy_tank/` and this report.  No common TB, manifest,
existing RTL, A1 file, or server input was modified.  The pre-existing
untracked `tests/clean_native/aer_ganghee_cluster2_real_direct_tb.sv` is not an
input and is deliberately excluded from the commit.

## 1. Structure and boundary

The model has 16 address-only sources, four transport lanes, and two registered
slots per lane.  It is a cycle-level slot fabric, not a generic FIFO model:

- an event advances at most one registered slot toward its endpoint per cycle;
- the vacancy left by a move is the reverse-moving empty-slot credit;
- source `s` has home lane `s mod 4`, but an idle lane may borrow a request;
- each source has one exact external pending occurrence and one candidate-owned
  inflight bit, so later occurrences cannot overtake an accepted occurrence;
- each lane retires at most one independent address-only event per cycle.

All three compared models have identical slots, lane-local rotating selection,
inflight guards, and four retire endpoints:

1. `baseline_elastic_credit`: any empty ingress may borrow;
2. `raw_energy_tank`: off-home borrowing spends one unit from the destination
   lane tank;
3. `empty_lane_bootstrap_escape`: raw rule plus the stateless escape
   `lane_empty && E == 0`.

The escape does not inspect age, wait, request count, or maximum pressure.  It
adds no state relative to raw; its incremental logic is an empty-lane reduction
and an enable term.  `ENERGY_MAX=1` is the minimum useful tank.

## 2. Update law and invariants

For lane `l`, let `b_l` be paid off-home admissions and `q_l` be forward moves
plus retirements.  The registered tank update is

```text
0 <= b_l <= E_l
E_l' = min(E_MAX, E_l - b_l + q_l)
```

A bootstrap admission has `b_l=0` only when the complete destination lane is
empty and `E_l=0`; ordinary borrowing still requires `b_l=1`.  Assertions check
`0 <= E_l <= E_MAX` every cycle.

Let `P` be exact source-pending count and slot stage `k=0..D-1` have remaining
work `D-k`.  The checked potential is

```text
Phi = P*(D+1) + sum(valid[l][k] * (D-k)) + sum(E_l)
```

With no new occurrence:

- pending-to-ingress admission decreases `Phi` by one;
- a slot move decreases work by one and replenishes at most one tank unit;
- retirement removes one work unit and replenishes at most one unit;
- paid borrowing additionally decreases tank energy;
- bootstrap still decreases pending work by one without increasing energy.

Therefore the implementation asserts `Phi' <= Phi` on every no-injection
cycle, including the complete drain after stimulus stops.

The other cycle assertions are

```text
accepted - retired == internal_stored
generated == overrun + retired              # after complete drain
one live copy of every (source, token)
inflight[source] iff an internal slot owns that source
retired_token[source] strictly increases
```

The token and occurrence cycle exist only in the checker.  Candidate state
stores address/source identity, not a payload.

## 3. Raw energy-island counterexample and escape

Directed state:

- occurrences from sources `{0,4,8,12}`, all homed on lane 0;
- lane 0 endpoint is stalled; lanes 1--3 remain ready;
- all lanes and all tanks begin empty with `E=0`.

After eight cycles:

| model | pending | stored | retired | bootstrap admissions |
| --- | ---: | ---: | ---: | ---: |
| elastic baseline | 0 | 1 | 3 | 0 |
| raw tank | 2 | 2 | 0 | 0 |
| escaped tank | 0 | 1 | 3 | 3 |

The raw candidate has routable pending events and three ready, empty lanes, but
those lanes cannot earn their first energy quantum because they have never
carried an event.  This is an energy-island deadlock, not ordinary lack of sink
capacity.  The escaped fabric bootstraps exactly those empty lanes while
keeping every `E_l` nonnegative.

The N=16 bounded search exhausts all `2^16` one-cycle occurrence masks for each
of four single-lane stalls, followed by seven no-injection cycles: 262,144
cases.  All invariants pass.  The raw deadlock appears in 20 cases and the
escape improves progress in all 20.  The smallest recorded witness is
`mask=0x0111`, sources `{0,4,8}`, stalled lane 0.

This is a bounded counterexample search, not an unbounded liveness proof.

## 4. Frozen trace replay

`evaluate.py` generates the exact current full50 and cap22 events in memory
from the committed manifests.  It does not edit or replace their traces.  In
addition to canonical always-ready replay, each trace is replayed with two
deterministic randomized independent-ready patterns; every eighth cycle is
forced all-ready so a permanent external stall is not mistaken for a candidate
deadlock.  This produced 300 full50 and 132 cap22 randomized mode/trace replays.
All completed, drained, and passed energy, potential, conservation, duplicate,
and per-source order assertions.

### Always-ready full50 aggregate

| model | fixed-window retired | overrun | mean/max latency | state bits | toggle/retired |
| --- | ---: | ---: | ---: | ---: | ---: |
| elastic baseline | 101,642 | 4,702 | 3.18269 / 7 | 72 | 12.35449 |
| raw tank | 101,640 | 4,704 | 3.20044 / 7 | 76 | 12.27422 |
| escaped tank | 101,641 | 4,703 | 3.18529 / 7 | 76 | 12.42591 |

### Always-ready cap22 aggregate

| model | fixed-window retired | overrun | mean/max latency | state bits | toggle/retired |
| --- | ---: | ---: | ---: | ---: | ---: |
| elastic baseline | 63,156 | 2,415 | 3.21691 / 7 | 72 | 11.75986 |
| raw tank | 63,155 | 2,416 | 3.22116 / 7 | 76 | 11.80780 |
| escaped tank | 63,155 | 2,416 | 3.22109 / 7 | 76 | 11.80869 |

State bits are a structural proxy: eight `(valid,address)` slots, 16 inflight
guards, four selection pointers, and for tank models four one-bit tanks.  The
common exact source-pending seam and TB-only tokens are excluded.  Toggle is
the bit-transition count across precisely those charged fields; it is not a
power or synthesis result.

Raw obtains a small full50 toggle reduction of 0.65% by refusing legal work,
but loses two events to extra overrun and raises latency.  The safe escape
removes the directed energy islands but is worse than baseline in both state
and toggle/event: +5.56% state, +0.58% full50 toggle/event, and +0.42% cap22
toggle/event.  It also has one extra overrun in each suite.

## 5. Predeclared GO gate and decision

The model had to satisfy all of the following before SV was allowed:

1. pass all invariants and bounded/random deadlock checks;
2. retain at least 99% of baseline cap22 fixed-window service;
3. add no cap22 overrun or full50 max-latency regression;
4. save at least 5% toggle/event or add no state;
5. strictly improve cap22 fixed-window service or overrun.

Only items 1, 2, and the max-latency portion of 3 pass.  The escaped fabric has
one more cap22 overrun, four more state bits, higher toggle/event, and no strict
performance benefit.  More permissive escape rules converge to the elastic
baseline; more restrictive rules recover raw's energy islands.  Adding age or
maximum-pressure selection would violate the assigned mechanism and still not
create capacity.

**Decision: REJECT / NO-GO.**  The minimal escape is correct but dominated by
the equal-lane, equal-depth elastic-credit baseline.  Per the gate, no SV RTL,
formal wrapper, or assertion TB was created.

## 6. Reproduction

Fast self-check:

```sh
python3 -m unittest -v tests.a3_passivity_energy_tank.test_passivity_model
```

Complete exhaustive and full50/cap22 replay:

```sh
python3 tests/a3_passivity_energy_tank/evaluate.py \
  --random-trials 2 --compact \
  --output tests/a3_passivity_energy_tank/w3_results.json
```

The committed `w3_results.json` is the compact machine-readable receipt.
