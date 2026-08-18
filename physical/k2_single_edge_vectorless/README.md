# A2/A3 single-edge mapped vectorless diagnostics

This directory provides a fail-closed, diagnostic-only Genus staging contract
for the complete A2 and A3 single-edge endpoints. Its maximum decision is
always:

```text
HOLD_PLACEHOLDER_IO_AND_NO_CONTROLLED_PRODUCER
```

Every preflight and successful qualification result has
`candidate_go=false` and `comparison_ready=false`. There is no keyring, HMAC,
authenticated-producer, or GO mode.

## Exact hardened RTL boundary

`source-manifests.json` binds the byte-identical relevant trees in hardened RTL
commits `a0a4eb38632245db8ff5937ea5b6c6e3f3839246` and
`6fc5e167918fa4c54786c9a3abb5f60ecd8b991b`. Both commit objects must contain
the same pinned bytes. The exact complete tops are:

- `a2_batched_iwrr_single_edge_top`
- `a3_exact_scalar_prefix_k2_single_edge_top`

Each six-source expansion contains its scheduler, the shared sticky
`w2_single_edge_error_latch`, TX, RX, endpoint, and complete top. Candidate and
nested generic filelists must expand to those sources in exact order. The full
input and output port sets are checked against both the contract and committed
top declarations. No P6 source or dependency belongs to this diagnostic.

## Constraint authority

The 6.5 ns clock, 0.25 ns uncertainty, I/O delays, input transition, and 0.01 pF
load are `UNCONFIRMED_TEAM_PLACEHOLDER` screening values. The active REDRED
policy keeps PDK endpoint-I/O rules on HOLD and says inherited 6.5 ns values are
not final competition rules. These values are not organizer, board, pad,
package, signoff, fmax, legality, comparison, or release claims.

`single_edge_strict.sdc` contains exactly one primary positive-edge clock on
`clk_i`, exact placeholder I/O/load values, no generated clock, and no timing
exceptions. Qualification requires the mapped SDC to contain exactly one of
each canonical constraint command in order. Values and selectors are exact:
the clock commands target `single_edge_clk`, input constraints target the
canonical nonclock-input collection, and output delay/load target
`[all_outputs]`. Duplicate, reordered, extra, wrong-target, false-path,
multicycle, falling-edge, generated-clock, and P6 constructs fail closed.

## Default-vectorless diagnostic command

`genus_default_vectorless.tcl` requires
`K2_SE_ACTIVITY_MODE=GENUS_DEFAULT_VECTORLESS`, reads the exact expanded source
snapshots and setup Liberty, elaborates the exact top, reads the exact SDC,
checks that Genus has exactly one `single_edge_clk`, performs generic/map/opt,
runs `check_design -all`, and emits mapped netlist/SDC/SDF plus area, timing,
power, QoR, timing-intent, clock, and check-design reports.

The driver contains no VCD/SAIF/TCF import or switching-activity override. Each
area, timing, QoR, timing-intent, clock, and check-design report requires one
Genus generator header, one exact design context, its native role-specific
header and complete rows, finite numeric values, and no error/fatal diagnostic.
Timing paths require beginpoint, endpoint, and consistent header/detail slack;
the QoR WNS must agree with the timing report. The power parser requires one
exact Genus tool identifier, one exact top instance, one noncontradictory W
unit, the exact ordered Category/Leakage/Internal/Switching/Total header, one
subtotal, native N.A. activity headers, native 0.2 defaults, finite nonnegative
components, and a consistent sum. Values are converted from W to mW.

These checks do not establish that Genus was actually run. Cadence startup
configuration and the full process environment are not controlled by this
package.

## Diagnostic artifacts and limitations

`k2_single_edge_vectorless_diagnostic_index_v2` locates exactly one A2 and one
A3 attempt. An attempt uses `diagnostic-receipt.json` with schema
`k2_single_edge_vectorless_diagnostic_receipt_v2`. Its complete ledger contains
exact source/filelist snapshots, driver, input/materialized/mapped SDC, setup
and hold Liberty, mapped netlist/SDF, log, reports, command receipt, and
environment receipt. Bytes, sizes, unique contained paths, regular-file type,
and single-link state are rechecked.

The receipt template has that exact accepted nested schema, but its producer
fields are null and its status is `HOLD_TEMPLATE_NOT_DIAGNOSTIC_ARTIFACTS`;
template bytes can never qualify as a completed attempt. Paths used by the
Genus argv/Tcl source list are restricted to whitespace-free Tcl-list-safe
absolute paths. Command-produced report, netlist, mapped-SDC, and SDF roles
must use the exact `work/<top>...` paths emitted by the pinned driver.

Structural validation requires the exact complete top port set, at least one
cell instance, no behavioral process, and an observable continuous or
conventional standard-cell output driver for every top output. Exact duplicate
signal drivers fail. The mapped SDF must be one balanced Genus DELAYFILE with
the exact top, a timescale, and populated CELL/CELLTYPE/INSTANCE/DELAY
structure. Check-design must contain one native `-all` summary with zero
unresolved references, black boxes, and errors. Netlist instance, SDF cell, and
area-report cell counts must agree.
Contradictory nonzero Cadence error/fatal counts or textual diagnostics fail
even when a zero summary and normal-exit marker are also present. Forbidden
activity directives fail in mapped-SDC comments as well as active commands.
The attempt-root directory identity is checked throughout artifact validation
to reject concurrent root replacement. These remain bounded structural
consistency diagnostics, not formal equivalence, complete bit-level logical or
physical connectivity proof, DRC, antenna, placement, routing, extraction, or
signoff.

There is currently:

- no repository-controlled producer runner or signer;
- no verifier-owned trust anchor;
- no binding to the live host identity;
- no freshness window or replay registry;
- no proof that a receipt's command/environment assertions came from the OS;
- no defense that distinguishes an authentic byte copy from its original; and
- no authority to promote the placeholder I/O/load values.

Consequently, copied or replayed bytes are not described as impossible. A
caller-created keyring is rejected rather than treated as provenance. Rehashed,
fabricated, or mocked inputs may at most satisfy diagnostic structure; the
decision remains HOLD.

## Commands

Local static preflight does not invoke Cadence:

```sh
python3 physical/k2_single_edge_vectorless/preflight.py preflight \
  --output /tmp/k2-single-edge-vectorless-preflight.json
```

An externally assembled diagnostic index can be structurally checked, but the
result remains the exact HOLD above:

```sh
python3 physical/k2_single_edge_vectorless/preflight.py qualify \
  --evidence /absolute/diagnostic/root/diagnostic-index.json \
  --output /absolute/diagnostic/root/qualification.json
```

There are intentionally no `--keyring` or `--keyring-sha256` options.

Run the adversarial regression with:

```sh
tests/k2_single_edge_vectorless/run_all.sh
```
