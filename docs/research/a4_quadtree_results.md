# A4 quadtree local verification and frozen-trace results

Status: first-pass reference-model evidence; RTL metrics are superseded by
[`a4_quadtree_verilator_qualification.md`](a4_quadtree_verilator_qualification.md).
Server qualification remains `PENDING_HEAD_XCELIUM`, 2026-08-07

The subsequent candidate-only topology/mapping falsification is reported in
[`a4_quadtree_mapping_sensitivity.md`](a4_quadtree_mapping_sensitivity.md). It
does not alter or supersede the frozen-46 RTL qualification; it rejects broad
mapping-neutrality claims and defines placement/padding rejection conditions.

## Evidence boundary

No common SSH/tmux pane or server file was used after head execution control.
No Xcelium, Genus, Innovus, synthesis, or P&R job is claimed here.  The local
evidence consists of:

- Icarus 12 node unit simulation;
- Icarus 12 direct full-tree simulation;
- an independently written cycle-accurate Python model of the committed RTL;
- the same model applied to a flat one-slot RR reference;
- all 46 frozen N=16 JSONL traces with their manifest SHA checked; and
- 100 additional randomized 256-cycle falsification seeds.

The Python model is not a substitute for RTL/common-TB qualification.  Its role
is to falsify architecture assumptions and quantify likely topology behavior
before the head spends a server slot.  Raw per-run results are frozen in
[`a4_local_model_46.csv`](results/a4_local_model_46.csv).

## Local RTL results

| Test | Result | Checked behavior |
| --- | --- | --- |
| `a4_quadtree_node_tb` | PASS | reset quiet, one-hot RR, stall stability, child 0/1/2/3 wrap, same-edge pop/refill |
| `a4_quadtree_fabric_tb` | PASS | 16 simultaneous source latches, four accepted on first edge, accepted=delivered=16, no duplicate/corruption, quiet drain |
| full-tree progress observation | PASS | last of 16 simultaneous events delivered at local model/test cycle 17, within the documented conservative bound 21 |

Reproduction used a `/tmp`-extracted Icarus binary because no simulator was
installed on the local PATH:

```bash
iverilog -g2012 -Wall -s a4_quadtree_node_tb -f tests/a4/node.f -o /tmp/a4-node.vvp
vvp /tmp/a4-node.vvp
iverilog -g2012 -Wall -s a4_quadtree_fabric_tb -f tests/a4/tree.f -o /tmp/a4-tree.vvp
vvp /tmp/a4-tree.vvp
```

The frozen interface uses constructs that local Icarus does not parse
(interface modports and concurrent SVA).  Therefore common-TB RTL results remain
`PENDING_HEAD_XCELIUM`; they are not inferred from the direct top test.

## Local model qualification

```bash
python3 benchmarks/clean_slate_aer/generate_trace.py \
  --manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  --output-dir /tmp/a4-traces
python3 tests/a4/quadtree_reference.py \
  --trace-dir /tmp/a4-traces \
  --output docs/research/results/a4_local_model_46.csv --self-test
```

Result: 46/46 A4 and 46/46 flat-reference runs completed, 92/92 transport
checks had zero accepted-event error, and 100/100 random A4 seeds passed loss,
duplicate, source-local order, overwrite, drain, and node-occupancy assertions.
The model checks trace SHA before each run.

## Required workload comparison

All latency values are occurrence-to-delivery cycles. `epc` counts deliveries
inside the fixed stimulus window. Fairness is demand-normalized Jain fairness;
`min ratio` is the minimum accepted/offered ratio among offered sources.

