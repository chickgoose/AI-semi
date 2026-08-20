# MC-WTB motion-qualified baseline control

This standard-library-only package adds a control-plane baseline above the
preserved phase-4 occurrence transport.  It does not modify metric v3, does not
rerun its consumed holdout, and does not claim a motion-performance PASS.

Each epoch is assigned exactly one class and route before its events are
processed:

| Class | Route | Control intent |
| --- | --- | --- |
| `UNRELIABLE` | `SENSOR_FIXED_BYPASS` | immediate fail-safe |
| `LOW` | `SENSOR_FIXED_BYPASS` | preserve raw sensor meaning |
| `MID` | `MC_CORRECT_SPARSE` | enable occurrence-time warp, no tile claim |
| `HIGH` | `MC_WTB_TILE` | allow warp and world-tile path |

The class uses only a pose-derived fixed-point displacement proxy.  Accuracy
scores, future traffic, and holdout membership are not inputs.  Ordered
hysteresis thresholds and a minimum dwell suppress boundary chatter.  An
unreliable pose bypasses immediately; reenabling a motion path requires dwell.
`pose_reliable`/`pose_reliable_i` is an upstream fail-closed summary and must
cover missing or stale pose, invalid calibration/reference, and unsupported
geometry; the qualifier does not silently infer those facts.

Thresholds are caller-supplied configuration.  No production threshold is
validated or frozen by this package.  `rotation_displacement_proxy_q()` is a
software reference for `focal_px * relative_rotation_angle`; an RTL producer
may instead supply the already-quantized displacement sideband.

The RTL numeric defaults are synthetic smoke values only.  Its runtime
`profile_authorized_i` input must be asserted for a reviewed threshold profile;
otherwise every epoch immediately takes the `UNRELIABLE` bypass.  Keeping this
as an input preserves both the fail-safe and the real classifier through
elaboration instead of constant-folding one path away.

The accompanying RTL is only the synthesizable classifier/control primitive.
`tile_enable_o` does not claim that a complete tile datapath has been
implemented or physically validated.

`mc_wtb_epoch_route_interlock.sv` now provides a standalone drain/commit
primitive: it closes admission, preserves the active route until abstract
transport/adapter empty acknowledgments arrive, and fails closed on transport
or pose/profile faults.  The qualifier result must be staged before its route
request; pulsing classifier and interlock requests on the same clock would
otherwise capture the previous class.  A complete wrapper that binds every
sparse/tile/raw pipeline, buffer, credit, and output to those acknowledgments
remains an integration HOLD.

The two `genus_elaborate*.tcl` scripts are educational
45 nm library syntax/elaboration smokes only.  They do not perform mapping,
timing, area, power, placement, routing, or signoff.
