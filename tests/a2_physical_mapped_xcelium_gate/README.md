# A2 mapped-Xcelium functional gate

This fail-closed gate is limited to mapped-netlist functional validation of the
three canonical scheduler-plus-P6 endpoints:

- `a2_batched_iwrr_p6_top`
- `a3_exact_scalar_prefix_k2_p6_top`
- `a4_paired_cortical_column_k2_p6_top`

It consumes existing mapped netlist, SDF, vendor simulation models, and a
mapped testbench. It does not run or modify Genus, Innovus, SDC, power/activity,
or implementation artifacts. A PASS is explicitly not physical qualification;
the result keeps that status at `HOLD`.

## Fail-closed checks

Before Xcelium, `gate.py` verifies SHA-256 for every input, the exact canonical
endpoint and mapped-TB top, the exact `<tb_top>.dut` SDF scope, the SDF `DESIGN`,
at least one annotatable SDF entry, unique module definitions, vendor-model
module/pin declarations, named mapped-cell connections, and complete cell-model
resolution. Duplicate modules anywhere in the compile closure are rejected.

After Xcelium, the gate requires transcript/SDF-log proof that a nonzero number
of delays or timing checks were annotated. It also requires exactly one endpoint
PASS and one structured conservation record proving:

```text
generated = overrun + accepted
accepted = retired
phantom = duplicate = order_errors = 0
```

The mapped testbench must emit:

```text
A2_MAPPED_XCELIUM_CONSERVATION_PASS endpoint=a2 generated=... overrun=... accepted=... retired=... phantom=0 duplicate=0 order_errors=0
A2_MAPPED_XCELIUM_PASS endpoint=a2
```

Use `a3` or `a4` for the other canonical endpoints. The manifest schema is
`a2_physical_mapped_xcelium_manifest_v1`; each artifact record contains a safe
relative `path` and lowercase `sha256`. Vendor records additionally declare the
exact module-to-pin list used during preflight.

Run one prepared endpoint:

```sh
python3 tests/a2_physical_mapped_xcelium_gate/gate.py \
  --manifest /path/to/read-only-bundle/manifest.json \
  --xrun /path/to/xrun \
  --work-dir /new/private/work \
  --output /new/private/result.json
```

Focused local tests use a fake simulator and do not run implementation:

```sh
tests/a2_physical_mapped_xcelium_gate/run_all.sh
```
