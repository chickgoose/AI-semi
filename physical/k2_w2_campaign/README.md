# K2 W2 fail-closed campaign v2

This directory renders a sealed server launch plan; it does not implement or
run Genus, Innovus, Tcl report extraction, or qualification. The only campaign
targets are the separate raw Fovea/Cluster2 diagnostic cohort and exactly three
technology-staged normalized endpoints: Fovea+A7, A2+P6, and A3+P6. The old
generic unequal-debug wrappers are explicitly forbidden.

The final common non-link boundary is `ref_clk_i`, `sample_clk_i`, `rst_n`,
`source_pending_i[15:0]`, `source_accept_o[15:0]`, `retire_valid_o[1:0]`, both four-bit
retire addresses, drain, and error. No candidate exposes `link_enable`.

Actual link outputs remain physical top ports and receive the same external
load. Every staged top uses `link_clk_o` and `link_data_o`; R1 retains one
clock plus two data bits and P6 retains one clock plus five
data bits. Each internal TX-to-RX cut net must carry the exact
`AER_LINK_CUT="tx_to_rx"`, direction, and clock/functional role attributes and
map bijectively to its physical port. Accounting is:

- R1: 47 native non-link bits + 3 link bits = 50 total physical bits.
- P6: 47 native non-link bits + 6 link bits = 53 total physical bits.

The native-nonlink and link inventories are disjoint. A receipt that omits a
link bit or adds a native total to a separate link total is rejected.

READY requires the following immutable chain:

1. a bound `PROVEN_ENVIRONMENT` receipt;
2. a real server report-format calibration receipt matching the exact Innovus,
   PNR Tcl, verifier, and environment hashes;
3. the committed canonical `k2_w2_tech_staged_compositions_v1` manifest with
   `READY_FOR_GENUS_AND_INNOVUS`, exact source closure,
   boundary, `AER_LINK_CUT`, and multi-clock v6 contracts;
4. the shared Genus receipt contract, published only after mapped-smoke PASS;
5. a staged-vs-mapped functional PASS using exact vendor functional models
   (and SDF when available) or formal LEC, hash-binding netlist, SDF/models,
   and logs. Fovea covers held pending, conservation, reset, and drain; P6
   covers ordered pairs, back-to-back traffic, and reset, with exact accepted,
   retired, order, and error observations;
6. an exclusive Innovus plan sealed by its expected SHA;
7. native reports plus a separate Tcl-owned machine summary, independently
   verified before the qualifier.

Mapped syntax and inventory checks alone can never satisfy step 5.

The calibration must retain and hash the native reports. `checkDesign -all`
must enumerate every calibrated error class at pre-place and post-route; every
count must be zero and missing, new, or duplicate classes are fatal. Synthetic
`key=0` fixtures are not server-format evidence.

Both timing views use the sole shared typical `gpdk045.tch`, with slow Liberty
for setup and fast Liberty for hold. No second QRC path is required or allowed.
The Genus synthesis timing model is the slow setup Liberty only. Its required
fast-Liberty, cell-LEF, and shared-QRC CLI inputs are immutable technology and
endpoint-mapping provenance for the Innovus handoff; they do not turn the
Genus screening receipt into MMMC or physical PPA evidence.

The checked-in campaign remains intentionally BLOCKED because the committed
canonical staged manifest, full-link multi-clock receipts, native-report calibration,
shared Genus/Innovus handoff, production-enabled activity-power producer, and updated
qualifier interface are not integrated. The fast-Liberty identity is now pinned by the
server-environment contract; it is not a remaining blocker.
Rendering without all of them creates only a no-overwrite BLOCKED plan and an
immediate-failure shell script.

The staged manifest also binds exact endpoint hierarchy inventories. R1 is one
ICG, two MX2, two positive-edge DFFRH, and five negative-edge DFFNS cells. P6
is one ICG, five MX2, five positive-edge DFFRH, and twelve negative-edge DFFNS
cells. Older 2/5 negative-edge expectations are rejected.

Run the local contract and mutation suite with:

```sh
tests/k2_w2_campaign/run_all.sh
```
