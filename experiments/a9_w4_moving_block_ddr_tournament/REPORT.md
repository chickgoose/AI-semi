# W4 A9 Moving-Block Core + DDR Link Pareto Tournament

## Decision and frozen inputs

This is a **simple serial composition, not a new architecture**.  The A4 core
changes vacancy propagation inside a one-output merge tree; A7 changes only the
four-bit address link after that output.  Neither mechanism creates the other's
benefit, and the link cannot raise the core's one-event/core-cycle peak.

The model reads, without checkout or modification:

- A4 `850fbcfa4ad168b1250223610780f11378f6c391`, exact
  `rtl/candidates/a4_moving_block_tree/model.py`;
- A7 `31947a71ddfcf678f6cd593954df34b27806a63d`, exact TX, RX, and wrapper RTL;
- common snapshot `47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be`, generator 4.0,
  full50 manifest SHA-256 `9fe40060...f2bba9`, and capacity22 manifest
  SHA-256 `99a8bbd3...8c62`.

The runner materializes the common commit in a secure temporary directory and
generates all 50/22 traces there.  It verifies every official trace hash.  It
does not read or write common result directories.

This report deliberately freezes the requested pre-ICG A7 commit.  The latest
reviewed A7 is `db3f04fe0e01699e63c596145fe71effc601e57c`; its structural-evidence
ancestor `a349d64d8b8b3d4398a258926af493b5da1e3ac2` includes the separate ICG
boundary and reports 13 state bits rather than 12.  Neither is silently
substituted here.  Consequently all 12-bit DDR state and toggle rows below
describe only `31947a7`.  The latest ICG latch/cell result needs a separate
combined replay before it can replace these rows.

The source-linked prior-art and claim boundary is recorded separately in
[`a9_w4_a7_ddr_novelty_literature_audit.md`](../../docs/research/a9_w4_a7_ddr_novelty_literature_audit.md).

## Cycle contract and accounting

Four points use the same occurrence stream and 16 one-pending source latches:

1. fixed one-step core + direct parallel 4-bit/strobe reference;
2. moving two-step core + parallel reference (A4 improvement only);
3. fixed core + A7 2-bit DDR link (A7 improvement only); and
4. moving core + A7 link (serial combination).

The core first drains completely.  Its retirement is the link admission.  At
an abstract legal event-token boundary, both links have service `R` events per
core cycle for `R=1,2,4`.  Since either core emits at most one, the analytical
maximum cumulative A4-to-A7 boundary backlog is zero and the required **added
boundary FIFO** is zero.  This does not mean the system has zero storage: the A4
core retains 31 event slots and the common ingress driver retains 16 source latches.
No
FIFO, skid entry, retransmit state, or backpressure repair is inserted.  A7's
quarter-shifted falling burst-clock edge commits after `3/(4R)` core cycles;
the parallel reference commits at its registered core output boundary.

Every DUT-visible 32-bit event word is now exactly the zero-extension of its
four-bit logical source/address.  Sequence and occurrence identity never enter
an A4 node: monotonic IDs and timing live only in source-local TB scoreboard
deques, which are popped on source-ordered retirement.  The model asserts
`payload == source <= 15` in every occupied node and retirement.

State is 1,162 bits for either A4 core: `31*(32 address + 4 source + 1 valid) +
15 phase`.  A7 `31947a7` adds exactly 12 functional register bits: TX address/enable (5)
and RX partial/address/toggle (7).  The parallel reference adds no state.  The
state-toggle proxy counts Hamming changes of committed core event blocks and
phase bits.  Because A4's two microsteps are combinational within a cycle, this
proxy can decrease while its separately reported control-touch proxy doubles;
it is not a power estimate.  Link toggles use the actual delivered address
sequence, clock/strobe edges, and the old A7's 12 register bits.  They also now
include `31947a7`'s idle behavior: `burst_data_o` remains selected by `ref_clk_i`
and can alternate the held address's low/high symbols even while the forwarded
burst clock is stopped.  A separate unweighted edge proxy counts both always-
running `ref_clk_i` and `sample_clk_i`; it is kept separate because their clock-
tree capacitances are unknown.  This corrects the former event-only undercount
without pretending an edge is a power value.

## Analytical full50 results at the rate-compatible R=1 boundary

These are cycle-model results, not an executed A4-RTL-to-A7-RTL composition.
The analytical launch envelope assumes one legal A7 admission per A4
retirement.  Reset is not represented in the composition model, so this table
provides no reset-path evidence.

All accepted events drain, so core A/D and link A/D are equal.  Latency is
occurrence through link commit.  `data/state tog/e` includes committed core-
state, link-wire, and link-register proxies.  It excludes child-control touches
and the separately shown unweighted A7 input-clock edges.

| core + link | core A/D | link A/D | overrun | throughput | mean / p95 / p99 e2e | pins | state bits | data/state tog/e | A7 clk edges/e | control touches/e |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed + parallel | 83,514 | 83,514 | 22,902 | 0.729214327 | 14.943 / 44 / 46 | 5 | 1,162 | 31.999 | 0 | 41.140 |
| moving + parallel | 83,555 | 83,555 | 22,861 | 0.729999388 | 14.073 / 45 / 47 | 5 | 1,162 | 22.253 | 0 | 82.192 |
| fixed + DDR | 83,514 | 83,514 | 22,902 | 0.729214327 | 15.693 / 44.75 / 46.75 | 3 | 1,174 | 38.996 | 5.485 | 41.140 |
| moving + DDR | 83,555 | 83,555 | 22,861 | 0.729999388 | 14.823 / 45.75 / 47.75 | 3 | 1,174 | 29.232 | 5.479 | 82.192 |

