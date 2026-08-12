# A5/A8 cross-validation

Pinned contracts:

- owner scheduler `632e68d247ec36a35b62dbd5c100b0a23d47cf7b`;
- A5 diagnostic evaluator `41c425bec79aca6c84f5856ca7dee2a4865a6447`;
- A8 scheduler falsifier `1248a19e1f3bea4c519645460cb810b19fab4c5d`.

## A8 result

A8 agrees with the candidate's pinned Ganghee Fovea equations and atomic
boundary. Its suite passed 22 tests and ten diagnostic mutations. The candidate
adapter matched all seven A8 scalar-prefix owner cases; an additional local
comparison matched grants and complete post-prefix policy state for all 65,536
initial masks.

## A5 latency correction and HOLD

A5 `41c425b` is diagnostic only because its per-lane retirement schema is not
the common atomic scheduler boundary. The follow-up exporter preserves owner
registered-offer latency and charges a separate synthesizable ordered link
instead of inventing a free behavioral queue. The link adds 10 state bits;
generic Yosys/ABC reports 96 mapped cells and depth 14. These are structural
proxies, not physical PPA.

The latency-faithful artifact remains explicit A5 `HOLD`. It accepts and
retires 180 events in `persistent_weight_120`; its first 120 committed row
counts remain exactly `[10,50,50,10]`. Sparse, same-row distinct-pair, and
ordered-stall runs pass their hard gates. The reset run cannot meet the old
vector's final-drain deadline once both the real scheduler register and charged
link are retained.

The evaluator reports 186 hard failures: 185 winner-oracle mismatches plus the
v1 reset vector's insufficient final drain allowance.

| Run | Grade | Failures |
| --- | --- | ---: |
| persistent_weight_120 | FAIL | 88 prefix + 88 primary |
| stale_second_revalidation | FAIL | 3 prefix + 3 primary |
| future_arrival_divergence_witness | FAIL | 1 prefix + 1 primary |
| reset_abort_no_phantom | PRIMARY_ONLY/HOLD | 1 prefix + final drain missing |

A5 expects `[0,4]` while canonical Ganghee Fovea produces `[4,11]` under full
reset-state demand. Rewriting owner policy for that oracle would invalidate the
A8 match, so policy remains unchanged.

## Actual first-divergence negative test

The original `29a5003` exporter committed a freshly calculated offer on the
same indexed edge as occurrence admission. That removed the owner RTL output
register and could change which generation survives one-entry source
admission. The regression materializes that exact legacy blob and exact A5
bundle `efa202c4ebd91caff2573d9ccd7956b1a1e5584b999fc001fccb02e2a8388f75`:

```text
persistent_weight_120 cycle 2
legacy 29a5003 accepts: [4,11]
registered owner RTL:  []
registered first commit, cycle 3: [4,11]
```

The test also executes owner RTL directly and requires
`A3_K2_A5_OWNER_REGISTERED_LATENCY_PASS`; it cannot pass by comparing two
Python paths. Owner scheduler RTL SHA-256 is
`bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9`;
owner `oracle.py` is
`c2c793a284cb6d58507de6e2d62c25ce54d7120bbd6f9ee642bd210528f0ff9c`;
A5 `k2_oracle.py` is
`193a3ac629b4e27418b29af58331b9261922002a74364a892c004340957cc6f8`.
