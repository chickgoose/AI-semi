# REDRED policy and release-dependency contract

`active_goal.json` version 2 separates four things that must not be collapsed:

1. team goal and architecture policy;
2. external canonical-digital evidence;
3. physical and power evidence owned by each candidate interface; and
4. the release dependency graph.

The verifier's PASS has exactly one meaning:

```text
POLICY_INTERNALLY_VALID evidence_qualified=false release_qualified=false
```

It validates structure, pinned local/Git-object identities, trace membership,
and dependency consistency. It neither runs the canonical campaign nor
qualifies its results, Cadence evidence, an interface, or the final system.

## Current architecture policy

- A2 remains primary. Its claim is persistent all-four-row weighted opportunity
  `[1,5,5,1]` with a persistent 12-opportunity calendar, sparse fallback, and no
  debt or catch-up. It is not scalar-prefix equivalent.
- A3 remains the semantic fallback. Its two selections are the exact scalar
  prefix of one held pending snapshot; future arrivals are outside that claim.
  A3 activates only for an exact-prefix requirement or an A2-specific gate that
  A3 independently passes. Shared interface, evidence, CDC/RDC, and PDK-I/O
  failures cannot activate A3.
- A4 is research-only, nonranking, and not a release candidate.
- The charged endpoint begins at synchronous `source_pending`/`source_accept`
  admission and ends at synchronous retirement. Coordinate processing remains
  an external post-retire stretch function and is outside endpoint PPA.

## Interface state

No release interface is selected; selection is **HOLD**.

P6 is structurally defined as one 10-bit cell over five DDR data wires plus one
forwarded clock. Bits 4:0 launch at the rising edge after the low half; bits 9:5
launch at the falling edge after the high half; the receiver commits at the
falling edge. The single allowed unconstrained endpoint is the intentional
standard-cell `link_clk_o` forwarded-clock output; data exceptions are zero.

The inherited 6.5 ns Fovea+A7/R1, A2+P6, and A3+P6 standard-cell cohort is
`PASS_WITH_CLAIM_LIMIT`. It covers logic top ports only, not pads, package, or
channel. Competition multi-edge legality, real pad/package/channel behavior,
and qualified complete-endpoint vectorless power remain separate P6 HOLDs.

The single-edge parallel fallback is
`HOLD_NO_INTEGRATED_DIGITAL_PNR_POWER`. It cannot borrow P6 physical or power
evidence.

## Evidence bindings

The canonical digital campaign is an external dependency. This policy binds
the official trace registry, both frozen manifests, exact suite membership,
clean TB/interface/assertion bytes, and required receipt fields. `capacity22`
is an exact subset view of `full50`: it contributes zero additional independent
runs, so 50+22 must never be presented as 72 samples.

The inherited 6.5 ns reference binds:

- Git document object commit `61de7fdbd3b3160d3ce91dcb3ce0a1cc5fc4d078`;
- repository path `docs/k2_endpoint_physical_results_20260814.txt`;
- document SHA-256
  `113d2ad1ffe3b52f59067e948868875f6ce509ad14970f73876418db176050b1`;
- P&R source and verifier commits; and
- evidence-archive SHA-256
  `5112c2a447725532f628d5eb4dba9df0f7bd36e52040261a0582128fe3a63645`.

The archive bytes are unavailable to this verifier, so the assertion is
inherited and cannot satisfy final release.

Core-only Fovea/Cluster2 evidence is a separate nonranking reference and cannot
be combined with the complete-endpoint cohort.

## Remaining release boundaries

Team canonical release remains HOLD on canonical digital evidence, an interface
with its own complete digital/PNR/power/legal evidence, final CDC/RDC, and
competition PDK endpoint I/O rules. Organizer data is not one of those blockers:
its absence blocks only organizer-data and generalization claims. Dataset
arrival creates a versioned extension and cannot mutate `full50` or
`capacity22`.

Coordinate numeric rules are a separate HOLD for coordinate RTL only. Real pad
PHY is separately unproven. Passwords, license payloads, PDK payloads, absolute
paths, and mutable relative path components are forbidden.

## Verification

From the repository root:

```bash
python3 contracts/redred_system_goal/verify_contract.py
bash tests/redred_system_goal/run_all.sh
```

Any policy change must update the structured JSON, exact-key verifier, mutation
tests, and this README together.
