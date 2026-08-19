# Independent A5/A8 cross-validation

These adapters translate the candidate's already-qualified atomic scheduler
semantics; neither changes owner policy or owner RTL.

`a8_owner_adapter.py` exposes the contract-level atomic transaction view used
by A8 commit `1248a19`. It uses the independent scalar-fold oracle; candidate
RTL is separately locked to that oracle by the original `run.py` qualification.

`a5_trace_exporter.py` is retained only as a diagnostic adapter for A5 commit
`41c425b`'s superseded per-lane transaction schema. It no longer normalizes
away the owner output register: a newly pending cohort first enters the owner
scheduler's registered offer and can commit only on a later indexed edge. The
scheduler behavior comes from `AtomicK2Model`, already locked cycle-for-cycle
to owner RTL.

Per-lane retirement is not free. `a3_k2_ordered_link_adapter.sv` is a separate
synthesizable two-entry FIFO after the scheduler atomic handshake. Its state
and generic structure are charged independently: 10 state bits, 96 mapped
generic cells, and generic topological depth 14 in Yosys 0.52. Partial link
drain changes only FIFO state and capacity; it cannot advance scheduler policy.
A scheduler-only evaluator must omit this link and remain atomic.

The exporter fails closed unless Git resolves full owner commit
`a57943adba759fc955b4506e99703c1dd9736fba`, and unless working owner RTL and
`oracle.py` are byte-identical to blobs at that commit. It also binds A5 commit
`41c425bec79aca6c84f5856ca7dee2a4865a6447` and that evaluator's
`k2_oracle.py` SHA-256. Evidence identity fields mean:

- `source_sha256`: owner scheduler RTL;
- `binding_sha256`: charged synthesizable link RTL; and
- `runner_sha256`: latency-faithful exporter.

A5 freezes a different scalar oracle: a 12-entry row wheel with four row-local
column pointers. This candidate implements pinned Ganghee Fovea state. The
exporter therefore remains an explicit A5 `HOLD`; it never rewrites winners.

`test_cross_validation.py` materializes the exact A5 vector generator and
legacy exporter directly from pinned Git commits. Its negative test requires
the actual first divergence: on `persistent_weight_120` cycle 2, legacy
`29a5003` immediately accepts `[4,11]`, whereas faithful registered RTL accepts
nothing and first commits `[4,11]` on cycle 3. Separate Icarus tests prove this
latency in owner RTL and exercise the charged link.

`compare_a8_oracle.py` exhaustively compares ordered grants and complete
post-prefix policy state for all 65,536 initial request masks.

```sh
cd rtl/candidates/a3_exact_scalar_prefix_k2
python3 -B -m unittest -v test_cross_validation.py
```
