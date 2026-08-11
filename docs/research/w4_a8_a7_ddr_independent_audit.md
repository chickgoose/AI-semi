# W4 A8 Independent Audit: A7 Event-Triggered DDR Link

Status: independent protocol/timing/CDC/fault audit. A7 source ownership remains
with A7; this audit did not edit A7. Digital architectural testing may continue,
but physical timing, CDC, and fault-containment qualification remain **HOLD**.

## Audited snapshots

The baseline was first read at
`31947a71ddfcf678f6cd593954df34b27806a63d` (`31947a7`) with a clean A7
worktree. During this audit the W4 owner first created, then committed, the
candidate RTL. The latest committed owner snapshot read by this audit is
`89760c8226193a1e8a6fef6053e7e3dd48c2ab2b` (`89760c8`,
`feat(a7): isolate DDR clock technology boundary`). It adds exactly the four
files under `rtl/candidates/a7_event_triggered_ddr_burst_link_w4/`.

After that commit, the owner added untracked SDC, manifest, TB, filelist, and
structural-proxy files. They were inspected read-only and hashed below, but are
dirty-worktree observations, not committed provenance. No CDC/RDC report, STA
report, mapped-netlist proof, SDF, PVT/SI report, or synthesizable fault monitor
was present in either owner snapshot.

| Evidence | SHA-256 at audit snapshot |
| --- | --- |
| committed `a7_ddr_burst_tx.sv` | `cd63486f5c3b05daf6e021a30b84aa85836a22abc6d24015ff1ab68051b0b5e3` |
| committed `a7_ddr_burst_rx.sv` | `372570603081c45ebad24583fc2bd344a33c296e180dbf0566ad14734139f83f` |
| committed link top | `1b4452d7fe48140190c5890c6db268f335e86b606fbf34a6bd51f87783d60944` |
| committed candidate TB | `752b78d411ca9f2629c52e44953ce388d6fed7da46319588c988af7c4e3f5937` |
| committed research report | `d3c36323b2125057a39a269dec0d8f2067b1a1798f7ae09a295466554b923fe4` |
| committed runner | `bfff3302934a0ba06a99ad9f7515e60ff8fb640ea32259b0d75a6a5409f1b8a0` |
| W4 committed top (`89760c8`) | `60a0ea92809fc207269ec3ab3601292eff8963cdd9aae798b6c20498dd0836cd` |
| W4 committed RX (`89760c8`) | `56bbc28e9a098573ac819c7269ee83ed63e22eb7bdd1dbd003f9f82f2a964625` |
| W4 committed TX (`89760c8`) | `3d747bfa8d5d992091b5cc083c14417421368b4bfdb3056605f575ba01a9382b` |
| W4 committed ICG boundary (`89760c8`) | `b1b17c028bf39cb32ce7ac530d21977bad92cd9819b18be31f05f8691963d97c` |
| W4 untracked SDC | `511122dfd3207c929e5d604867755790d1c1983dcced75a992f29737f9a6f019` |
| W4 untracked constraint manifest | `3bd68b8875c8e183f71583a94867b4b86eb475453b2c93072eedbd70cffc00cd` |
| W4 untracked ICG TB | `0282121da4f10b0eb63e790b7fe9d6813c6b3a56ab0e359348518125175cc651` |
| W4 untracked test aliases | `eb28073addff39064ce1db876c8a60786e22e3919e6ae197c666bca2b6fda1e0` |
| W4 untracked ICG filelist | `1d491641e43355e9ccf0458a77b85d0c9c6736a9567c5ee89d4e9546fe015b8e` |
| W4 untracked unit filelist | `bc512fc015759bc3d7ed092035f7ad547c33fe76068396fee0db8214038ba8a1` |
| W4 untracked structural filelist | `4d4c67ed2e9d9acd2c688144a4f2437df07aced9777a90cddd877099c8fe3c7a` |
| W4 untracked structural compare RTL | `adfc1bec6e9a07e01fbfe69c71f9e387ce02f7b081090dc3999227da9aed1032` |
| W4 untracked structural compare TB | `f678b0b0b6e0a86bf1e7f52cd307c56bc11a6568698dc4029d4e5389c7b5ddfd` |
| W4 untracked structural compare script | `f92c0239c872d1214ab35b17dab2e4d45dc9be1d22c2123c2b5695a378af06cd` |

The committed A7 regression was run read-only with Verilator 5.032. Ratios 1,
2, and 4, normal back-to-back traffic, and its three directed fault tests all
reported PASS. The four Python metric tests also passed. This establishes that
the owner suite is reproducibly green; it does not close the independent
falsifiers below. The committed W4 RTL also passed a read-only Verilator lint;
that is syntax/structural evidence, not timing closure.

## RTL and checker findings

1. The committed clock is a combinational
   `sample_clk_i & frame_enable_q & rst_n` expression
   ([TX lines 46-49](/home/chickgoose/projects/a7/rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_tx.sv)).
   Its own comment correctly requires a characterized ICG or source-synchronous
   output cell. Generic RTL does not prove glitch rejection or pulse width.

