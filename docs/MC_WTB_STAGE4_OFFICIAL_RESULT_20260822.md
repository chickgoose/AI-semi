# MC-WTB Stage-4 official motion result

Date: 2026-08-22 KST  
Branch: `dev/mcwtb-freshness-stage4`  
Scored implementation commit: `3e8877f40ed848e83a33360633fdd782741ed207`

## Frozen evidence identity

- assay manifest SHA-256: `90b5286d42c8d85d88b14148c7150b8b9d1be252bc3465c90a4f80dc1f87d7f2`
- comparison contract SHA-256: `209ced8816834612439aa33c5589d1da0ed217cc065660218e683774a8172a67`
- official score-free seal SHA-256: `a9dc53799242e6bd92e3df3213ddc67f9f11d58325f50617d7f30a497f1d72ed`
- official result file SHA-256: `08ed4cc7a8a80616003fad7061e33ce2ee47fd4b7ac5fbd1e738f102c5d2da16`
- canonical result-body seal: `20f9af927039fecad7e5e79e7ae01cfd46501236b85be2bf10816c21fee13b67`
- assay authority SHA-256: `0310aea9e8e576f37df1cd1fd3cfcc479d806c4509ce8993a2c2a061ca3947cd`
- authoritative window-cycle inputs SHA-256: `bde13007743221ddfbecf135c7898836964b3eb16c604dcae79b64fd626a462e`
- execution: 24 windows x 4 arms = 96 `score_window` calls, four aggregate calls, and one complete-comparison validation
- accepted events: 8,914; accepted-event loss, source overrun, causality violation, and leakage violation: zero for every arm
- official scoring runtime: CPython 3.14.4, runtime identity SHA-256 `b18feabf0b5c1e374d1f9f320959c59975f7f8fc913ceb8687148f5bd05add5d`

The score-free seal was independently reopened before scoring: 24 windows,
96 arm leaves, 824 indexed files. The official scoring function was invoked
once. Resume and overwrite were disabled.

## Aggregate result

`R_all` is `1 - policy_loss / sensor_fixed_loss`, so a positive percentage is
a reduction in loss relative to the sensor-fixed reference.

| Arm | R_all | Positive windows | Enable rate | Quality-waste rate | Added p99 | FIFO peak / minimum zero-loss | Numeric / final disposition |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| causal CAV | 4.559204% | 21/24 | 100.000% | 36.762% | 0 cycles | 0 / 0 | `GO_NUMERIC` / `GO_TO_EPOCH_INTEGRATION` |
| delayed exact | 4.767410% | 21/24 | 100.000% | 37.032% | 201,648 cycles (1.310712 ms) | 1,024 / 1,424 | `STOP` / `STOP` |
| oracle resampled ground truth 1 kHz | 3.365728% | 22/24 | 99.944% | 38.702% | 0 cycles | 0 / 0 | `GO_NUMERIC` / `INTERFACE_VALUE_ONLY` |
| ZOH freshness | 0.001500% | 2/24 | 0.101% | 44.444% | 0 cycles | 0 / 0 | `HOLD` / `HOLD` |

The causal CAV arm captured 95.63% of the delayed-exact diagnostic upper
bound's aggregate benefit while adding no policy latency or delayed-event
FIFO. Its occurrence latency was p99 two cycles and its modeled incremental
state was 108,799 bits (106.25 Kibit), within the frozen 128-Kibit limit.

Delayed exact is not a deployable winner: its minimum zero-loss depth exceeded
the frozen 1,024-entry capacity by 400 entries, and its aggregate added p99
exceeded the 1-ms limit. ZOH freshness rejected 8,905 of 8,914 events, so it
did not provide useful coverage. The oracle arm is an interface-value control,
not an implementation candidate.

## Decision and evidence boundary

Stage-4 baseline decision: retain **causal CAV** as the only practical arm to
take into the next epoch-integration design review. This is a motion-quality
GO under the frozen development assay, not a direct RTL, PPA, P&R, holdout,
production, or novelty GO. The 108,799-bit state and bandwidth entries are
logical comparison accounting, not synthesized area or power.

No innovation candidate was added and no PPA/P&R was run in this stage. The
next stage must be reviewed with the team before implementation, as requested.

## Verification summary

- local related suites after the final sealer fix: 344 passed, four optional skips, zero failures
- server CPython 3.8.10 core Stage-4 suites: 132 passed, zero failures
- coherent `buffer_entries=1025` resealed mutation: rejected
- legitimate 1,024-entry boundary over 1,026 receipts: accepted

The full official result remains an external large artifact at
`/tmp/mcwtb-stage4-official-score-20260822-v1.json` in the scoring environment;
the path is ephemeral. Its identity is the result-file SHA-256 above.