Moving admits 41 additional events, reduces overrun by 41, and raises aggregate
throughput by 0.000785061 (0.108%).  The lower mean is not a uniform tail win:
p95/p99 are one cycle worse and the survivor sets differ.  DDR changes none of
accepted, delivered, overrun, or throughput.  It saves two pins at the cost of
12 bits, 0.75 cycle, and about 6.98 additional modeled wire/register toggles per
delivered event at R=1.  Correct address-only data lowers the core state-toggle
proxy; it does not change accepted, delivered, latency, or overrun.

## Analytical capacity22 results at the rate-compatible R=1 boundary

| core + link | core A/D | link A/D | overrun | throughput | mean / p95 / p99 e2e | pins | state bits | data/state tog/e | A7 clk edges/e | control touches/e |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed + parallel | 42,948 | 42,948 | 22,668 | 0.789979031 | 22.742 / 45 / 46 | 5 | 1,162 | 32.729 | 0 | 37.976 |
| moving + parallel | 42,983 | 42,983 | 22,633 | 0.790855566 | 22.586 / 46 / 47 | 5 | 1,162 | 24.117 | 0 | 75.867 |
| fixed + DDR | 42,948 | 42,948 | 22,668 | 0.789979031 | 23.492 / 45.75 / 46.75 | 3 | 1,174 | 39.624 | 5.063 | 37.976 |
| moving + DDR | 42,983 | 42,983 | 22,633 | 0.790855566 | 23.336 / 46.75 / 47.75 | 3 | 1,174 | 30.984 | 5.058 | 75.867 |

Moving admits 35 additional events, reduces overrun by 35, and raises aggregate
throughput by 0.000876535 (0.111%).  Again p95/p99 regress by one cycle.  DDR is
a pin/latency/state trade only.

Across both suites, fixed A4 reaches 30/31 occupied internal nodes and moving A4
reaches all 31; both exercise all 16 ingress source latches.  These existing
storage resources are included in the functional model even though the
candidate comparison counts their state equally.  Only the new serial boundary
FIFO count is zero.

## Link-ratio envelope and boundary blocker

The capacity envelope is identical for fixed and moving cores except for their
slightly different output rates:

| suite/core | R | service utilization | DDR commit delay | added buffer | throughput limiter | analytical boundary disposition |
|---|---:|---:|---:|---:|---|---|
| full50 fixed | 1 / 2 / 4 | 0.7292 / 0.3646 / 0.1823 | 3/4 / 3/8 / 3/16 | 0 | core/ingress | rate-compatible / HOLD / HOLD |
| full50 moving | 1 / 2 / 4 | 0.7300 / 0.3650 / 0.1825 | 3/4 / 3/8 / 3/16 | 0 | core/ingress | rate-compatible / HOLD / HOLD |
| capacity22 fixed | 1 / 2 / 4 | 0.7900 / 0.3950 / 0.1975 | 3/4 / 3/8 / 3/16 | 0 | core/ingress | rate-compatible / HOLD / HOLD |
| capacity22 moving | 1 / 2 / 4 | 0.7909 / 0.3954 / 0.1977 | 3/4 / 3/8 / 3/16 | 0 | core/ingress | rate-compatible / HOLD / HOLD |

The old DDR link's modeled wire+register toggles and separate unit-clock edges
per delivered moving-core event grow with the faster always-running reference,
sample clock, and mux activity:

| suite | R=1 data+state / clk | R=2 envelope | R=4 envelope |
|---|---:|---:|---:|
| full50 | 11.144 / 5.479 | 15.467 / 10.959 | 20.599 / 21.918 |
| capacity22 | 11.276 / 5.058 | 15.533 / 10.117 | 20.201 / 20.235 |

The R=2/4 numbers are a capacity envelope, **not an exact composed RTL result**.
A4 exposes a level-valid root for one core period.  Exact A7 samples
`event_valid_i` at every faster `ref_clk_i` edge and advertises no queue or
one-shot qualifier.  Direct wiring therefore creates `R-1` extra capture
opportunities per asserted core period, allowing duplicate or early frames.
Providing a one-link-period launch pulse requires unimplemented boundary
functionality and state whose PPA/toggle/latency cost is unknown.  This
tournament neither restores it for free nor
adds an arbitrary queue, so R=2 and R=4 fail closed.  R=1 is only analytically
rate-compatible: no composed RTL replay or reset-path test was run.  Old A7's
generated-clock/DDR physical qualifications also remain HOLD.  The separate
latest ICG-latch implementation is not evidence for this frozen row.

## Pareto disposition

At the only analytically rate-compatible point, R=1, none of the four points
strictly dominates all others:

- fixed + parallel minimizes state, local logic depth, control touches, and
  latency, but uses five pins and has the lower accepted count;
- moving + parallel gains only 0.11% throughput and mean latency while doubling
  control touches and worsening aggregate p95/p99;
- fixed + DDR reaches three pins without changing throughput, but adds state,
  toggles, three quarters of a cycle, and physical risk;
- moving + DDR merely combines those independent tradeoffs.  It is not a fifth
  scheduling or transport principle.

The tournament is **HOLD for adoption/PPA**.  A4 is locally modeled but common
qualification/PPA remain HOLD; A7 physical timing, forwarded-clock, and PVT
closure remain HOLD; and R=2/4 additionally lack an exact legal launch boundary.

## Reproduction

```sh
cd /home/chickgoose/projects/a9/experiments/a9_w4_moving_block_ddr_tournament
python3 -m unittest -v test_w4_tournament.py
tmp_dir=$(mktemp -d /tmp/a9-w4.XXXXXX)
python3 w4_tournament.py --output "$tmp_dir/report.json"
```