2. The owner W4 snapshot improves structural intent by latching enable only
   while the source clock is low
   ([ICG lines 15-20](/home/chickgoose/projects/a7/rtl/candidates/a7_event_triggered_ddr_burst_link_w4/a7_w4_icg_boundary.sv)).
   It still implements the output as `clock_i & enable_latched_q & rst_n` and
   explicitly places reset assertion during clock-high outside its guarantee
   ([lines 22-25](/home/chickgoose/projects/a7/rtl/candidates/a7_event_triggered_ddr_burst_link_w4/a7_w4_icg_boundary.sv)).
   It is therefore an ICG integration boundary, not characterized ICG evidence.

3. Both committed and W4 RX capture directly on opposite forwarded-clock edges
   and expose the resulting toggle without a receiver-core synchronizer
   ([committed RX lines 15-33](/home/chickgoose/projects/a7/rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_rx.sv),
   [W4 RX lines 15-32](/home/chickgoose/projects/a7/rtl/candidates/a7_event_triggered_ddr_burst_link_w4/a7_w4_ddr_rx.sv)).
   Source-synchronous symbol capture can be valid with proven clock/data timing,
   but any later use of `retire_toggle_o` in another clock domain requires an
   explicitly charged CDC boundary. None exists in the observed top.

4. The manual fault checker sets `manual_frame_open=1` on every rise, overwriting
   an already-open frame
   ([TB lines 190-195](/home/chickgoose/projects/a7/tb/candidates/a7_event_triggered_ddr_burst_link/a7_event_triggered_ddr_burst_link_tb.sv)).
   Its fall block detects only fall-without-rise and high pulse below 1 ns
   ([lines 197-205](/home/chickgoose/projects/a7/tb/candidates/a7_event_triggered_ddr_burst_link/a7_event_triggered_ddr_burst_link_tb.sv)).
   The missing-fall “monitor” is not a general monitor: the test task manually
   increments the counter after a chosen delay
   ([lines 395-405](/home/chickgoose/projects/a7/tb/candidates/a7_event_triggered_ddr_burst_link/a7_event_triggered_ddr_burst_link_tb.sv)).

5. The manual fault RX data and retirement outputs are instantiated but never
   checked in `run_faults`. Consequently, the directed fault regression cannot
   detect corrupted symbols, an extra well-formed edge pair, or an abstract
   metastable/unknown sample. The normal ideal-path scoreboard is stronger, but
   the mutations are not injected into that path.

6. The committed report is appropriately explicit that ordinary RTL simulation
   does not prove pulse width, skew, duty distortion, MTBF, ICG, DDR I/O mapping,
   or PVT closure
   ([report lines 11-15](/home/chickgoose/projects/a7/docs/research/a7_event_triggered_ddr_burst_link.md)).
   It also excludes mid-frame reset from the delivery contract
   ([lines 38-41](/home/chickgoose/projects/a7/docs/research/a7_event_triggered_ddr_burst_link.md)).
   These HOLD statements are supported by this audit.

7. The untracked owner SDC specifies 16 ns reference/sample periods, 4 ns phase,
   7 ns minimum high/low pulse width, and 0.5 ns uncertainty/skew
   ([SDC lines 3-26](/home/chickgoose/projects/a7/constraints/a7_event_triggered_ddr_burst_link_w4.sdc)).
   This is a useful intended contract, but not a tool report. In particular,
   `set_false_path -from rst_n` ([lines 33-36](/home/chickgoose/projects/a7/constraints/a7_event_triggered_ddr_burst_link_w4.sdc))
   cannot establish the recovery/removal and RDC proof that the adjacent comment
   says remains required. Likewise, output delay relative to `a7_burst_clk`
   does not synchronize `retire_*` into an unstated consuming core domain.

8. The untracked manifest honestly labels itself `physical_hold` and lists
   library ICG mapping, duty distortion, post-route skew, recovery/removal,
   MTBF, PVT, and extracted power as unproved
   ([manifest lines 31-42](/home/chickgoose/projects/a7/constraints/a7_event_triggered_ddr_burst_link_w4.manifest.json)).
   Its fields are owner declarations, not measured evidence.

9. The untracked W4 unit alias reuses the original candidate TB, and therefore
   inherits the manual checker gaps identified above. The separate ICG TB checks
   legal low-phase enable/disable and idle-low reset, but does not inject
   reset-mid-high, recovery/removal, metastability, or analog runt behavior.
   The structural comparison is a generic Yosys area/depth proxy; it cannot
   establish characterized ICG/DDR mapping or physical timing.

## Executable independent oracle

`tests/w4_a7_ddr_independent/ddr_protocol_oracle.py` does not import A7 RTL. It
consumes timestamped accepts, rise/fall edges, symbols, stability annotations,
and reset actions. Its invariants are:

- every accepted occurrence has one scheduled rise followed by one scheduled
  fall and at most one retirement;
- rise captures known/stable address `[1:0]`, fall captures known/stable
  address `[3:2]`, and reconstruction equals the accepted N16 address;
