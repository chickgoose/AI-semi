# REDRED active system-goal contract

`active_goal.json` is the machine-readable source of truth for the current
REDRED system objective and its release boundary. It intentionally records a
mixed state: core AER implementation may continue, while unresolved inputs
remain explicit, scoped HOLDs.

The contract fixes the following decisions:

- The charged endpoint begins at synchronous `source_pending`/`source_accept`
  admission and ends at synchronous `retire_valid` observation. Scheduler,
  buffering, link TX/RX, and drain/error logic are inside the boundary.
- A2 is the primary weighted-aggregate `[1,5,5,1]` K2 candidate. It must not be
  described as exact scalar-prefix. A3 is the exact scalar-prefix fallback.
- P6 remains on HOLD until both written organizer approval and educational-PDK
  implementability are recorded. The single-edge parallel fallback is kept
  available and is selected until then.
- `generated = source_overrun + accepted` applies to every completed run;
  `accepted = delivered` applies to every hard-correct drained run. Source
  overrun is a capacity outcome, not a hard-correctness failure.
- `capacity22` is an exact subset of `full50`, not 22 additional independent
  samples. A release receipt must pin the trace, harness, RTL, link, tools,
  commands, commit, time interval, and result with immutable provenance.
- Coordinate stabilization is an external post-retire stretch demonstration.
  It cannot modify core RTL, canonical traffic, transport-loss accounting, or
  the core AER release decision.
- The pinned 6.5 ns post-route point is a qualified reference boundary, not an
  exact-Fmax claim. Any boundary, RTL, constraint, or interface change requires
  a fresh run. Complete-endpoint vectorless power remains on HOLD until a
  same-method receipt exists; diagnostic activity propagation does not pass
  that gate.
- The missing official dataset and missing coordinate numeric/I/O rules are
  explicit HOLD records. Neither stops core AER implementation.

## Verification

From the repository root:

```bash
python3 contracts/redred_system_goal/verify_contract.py
python3 -m unittest discover -s tests/redred_system_goal -p 'test_*.py' -v
```

The verifier uses only the Python standard library. It rejects malformed JSON,
duplicate keys, missing or unknown fields, altered mandatory semantics,
inconsistent release decisions, incomplete provenance requirements, unscoped
coordinate work, unsafe evidence-path policy, and contradictory gate/HOLD
states. Its success means the document is internally complete and consistent;
it does not manufacture or independently validate the physical and workload
evidence described by the contract.

## Change policy

This is deliberately stricter than a permissive schema. A policy change must
update the JSON, verifier invariants, mutation tests, and README together. A
missing new field therefore fails closed instead of inheriting a default.
