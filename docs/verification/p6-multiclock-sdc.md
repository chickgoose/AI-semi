# P6 multi-clock SDC/MMMC template

This additions-only template is bound to Git base
`13c60f936fe5a265e650b4b91436ed79fc20dc91`.  It applies to the committed
A2, A3, and A4 P6 integration tops, which all expose `ref_clk_i`,
`sample_clk_i`, `rst_n`, `p6_clk_o`, and `p6_data_o[4:0]` and contain one
`a7_p6_atomic_bundle_adapter`.

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

[`p6_multiclock_mmmc.tcl`](../../scripts/ppa/p6_multiclock_mmmc.tcl) requires
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

The static gate pins the three source boundaries and rejects removal of any
clock, falling-edge DDR delay, gating, pulse, reset, setup/hold view, or
nonempty-collection guard.  It does not execute Cadence tools and is not STA
or physical qualification.  A future physical run must still prove nonempty
setup, hold, recovery, and removal path sets, nonnegative slack, propagated
generated clocks, and post-route connectivity/DRC under the exact selected
technology inputs.
