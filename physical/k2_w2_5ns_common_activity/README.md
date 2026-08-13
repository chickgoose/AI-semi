# Immutable 5.0 ns common-workload activity

This directory defines one runtime producer for exactly these canonical staged
compositions:

- Fovea+A7: `w2_fovea_r1_physical_staging_top`
- A2+P6: `w2_a2_p6_physical_staging_top`
- A3+P6: `w2_a3_p6_physical_staging_top`

It does not change the frozen common testbench, official workload manifests or
traces, staged RTL, or any Genus/Innovus core-flow file. The added SystemVerilog
is testbench-only. A bound override gives the frozen bench a 5.0 ns reference
clock; each binding supplies a 5.0 ns sample clock whose rising edge is 1.25 ns
after the reference edge. The probe observes only
`aer_clean_tb.candidate.dut`, checks both periods and their phase, and records
the frozen bench's exact `measurement_active` window.

The selected workload is the official generator-v4
`mixed_phase_always_ready_identity` member shared by full50 and capacity22.
The producer generates both official suites from pinned manifests, validates
every generated trace and per-run manifest against the pinned official identity
module, then proves capacity22 is the exact ordered, byte-identical 22-member
subset of full50. The three simulations all consume the same pinned workload
bytes from full50; capacity22 causes no additional candidate execution.

## Runtime invocation

Do not use this command unless the pinned Xcelium runtime and canonical staged
worktree are available. The output path must not exist:

```sh
python3 physical/k2_w2_5ns_common_activity/produce_activity.py \
  --output /new/exclusive/output/path \
  --staged-root /tmp/k2-phys-w2-techmap \
  --xrun /tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun
```

The producer invokes only Python and Xcelium. It never invokes Genus or
Innovus. Xcelium is required to match the exact path, SHA-256, and
`23.09-s013` banner pinned in `registry.json`. Python must be 3.11 or newer;
its resolved executable path, executable SHA-256, and exact version banner are
recorded in the receipt. Before each command and again before success, the
producer checks the relevant executable identity.

The output root is created exclusively and never reused. All inputs are
verified as regular non-symlink files, then copied byte-for-byte into an
exclusive provenance snapshot before execution. That snapshot includes the
registry, generator/preparer, official identity, frozen common TB, TB-only
clock/probe/bindings/filelists, and every staged candidate source closure.
Execution and tool-version logs are also immutable artifacts.

Each candidate receives raw VCD, a zero-rebased DUT-scope VCD, and a per-bit
SAIF produced from actual VCD transitions. Unknown/uninitialized activity,
signals outside the DUT scope, malformed/non-monotonic VCD, an incorrect clock
or phase, an incorrect window, missing exact success markers, scoreboard
errors, or conservation failures all abort without a success receipt. There is
no vectorless or synthetic-activity path.

The benchmark measurement is 4,096 cycles. Its frozen gate includes the
defined following service edge, so the activity interval is exactly 4,097
reference cycles, or 20,485,000 ps at 5.0 ns. Receipts preserve both values.

On success, `campaign-receipt.json` hash-binds all source identities, suite
identities, tools, clock/scope/window metadata, commands, logs, VCD, and SAIF.
Only after a complete independent validation is
`campaign.success` exclusively created. Verify a transported output with:

```sh
python3 physical/k2_w2_5ns_common_activity/verify_receipt.py /campaign/root
```

No activity outputs or measured-looking receipts are committed here; they can
only be created by a successful runtime execution.
