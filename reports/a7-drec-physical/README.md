# DREC physical evidence

This directory preserves raw, immutable-tool evidence for the N=16, K=4 DREC
qualification.  It does not turn pre-layout synthesis estimates into final PPA
or Fmax claims.

## Genus screening: 2026-08-08

- RTL commit: `0e04b8f5052fa1aac84ff2350966c210a8b9854f`
- Source boundary: N=16, K=4, ADDR_WIDTH=16, 104 registered state bits,
  376 functional pin bits
- Target: 5.000 ns
- Library: slow GSCLIB045, library checksum recorded in each manifest
- Imported archive SHA-256:
  `2d279d74b7968cfccbccbe72597175e417b8a00d73112a211732ea7505f68dcc`
- Byte-for-byte archive: `archives/drec-genus-n16-k4-5ns-0e04b8f.tar.gz`

| Metric | DREC prefix K4 | Equal-state replicated K4 | DREC delta |
| --- | ---: | ---: | ---: |
| mapped cell area | 5,826.569 um2 | 7,928.415 um2 | -26.510% |
| combinational cells | 3,089 | 4,407 | -29.907% |
| sequential cells | 104 | 104 | equal |
| 5 ns WNS | 0.0000 ns | -1.0435 ns | DREC meets target only |
| screening frequency | 200.000 MHz | 165.467 MHz | +20.869% |
| vectorless total power | 0.550772 mW | 0.739729 mW | -25.544% |

Both runs report zero latches, unresolved references, and error/fatal lines.
The power rows are vectorless screening estimates.  No workload power,
energy/event, post-route Fmax, or signoff claim may be derived from them.

The raw comparison, manifests, native reports, generated netlists, and tool
logs are under `genus-n16-k4-5ns-20260808/`.

## Post-route status

The first 5 ns Innovus fixed-netlist attempt is preserved under
`innovus-interrupted-n16-k4-5ns-20260808/`; imported archive SHA-256 is
`b104119f804e44ba02cb02d85e428612a68bfda7d83123f618f706db7ed37a7c`.
The byte-for-byte archive is
`archives/drec-innovus-interrupted-5ns-0e04b8f.tar.gz`.
It was deliberately stopped during pre-CTS optimization after more than
13 minutes.  The DREC prefix had not met timing, the reference had not started,
and the exploratory flow lacked a frozen deterministic pin policy and a fast
hold view.  Its `FAIL_INNOVUS_1` status records the interruption, not an RTL or
functional failure.  It must not be ranked as a completed P&R result.

A future Innovus fixed-netlist diagnostic is tracked separately.  Until placement,
CTS, detailed route, extraction, setup/hold, unconstrained-path, connectivity,
DRC, antenna, and deterministic-pin-policy evidence is complete, it is not a
qualified PPA/Fmax result.
