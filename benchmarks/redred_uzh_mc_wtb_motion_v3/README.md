# MC-WTB motion metric v3 contract

This directory defines a development-only preregistration contract for a
replacement MC-WTB motion assay. It does not implement an evaluator and it does
not change, supersede, reinterpret, or delete the frozen v2 assay in
`benchmarks/redred_uzh_mc_wtb_motion/`. The development registration binds
the exact bytes of its frozen `paret_preregistered.json`; v3 results remain a
separate metric family and cannot be substituted for v2 results.

Files:

- `preregistered.schema.json` is the strict JSON Schema for the development
  contract and its score-blind selected internal holdout.
- `development_preregistered.json` binds both exact cohorts and the metric
  gates. Development observations were already available, so development
  estimates are not confirmatory evidence.

This schema revision is `preregistration/v2` because cohort selection and the
mandatory complementary gate are material contract changes. That revision is
unrelated to, and does not alter, the preserved legacy PARET metric v2.

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
anchor of the same polarity. Its denominator is the exact ordered query
identity ledger for every arm: 1,100 development IDs or 370 internal-holdout
IDs. Missing, duplicate, reordered, or arm-filtered events are hard failures.
A valid world ray remains valid even when its continuous projection lies
outside the 240x180 reference image. Reference-image OOF therefore never
becomes a scalar accuracy penalty.

Invalid distortion, a non-finite ray, or a ray behind a domain that requires a
forward projection is reported through the separate coverage/escape ledger.
Each cohort requires every registered world ray; an invalid event causes a
HOLD instead of an arm-local drop or an invented score.

## Primary, mandatory complementary gate, and secondaries

There is exactly one primary:

```text
mean same-polarity query-to-canonical-anchor angular nearest-neighbor cost
relative reduction = 1 - mean(MC_CORRECT) / mean(SENSOR_FIXED)
```

The angular NN above remains the only primary. The preregistration records the
legacy-v2-compatible candidate effect threshold and moving-block bootstrap,
but a development result can never emit a confirmatory PASS.

The analytic Gaussian focus is mandatory complementary evidence, not a second
primary and never a substitute for angular NN. For continuous reference-image
coordinates `u_i`, fixed `sigma = 1.0 px`, and distinct ordered same-polarity
pairs, its bounded score is:

```text
G = sum_(p_i=p_j, i!=j) exp(-||u_i-u_j||^2 / (4 sigma^2))
    / (N0(N0-1) + N1(N1-1))
```

It has no raster, grid phase, bilinear splat, or self-energy subtraction.
It consumes only the exact ordered query events, projected from the same
cohort-reference axes as the primary. Every event has unit mass and the same
exact cohort ID denominator. Finite reference-image OOF coordinates remain
admitted on the fixed padded canvas
`[-16,255] x [-16,195]`; a coordinate beyond that predeclared admission
envelope causes HOLD, never clipping, dropping, or resizing. A motion-benefit
PASS requires both the angular primary gate and a strictly positive
`MC_CORRECT - SENSOR_FIXED` Gaussian-focus effect with a positive one-sided
97.5% lower bound. Each moving-block resample uses paired equal-timestamp
clusters and recomputes both complete arm scores and their difference. Passing
focus cannot rescue a failed primary, and passing the primary cannot override
a failed focus gate.

The registered secondaries remain descriptive only:

- symmetric angular Chamfer distance;
- polarity-stratified primary components.

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
registration.holdout_metric_or_arm_scores_inspected = false
registration.frozen_before_holdout_score_access = true
```

The internal holdout is no longer unspecified. The registration binds the
score-blind selection contract
`benchmarks/redred_uzh_mc_wtb_motion_v3/cohorts.json` at SHA-256
`5a24829fdaaaec679e8ef82ac435158ee0225af5b644d3056895e7fcc94acef4`.
That contract selects `shapes_rotation_holdout_43_321` by the first eligible
positive whole-second offset rule without consulting metric or arm scores. Its
102-event anchor is `[43.320750000,43.321000000)` and its exact 370-event query
is `[43.321000000,43.322000000)`. Raw-line and ordered-ID hashes are repeated
in the preregistration. Development and holdout intervals and IDs are
disjoint.

The existing six-arm artifact SHA is explicitly development-only. A derived
internal-holdout artifact must verify the registered source IDs, timestamps,
and polarities and bind its own SHA in an evaluation receipt before any metric
computation; the development artifact SHA cannot be reused as its authority.

No holdout score was inspected to write this contract, and no holdout result
is present here. The cohort bytes/counts needed for score-blind selection are
not called unseen data; the protected boundary is metric/arm score access.
Changing the primary, complementary focus definition or sigma, thresholds,
controls, padding, or bootstrap after this freeze requires a new assay version
and cannot reuse the same confirmatory claim.

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
