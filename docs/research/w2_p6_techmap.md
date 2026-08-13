# W2 P6 clock and edge technology map

Status: **partial logical binding GO; DDR and P6 physical implementation HOLD**.

W2 adds a technology boundary beside the frozen P6 owner. It does not edit the
owner's launch, TX, RX, observer, or endpoint files. The generic selection is a
simulation and synthesis reference. The GSCLIB045 selection uses only mapped
cell names and named connections observed in the authoritative Ganghee raw and
buffered server archives:

| Function | Binding | Authoritative mapped evidence | Status |
| --- | --- | --- | --- |
| clock gate | `TLATNTSCAX2(CK,E,SE,ECK)`, `SE=0` | raw 5/5 netlists; buffered 47/14 | logical binding GO; P6 timing HOLD |
| TX symbol mux bit | `MX2X1(A,B,S0,Y)` | raw 2/1; buffered 74/12 | flow-mapped polarity GO; P6 timing HOLD |
| rising-edge async-clear bit | `DFFRHQX1(RN,CK,D,Q)` | raw 0/0; buffered 53/14 | buffered-only logical binding GO; P6 timing HOLD |
| falling-edge capture | inferred `always_ff` | no mapped candidate in either archive | explicit HOLD |
| ODDR / IDDR | no cell | no mapped candidate in either archive | explicit HOLD |

Counts are instances/netlists-containing-the-cell. `TLATNCAX2`, selected by an
earlier draft, has zero mapped instances in both authoritative inventories and
is no longer used. The raw archive also has no `DFFRHQX1`; the manifest exposes
that limitation instead of generalizing the buffered observation to the raw
workload.

The only Kanghee/PDK-flow evidence used here is:

- raw `/tmp/ganghee-pnr-raw-golden-20260813.tar.gz`, SHA-256
  `7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3`;
- buffered `/tmp/ganghee-pnr-golden-20260813.tar.gz`, SHA-256
  `1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f`.

The server Tcl references GSCLIB045 v4.7
`slow_vdd1v0_basicCells.lib`, `gsclib045_tech.lef`,
`gsclib045_macro.lef`, and `qrc/qx/gpdk045.tch` under
`/home/aiasic26911/gsclib045_all_v4.7/gsclib045`. Neither archive contains
those payloads. The receipt therefore claims mapped connectivity and tool-flow
references only: it does **not** claim Liberty functions, Liberty pin timing
arcs, LEF geometry, or a real-library functional simulation.

All 24 input SDCs describe one positive-edge port clock named `clk`, with
0.100 ns uncertainty and 0.250 ns input/output delays. They do not constrain a
P6 `ref_clk`/`sample_clk` relationship, generated/gated P6 clock, falling edge,
or DDR interface. The Genus and Innovus artifacts demonstrate that the cells
were used by those unrelated server workloads; they do not close P6 STA, clock
gating, CTS, routing, or PPA. Those remain HOLD.

The generic branch preserves the owner's exact
`sample_clk_i & frame_active_o & rst_n` expression and ternary data selection.
The GSCLIB branch uses the mapped ICG and mux interfaces. The behavioral cell
tests cover the owner's legal drained reset sequence with each reset transition
made while the sample clock is low. Arbitrary high-phase asynchronous reset
equivalence through the mapped clock gate is not claimed.

Exactly one compile selection is required:

- `W2_P6_TECH_GENERIC`, normally through `filelists/generic.f`;
- `W2_P6_TECH_GSCLIB045`, normally through `filelists/gsclib045.f`.

No selection and multiple selections fail elaboration. The GSCLIB filelist
does not include permissive cell stubs. Tests use guarded functional models
only to verify wrapper wiring and owner-versus-tech digital behavior; those
models are forbidden from production filelists and are not library evidence.

Run:

```sh
scripts/run_w2_p6_techmap.sh
```

Passing tests establish archive identity and inventory, server Tcl/SDC scope,
frozen-owner identity, compile selection closure, nominal four-phase digital
equivalence, reset/protocol behavior, and selected-cell structural presence.
They do not close the explicit physical or DDR HOLDs.
