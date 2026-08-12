# A7 P6 exact-pair endpoint digital result

Date: 2026-08-13
Scope: isolated reusable endpoint; no scheduler ranking and no server P&R

## Result

The P6 endpoint accepts one atomic singleton or ordered pair per reference
cycle after reset arm, transmits it in one five-data-wire DDR cell, and retires
one or two addresses together in original lane order.  It contains no queue,
aggregation wait, predictor, or scheduler state.

The fair parallel reference has the same input guard, commit edge, ref-domain
observer, retirement semantics, and maximum two-event/cycle capacity.  It
differs only in physical link representation: ten signals rather than P6's six.

## Digital tests

- frozen N16 neutrality generator: 46/46 identities PASS;
- independent model: all 272 legal singleton/ordered-pair words unique and
  exact round-trip;
- RTL/parallel lockstep: 1,262 accepted and retired events, including all 272
  legal words, back-to-back cells, idle gaps, legal drain-reset-rearm, and raw
  P6 edge/word checks;
- illegal-count/overflow mutation: expected fail;
- early-ready/stall mutation: expected fail;
- retained-reset/phantom mutation: expected fail;
- pair-order-swap mutation: expected fail; and
- protected common/team paths: zero diff.

## Frozen trace replay

The exact 46 generated traces contain 87,000 events.  A deterministic rotating
K2 source-latch projection accepts all 87,000 with zero source overrun and emits
68,476 endpoint transactions:

| Metric | Result |
| --- | ---: |
| singleton records | 49,952 |
| ordered-pair records | 18,524 |
| total link cells | 68,476 |
| events/link cell | 1.270518 |
| endpoint queue overflow | 0 |
| endpoint queue state | 0 bits |
| accepted-to-retire latency | 1 reference cycle |
| frozen A/B gaps exact | 256/256 (100%) |

This replay establishes endpoint compatibility with a K2 stream.  It does not
rank original-prefix, segmented-prefix, replicated, or weighted schedulers.

## Generic Yosys structure

Yosys 0.52 used the same `proc; flatten; opt; ltp; techmap; opt; ltp` flow for
both complete endpoints.

| Implementation | Link signals | Operator cells | Generic comb gates | State bits | Queue bits | Operator/gate depth |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| P6 DDR5 | 6 | 58 | 59 | 40 | 0 | 13 / 15 |
| parallel pair | 10 | 50 | 48 | 34 | 0 | 13 / 15 |

P6 removes four physical signals at this model boundary but adds eight
operator cells, eleven generic combinational gates, and six state bits versus
the fair parallel endpoint.  These are technology-independent generic proxies,
not area, Fmax, or power results.

## Qualification

**Digital functional GO** is limited to the frozen phase-related, always-ready
receiver contract.  The 6-pin physical link, characterized ICG/ODDR/IDDR cells,
half-cycle setup/hold, duty/skew, reset recovery/removal, CDC/RDC, pin loading,
CTS/routing, signal integrity, extracted power, and energy/event all remain
**HOLD**.  No server synthesis or P&R was run.

## Reproducibility hashes

The release run at `/tmp/a7-p6-release` recorded:

```text
799a3ab7fda211f9cda8109fce09df88e1462deae0e72bc2e775262c9bc8890e  frozen.expected.json
7eb9951e30a2220e3aebb2fdd28bce3b6b2920b88d147ff383562706fd6f9aa1  frozen.observed.csv
73710e2bdf6ed256df3b44186df29373b3c187623449bbbfa6060795ae99007c  structural.csv
```
