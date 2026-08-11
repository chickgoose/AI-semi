# A4 W4 Moving-Block Control Optimization

Status: local structural gate complete; common and physical PPA qualification HOLD

## Scope and frozen semantics

W4 starts from commit `850fbcfa4ad168b1250223610780f11378f6c391` and does not
modify that RTL or its W3 results.  The new RTL lives only under
`rtl/candidates/a4_moving_block_w4/`.  `MAX_ADVANCE=2`, tree storage, branch
phase updates, source-ready timing, same-cycle retire/refill, and address-only
trace semantics are unchanged.  Consequently W4 is not allowed to repair the
known moving-block p99 tradeoff by changing admission or scheduling.

The structural comparison uses a candidate-owned flattened event bus because
local Yosys 0.52 cannot parse the frozen RTL's unpacked array port.  The
`frozen_850fbcf_normalized` implementation is a literal cycle-function
transcription behind that normalized boundary.  Verilator compares it and both
variants directly against the original frozen module on every cycle, including
ready, retire valid, address, and source.

## Two variants

Only two variants were evaluated.

### Shared clearance and predecode

The first and second ingress opportunities are expressed as an explicit
two-position clearance prefix.  `accept_first` consumes an already-empty leaf;
`accept_second` consumes a leaf vacancy returned by the first merge pass, only
when the source was not already accepted.  Each merge predecodes mutually
exclusive left/right grants before updating payload state.  This removes the
generic `accepted` feedback loop and makes the two passes statically visible to
synthesis without altering their ordering.

### Shared clearance plus local register enable

The second variant retains the first optimization and stops clearing/reloading
payload and source registers when a slot merely becomes invalid.  Valid and
phase registers still update exactly as before.  A local write-enable updates a
payload register only when an event actually enters that node.  Invalid output
data is explicitly gated to zero, preserving the frozen module's observable
cycle result.  This is storage-local clock-enable inference, not an additional
pipeline stage or a predictive enable.

## Exact functional gate

The W4 harness regenerated the exact generator-v4 suites after checking the
frozen generator, policy, manifest, and every trace hash.  One Verilator binary
instantiated the original `850fbcf` RTL, its normalized transcription, and both
variants.  Results:

- full50: 50/50 traces exact cycle lockstep;
- capacity22: 22/22 traces exact cycle lockstep;
- three normalized tops: zero Verilator lint warnings; and
- all six N/design Yosys cases: `check` PASS with identical state counts.

Because the variants are exactly equivalent, their full50/cap22 metrics are
the frozen moving-block metrics: 83,555 accepted and 22,861 overrun in full50;
42,983 accepted and 22,633 overrun in cap22; p99 remains 47 in both.  No metric
improvement is claimed from changing functionality.

## Identical local Yosys proxy

All designs use the same 32-bit address, source/state widths, reset, ingress,
output, and register boundaries.  Both N=16 and N=64 use:

```text
read_verilog -sv -DSYNTHESIS
hierarchy -check -top <TOP> -chparam NUM_SOURCES <N> -chparam ADDR_WIDTH 32
proc; flatten; opt; memory; opt; techmap; opt
abc -g simple; clean; check; stat; write_json
```

| N | design | cells / comb / state | depth | max / p95 fanout | nets fanout >=16 |
| ---: | --- | ---: | ---: | ---: | ---: |
| 16 | frozen normalized | 11,421 / 10,259 / 1,162 | 25 | 40 / 3 | 185 |
| 16 | shared clearance | 9,484 / 8,322 / 1,162 | 25 | 75 / 2 | 150 |
| 16 | shared + local enable | 7,699 / 6,537 / 1,162 | 24 | 40 / 2 | 139 |
| 64 | frozen normalized | 49,853 / 44,837 / 5,016 | 31 | 129 / 2 | 733 |
| 64 | shared clearance | 40,563 / 35,547 / 5,016 | 32 | 79 / 2 | 612 |
| 64 | shared + local enable | 33,784 / 28,768 / 5,016 | 31 | 92 / 2 | 579 |

Shared clearance alone is rejected as a Pareto replacement: N=16 maximum
fanout increases from 40 to 75, and N=64 depth increases from 31 to 32.  Cell
count alone does not override either regression.

The local-enable combination is a local generic Pareto improvement.  Relative
to frozen normalized RTL, mapped cells/comb cells change by -32.59%/-36.28% at
N=16 and -32.23%/-35.84% at N=64.  State is identical; depth is 24 versus 25 at
N=16 and equal at 31 for N=64; maximum fanout is equal at N=16 and falls from
129 to 92 at N=64.

This result depends materially on generic integrated-enable flops: 1,116 of
1,162 state bits at N=16 and 4,826 of 5,016 at N=64 map to Yosys DFFE cells.  A
conservative sensitivity that charges every DFFE as one DFF plus one external
mux still gives -22.82% and -22.55% effective cell count, but that mux may add a
logic level.  Therefore generic mapping establishes a local structural
shortlist, not physical timing/area/power Pareto proof.

## p99 46 to 47 cause separation

The analyzer joins accepted events by frozen `tb_only_event_id` and separates
the common accepted cohort from events admitted by only one model.

| suite/cohort | events | fixed p99 | moving p99 | moving events latency >=47 |
| --- | ---: | ---: | ---: | ---: |
| full50 common | 78,023 | 46 | 46 | 496 |
| full50 moving-only | 5,532 | n/a | 47 | 1,054 |
| full50 fixed-only | 5,491 | 46 | n/a | n/a |
| cap22 common | 37,545 | 46 | 47 | 496 |
| cap22 moving-only | 5,438 | n/a | 47 | 1,054 |
| cap22 fixed-only | 5,403 | 46 | n/a | n/a |

Admission-set churn is the larger tail contributor: 1,054 of the 1,550 moving
events at latency 47 or above are moving-only admissions.  It is not the whole
cause.  The common cohort contains another 496 such events, and cap22 common
p99 itself rises to 47.  Those events isolate the second cause: two merge
microsteps change branch-phase timing under sustained overload even for events
accepted by both designs.  The easier full50 traces dilute the common-cohort
aggregate back to p99 46.

W4 cannot remove either effect without changing the frozen cycle semantics.
The structural variants therefore preserve, rather than conceal, the p99
tradeoff.

## Decision

`shared_clearance` is **REJECTED**.  `shared_clearance_local_enable` is
**LOCAL_PARETO_CONDITIONAL** and is the only W4 implementation worth a later
real-library comparison.  The original W3 decision remains **CONDITIONAL**
until a common qualification and physical flow prove that integrated enable
flops and their control routing preserve the local proxy advantage.  W4 is
explicitly `HOLD` for common qualification and physical PPA; it does not replace
or overwrite the frozen baseline evidence.

Reproduce into new paths only:

```bash
w4_tmp=$(mktemp -d /tmp/a4-w4-qualification.XXXXXX)
python3 rtl/candidates/a4_moving_block_w4/run_w4_qualification.py \
  --common-root /home/chickgoose/projects/a1 \
  --verilator /tmp/a7-sim-bin/verilator \
  --yosys /tmp/a7-yosys/usr/bin/yosys \
  --work-dir "$w4_tmp/work" --output "$w4_tmp/result.json"
```

The runner refuses an existing work or output path.  Detailed reproducible
metrics and locked source hashes are summarized in
`rtl/candidates/a4_moving_block_w4/results/w4_local_summary.json`.
