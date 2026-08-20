# Source-bound UZH MC-WTB six-arm companion generator

This standard-library package consumes five runtime inputs: a completed
pose-join package, its exact bound join spec, a completed source-bound geometry
adapter package, a per-event retire receipt, and a frozen generator spec.  It emits the strict controls
V2 arms `RAW`, `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`, `MC_DELAYED`, and
`RETIRE_WARP` without changing the controls evaluator.

The generator imports the pose-join and adapter **inspectors** only for source
qualification.  Its quaternion interpolation, rotation matrices, analytic
Jacobian radtan inverse, projection, status and arm rays are implemented here
independently; the production adapter is compared against this path and is not
used as the geometry oracle.
The production gate also binds the independent oracle's canonical Q12 hashes,
status counts, and OOF-ID lists for all five arms available before a retire
receipt (`RAW` through `MC_DELAYED`).  Its radtan polynomial uses the frozen
explicit multiplication chain so Python exponentiation reassociation cannot
silently move a Q12 boundary.

The public API is exactly:

```python
generate(pose_join_dir, join_spec_path, adapter_dir, retire_receipt_path,
         generator_spec_path, result_dir)
inspect(result_dir, pose_join_dir, join_spec_path, adapter_dir,
        retire_receipt_path, generator_spec_path)
```

`RETIRE_WARP` is never inferred from occurrence time, a constant latency,
cycle period, average, or adapter output.  An official-source run requires an
external JSONL receipt whose provenance class is `OBSERVED_ENDPOINT_RUN`,
whose 1:1 IDs/timestamps bind to the pose-join source epoch, and whose records
contain the supplied per-event retire timestamps.  Missing, synthetic,
duplicate, reordered, pre-occurrence, or out-of-pose-coverage retire data
fails before publication.  The repository contains no official retire receipt
and therefore makes no official six-arm output claim by itself.
The provenance-class string alone is not authority: the production generator
accepts only retire JSONL bytes that a reviewer has approved out-of-band and
pinned by SHA-256 in the generator spec.  Authenticity of the producer,
configuration, raw-run artifact, and clock-mapping evidence is not established
by their digest strings and remains an external review gate.  Official
promotion therefore remains HOLD even when the generator accepts the receipt.

Synthetic retire receipts are accepted only with a `SYNTHETIC_FIXTURE` generator
spec and non-production fixture authorities.  They receive `PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE`
and exist solely for native tests.  They can never produce the official-source
status.

Successful production generation is scoped to
`PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED`; every output retains
`HOLD_MC_WTB_REAL_DATA_BENEFIT`.  Actual retire provenance is
not a throughput, loss, latency-benefit, clock-alignment, codec, wire, RTL or
PPA measurement.

Implementation acceptance is only
`PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED`.  Until a complete reviewed
1,100-record retire receipt exists, the release ledger remains
`HOLD_OFFICIAL_SIX_ARM_GENERATOR`, `HOLD_SOURCE_BOUND_RETIRE_TIMESTAMPS`, and
`HOLD_MC_WTB_REAL_DATA_BENEFIT`.

`inspect(result, pose_join, join_spec, adapter, retire_receipt, generator_spec)`
always requires the result plus all five source authorities and recomputes the complete JSONL and receipt.
Self-contained hashes are insufficient.  Publication is deterministic,
no-overwrite, files-first and `COMPLETE.json`-last through a private sibling
staging directory.  Concurrent same-UID input swaps and mutable network
filesystems remain outside this local filesystem threat model.

Publication requires Linux `renameat2(RENAME_NOREPLACE)` and fails closed when
that primitive is unavailable.  The supported development path is WSL/Linux,
matching the Linux server environment.  Native Windows execution is not
supported or claimed.  The package otherwise uses only the Python standard
library and has no external runtime dependency.

The available A23 projected replay is negative evidence, not a retire input:
its 1x summaries report `generated=1100`, `source_overrun=81`, and
`accepted=retired=1019`.  The generator requires the exact full source cohort;
it rejects that 1,019-event stream and never fills the missing 81 timestamps.
