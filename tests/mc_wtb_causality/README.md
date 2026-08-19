# MC-WTB test-only four-arm causal core

This package is an executable synthetic causal check layered on the hardened
Stage-1 interface. It does not change the production model. Its narrow question
is whether the supplied pose reconstructs each event's absolute static-landmark
pixel, not whether a locality projection becomes smaller.

## Independence boundary

`independent_fixture_generator.py` uses standard-library JSON/SHA-256 and
integer quarter-turn arithmetic only. `independent_oracle.py` consumes plain
dictionaries and a committed absolute-pixel table. Neither module imports
`demos.mc_wtb`, `demos.known_motion_coordinate`, a production warp, loader, or
tiler. The integration test imports the production analyzer separately as the
system under test.

The camera is a 65×65 pinhole with `fx=fy=32`, `cx=cy=32`. Seven asymmetric
positive landmarks and one negative sentinel are observed under exact 0°, 90°,
180°, and 270° world-to-sensor roll matrices. Pose timestamps are exactly the
corresponding event-group timestamps (`1000..4000 ns`), so all arms satisfy the
hardened deterministic-latest-pose rule with age zero.

## Four arms

- `C0_IDENTITY`: 8 identity observations. Absolute SSE 0 and packet-key delta
  0 are mandatory.
- `C1_CORRECT`: 32 observations with correct matrices. All 32 must reconstruct
  their event-ID-bound reference pixels, SSE 0.
- `C2_WRONG_VALID`: byte-identical C1 events and timely pose IDs/timestamps,
  with all matrices set to identity. Execution must succeed, while the oracle
  reports 8/32 exact and SSE 9664.
- `C3_POSE_PERMUTED`: the same event bytes with matrices cyclically shifted.
  Execution must succeed, while the oracle reports 0/32 exact and SSE 9664.
  Its packet-key projection misleadingly improves from 28 packets/3612 bits to
  24 packets/3096 bits; geometry therefore has precedence over that proxy.

`events_causal.jsonl` is the single common event input for C1/C2/C3. The three
pose streams have identical header, pose IDs, timestamps, timebase, and record
fields except for `rotation_matrix`. Exact fixture bytes, generator source,
landmark table, artifact bundle, expected results, and the deterministic
summary are SHA-256-bound by `fixtures/manifest.json`.

## Status and claim boundary

The only success status published by this slice is:

```text
PASS_SYNTHETIC_CAUSAL_CORE
```

It is not `PASS_SYNTHETIC_CAUSAL_DISCRIMINATION`; the full mutation campaign is
not part of this slice. It also makes no claim about real-data generalization,
tile/time robustness, an actual codec or wire bandwidth, reversibility,
translation/depth, RTL, or PPA.

## Run

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/mc_wtb_causality -p 'test_*.py' -v
```

The committed fixtures can be regenerated deterministically into a temporary
directory with:

```bash
python3 tests/mc_wtb_causality/independent_fixture_generator.py /tmp/mcwtb-causal
```
