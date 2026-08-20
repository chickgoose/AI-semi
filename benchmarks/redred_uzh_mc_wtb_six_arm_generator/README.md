# Source-bound UZH MC-WTB six-arm companion generator

This standard-library package consumes four external authorities: a completed
pose-join package with its exact bound spec, a completed source-bound geometry
adapter package, and a per-event retire receipt.  It emits the strict controls
V2 arms `RAW`, `SENSOR_FIXED`, `MC_CORRECT`, `MC_WRONG`, `MC_DELAYED`, and
`RETIRE_WARP` without changing the controls evaluator.

The generator imports the pose-join and adapter **inspectors** only for source
qualification.  Its quaternion interpolation, rotation matrices, analytic
Jacobian radtan inverse, projection, status and arm rays are implemented here
independently; the production adapter is compared against this path and is not
used as the geometry oracle.

`RETIRE_WARP` is never inferred from occurrence time, a constant latency,
cycle period, average, or adapter output.  An official-source run requires an
external JSONL receipt whose provenance class is `OBSERVED_ENDPOINT_RUN`,
whose 1:1 IDs/timestamps bind to the pose-join source epoch, and whose records
contain the supplied per-event retire timestamps.  Missing, synthetic,
duplicate, reordered, pre-occurrence, or out-of-pose-coverage retire data
fails before publication.  The repository contains no official retire receipt
and therefore makes no official six-arm output claim by itself.

Synthetic retire receipts are accepted only with a `SYNTHETIC_FIXTURE` generator
spec and non-production fixture authorities.  They receive `PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE`
and exist solely for native tests.  They can never produce the official-source
status.

Successful production generation is scoped to
`PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED`; every output retains
`HOLD_MC_WTB_REAL_DATA_BENEFIT`.  Actual retire provenance is
not a throughput, loss, latency-benefit, clock-alignment, codec, wire, RTL or
PPA measurement.

`inspect(result, pose_join, join_spec, adapter, retire_receipt, generator_spec)`
always requires all six objects and recomputes the complete JSONL and receipt.
Self-contained hashes are insufficient.  Publication is deterministic,
no-overwrite, files-first and `COMPLETE.json`-last through a private sibling
staging directory.  Concurrent same-UID input swaps and mutable network
filesystems remain outside this local filesystem threat model.

The available A23 projected replay is negative evidence, not a retire input:
its 1x summaries report `generated=1100`, `source_overrun=81`, and
`accepted=retired=1019`.  The generator requires the exact full source cohort;
it rejects that 1,019-event stream and never fills the missing 81 timestamps.
