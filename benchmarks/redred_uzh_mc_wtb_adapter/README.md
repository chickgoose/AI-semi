# UZH pose-join to MC-WTB disposition adapter

This standard-library-only package consumes a completed
`redred_uzh_shapes_pose_join` package together with the exact specification to
which that package is bound.  It rejects an unbound, incomplete, promoted, or
tampered input package before creating an output directory.

For the selection start and for every admitted event timestamp, the adapter
uses the two source pose records to interpolate `T_WC`: translation is linear
and orientation uses the geometry package's normalized, shortest-arc xyzw
SLERP.  It then applies the existing orientation-only raw-radtan geometry from
`benchmarks.redred_uzh_mc_wtb.geometry`.

Every source occurrence produces exactly one record with one disposition:

- `WORLD_REFERENCE_EVENT`
- `RAW_ESCAPE_GEOMETRIC_OOF`
- `RAW_BYPASS_INVALID_GEOMETRY`

The record always preserves the source dataset index, join sequence index,
timestamp lexeme and integer nanoseconds, raw x/y, and 0/1 polarity.  A RAW
disposition is only a lossless semantic escape marker.  This package does not
implement a RAW FIFO, packet, codec, link, or decoder.

The official pinned 1 ms `shapes_rotation` package is expected to produce
1,094 world-reference dispositions, six geometric RAW escapes, and zero
invalid-geometry bypasses.  Those numbers are regression anchors for this one
source window, not accuracy, compression, or MC-WTB benefit.

The output status remains scoped:

```text
PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED
HOLD_MC_WTB_REAL_DATA_BENEFIT
```

No translation/depth compensation, controls, tile aggregation, wire format,
RTL, timing, power, or PPA claim is made.

## Offline boundary and threat model

This is an offline, source-bound analytical adapter.  Both the reference pose
and every event pose use a closed left/future-right bracket, so normalized
SLERP requires future-pose lookahead.  The result is not evidence of a causal
hardware path, real-time pose availability, or validated event/pose clock
alignment.  The receipt records these four facts explicitly as
`offline_future_bracket_slerp=true`, `future_pose_lookahead_required=true`,
`causal_hardware_claimed=false`, and `clock_alignment_validated=false`.

The inspector treats the result directory, its receipt/completion hashes, and
all generated fields as attacker-replaceable.  Consequently `inspect` always
requires the original completed pose-join directory and its exact bound spec,
revalidates that source package, then recomputes the complete adapter artifact
and receipt semantics.  Hash-only or source-free inspection cannot return
PASS.  This protects the scoped package from ordinary post-publication tamper
or coordinated local rehash, but it is not a signature, trust anchor, secure
clock, or proof that the supplied official source itself establishes MC-WTB
benefit.

A concurrent same-UID source-package swap and a mutable network filesystem are
explicitly outside this threat model.
