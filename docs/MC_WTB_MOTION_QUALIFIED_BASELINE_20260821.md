# MC-WTB motion-qualified baseline reinforcement

This post-unblind baseline addition is separate from the consumed metric-v3
assay.  It does not alter or reinterpret the preserved failure receipt and it
must not access the 43.321 s holdout again.

## Implemented control contract

One pose-derived displacement value is supplied per epoch in fixed-point pixel
units.  The qualifier assigns the complete epoch to one of four modes:

- `UNRELIABLE`: immediate sensor-fixed bypass;
- `LOW`: sensor-fixed bypass;
- `MID`: occurrence-time motion warp with sparse output;
- `HIGH`: occurrence-time motion warp with the world-tile path enabled.

Hysteresis and a minimum dwell apply when enabling or changing reliable motion
classes.  A pose fault bypasses immediately.  A mode transition means epoch
commit/drain, never event discard.  Thresholds remain explicit configuration;
the consumed development/holdout pair is not authority for production values.

The Python reference and synthesizable SystemVerilog classifier are implemented
under `benchmarks/redred_mc_wtb_motion_qualification/` and
`rtl/candidates/mc_wtb_motion_qualification/`.  This is a control-plane result,
not proof of a complete sparse-warp or world-tile RTL datapath, PPA, or motion
benefit.

The qualifier is harm containment, not a motion-quality improvement by itself.
LOW bypass contributes approximately zero motion benefit; aggregate benefit is
possible only if independently proven HIGH-regime gain exceeds classifier,
transition, and datapath cost.  Future evaluation must report LOW
non-inferiority, HIGH benefit, regime prevalence, and charged overall PPA
separately.

The later stage-1–3 branch adds a standalone drain/commit interlock and an
exact-once software route ledger.  They close the primitive/control contract,
but a complete wrapper over every sparse/tile/raw pipeline and buffer remains
HOLD.  See `docs/MC_WTB_STAGE1_3_CAUSAL_DEVELOPMENT_20260821.md` for the newer,
narrowly scoped evidence boundary.

## Relationship to Fovea/A2

The phase-4 occurrence transport intentionally reuses the A2 scheduler and its
Fovea-derived spatial service policy.  That reuse is not an MC-WTB novelty
claim.  Fovea/A2 chooses which sensor source is serviced; this qualifier chooses
the coordinate/representation path for a complete accepted epoch.  The MC-WTB
research contribution remains the occurrence-time sensor-to-world transform
and world representation, not generic selective processing.

## Preserved evidence boundary

- Existing phase-4 occurrence RTL and receipts are unchanged.
- Existing metric-v3 runtime, lock, and failed holdout receipt are unchanged.
- Unit tests use synthetic thresholds and synthetic rotations only.
- Server Python 3.8 unit execution, Xcelium 23.09 RTL simulation, and Genus
  23.14 educational-45-nm library elaboration passed for the qualifier.
- Genus stopped after elaboration/unresolved-reference checking; no mapped
  timing, area, power, place, or route evidence was produced.
- A future benefit claim requires a new assay ID and untouched cohorts selected
  without consulting arm scores.

## Stored innovation candidate: predictive representation controller

Status: **IDEA SAVED — IMPLEMENTATION HOLD**.

The future candidate extends the non-predictive qualifier with a causal
next-epoch estimate over motion, event density, tile compressibility, link
pressure, and energy cost.  It would select raw, sparse-warp, or world-tile
representation at a predeclared break-even point while preserving the original
event identity, occurrence time, sensor coordinate, polarity, pose binding,
and an exact escape disposition.

The novelty claim is not “motion compensation” or “large-motion gating”; both
are established ideas.  The candidate contribution to investigate is a
lossless AER hardware controller that jointly selects representation using
motion and transport/PPA utility, with exact-once epoch handoff even after a
misprediction.

GO requires a frozen causal feature set, a predictor-independent correctness
fallback, benefit on untouched workloads, and charged classifier/buffer/link
PPA.  STOP applies to score-aware window selection, silent drops, free shadow
storage, post-hoc thresholds, or a design whose predictor error changes event
meaning.  No predictor is implemented in this baseline change.