| Frozen trace | Candidate | overrun | epc | p95 | p99 | max wait | fairness | min ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sparse identity | A4 | 0 | 0.03125 | 2 | 2 | 0 | 1.0000 | 1.0000 |
| sparse identity | flat RR | 0 | 0.03125 | 1 | 1 | 0 | 1.0000 | 1.0000 |
| simultaneous/global fan-in | A4 | 0 | 0.0625/0.25 | 17 | 17 | 12 | 1.0000 | 1.0000 |
| simultaneous/global fan-in | flat RR | 0 | 0.0625/0.25 | 16 | 16 | 15 | 1.0000 | 1.0000 |
| matched local | A4 | 0 | 0.75 | 5 | 5 | 0 | 1.0000 | 1.0000 |
| matched local | flat RR | 0 | 0.75 | 4 | 4 | 3 | 1.0000 | 1.0000 |
| matched dispersed | A4 | 0 | 0.75 | 5 | 5 | 2 | 1.0000 | 1.0000 |
| matched dispersed | flat RR | 0 | 0.75 | 4 | 4 | 3 | 1.0000 | 1.0000 |
| rotating victim identity | A4 | 150 | 0.99194 | 8 | 11 | 13 | 0.99983 | 0.93907 |
| rotating victim identity | flat RR | 215 | 0.97632 | 4 | 6 | 10 | 0.99980 | 0.92832 |
| timing pair seed 3901 | A4 | 1 | 0.61377 | 3 | 4 | 5 | 0.99999 | 0.98824 |
| timing pair seed 3901 | flat RR | 6 | 0.61182 | 2 | 3 | 4 | 0.99988 | 0.95714 |
| phase transition seed 3501 | A4 | 1015 | 0.51855 | 19 | 20 | 15 | 0.99882 | 0.62443 |
| phase transition seed 3501 | flat RR | 1017 | 0.51807 | 13 | 15 | 15 | 0.99794 | 0.61086 |

Rate shape preserves all events for both candidates. A4 versus flat RR p99 is
2/1 cycles at burst 1, 5/4 at burst 4, and 17/16 at burst 16. A4 reduces the
maximum source acceptance wait from 3 to 2 for four-event bursts and from 15
to 12 for 16-event bursts, but its extra registered level adds one delivery
cycle.

Moving-hotspot single, dispersed-four, row-four, and column-four cases all have
zero overrun and unit service ratio for both candidates at the frozen 0.9 load.
A4 p99 is 2 versus flat 1 in these traces. Thus the frozen moving cases do not
show a topology win; they expose A4's one-cycle sparse pipeline cost. This loss
is retained rather than hidden.

## Uniform load sweep (three-seed means)

| load | candidate | overrun | epc | p99 | fairness | min ratio |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 0.125 | A4 / flat | 0 / 0 | 0.12093 / 0.12093 | 2 / 1 | 1 / 1 | 1 / 1 |
| 0.5 | A4 / flat | 0 / 0 | 0.49642 / 0.49658 | 2 / 1 | 1 / 1 | 1 / 1 |
| 0.9 | A4 / flat | 0 / 0 | 0.90332 / 0.90381 | 2 / 1 | 1 / 1 | 1 / 1 |
| 1.0 | A4 / flat | 0 / 0 | 0.99902 / 0.99951 | 2 / 1 | 1 / 1 | 1 / 1 |
| 1.25 | A4 / flat | 500.7 / 502.7 | 0.99902 / 0.99951 | 18 / 10.3 | 0.99812 / 0.99890 | 0.74129 / 0.75867 |
| 1.5 | A4 / flat | 1003.7 / 1005.7 | 0.99902 / 0.99951 | 20 / 13 | 0.99780 / 0.99824 | 0.61749 / 0.62642 |
| 2.0 | A4 / flat | 2037 / 2040 | 0.99902 / 0.99951 | 20 / 15 | 0.99783 / 0.99827 | 0.46274 / 0.47215 |

Both candidates saturate at the same one-event/cycle root. Distributed leaf
slots save only 2--3 overruns on average above saturation and do not move the
sustainable egress knee. Their queue placement increases A4's overload p99.
This is a material limitation of the chosen minimal one-slot-per-node design.

## Topology and permutation audit

- Matched local and its mirror are exactly equal for A4: overrun 0, epc 0.75,
  p99 5, max wait 0. No mirror bias was observed.
- Matched dispersed has the same epc/p99 but max wait 2. Local placement wins
  acceptance wait because that trace's source-latch timing aligns with the
  leaf pipeline; it does not win delivery latency.
