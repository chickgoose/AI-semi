# A5 `41c425b` compatibility boundary

The separate behavioral link adapter in `tools/run_a5_schema_trace.py` accepts
the scheduler's K2 bundle atomically, queues both accepted events in order, and
then absorbs A5's independent `retire_ready[1:0]` stalls.  Link retirement never
changes the scheduler token cursor or row pointers.  The tool emits complete
`a5_k2_candidate_evidence_v1` output for any `a5_k2_vector_bundle_v1` input.

This is schema-compatible but is intentionally an **A5 semantic HOLD**.  A5
freezes the scalar wheel

`[0,1,1,1,1,1,2,2,2,2,2,3]`,

whereas this candidate's deliberately interleaved calendar is

`[1,2,0,1,2,3,1,2,1,2,1,2]`.

The mismatch is visible at the first persistent all-row cohort after reset:

```json
{
  "cycle": 2,
  "a5_expected_scalar_prefix": [0, 4],
  "a2_atomic_accepts": [4, 8],
  "adapter_outputs_before_edge": [
    {"lane": 0, "valid": false},
    {"lane": 1, "valid": false}
  ]
}
```

No output adapter can repair that accepted-winner mismatch without mutating
scheduler policy.  Accordingly, the adapter is useful for producing honest A5
traces and exercising lane stalls, but the candidate must not be reported as an
A5 oracle PASS.  Changing the calendar or sparse token-search rule to A5's
oracle would be a distinct candidate, not an interface adapter.

Against A5's exact adversarial bundle SHA
`efa202c4ebd91caff2573d9ccd7956b1a1e5584b999fc001fccb02e2a8388f75`,
the emitted seven-run document passed A5's schema/provenance validator.  The
evaluator then reported semantic `HOLD` with 185 hard failures: 179 in
`persistent_weight_120`, four in `stale_second_revalidation`, and two in
`future_arrival_divergence_witness`.  The sparse, same-row-pair, ordered-stall,
and reset runs passed.  The generated full trace is intentionally not canonical
GO evidence; regenerate it with:

```sh
python3 candidates/a2_batched_iwrr_k2/tools/run_a5_schema_trace.py \
  --vectors /tmp/a5-k2-adversarial-v1.json \
  --output /tmp/a2-k2-a5-evidence.json
```
