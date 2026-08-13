# P6 multi-clock SDC/MMMC template

This additions-only template is bound to Git base
`13c60f936fe5a265e650b4b91436ed79fc20dc91` and to the preserved Ganghee
physical archive SHA-256
`1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.
The archive is extracted at `/tmp/ganghee-pnr-golden-20260813`; exact member
hashes and assumptions are frozen in
[`p6_ganghee_golden_registry.json`](../../constraints/p6_ganghee_golden_registry.json).
It applies to the committed
A2, A3, and A4 P6 integration tops, which all expose `ref_clk_i`,
`sample_clk_i`, `rst_n`, `p6_clk_o`, and `p6_data_o[4:0]` and contain one
`a7_p6_atomic_bundle_adapter`.

## Exact Ganghee golden baseline

Fovea and Cluster2 use byte-identical source SDC at the same period.  Their
period sets are Fovea `{0.8,1.0,1.2,1.4,1.6,1.8,2.0,2.2,2.5}` ns and Cluster2
`{0.8,1.0,1.3,1.6,2.0}` ns.  Only the period literal changes:

| assumption | golden value |
| --- | --- |
| clock | one `clk` input, 50% default waveform |
| uncertainty | 0.100 ns, setup and hold |
| input delay | 0.250 ns for every non-clock input, including active-high `rst` |
| output delay | 0.250 ns for every output |
| output load | 0.010 pF for every output |
| input drive/transition | not declared; golden `check_timing` reports 19 Fovea and 20 Cluster2 `no_drive` warnings |
| Genus | 23.14-s090_1; `lp_insert_clock_gating=true` |
| Liberty | `slow_vdd1v0_basicCells.lib`, process 1.0, 0.9 V, 125 C |
| Innovus | 23.14-s088_1, `MMMC Non-OCV` |
| RC | `gpdk045.tch`, `rc_typical`, 25 C |
| MMMC | one `view_slow` used for both setup and hold |
| LEF | `gsclib045_tech.lef` plus `gsclib045_macro.lef` |
| physical skeleton | process 45, aspect 1.0, utilization 0.5, margins 10, M6/M7 core ring, placement, CTS, route, extraction |

The golden source SDC has no generated clock, DDR edge constraint, min-pulse
constraint, explicit reset-release clock, recovery/removal policy, or separate
hold corner.  Its reported P&R numbers remain valuable single-clock baseline
evidence, but they are not by themselves a qualified P6 constraint set.  In
particular, the golden scripts `catch` `check_timing`, DRC, antenna, and DB
writes; the P6 qualifier must instead propagate those failures.

## Required P6 extensions

The P6 template inherits the golden numerical I/O baseline through explicit
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
6. explicit input transition or characterized driver, closing the golden
   `no_drive` warning rather than inheriting it;
7. distinct setup and hold Liberty views plus distinct RC conditions.  The
   golden slow/typical single view may seed setup diagnostics, but it cannot be
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

The runner defaults to the authoritative extracted root and archive paths.
Relocated byte-identical evidence can be selected without weakening hashes:

```sh
P6_GANGHEE_GOLDEN_ROOT=/path/to/extracted \
P6_GANGHEE_GOLDEN_ARCHIVE=/path/to/golden.tar.gz \
  tests/p6_multiclock_sdc/run_all.sh
```

The static gate first verifies the archive SHA, selected member hashes, both
period sweep sets, exact common SDC body, technology/MMMC/tool markers, and
golden physical-flow skeleton.  It then pins the three source boundaries and
rejects removal of any
clock, falling-edge DDR delay, gating, pulse, reset, setup/hold view, or
nonempty-collection guard.  It does not execute Cadence tools and is not STA
or physical qualification.  A future physical run must still prove nonempty
setup, hold, recovery, and removal path sets, nonnegative slack, propagated
generated clocks, and post-route connectivity/DRC under the exact selected
technology inputs.
