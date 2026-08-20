# MC-WTB motion metric v3 contract

This directory defines a development-only preregistration contract for a
replacement MC-WTB motion assay. It does not implement an evaluator and it does
not change, supersede, reinterpret, or delete the frozen v2 assay in
`benchmarks/redred_uzh_mc_wtb_motion/`. The development registration binds
the exact bytes of its frozen `paret_preregistered.json`; v3 results remain a
separate metric family and cannot be substituted for v2 results.

Files:

- `preregistered.schema.json` is the strict JSON Schema for development and
  future holdout preregistrations.
- `development_preregistered.json` is the exact development partition
  registration. The development observations were already available when this
  contract was written, so it is not confirmatory evidence.

## Assay question

The single primary asks whether `MC_CORRECT` query rays are closer than the
`SENSOR_FIXED` identity-pose ablation to a canonical same-polarity anchor cloud
when every coordinate is expressed in the axes of one reference camera pose.
For an undistorted sensor ray `r` and camera-to-world rotation `R(t)`, the
canonical transform is:

```text
r_reference(t) = normalize(transpose(R(reference_time)) * R(t) * r)
```

Every anchor uses its own timestamp in that transform. `MC_CORRECT` query
events do likewise. `SENSOR_FIXED` is the explicit no-motion ablation: its raw
sensor ray is interpreted in reference axes with the identity relative
rotation. Wrong-direction, delayed-pose, and observed-retire-pose arms are
predeclared controls, not alternate primaries.

The primary per-event cost is the angular distance to the nearest canonical
anchor of the same polarity. Its denominator is the exact ordered 1,100-event
query identity ledger for every arm. Missing, duplicate, reordered, or
arm-filtered events are hard failures. A valid world ray remains valid even
when its continuous projection lies outside the 240x180 reference image.
Reference-image OOF therefore never becomes a scalar accuracy penalty.

Invalid distortion, a non-finite ray, or a ray behind a domain that requires a
forward projection is reported through the separate coverage/escape ledger.
The development primary requires all 1,100 world rays; an invalid event causes
a HOLD instead of an arm-local drop or an invented score.

## Primary, secondaries, and controls

There is exactly one primary:

```text
mean same-polarity query-to-canonical-anchor angular nearest-neighbor cost
relative reduction = 1 - mean(MC_CORRECT) / mean(SENSOR_FIXED)
```

The preregistration records the v2-compatible candidate effect threshold and
moving-block bootstrap, but a development result can never emit a confirmatory
PASS. The registered secondaries are descriptive only:

- symmetric angular Chamfer distance;
- polarity-stratified primary components;
- self-energy-subtracted, polarity-separated image-of-warped-events (IWE)
  concentration on one fixed padded canvas, including a fixed grid-phase
  sensitivity set.

The IWE canvas retains finite OOF coordinates. Any coordinate outside the
declared padding produces HOLD rather than clipping or dropping. IWE is not a
substitute for the angular primary: raster phase, splat kernel, and coherent
but wrongly shifted clouds can change concentration without changing absolute
world-coordinate correctness.

`MC_WRONG` and `MC_DELAYED` must be identified negative controls.
`RETIRE_WARP` must bind exact observed retire timestamps and is tested for
informativeness before it can participate as a control. An uninformative
retire control reports HOLD. `RAW` is only an identity/serialization control
for the `SENSOR_FIXED` ablation.

## Development and holdout separation

`development_preregistered.json` is explicitly marked:

```text
registration.stage = DEVELOPMENT
registration.confirmatory_eligible = false
registration.development_data_previously_observed = true
```

A future holdout requires a separate preregistration instance validated by the
same schema, exact source/cohort byte pins frozen before access, and event-ID
and time-interval disjointness from this development partition. The holdout
partition is intentionally not selected or named here. Changing the metric,
threshold, controls, padding, kernel, or bootstrap after holdout selection
requires a new version and cannot reuse the same confirmatory claim.

## Validation

Both files are ordinary JSON and require no non-standard runtime dependency:

```sh
python3 -m json.tool \
  benchmarks/redred_uzh_mc_wtb_motion_v3/preregistered.schema.json >/dev/null
python3 -m json.tool \
  benchmarks/redred_uzh_mc_wtb_motion_v3/development_preregistered.json >/dev/null
```

When the `jsonschema` package is available, validate the instance with Draft
2020-12 semantics:

```sh
python3 -c 'import json, jsonschema, pathlib; p=pathlib.Path("benchmarks/redred_uzh_mc_wtb_motion_v3"); jsonschema.Draft202012Validator.check_schema(json.loads((p/"preregistered.schema.json").read_text())); jsonschema.validate(json.loads((p/"development_preregistered.json").read_text()), json.loads((p/"preregistered.schema.json").read_text()))'
```

## Claim boundary

This package is a metric contract only. It contains no motion-benefit result,
holdout result, dataset generalization, codec or bandwidth evidence, RTL,
synthesis, timing, area, power, P&R, or phase-5 architecture claim.
