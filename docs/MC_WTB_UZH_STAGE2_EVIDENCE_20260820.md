# MC-WTB UZH Stage-2 evidence — 2026-08-20

## Current decision

The pinned UZH `shapes_rotation` source-preserving pose join and the
orientation-only geometry core are implemented and independently replayed.
This is a scoped data/geometry baseline, not an MC-WTB benefit, codec, RTL, or
PPA result.

```text
PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED
PASS_UZH_ROTATION_ONLY_NUMERIC_CORE_SCOPED
HOLD_MC_WTB_ADAPTER
HOLD_MC_WTB_REAL_DATA_BENEFIT
HOLD_CODEC_WIRE_RTL_PPA
```

## Pinned source

- Official landing page: <https://rpg.ifi.uzh.ch/davis_data.html>
- Requested ZIP URL:
  <https://rpg.ifi.uzh.ch/datasets/davis/shapes_rotation.zip>
- Local archive SHA-256:
  `56aade6bf53dcf73e8fe40905ccac8385cd7606bc9a85103bf2c9f9045117551`
- `events.txt`: 23,126,288 lines,
  SHA-256 `d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda`
- `groundtruth.txt`: 11,883 lines,
  SHA-256 `bb62c320a51c1be412e17065eb86cfffa9041841290d439c23e447f1991aabdb`
- `calib.txt` SHA-256:
  `ab797c55a990c03656fbddac2473d3eace2a22f87fea4ca3b0497862b50545cd`
- Exact license bytes SHA-256:
  `8812f83442fd0eca14eb0208988e190fdcbfebec58fa5459d3218edfdfdc5a32`

The official upstream basename `shapes_rotation.zip` and the required local
basename `uzh-shapes_rotation.zip` are recorded separately. The license deed
URL and the exact legal-code byte URL are also separate fields.

## Pose-join result

The half-open source interval is `[41.321000000, 41.322000000)` seconds.

```text
source events       23,126,288
before window       13,856,250
admitted/joined          1,100
after window         9,268,938
join dropped                 0
dataset indices     13,856,250..13,857,349
pose bracket        8,241 / 8,242
timestamp tie extras          458
```

The independent canonical projection of all 1,100 event/bracket records is:

```text
f64feafeafc6d88789984a17d532d34f90c0ee50247b336312cdc63c95809c87
```

The causal pose age is 3.712128–4.711128 ms. The strictly-future bracket lead
is 0.287058–1.286058 ms. Therefore the source join preserves inputs required
for offline SLERP, but does not claim zero-lookahead causal hardware.

The package status is generic because the same API is exercised with synthetic
test fixtures. `official_uzh_source=true` is emitted only when archive, member,
license, and selection authority match the compiled production pins.
`generated_artifact_official_uzh` is always false.

## Geometry result

UZH poses are interpreted as xyzw camera-to-world `T_WC`:

```text
R_Ct_C0 = R_WCt.T @ R_WC0
R_C0_Ct = R_WC0.T @ R_WCt
```

The current sensor ray is mapped with `R_C0_Ct`. Raw OpenCV radtan distortion
is inverted and reapplied. Continuous reference-image bounds are classified
before deterministic pixel rounding.

```text
in reference FOV          1,094
valid geometric OOF           6
behind reference              0
invalid distortion            0
total                     1,100
```

All six OOF records cross the bottom reference boundary and require a future
RAW escape path; they must not be clamped or dropped. Translation is nonzero
(up to about 0.849 mm inside the selected window), retained in geometry state,
and deliberately not applied without depth/plane information. This is an
orientation-only baseline, not a pure-rotation or world-reconstruction claim.

## Reproduced gates

From the integration worktree:

```bash
REDRED_RUN_UZH_FULL_BYTES=1 \
  python3 -B -m unittest discover \
  -s tests/redred_uzh_shapes_pose_join -p 'test_*.py' -v

REDRED_UZH_SHAPES_ROTATION_ROOT=/tmp/uzh-shapes_rotation \
  python3 -B -m unittest discover \
  -s tests/redred_uzh_mc_wtb -p 'test_*.py' -v
```

Observed results:

- pose join: 10/10 PASS, including the full archive and a coherent-rehash
  selection/spec-binding falsifier;
- UZH geometry: 19/19 PASS, including the full 509 MB event member;
- existing MC-WTB model: 28/28 PASS;
- independent causality core: 10/10 PASS;
- prior UZH projection path: 8/8 PASS.

## Claim boundary and next gate

This evidence does not yet measure tile locality, packet count, total wire
bits, loss, decoder reconstruction, or motion fidelity. It therefore does not
show that MC-WTB solves bottleneck 1 or 5.

The next implementation gate is a separate adapter that consumes the joined
event/pose records, performs explicit SLERP plus orientation-only radtan warp,
and emits exactly one disposition per source event:

```text
WORLD_REFERENCE_EVENT
RAW_ESCAPE_GEOMETRIC_OOF
RAW_BYPASS_INVALID_GEOMETRY
```

Only after equal-event-ID RAW, SENSOR_FIXED, MC_CORRECT, MC_WRONG, MC_DELAYED,
and RETIRE_WARP controls are produced may locality opportunity be evaluated.
Codec/wire, RTL, and PPA remain later gates.
