# A9 W5 ASIC/Vivado DDR Technology Boundary

## Outcome

W5 provides an optional, fail-closed technology wrapper for the production R1
endpoint contract of exact A7 commit
`42377ca81340951bfcd453b3bd664e673091f9f3`. It does not change A7 and does not claim
that generic RTL, an ASIC cell library, and a Xilinx device are interchangeable
for free. The implementation status is **digital mapping-plan GO, physical
HOLD**. No physical PPA, STA, Vivado implementation, or standard-cell mapping
was run.

Exactly one compile macro is mandatory:

| Selection | Clock boundary | TX launch | RX capture | Additional required closure |
|---|---|---|---|---|
| `A9_W5_TECH_GENERIC` | low-transparent latch plus gate | exact A7 level-selected registered address | opposite-edge generic registers | functional reference only; not a physical mapping |
| `A9_W5_TECH_ASIC` | `a9_w5_asic_icg_cell_adapter` | `a9_w5_asic_oddr2_cell_adapter` | `a9_w5_asic_iddr2_cell_adapter` | target-owned, characterized adapter source and exact library cells |
| `A9_W5_TECH_XILINX_7SERIES` | `BUFGCE` | two `ODDR` primitives | two `IDDR` primitives | Vivado UNISIM, selected part/package, legal clock/IO placement and XDC |

No selection, multiple selections, an ASIC selection without all adapter
modules, and a Xilinx selection without UNISIM all fail elaboration. The ASIC
filelist intentionally does not ship a permissive black-box stub. Test-only
mock cells are excluded from every synthesis filelist and from PPA evidence.
`a9_w5_ddr_tx_endpoint` and `a9_w5_ddr_rx_endpoint` are the separately placeable
pad-side boundaries. `a9_w5_ddr_link` connects them only as a local loopback for
exact A7 comparison; it is not the physical two-chip top.

## Technology-neutral observable contract

The wrapper freezes `ADDR_WIDTH=4`, `DATA_WIDTH=2`, and address-only event
identity. A charged reset-release arming bit holds `event_ready_o` low through
the first safe reference edge after reset. Admission is exactly
`event_valid_i && event_ready_o` on later `ref_clk_i` rising edges; continuous
valid with a changing address is one occurrence per handshake. The low address symbol
is captured on the event-gated forwarded clock's rising edge; the high symbol
is captured on its falling edge, which commits one toggle. Idle suppresses the
forwarded clock, while adjacent valid cycles form a continuous burst.

The ODDR mapping is deliberately clocked by `ref_clk_i`, not by
`sample_clk_i`. At a valid rising edge its D1 input uses the currently admitted
low bits because the holding register updates on that same edge; D2 uses the
registered high bits at the following falling edge. This preserves A7's
quarter-cycle relationship:

| Path | Launch | Capture | Nominal window |
|---|---|---|---:|
| low symbol | ref rising, 0 ns | burst/sample rising, 4 ns | 4 ns |
| high symbol | ref falling, 8 ns | burst/sample falling, 12 ns | 4 ns |
| gate enable | ref rising while sample clock is low | next sample rising | 4 ns nominal setup |

Mapping an ODDR directly on `sample_clk_i` would move data at the receiver's
capture edge and is therefore not equivalent to this contract. A target adapter
that adds a register, changes `SAME_EDGE`/`OPPOSITE_EDGE` behavior, reverses D1
and D2, or changes reset values is a different design and must fail the W5
lockstep until its latency/edge contract is explicitly revised.

The RX primitive contract exposes the rising and falling samples after their
respective edge updates. A low-phase commit hold admits the complete
`{fall,rise}` pair only after the falling sample updates; this prevents the next
rising sample from changing half of `retire_addr_o` before commit.
`retire_toggle_o` changes on the falling burst edge. The combination matches
the exact A7 post-delta value and between-commit address stability; it is not
permission to sample the retirement signals in an unrelated core domain. The
target mapping must include this commit hold or prove an equivalent stable
registered output—it is not free IDDR behavior. A charged `seen_toggle`
observer samples the raw falling-edge toggle on the next phase-related
`ref_clk_i` rising edge and emits the complete registered `retire_addr_o` with
`retire_valid_o`. This half-cycle observer is not a 2FF CDC synchronizer.

The registered output is available one reference cycle after admission. An
always-ready synchronous consumer samples that output in the pre-NBA region of
the following edge, so architectural consumption is two cycles after admission.
`drain_idle_o` is true only when there is no combinational same-cycle launch,
TX frame state is inactive, the forwarded clock is low, raw and seen toggles
agree, and `retire_valid_o` is low. The last guard keeps reset blocked through
the pending valid cycle until the synchronous consumer samples it. Drain is the
required legal-reset precondition; it is not a queue-empty indication for any
downstream adapter.

The final A7 owner generic reference charges the complete boundaries as follows:

| A7 `42377ca` endpoint | Pins | State bits | Charged functional cells |
|---|---:|---:|---:|
| DDR2 | 3 | 20 | 29 |
| parallel4 | 5 | 18 | 27 |