- Moving dispersed/row/column four-hotspot traces are equal in the frozen case.
- Rotating-victim identity versus affine differs by one overrun (150 versus
  149) and max wait (13 versus 12); flat also changes (215 versus 212). This is
  consistent with finite trace phase, not a severe fixed-address privilege.
- Retrigger identity/affine are equal and lossless for A4.

The evidence does not justify extra per-level phase offsets. Transfer-driven
local RR is retained; adding a phase mechanism now would add state without a
measured neutrality failure.

## Architecture cost proxies

| Metric | A4 | flat RR reference | Interpretation |
| --- | ---: | ---: | --- |
| architectural state | 155 bits | about 25 bits | A4 pays for five elastic slots |
| registered merge levels | 2 | 1 | A4 sparse latency +1 cycle |
| maximum arbitration fan-in | 4 | 16 | A4 bounds local choice depth |
| control wire proxy | 48 bit-grid | 64 bit-grid | A4 -25% under declared placement proxy |
| full-channel wire proxy | 720 bit-grid | 704 bit-grid | A4 +2.3%; repeated payload transport offsets control saving at N=16 |

These are structural counts, not PPA. The physical hypothesis only survives if
larger-N or routed evidence converts bounded fan-in/control span into timing or
energy benefit large enough to pay for the registers and repeated data wires.

## Head-owned server gates

### `PENDING_HEAD_XCELIUM`

Xcelium is required because it supports the frozen interface/SVA seam that the
local simulator cannot parse. After the head checks out the immutable A4 commit
in its own server location and sources the standard environment, the exact
candidate command is:

```csh
setenv TERM xterm
source ~/control_digi.cshrc
rehash
cd <HEAD_IMMUTABLE_A4_CHECKOUT>
bash tests/a4/run_frozen_46.sh both
```

The runner elaborates the unmodified frozen TB once per candidate, verifies all
46 trace SHAs, enables `a4_quadtree_properties.sv`, emits per-event CSV, and
calls the common aggregator with `--fail-on-correctness`. Any Xcelium mismatch
against the local model supersedes these model numbers.

The native capability declaration is
[`tests/a4/capability_profile.json`](../../tests/a4/capability_profile.json):
fixed N=16, one source-observable retire lane, native sink ready, and full
event-payload preservation. Multi-lane retirement is the only declared SKIP.

### `PENDING_HEAD_GENUS`

Genus screening is needed to test the bounded-fan-in area/timing hypothesis.
The current shared `run_stage.sh` accepts only the historical baseline, so there
is intentionally no runnable A4 Genus command yet. The head must first freeze a
candidate-registry entry with top `a4_quadtree_fabric`, synthesis filelist
`rtl/candidates/a4_quadtree_fabric/a4_quadtree_fabric.f`, N=16 parameters,
common SDC/library, and a synthesis-only RTL filelist. After that registry
extension, the intended exact invocation is:

```bash
env AER_RUN_ID=a4-n16-genus-screen-<HEAD_FREEZE_ID> \
  scripts/run_stage.sh synth a4-quadtree /absolute/path/to/head-a4-config.sh
```

Running this command before the registry extension correctly fails `unknown
design`; that protects the shared flow from an unregistered candidate.

### `PENDING_HEAD_INNOVUS`

Innovus is required only if A4 survives functional and Genus shortlist gates.
The repository currently has no qualified generic Innovus driver, so inventing
an “exact” executable command would be misleading. The head must use the common
physical contract to add the same fixed-netlist diagnostic and per-target
resynthesis driver used for every shortlisted candidate. Required command shape
after that driver is frozen is:

```bash
env AER_RUN_ID=a4-n16-pr-<PERIOD_NS>-<HEAD_FREEZE_ID> \
  AER_CLOCK_PERIOD_NS=<PERIOD_NS> \
  scripts/run_stage.sh pnr a4-quadtree /absolute/path/to/head-a4-pr-config.sh
```

Today `run_stage.sh` has no `pnr` stage and must reject this. Final reporting
requires setup and hold WNS >= 0, completed detailed route, zero unconstrained
paths, DRC/antenna disclosure, and separate fixed-netlist versus per-target
resynthesis labels. No server PPA number is claimed in A4's current commits.
