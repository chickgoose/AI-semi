# W2 activity-power Fmax/PPA comparison

Status: fail-closed comparison contract, 2026-08-13

## Authoritative server update (2026-08-13)

The server bundle inspected for this revision is
`/tmp/ganghee-pnr-golden-20260813.tar.gz`:

```text
SHA-256     1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f
size        4,626,544 bytes
members     305 (302 regular files and 3 directories)
```

`parse_ganghee_pnr_archive.py` opens report bytes directly from that SHA-bound
tar archive; summary rows retain the unique archive member path and member
SHA-256.  Loose extracted files cannot replace archive members.  Its output
contract is `ganghee_pnr_archive_summary.schema.json`.

```sh
python3 benchmarks/physical_ppa/parse_ganghee_pnr_archive.py \
  /tmp/ganghee-pnr-golden-20260813.tar.gz
```

The expected archive SHA, byte size, member count, and power-row counts are
repository-pinned in `ganghee_pnr_golden_20260813.lock.json`; the CLI has no
caller override for that authority.

The archive contains 14 Genus `*_gpower.rpt` files and 14 Innovus-integrated
Voltus `*_pnr_power.rpt` files, but zero VCD and zero SAIF members.  Every PNR
power report says `User-Defined Activity : N.A.`, `Activity File: N.A.`, and
uses default sequential/primary-input activity `0.2`.  The matching Genus logs
explicitly use vectorless mode.  Consequently all 14 imported PNR power rows
are `vectorless_report_power`, with
`accepted_for_activity_comparison: false`.  A plain `report_power` command is
not activity provenance.

The imported designs are core-only TX/arbitration plus output buffering, not a
full TX-link-RX endpoint.  The archive also has no frozen source commit, library
bytes, workload result, event denominator, VCD/SAIF import receipt, scope/window
or annotation coverage.  All Innovus logs contain tool errors, and the timing
checks do not prove zero unconstrained paths.  Clean textual DRC/antenna reports
do not cure those omissions.  Therefore its binding is
`HOLD_NO_ACTIVITY_PROVENANCE`, never candidate GO.

The importer derives the mapped-top functional boundary consistently across
each resynthesis sweep: `aer_fovea_buffered` has 17 functional input bits and 6
functional output bits (23 total), while `aer_cluster2_buffered` has 18 input
and 16 output bits (34 total).  Clock and reset are excluded.  These are
core-only pin diagnostics and must not be compared as full-endpoint pin costs.

The report-derived diagnostic timing intervals are `[1000, 1250) MHz` for
`aer_cluster2_buffered` and `[714.286, 833.333) MHz` for
`aer_fovea_buffered`.  They are post-route screening intervals only.  The
associated power values remain vectorless and cannot be divided by a workload
event count to manufacture energy/event.

## Evidence boundary

The machine-readable input is
`benchmarks/physical_ppa/activity_power_ppa_comparison.schema.json`; the only
decision authority is `evaluate_activity_power_ppa.py`.  Input records contain
evidence and never contain a submitted decision.  The evaluator verifies every
artifact SHA-256 against a normalized, single-link regular file and derives
cohort IDs, metrics, and publication status.

Activity-annotated power accepts only an archive-bound VCD or SAIF plus a
complete import-provenance sidecar.  The power report
must repeat the waveform, canonical resolved-scope, exact half-open window,
implementation netlist, and library hashes.  The resolved-scope hash is derived
from the sorted object/bit inventory, not from the submitter's root-name string.
Coverage is recomputed as annotated eligible object bits divided by all eligible
object bits and must meet the evaluator/production-registry policy (95 percent
for the self-test policy).

The window hash binds the trace, waveform, workload/test/seed, clock and period,
start/end cycles, warm-up, and drain policy.  The common result and power report
must repeat that hash.  An equal-length shifted window therefore cannot be
paired with a workload denominator from another interval.

## Work and loss denominator

The sole energy/throughput denominator is
`delivered_logical_events_in_exact_window`.  It must equal the common result's
delivered count, and its cycle count and window hash must equal the activity
window.  The evaluator enforces:

```text
generated = source_overrun + accepted
accepted = delivered + loss
```

A release-capable cohort requires zero source overrun, loss, duplicate,
corruption, phantom delivery, and late-after-drain events.  It also requires
separate `sparse`, `near_saturation`, and `loss` workload rows.  Power,
events/cycle, events/functional-pin-cycle, and energy/delivered-event are always
recomputed.  Functional pins are likewise recomputed from the bit-exact pin
inventory; clock, reset, power, and ground roles are excluded.

## Cohorts and release rule

The evaluator derives cohort identity from the boundary class, power mode,
analysis class, flow/SDC/library/corner, clock, activity format, and frozen
workload manifest.  It never pools these distinct evidence classes:

- `vectorless_screening` is a diagnostic cohort.  It has no workload-derived
  throughput or energy and cannot produce candidate GO.
- `core_only` remains a separate architecture diagnostic and cannot produce
  candidate GO.
- `full_endpoint` includes TX, link, and RX and charges its complete functional
  pin boundary.

Candidate GO additionally requires at least two production-authorized
candidates in the same full-endpoint, activity-annotated cohort; all three
operating points for every candidate; clean conservation; and a monotonic,
bracketed per-target-resynthesis sweep.  Timing PASS requires nonnegative setup
and hold WNS, completed route, zero unconstrained paths, and zero DRC and antenna
violations.  The activity clock period must name exactly one timing point and
the power report must bind that point's resynthesized netlist.  Fixed-netlist
diagnostics and lower-bound-only sweeps remain HOLD evidence.

`measured_candidate` input is rejected unless the caller supplies an
out-of-band registry whose exact candidate identities, bundle hashes, workload
manifests, and coverage threshold match the comparison.  Because no
repository-owned production extractor/authority is frozen yet, the evaluator
currently forces even matching measured inputs to `HOLD_UNAUTHENTICATED` and
cannot emit candidate GO.  This containment is intentional: an arbitrary
caller-authored registry cannot turn synthetic or self-authored summaries into
measurement truth.

## Synthetic fixture firewall

All committed tests use the origin marker `TEST_ONLY_NOT_RTL_EVIDENCE`.  That
marker unconditionally forces `publication_status: TEST_ONLY`,
`decision: TEST_ONLY`, and `candidate_go: false`, even if every other gate is
satisfied.  No committed synthetic fixture is production-authorized and none
may be cited as candidate, simulator, P&R, timing, power, or workload evidence.

Run the focused evaluator regression with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s benchmarks/physical_ppa/tests -v
```
