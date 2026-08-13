# W2 P6 clock and edge technology map

Status: **partial logical binding GO; DDR and physical implementation HOLD**.

W2 adds a technology boundary beside the frozen P6 owner.  It does not edit
the owner's launch, TX, RX, observer, or endpoint files.  The generic selection
is a simulation and synthesis reference.  The GSCLIB045 selection binds only
cell names and pins supported by pinned local Genus/Innovus evidence:

| Function | Binding | Status |
| --- | --- | --- |
| P6 generic clock expression | RTL AND | simulation/synthesis reference |
| P6 selected clock gate | `TLATNCAX2(CK,E,ECK)` | legal reset contract GO; physical timing HOLD |
| rising-edge async-clear bit | `DFFRHQX1(RN,CK,D,Q)` | logical binding GO; timing HOLD |
| falling-edge capture | inferred `always_ff` | `HOLD_NO_IMMUTABLY_EVIDENCED_COMPLETE_CELL_INTERFACE` |
| ODDR / IDDR | no cell | explicit HOLD |

The generic branch preserves the owner's exact
`sample_clk_i & frame_active_o & rst_n` expression.  The GSCLIB branch uses the
locally evidenced ICG.  Because `TLATNCAX2` has no asynchronous reset pin, its
behavior matches only the frozen legal reset contract: assert and release reset
after drain while the sample clock is low.  The pinned source evidence includes
a negative fixture showing why arbitrary high-phase reset would differ.
Physical duty-cycle, minimum-pulse, CTS, and gating checks remain HOLD.

Exactly one compile selection is required:

- `W2_P6_TECH_GENERIC`, normally through `filelists/generic.f`;
- `W2_P6_TECH_GSCLIB045`, normally through `filelists/gsclib045.f`.

No selection and multiple selections fail elaboration.  The GSCLIB filelist
does not include a permissive cell stub.  Tests use guarded functional models
only to prove wrapper wiring and owner-versus-tech digital behavior; those
models are forbidden from production filelists and are not library evidence.

The strict manifest pins owner SHA-256 identities, the evidence archive and
member SHA-256, exact supported cell pins, source closure, and all HOLDs.  Run:

```sh
scripts/run_w2_p6_techmap.sh
```

Passing tests establish nominal four-phase digital equivalence, reset and
protocol behavior, compile selection closure, manifest integrity, and mapped
cell presence.  They do not close the explicit physical/DDR HOLDs.
