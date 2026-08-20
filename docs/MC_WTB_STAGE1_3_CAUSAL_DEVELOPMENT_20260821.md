# MC-WTB stages 1–3: causal development closure

Status: **stages 1–3 execution complete**. Stage 1 and the Stage-2 software
contract are scoped functional PASS; Stage 3 produced a **NO-GO/HOLD motion-
benefit result** under strictly causal pose availability. This is not a
complete-system, PPA, P&R, or innovation PASS. Stage 4 has not started.

## Outcome

The intended root improvement direction remains **advantage maximization**:
LOW-motion harm is contained by sensor-fixed bypass, while MID/HIGH should use
motion correction. However, strict causal evaluation exposed the actual
blocker: the supplied UZH pose is sampled about every 5 ms and is already
3.63–4.07 ms old at these query boundaries. Latest-at-or-before correction is
therefore piecewise constant over most events and provides practically no
improvement. The earlier multi-percent result depended on interpolation through
a future pose sample and was rejected rather than retained.

### Stage 1 — exact-once route boundary and drain/commit primitive

- Each epoch receives one frozen route: sensor-fixed bypass, corrected sparse,
  or corrected world-tile member.
- The software router requires the complete expected ordered event-ID ledger.
  Missing, duplicate, reordered, cross-epoch, or overlapping input fails closed.
- Event ID, occurrence timestamp, sensor coordinate, polarity, and epoch
  reference timestamp are bound across the warp provider.
- Out-of-FOV and invalid geometry produce explicit raw escape/bypass
  dispositions; neither is filtered.
- The standalone RTL interlock blocks admission during a change, retains the
  acceptance-time route until both abstract drain inputs are empty, commits the
  new epoch atomically, automatically falls back after a pose/profile fault,
  and enters an error hold after a transport-health fault.

The RTL primitive passed server Xcelium 23.09 simulation with marker
`MC_WTB_EPOCH_ROUTE_INTERLOCK_RTL_PASS` and Genus 23.14 educational-45-nm
elaboration/unresolved-reference checking with marker
`MC_WTB_EPOCH_ROUTE_INTERLOCK_GENUS_ELABORATION_PASS`.

Scope limit: the interlock is not yet wrapped around complete sparse/tile/raw
datapaths. Its `transport_empty_i` and `route_adapters_empty_i` inputs are a
contract boundary, not proof that every future pipeline/skid/merge buffer has
been implemented and included.

### Stage 2 — causal rolling world-reference model

- Two fixed-capacity banks are separated by polarity.
- Capacity is 256 prior observations per polarity; maximum age is 2 ms.
- A complete equal-timestamp cluster is scored before any member is inserted.
- Splitting one equal-timestamp cluster across calls is rejected, preventing a
  call-boundary leakage path.
- Every selected reference records its event ID, timestamp, and age.
- Every event world ray uses only the latest pose sample at or before that
  event; no future interpolation is permitted.
- The route decision uses only the two latest supplied pose samples already
  available at query start and normalizes their observed rotation to a recent
  1 ms rate proxy.

This model is a rolling online reference, not the old frozen 0.25 ms anchor.
Later query events may causally use strictly earlier query events. It remains a
floating-point software model: ray quantization, nearest-neighbor hardware,
memory ports, and bounded cluster ingestion are Stage-4/full-datapath work.

### Stage 3 — development-only UZH observation

Source: official UZH DAVIS `shapes_rotation`, with source hashes checked before
event selection. Twenty-four deterministic 1 ms queries use a preceding 1 ms
warm-up. The consumed interval `[43.320750000, 43.322000000)` is explicitly
blacklisted and none of the registered windows overlap it.

The exploratory, score-blind tier cutpoints are 0.35 and 1.40 pixels per prior
1 ms. They are not production thresholds and cannot authorize a new holdout.

| Tier | Windows | Always-corrected mean reduction | Range |
| --- | ---: | ---: | ---: |
| LOW | 9 | 0.0154% | -0.0398% to 0.1227% |
| MID | 10 | approximately 0% | approximately 0% |
| HIGH | 5 | approximately 0% | approximately 0% |

Under an **ideal tier rule** that assigns LOW to sensor-fixed and MID/HIGH to
the corrected arm, the equal-window mean reduction is only
`3.115e-10` as a fraction, or about `0.000000031%`: practically zero. Even
always enabling causal correction across all windows averages only 0.00577%.
Thus Stage 3 does not support a motion-benefit claim. The result is also not the
implemented qualifier's hysteresis/dwell result, event-weighted performance,
or a bandwidth/energy/system benefit.

The complete per-window registry, event-ID hashes, arm costs, provenance, and
claim limits are stored in
`benchmarks/redred_mc_wtb_causal_reference/stage3_development_result.json`.

## Verification boundary

Passed:

- local Python unit tests: 14/14 for routing/reference/registry;
- server Python 3.8.10: the same 14/14 tests;
- server Xcelium 23.09: standalone epoch interlock RTL simulation;
- server Genus 23.14 with the 45 nm slow library: syntax/elaboration and no
  unresolved references;
- deterministic development rerun on the hash-pinned official source, with all
  result-determining Python dependencies hashed in the result.

The full 509 MB event source was read for its hash and monotonic extraction
pass, so raw bytes in the blacklisted interval were necessarily scanned. No
event from that interval was selected, transformed into an arm, or scored.

Still HOLD:

- actual qualifier hysteresis/dwell execution over a newly frozen assay;
- complete sparse warp, world-tile, raw escape, ordered merge, and link paths;
- fixed-point/RTL causal-reference datapath and complete memory/port cost;
- mapped synthesis, timing, area, vectorless/activity power, P&R, and post-route
  timing for the complete boundary;
- higher-rate causal supplied pose, explicitly charged event buffering until a
  later pose arrives, or prediction/extrapolation to overcome pose staleness;
- confidence bounds, a new untouched holdout, other scenes/datasets, and any
  general motion-benefit or innovation claim.

Stage 4 must begin only after joint review. The immediate decision is whether
to obtain a higher-rate supplied motion stream, accept and charge roughly one
pose-period of buffering/latency, or promote the saved causal prediction idea.
No predictor was implemented in stages 1–3.
