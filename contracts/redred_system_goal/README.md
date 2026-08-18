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

The single-edge parallel fallback now has bounded digital and source-structure
evidence. Its competition release remains
`HOLD_INCOMPLETE_MAPPED_PHYSICAL_POWER_AND_SELECTION`: it cannot borrow P6
physical, power, pad/package/channel, or inherited 6.5 ns evidence.

## Bounded current evidence

`PASS` below describes only the named evidence class. It does not change the
verifier's policy-only authority, select an interface or candidate, or release
the team result.

| Evidence class | Status | Exact claim boundary | Remaining HOLD |
| --- | --- | --- | --- |
| Hardened single-edge actual RTL | PASS | Synthetic `full50` semantics, exact accepted-event retirement, overrun accounting, reset and activated mutations | Canonical campaign, physical, power, release and selection |
| Public UZH projected actual RTL extension | PASS | Noncanonical/nonofficial projected-extension behavior on A2/A3 single-edge actual RTL | Official/canonical status, release and selection |
| Single-edge CDC/RDC | PASS | Source/elaborated, one posedge domain, with inputs assumed synchronous to the primary clock | Mapped CDC/RDC and final selected-interface CDC/RDC |
| Single-edge PDK legality | PASS | RTL source structure only | Mapped legality and organizer PDK approval |
| Single-edge physical | HOLD | Source-bound static flow scaffold only | Real P&R, post-route timing and authoritative constraints |
| Single-edge vectorless | HOLD | Diagnostic-only scaffold with placeholder I/O and no controlled producer | Real mapped vectorless power and comparison eligibility |
| Supplied-rotation known-motion demo | PASS | Synthetic, post-retire, rotation-only behavior | Canonical coordinate evidence, coordinate RTL and release |

The synthetic actual-RTL result is pinned at integration commit
`72491e45a35e6883bd4ee65d5c30409c108ab190`: result SHA-256
`e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4`
and pins SHA-256
`0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0`.
It binds hardened source commit
`6fc5e167918fa4c54786c9a3abb5f60ecd8b991b` and integrated RTL commit
`a0a4eb38632245db8ff5937ea5b6c6e3f3839246`. The 100 `full50` actual-RTL
executions report A2 `generated=106416, overrun=2370, accepted=retired=104046`
and A3 `generated=106416, overrun=12771, accepted=retired=93645`.

The public UZH projection publication is pinned at
`f30fec14572d9efb58a98d8f61dd22604a91446b`; publication/result/pins SHA-256
values are respectively
`3e12686de29459bbe8f2d292ca23892281e9760e9fbe6f65d979bc43a259c725`,
`c6172d39d476c1db0733b1952613e9f17d2b0849e8b398b33ee66bb6e24d30da`,
and `a29ad84883ef00afbc781f2328ed805c9abd24c5a9fd8449abe1886f38ff7958`.
Its 1x/64x/256x scenarios are three retimings of 1,100 unique projected
events, not 3,300 independent unique events. It is explicitly noncanonical,
nonofficial, uses no P6 evidence, and remains release/selection HOLD.

The source CDC/RDC contract is pinned at
`9d1dced49d3fceabf812d2ba2275c8d4c02eef13`; its contract SHA-256 is
`c4cbe85d704274a2f5d41a80652222880761465abeeca23df5b8291a7b4db44d`.
The source-structure PDK matrix is pinned at
`bbc6d8b8e82c795659d0bfe6b27b97a3428953e4`, SHA-256
`6db4310f30b274f6055a82b12a075776d4c84aa8aafa108a337e754c57344247`.
Those PASS rows do not promote mapped or organizer legality.

The physical HOLD contract is pinned at
`597fdf68cd5b0ff9b08c3d5304c2a1b63cb8e46a`, SHA-256
`c6a955e1da7effffead63212e65285e3510fe26c5fbe2b6fe7bfca48f432fc81`.
The vectorless HOLD contract and source manifest are pinned at
`c68af0e73bb06bb99eb838c684dbffb2a8dd4995`, with SHA-256 values
`b6aed31406fc8dee4566e1905313aced6998cf0be621817101f694221ef2e328`
and `7a32863c8da85bc6dc80086476950d16bf84912125c051e4e52e18454598aa4c`.
Neither is real physical or vectorless evidence.

The known-motion package is pinned at
`78eb019c56f2aab4b844c0fe925a5f2252fca256`. It consumes already-retired
events and supplied rotations, and remains outside endpoint PPA. The exact six
package/test Git-object hashes are recorded in `active_goal.json`.

## Evidence bindings

The canonical digital campaign remains an external dependency. The bounded
single-edge results above do not satisfy or replace it. This policy binds
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
with its own complete digital/PNR/power/legal evidence, final selected-interface
CDC/RDC, the final A2-versus-A3 decision, and competition PDK endpoint I/O rules.
No release interface is selected. Organizer data is not one of those blockers:
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
