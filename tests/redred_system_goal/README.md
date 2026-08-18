# REDRED policy contract tests

The standard-library test suite verifies the committed policy and then mutates
each reviewed boundary. Negative cases cover:

- policy PASS falsely claiming evidence or release qualification;
- missing and unknown keys at nested levels;
- replacement of the implemented one-posedge `clk_i`, synchronous active-high
  `rst_i`, `link_enable_i`, nine-wire single-edge boundary with stale
  ref/sample-clock, active-low-reset, or forwarded-clock language;
- selected-interface promotion and cross-interface evidence borrowing;
- A2 calendar/sparse/no-debt semantics;
- A3 held-snapshot scope and bounded activation rules;
- A4 becoming a release or ranking candidate;
- leakage from the exact P6 10-bit/five-data-wire DDR transfer contract;
- forwarded-clock exception widening to data endpoints;
- P6 legality, pad PHY, and vectorless-power promotion;
- parallel fallback release promotion from its bounded digital/source evidence
  without independent mapped PNR/power evidence and final selection;
- trace, manifest, harness, and inherited-document digest mutation;
- `capacity22` being treated as independent from `full50`;
- weakened cycle equations, pending/reset behavior, error counters, event
  identity, or raw latency/throughput reporting;
- inherited 6.5 ns evidence promoted beyond its claim limit;
- core-only evidence entering endpoint ranking;
- removal or promotion of final CDC/RDC and PDK-I/O HOLDs;
- official data or coordinate rules becoming team-release dependencies; and
- absolute paths or relative paths containing mutable `tmp`/`latest`
  components.

Run from the repository root:

```bash
bash tests/redred_system_goal/run_all.sh
```

No EDA tool, network access, or third-party Python package is required. Test
success proves policy-verifier behavior only, not design or evidence PASS.
