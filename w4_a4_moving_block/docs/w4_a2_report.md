# W4 A2 independent always-ready replay of A4 moving-block

Evidence result: **PASS only for always-ready generator-v4 full50+capacity22
actual-RTL lockstep at A4 commit `850fbcf`.**

Complete common functional qualification: **HOLD**.  The mandatory direct-SV
`basic_reset_drain` test was not present or executed, and the historical tool
record is not an immutable tool receipt.  A4's economic gate is **NO-GO**, so
no expensive replacement qualification is initiated by this correction.  This
is also not a PPA qualification.

## Frozen inputs and ownership

The runner reads the A4 repository but never modifies it.  Because its branch
may advance independently, the runner obtains
`rtl/candidates/a4_moving_block_tree/a4_moving_block_tree.sv` directly from
Git object `850fbcfa4ad168b1250223610780f11378f6c391` and verifies SHA-256
`18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677`
before compiling it.

Common commit `47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be` and these inputs are also
fail-closed:

- generator-v4: `59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`;
- full50 manifest: `9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9`;
- capacity22 manifest: `99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62`.

The recorded simulator string is Verilator 5.032.  The receipt does not contain
the simulator executable SHA, package/container identity, or an immutable
capture of the complete command and options.  It therefore proves neither the
exact tool binary nor a reproducible tool environment.  A missing/non-Verilator
tool, compile error, pinned input drift, or absent scoped lockstep sentinel does
exit nonzero, but that fail-closed behavior is not a substitute for the missing
immutable receipt.

## Interface and checks

The zero-state adapter has no behavioral or sequential process and no storage.
It drives the native 32-bit event input with only the mandatory source address,
and exposes every raw retirement without valid gating or reconstruction.
Adapter state is therefore zero bits.

Two instances of the exact RTL run concurrently:

- candidate `MAX_ADVANCE=2`; and
- fixed reference boundary `MAX_ADVANCE=1`.

An independently written Python model produces cycle expectations for each.
The direct-SV harness checks every source-ready bit and every raw retire
valid/source cycle, initial-preamble reset quiet, address-only raw event value, accepted-credit
conservation, phantom/duplicate absence, per-source causal order, complete
drain, one post-drain quiet cycle, fixed-window delivery, and
occurrence-to-delivery latency.

The reset coverage is deliberately narrower than common conformance.  Each
generated vector begins with two reset rows, but there is no drained first
epoch, second reset, no-traffic stale guard, disjoint post-reset address epoch,
or post-reset conservation check.  Consequently this evidence must not be
cited as a `basic_reset_drain` PASS.

Occurrence IDs and timestamps are TB-only bookkeeping and never enter the
adapter or DUT.  Under address-only semantics, repeated same-source events are
indistinguishable at the output; “source order” therefore means every raw
source retirement consumes the oldest unconsumed sampled acceptance credit.
The harness makes no arbitrary-payload or internal occurrence-identity claim.

## Results

All 50 full50 and 22 capacity22 traces passed **always-ready generator-v4
actual-RTL cycle lockstep** for both configurations: 72 trace invocations and
144 RTL/reference comparisons.
Every accepted event retired and every trace reached the post-drain quiet
cycle.

| suite | configuration | accepted=retired | overrun | fixed-window delivered | mean occurrence→delivery | p95/p99/max |
|---|---|---:|---:|---:|---:|---:|
| full50 | fixed MAX_ADVANCE=1 | 83,514 | 22,902 | 83,108 | 14.943 | 44/46/46 |
| full50 | moving MAX_ADVANCE=2 | 83,555 | 22,861 | 83,180 | 14.073 | 45/47/47 |
| capacity22 | fixed MAX_ADVANCE=1 | 42,948 | 22,668 | 42,617 | 22.742 | 45/46/46 |
| capacity22 | moving MAX_ADVANCE=2 | 42,983 | 22,633 | 42,652 | 22.586 | 46/47/47 |

Moving accepts 41/35 more events, removes the corresponding overruns, and
delivers 72/35 more events inside the fixed windows for full50/capacity22.
Mean latency improves, but p95, p99, and maximum latency are each one cycle
worse in both suites.  The accepted populations differ, so the latency result
is descriptive rather than a same-survivor dominance claim.

The corrected machine-readable evidence record is
`results/qualification.json`; it includes all 72 trace and vector hashes plus
per-trace metrics, marks the scoped lockstep evidence PASS, and marks complete
common qualification HOLD.  Its recorded historical execution hashes are
preserved, while the absent immutable tool receipt and absent
`basic_reset_drain` evidence are explicit gaps.  This disposition correction
does not regenerate the metrics: the record identifies `aef76b8` as the
historical execution origin and marks the correction as applied without replay.

## Reproduction

```bash
VERILATOR_ROOT=/path/to/verilator/share \
W4_VERILATOR=/path/to/verilator \
W4_COMMON_ROOT=/home/chickgoose/projects/a1 \
W4_A4_ROOT=/home/chickgoose/projects/a4 \
bash w4_a4_moving_block/run_w4.sh
```