- high and merged-low phases remain inside the configured timing window and
  below-minimum pulses fail separately as runts;
- a second rise cannot overwrite an open frame and a fall cannot commit without
  an open frame;
- mid-frame reset explicitly aborts the in-flight occurrence, produces no
  retirement, clears pairing state, and permits a clean post-reset frame; and
- trace end with an open frame or accepted occurrence is a missing-edge failure.

The same file contains `LegacyFaultChecker`, a behavioral transcription of the
manual checker at TB lines 190-205. This is used only to demonstrate false PASS
conditions; it is not represented as the full A7 scoreboard.

| Mutation | Independent oracle | Legacy manual checker model | Finding |
| --- | --- | --- | --- |
| golden two-event merged burst | PASS, two exact retirements | PASS | positive control |
| missing rise | FAIL | FAIL | covered |
| 100 ps runt high | FAIL | FAIL | covered at chosen threshold |
| missing fall followed by next rise/fall | FAIL: extra rise/open frame | **PASS** | open state is overwritten |
| extra normal-width rise/fall pair | FAIL: phantom edge pair | **PASS** | no expected-edge count |
| long high duty distortion | FAIL | **PASS** | only minimum high width checked |
| short/shifted merged-low phase | FAIL | **PASS** | low phase not checked |
| removed back-to-back fall/rise boundary | FAIL: corrupt merge/missing event | **PASS** | frame count and symbols unchecked in fault path |
| unstable/unknown low symbol | FAIL | **PASS** | clock-only checker ignores data |
| fall-before-rise ordering | FAIL | FAIL | covered |
| legal mid-frame reset abort then clean frame | PASS with one abort, one retire | not specified | oracle policy, not current delivery claim |
| post-reset unmatched fall | FAIL | FAIL | phantom commit prevented by oracle |

All 13 oracle/mutation tests pass. “Legacy PASS” means the modeled manual fault
checker would not increment its fault counters; it does not assert that an
arbitrary mutation would pass the full normal-path A7 scoreboard.

## What ordinary RTL simulation can and cannot establish

| Property | Ideal RTL simulation | Independent oracle/mutations | Required physical evidence still missing |
| --- | --- | --- | --- |
| accepted address identity/order | Checks enumerated ideal traces | Exact accept-edge-symbol-retire conservation | Gate-level mapped DDR endpoints and complete link scoreboard |
| rise/fall count and ordering | Counts simulator transitions | Fails missing, extra, reordered, merged boundaries | Clock waveform integrity at receiver pin across PVT/SI |
| runt pulse handling | Can inject a numerical short pulse | Fails below configured abstract minimum | Liberty min-pulse checks, ICG/DDR characterization, post-route STA |
| duty-cycle distortion/jitter | Only values explicitly driven by TB | Fails abstract high/low window violations | Generated-clock waveform, jitter/uncertainty, duty-cycle specs and STA |
| clock/data setup and hold | Ideal event scheduling only | `stable=False`/unknown fails closed | Both-edge source-synchronous STA with board/package/route skew |
| metastability | Cannot model analog resolution or MTBF | Unknown/unstable abstraction exposes unsafe assumptions | CDC/RDC report, aperture/MTBF analysis, characterized capture cells |
| back-to-back gated-clock merge | Proves nominal edge sequence | Detects missing boundary and corrupt pairing | ICG enable timing, min low/high pulse, ODDR/IDDR mapping and SDF |
| mid-frame reset | Can demonstrate chosen digital abort | Defines abort/no-phantom/recovery behavior | Reset assertion/deassertion sequencing, recovery/removal and RDC proof |
| downstream retire toggle | Toggles correctly in burst domain | Counts exactly one commit per valid frame | Charged synchronizer/handshake into every consuming clock domain |
| fault detection/containment | External TB can observe mutations | Fail-closed oracle diagnoses them | Synthesizable monitor/error/resync if runtime fault tolerance is claimed |
| crosstalk, ringing, PVT | Not represented | Not represented | Post-layout extraction, SI/noise, package/board and lab margin evidence |

## Decision and release blockers

The W4 owner ICG boundary is directionally better than the original clock AND,
and the new SDC/manifest state a clearer intended timing contract. Neither the
committed nor dirty owner snapshot supplies the measured physical artifacts
needed for DDR timing or CDC qualification. A digital GO must not be promoted
to physical/full-link GO.
Before such a claim, at minimum require:

1. mapped characterized ICG and ODDR/IDDR or equivalent endpoint cells;
2. explicit clocks, quarter-cycle relationship, uncertainty, both-edge
   setup/hold, minimum pulse, and clock-gating checks with unconstrained count 0;
3. CDC/RDC ownership for `retire_toggle_o` and reset release at every consumer;
4. post-route/SDF edge and data timing plus duty/PVT/SI evidence;
5. a defined mid-frame reset policy or a proven idle-only reset protocol; and
6. if fault resilience is claimed, synthesizable detection/containment rather
   than an external TB-only checker.

Reproduce only the independent A8 tests with:

```bash
tests/w4_a7_ddr_independent/run_tests.sh
```
