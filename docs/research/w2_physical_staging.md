# W2 physical staging compositions

Three uniquely named synthesis tops replace the generic behavioral link
endpoint at the exact frozen owner seam:

| composition | staging top | charged link | GS mapped inventory |
|---|---|---|---|
| canonical Fovea + R1 | `w2_fovea_r1_physical_staging_top` | clock + 2 data | 1 `TLATNTSCAX2`, 2 `MX2X1` |
| A2 batched IWRR + P6 | `w2_a2_p6_physical_staging_top` | clock + 5 data | 1 `TLATNTSCAX2`, 5 `MX2X1`, 5 `DFFRHQX1` |
| A3 exact scalar prefix + P6 | `w2_a3_p6_physical_staging_top` | clock + 5 data | 1 `TLATNTSCAX2`, 5 `MX2X1`, 5 `DFFRHQX1` |

The non-link boundary is intentionally physical and charged:
`source_accept[15:0]`, two retire-valid bits and ordered retire addresses,
drain, and combined protocol error. Candidate debug signals are internal. The
R1 top has no artificial link-enable input; A2/A3 retain their native link
admission gate. The canonical Fovea sources are immutable production mirrors
whose bytes match both authoritative server archives and the original local
fixtures.

The R1 abstraction replaces only the TX clock and two data muxes. Its launch,
RX edge state, and observer remain frozen inferred RTL. Thus R1 does not claim
an edge-FF, ODDR, or IDDR binding. The P6 abstraction uses the previously
qualified mapped interfaces. In both cases `TLATNTSCAX2` test enable is tied
low. Cell names and named connections are mapped-netlist evidence only; the
archives do not contain the referenced Liberty/LEF payloads, so no cell
function, pin arc, geometry, or PVT claim is made.

Owner-versus-staged tests compare every normalized output and every raw link
transition in generic and guarded test-model GS selections. They include link
stalls, persistent traffic, A2 elastic refill/pop, A3 held offers, canonical
Fovea contention, drain, and legal reset/re-arm. Reset equivalence remains
limited to drained transitions while the sample clock is low.

The strict receipt is
`rtl/technology/physical_staging/physical_staging_manifest.json`. Production
filelists contain no test models or generic fallback P6 endpoint. Missing or
multiple selection and GS elaboration without a real library fail closed.
Run `scripts/run_w2_physical_staging.sh` for manifest, behavioral, negative,
and structural checks.

Server launch is still a physical qualification step, not an accomplished
result. Generated/gated-clock constraints, P6 half-cycle timing, CTS, routing,
power, frequency, and PPA remain explicit HOLD until these exact staging tops
run through the authoritative server flow.
