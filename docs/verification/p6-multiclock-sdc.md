# P6 multi-clock SDC/MMMC template

This additions-only template is bound to Git base
`13c60f936fe5a265e650b4b91436ed79fc20dc91` and two deliberately separate
preserved Ganghee cohorts:

| cohort | role | archive SHA-256 |
| --- | --- | --- |
| raw core | authoritative Ganghee baseline; no adapter, queue, ready, or backpressure | `7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3` |
| buffered extension | comparison evidence only; adds `lane_buffer2`, ready, and overrun signals | `1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f` |

The raw archive is extracted at `/tmp/ganghee-pnr-raw-golden-20260813` and
the buffered archive at `/tmp/ganghee-pnr-golden-20260813`.  Exact archive
and representative member hashes are frozen separately in
[`p6_ganghee_raw_golden_registry.json`](../../constraints/p6_ganghee_raw_golden_registry.json)
and
[`p6_ganghee_golden_registry.json`](../../constraints/p6_ganghee_golden_registry.json).
Only their verified intersection is recorded in
[`p6_ganghee_common_constraints.json`](../../constraints/p6_ganghee_common_constraints.json).
It applies to the committed
A2, A3, and A4 P6 integration tops, which all expose `ref_clk_i`,
`sample_clk_i`, `rst_n`, `p6_clk_o`, and `p6_data_o[4:0]` and contain one
`a7_p6_atomic_bundle_adapter`.

## Exact cohort evidence and proven intersection

Within each cohort, Fovea and Cluster2 use the same source-SDC body.  Raw and
buffered SDC files are also byte-identical at the overlapping Fovea 1.2 ns
and Cluster2 1.0 ns points.  The sweep sets themselves are not common:

| cohort | Fovea periods (ns) | Cluster2 periods (ns) |
| --- | --- | --- |
| raw core | `{1.2,1.3,1.4,1.6,2.0}` | `{0.7,0.8,0.9,1.0,1.3}` |
| buffered extension | `{0.8,1.0,1.2,1.4,1.6,1.8,2.0,2.2,2.5}` | `{0.8,1.0,1.3,1.6,2.0}` |

The following table is the exact proven intersection, not a merger of the
two result populations:

| assumption | golden value |
| --- | --- |
| clock | one `clk` input, 50% default waveform |
| uncertainty | 0.100 ns, setup and hold |
| input delay | 0.250 ns for every non-clock input, including `rst` |
| output delay | 0.250 ns for every output |
| output load | 0.010 pF for every output |
| input drive/transition | not declared; both cohorts leave this unresolved |
| Genus | 23.14-s090_1; `lp_insert_clock_gating=true` |
| Liberty | `slow_vdd1v0_basicCells.lib`, process 1.0, 0.9 V, 125 C |
| Innovus | 23.14-s088_1, `MMMC Non-OCV` |
| RC | `gpdk045.tch`, `rc_typical`, 25 C |
| MMMC | one `view_slow` used for both setup and hold |
| LEF | `gsclib045_tech.lef` plus `gsclib045_macro.lef` |
| physical skeleton | process 45, aspect 1.0, utilization 0.5, margins 10, M6/M7 core ring, placement, CTS, route, extraction |

The raw RTL boundary is the owner core itself: Fovea exports `valid/addr`,
Cluster2 exports its two native `valid/row/col_mask` lanes, neither has ready
or a queue, and both implement active-high synchronous reset.  The buffered
boundary wraps those cores with two-slot queues and ready/overrun signals;
the queues use active-low asynchronous reset internally.  Interface, reset
implementation, sweep membership, and every reported area/timing/power/DRC
result therefore remain cohort-specific and cannot be transferred between
them.

Both cohorts' source SDC has no generated clock, DDR edge constraint, min-pulse
constraint, explicit reset-release clock, recovery/removal policy, or separate
hold corner.  Its reported P&R numbers remain valuable single-clock baseline
evidence, but they are not by themselves a qualified P6 constraint set.  In
particular, the golden scripts `catch` `check_timing`, DRC, antenna, and DB
writes; the P6 qualifier must instead propagate those failures.

## Required P6 extensions

The P6 template inherits only the proven common numerical I/O baseline through explicit
environment variables: a qualification run should start with uncertainty
0.100 ns, min/max input and output delays 0.250 ns, and load 0.010 pF unless a
single approved common contract replaces them for every compared candidate.
P6 then necessarily adds:

1. `ref_clk_i` and quarter-period-shifted `sample_clk_i` as related clocks;
2. `p6_clk_o` as a generated, gated forwarded clock;
3. explicit rising- and falling-edge min/max constraints for all five DDR data
   outputs, preserving the half-cycle launch/capture apertures;
