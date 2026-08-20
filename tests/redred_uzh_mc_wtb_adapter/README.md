# UZH pose-join to geometry adapter acceptance tests

These tests independently define the smallest public adapter contract between
the committed UZH source-preserving pose join and the committed orientation-only
geometry core. They are test-owned: expected rotations, SLERP values, OpenCV
radtan projection, and OOF coordinates are produced by analytical code in the
test module, not by production helpers.

The expected production package is
`benchmarks.redred_uzh_mc_wtb_adapter` with this API:

```python
adapt(join_result_dir: Path, join_spec_path: Path, result_dir: Path) -> dict
inspect(result_dir: Path, join_result_dir: Path, join_spec_path: Path) -> dict
AdapterFailure
```

`adapt` must fail closed when the completed join package is tampered or does
not bind to the supplied spec. It publishes a new result directory without
overwriting an existing path. The exact published inventory is:

```text
events_mc_wtb_adapter.jsonl
receipt.json
COMPLETE.json
```

The JSONL contains one header followed by exactly one record per joined source
event. Every record has exactly one recognized `disposition`:

- `WORLD_REFERENCE_EVENT`: a valid in-FOV orientation-only warp.
- `RAW_ESCAPE_GEOMETRIC_OOF`: valid geometry outside the raw reference image
  or behind it.
- `RAW_BYPASS_INVALID_GEOMETRY`: calibration/inversion/non-finite geometry,
  kept separate from geometric OOF.

Every disposition retains the complete occurrence under `source_event`:
dataset and join indices, timestamp and lexeme, raw `x_sensor`, `y_sensor`,
and polarity, plus a canonical identity SHA-256. Raw escape identity is thus
preserved without inventing a packet/payload schema. `geometry` carries its
status, continuous decimal-string coordinates, optional rounded pixel, ray Z,
retained relative translation, and
`translation_applied_to_pixel_warp=false`.

The fixed reference time is the joined selection start. Reference and event
poses use offline shortest-arc SLERP from each event's exact source bracket.
The adapter preserves the bracket indices/timestamps/fraction and does not
relabel future lookahead as causal zero-age hardware. Tests independently
derive `R_Ct_C0 = R_WCt^T R_WC0` and transpose `R_C0_Ct`, then use an
analytic-Jacobian radtan inverse to check the emitted warp. Redundant matrices
need not be serialized.

The native nested event ABI and filename above are accepted. The semantic
status boundary is nevertheless exact:

```text
status = PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED
promotion_status = HOLD_MC_WTB_REAL_DATA_BENEFIT
```

Both the artifact header and receipt claim scope must bind the offline nature
of the pose interpolation: `offline_future_bracket_slerp=true`,
`future_pose_lookahead_required=true`, `causal_hardware_claimed=false`, and
`clock_alignment_validated=false`. A public inspection without both the
source pose-join directory and its exact spec must reject and cannot return the
scoped PASS status.

The receipt contains exact source/output identities, reference and convention
IDs, one exact artifact identity, and these conservation counters:

```text
input_joined_events
output_dispositions
world_reference_events
raw_escape_geometric_oof
raw_bypass_invalid_geometry
dropped_events
duplicate_events
reordered_events
```

The accepted equations are:

```text
input_joined_events == output_dispositions
output_dispositions == world_reference_events + raw_escape_geometric_oof + raw_bypass_invalid_geometry
dropped_events == duplicate_events == reordered_events == 0
```

The adapter is a deterministic software geometry artifact. Its receipt must
keep codec, wire, transport replay, MC-WTB benefit, RTL, and PPA claims false
or HOLD. Translation is retained but not applied without depth/plane data.

Run the complete native plus official independent gate with all four source
bindings set. `run_all.sh` rejects a partial invocation instead of reporting a
green exit with skipped official cases. It also clears external production-root
and `PYTHONPATH` overrides so the integrated checkout is the implementation
under test:

```bash
REDRED_UZH_POSE_JOIN_PACKAGE=/tmp/uzh-posejoin-c6a \
REDRED_RUN_UZH_ADAPTER_OFFICIAL=1 \
REDRED_UZH_JOINED_ROOT=/tmp/uzh-posejoin-c6a \
REDRED_UZH_JOIN_SPEC=benchmarks/redred_uzh_shapes_pose_join/join_spec.json \
  bash tests/redred_uzh_mc_wtb_adapter/run_all.sh
```

Run only the always-on independent synthetic suite with:

```bash
bash tests/redred_uzh_mc_wtb_adapter/run_independent.sh
```

To run tests against production in another worktree:

```bash
REDRED_ADAPTER_PRODUCTION_ROOT=/tmp/redred-mcwtb-adapter-impl \
  bash tests/redred_uzh_mc_wtb_adapter/run_independent.sh
```

The authoritative generated input for the opt-in test is
`/tmp/uzh-posejoin-c6a`, whose `receipt.json` SHA-256 is
`85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87`.
The test never downloads data:

```bash
REDRED_RUN_UZH_ADAPTER_OFFICIAL=1 \
REDRED_UZH_JOINED_ROOT=/tmp/uzh-posejoin-c6a \
REDRED_UZH_JOIN_SPEC=/path/to/join_spec.json \
REDRED_ADAPTER_PRODUCTION_ROOT=/tmp/redred-mcwtb-adapter-impl \
  bash tests/redred_uzh_mc_wtb_adapter/run_independent.sh
```

Its exact expected partition is 1,094 world-reference events, six geometric
raw escapes with the pinned dataset IDs/continuous coordinates, and zero
invalid bypasses or drops.