These are the owner's generic complete-endpoint counts, including the four-cell
drain guard. They are recorded as the comparison contract, not reused as A9
ASIC/Vivado mapped counts and not presented as physical PPA. Primitive and
adapter mapping can change the charged cell/state representation.

## ASIC mapping plan

1. Choose one characterized glitch-free ICG whose enable aperture admits the
   `frame_enable_q` transition while `sample_clk_i` is low. Bind the complete
   ICG, including scan/test enable policy, inside
   `a9_w5_asic_icg_cell_adapter`; a combinational clock AND is prohibited.
2. Choose ODDR and IDDR cells with opposite-edge operation and compatible async
   clear behavior. If the library has only separate positive/negative flops,
   treat the replacement as a new target adapter and prove that its output
   mux, clocking, test mode, and reset cannot glitch.
3. Add the target adapter source explicitly before `filelists/asic.f`. Resolve
   all three adapter modules and reject unresolved black boxes in synthesis.
4. Bind exact Liberty corners and retain ICG enable, minimum-pulse,
   recovery/removal, and both-edge setup/hold arcs. Run clock-gating checks and
   generated-clock propagation through the mapped ICG.
5. Declare actual data-pad and clock-pad cells, package/board loads, drive/slew,
   and clock-to-data skew. W5 supplies no default zero load.

An ASIC adapter is not eligible merely because the mock passes. The mock proves
only the required Boolean/edge contract.

## Vivado 7-series mapping plan

The optional Xilinx branch names actual `BUFGCE`, `ODDR`, and `IDDR` UNISIM
interfaces. `ODDR` and `IDDR` use `DDR_CLK_EDGE="OPPOSITE_EDGE"` and async reset.
The build must use a selected 7-series part whose primitive parameters and
clocking rules match these interfaces; another family requires a new branch,
not macro reuse.

Before implementation eligibility, the target XDC must specify every clock,
`PACKAGE_PIN`, `IOSTANDARD`, data/clock `DRIVE`, `SLEW`, input/output delay, and
board timing assumption. ODDR/IDDR placement in IOB resources and the legality
of routing `BUFGCE` to the forwarded-clock output must be checked from actual
Vivado reports. Data and forwarded-clock package/board skew must fit inside the
4 ns nominal window after jitter, duty-cycle distortion, primitive clock-to-Q,
pad delay, receiver setup/hold, and margin. The test UNISIM subset contains no
timing and cannot support any of those conclusions.

## SDC, reset, CDC, and pad/load boundary

[`a9_w5_ddr_technology_boundary.sdc`](../../constraints/a9_w5_ddr_technology_boundary.sdc)
defines related 16 ns reference/sample clocks, the 4 ns phase, and a
phase-preserving generated burst clock. It constrains rising and falling output
delays separately and refuses to load unless target-specific uncertainty,
receiver setup/hold, data-pad load, and clock-pad load variables are supplied.
The generated-clock sink must be rebound to the actual ICG/BUFGCE output pin if
the top-level burst port is not the propagated clock object.

The checked-in SDC names the combined contract ports so all timing relationships
are visible in one specification. It is not a drop-in physical script for the
split TX and RX pad tops. A target flow must split and rebind the constraints to
its exact endpoint hierarchy and must treat every missing clock, port, pad, or
primitive pin as an error rather than allowing an empty collection.

Do not false-path the ref-to-burst relationship, the burst-fall-to-next-ref-rise
observer path, or replace either nominal 4 ns path with a full-cycle exception.
Do not false-path reset: reset assertion is supported only after drain while the
burst clock is low, and target recovery/removal plus RDC analysis remain
required. Public `retire_addr_o`/`retire_valid_o` are registered in the related
reference domain; raw address/toggle remain in the burst domain. An unrelated
consumer clock still requires an explicitly verified CDC or queue outside W5.

## Executed digital evidence and limits

The test materializes eight exact A7 production files from commit `42377ca` and
checks every SHA-256 before compilation. It first executes A7's own complete
DDR-versus-parallel digital regression, including reset arming, continuous
changing-address traffic, legal drain/reset, and the observed invalid
mid-frame-reset case. Generic, ASIC-adapter-mock, and Xilinx-UNISIM-mock branches
then each pass an 18-event comparison against both the exact A7 DDR endpoint and
its complete parallel reference at ready, raw link, one-cycle availability,
two-cycle synchronous consumption, and launch/pending-valid guarded drain
boundaries. Four negative tests prove fail-closed compilation for
missing/multiple selection and missing ASIC/Xilinx closures.

This establishes wrapper behavioral equivalence under the nominal digital
contract only. It does not establish primitive timing equivalence, glitch-free
silicon behavior, device support, placement, routing, CDC/RDC closure, power,
area, frequency, or PPA. Those remain **HOLD**.

```sh
scripts/run_a9_w5_ddr_technology_boundary.sh
```