4. nonzero clock-gating setup/hold and high/low minimum-pulse checks;
5. a common-low reset-release virtual clock, no reset false path, and nonempty
   ref/link asynchronous-reset endpoint sets so recovery/removal remains
   observable;
6. explicit input transition or characterized driver, closing the common
   `no_drive` warning rather than inheriting it;
7. distinct setup and hold Liberty views plus distinct RC conditions.  The
   common slow/typical single view may seed setup diagnostics, but it cannot be
   relabeled as minimum-delay hold qualification; and
8. fail-closed timing, connectivity, DRC, antenna, and report commands.  This
   SDC/MMMC work supplies constraints only; those run/qualification changes
   remain outside this W2 commit.

[`p6_multiclock.sdc`](../../constraints/p6_multiclock.sdc) keeps the clocks
phase-related.  From `P6_REF_PERIOD_NS` it derives a 50% reference clock, a
sample clock at quarter-period and three-quarter-period edges, and a generated
forwarded clock at `p6_clk_o`.  Both P6 data edges receive min/max output
delays.  The template also retains clock-gating setup/hold, high/low minimum
pulse width, external input/output delay and load, and a reset-release virtual
clock in the common-low interval at 13/16 of the period.  Reset is never false
pathed, so Liberty recovery/removal arcs remain eligible for analysis.

Required SDC environment variables are:

```text
P6_REF_PERIOD_NS
P6_CLOCK_UNCERTAINTY_NS
P6_INPUT_DELAY_MIN_NS       P6_INPUT_DELAY_MAX_NS
P6_OUTPUT_DELAY_MIN_NS      P6_OUTPUT_DELAY_MAX_NS
P6_RESET_DELAY_MIN_NS       P6_RESET_DELAY_MAX_NS
P6_INPUT_TRANSITION_NS      P6_OUTPUT_LOAD_PF
P6_CLOCK_GATING_SETUP_NS    P6_CLOCK_GATING_HOLD_NS
P6_MIN_PULSE_HIGH_NS        P6_MIN_PULSE_LOW_NS
```

All values are mandatory.  The SDC fails if any named top port, the single
`endpoint/tx/frame_active_o` gating point, either ref/link register set, the
global asynchronous-reset endpoint set, or the ref/link-domain asynchronous
reset endpoint subsets are empty.  It also rejects nonsensical numeric
relationships such as uncertainty consuming the quarter-cycle aperture.

[`p6_multiclock_mmmc.tcl`](../../scripts/ppa/p6_multiclock_mmmc.tcl) uses the
golden-proven Innovus `create_rc_corner -qrc_tech` form and requires
nonempty setup/hold Liberty, setup/hold QRC technology, and SDC files.  It
builds distinct setup and hold library, RC, delay-corner, and analysis-view
objects.  Reusing one Liberty for both views is rejected.  Reusing the same
QRC file is allowed only when setup and hold RC temperatures differ.

```text
P6_SETUP_LIBERTY             P6_HOLD_LIBERTY
P6_SETUP_QRC_TECH            P6_HOLD_QRC_TECH
P6_SETUP_RC_TEMPERATURE_C    P6_HOLD_RC_TEMPERATURE_C
P6_MULTICLOCK_SDC
```

Run the local static gate with:

```sh
tests/p6_multiclock_sdc/run_all.sh
```

The runner defaults to both canonical extracted roots and archive paths.
Relocated byte-identical evidence can be selected without weakening hashes:

```sh
P6_GANGHEE_RAW_GOLDEN_ROOT=/path/to/raw-extracted \
P6_GANGHEE_RAW_GOLDEN_ARCHIVE=/path/to/raw-golden.tar.gz \
P6_GANGHEE_BUFFERED_GOLDEN_ROOT=/path/to/buffered-extracted \
P6_GANGHEE_BUFFERED_GOLDEN_ARCHIVE=/path/to/buffered-golden.tar.gz \
  tests/p6_multiclock_sdc/run_all.sh
```

The static gate first verifies both archive SHAs, cohort-specific member
hashes and period sweep sets, same-period SDC identity, the exact common
technology/MMMC/tool/flow intersection, and the raw-versus-buffered RTL
boundary.  It then pins the three P6 source boundaries and
rejects removal of any
clock, falling-edge DDR delay, gating, pulse, reset, setup/hold view, or
nonempty-collection guard.  It does not execute Cadence tools and is not STA
or physical qualification.  A future physical run must still prove nonempty
setup, hold, recovery, and removal path sets, nonnegative slack, propagated
generated clocks, and post-route connectivity/DRC under the exact selected
technology inputs.
