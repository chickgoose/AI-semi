# A7 P6 exact-pair endpoint digital result

Date: 2026-08-13
Scope: isolated reusable endpoint; no scheduler ranking and no server P&R

## Result

The P6 endpoint accepts one atomic singleton or ordered pair per reference
cycle after reset arm, transmits it in one five-data-wire DDR cell, and retires
one or two addresses together in original lane order.  It contains no queue,
aggregation wait, predictor, or scheduler state.

Follow-up audit of commit `4dcafd8` added a normalized scheduler wrapper.  A
valid bundle carries `grant_count=0/1/2`; one `bundle_ready` handshake commits
all valid lanes and reports exactly 0/1/2 policy microsteps.  A count-zero
commit launches no P6 cell.  No per-lane scheduler ready or commit exists.

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
- pair-order-swap mutation: expected fail;
- atomic partial-pair policy-step mutation: expected fail;
- normalized atomic bundle RTL: 7 bundles, including one count-zero no-op,
  11 committed/retired policy events, zero partial scheduler commits;
- held legal bundle across reset/arm stall: stable and committed once;
- A5 evaluator commit `41c425b`: 5/5 unit tests and 7/7 declared mutations
  PASS in an isolated read-only extraction; this qualifies the evaluator only,
  not A7 scheduler RTL; and
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

The structural table remains the nonempty link-core comparison from commit
`4dcafd8`.  The follow-up atomic frontend is scheduler-side normalization,
adds no physical link pin, and is shared identically by P6 and parallel
wrappers; it is not folded into either endpoint's published core proxy.

Independent `retire_ready[1:0]` stalls from the A5 evaluator are downstream of
this always-ready receiver.  A separately buffered lane adapter is required
where those stalls exist and its queue/state must be charged equally.  It may
drain its own lanes independently, but it cannot create partial scheduler
commits or advance scheduler policy.  No hidden Q2 was added here.

## Qualification

**Digital functional GO** is limited to the frozen phase-related, always-ready
receiver contract.  The 6-pin physical link, characterized ICG/ODDR/IDDR cells,
half-cycle setup/hold, duty/skew, reset recovery/removal, CDC/RDC, pin loading,
CTS/routing, signal integrity, extracted power, and energy/event all remain
**HOLD**.  No server synthesis or P&R was run.

## Reproducibility hashes

The follow-up release run at `/tmp/a7-p6-contract-final` reproduced:

```text
799a3ab7fda211f9cda8109fce09df88e1462deae0e72bc2e775262c9bc8890e  frozen.expected.json
7eb9951e30a2220e3aebb2fdd28bce3b6b2920b88d147ff383562706fd6f9aa1  frozen.observed.csv
73710e2bdf6ed256df3b44186df29373b3c187623449bbbfa6060795ae99007c  structural.csv
```
