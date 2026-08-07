# Eight-track clean-slate AER innovation program

Date: 2026-08-07

This program runs eight independent candidate branches against the frozen
N=16, 46-trace candidate-neutral benchmark.  It is an exploration stage, not a
request to merge eight mechanisms into one design.  Each branch must be useful
and falsifiable on its own.

## Track boundaries

| Agent | Primary mechanism | Contribution that must remain unique |
| --- | --- | --- |
| A2 | adaptive sparse bypass plus finite burst reservoir | change the data path with load derivative and hysteresis |
| A3 | bio-inspired homeostatic inhibition | fixed-point excitation, leak, inhibition, and feedback-stabilized service |
| A4 | distributed quadtree spatial fabric | remove the flat arbiter through coordinate hierarchy, without ROW/COL serialization |
| A5 | confidence-based speculative pre-grant | precompute selection while a deterministic fallback owns correctness |
| A6 | exact lossless AER codec | improve event/pin-cycle and link toggles with encoder and decoder both charged |
| A7 | parallel-prefix event compactor | produce K exact retire indices from shared scan state rather than replicated selection |
| A8 | O(1) age calendar wheel | replace global per-source aging updates with epoch buckets |
| A9 | distributed empty-slot/token fabric | remove the central grant vector through local slot conservation and transport |

Using buffering, pipelining, or more than one retire lane as a supporting
implementation detail does not make two tracks equal.  A result is considered
overlapping when its claimed benefit depends primarily on another track's
unique mechanism.  In that case the head either removes the borrowed mechanism
or reassigns the branch before comparison.

## Frozen boundary

Every branch consumes the exact traces and normalized event meaning at commit
`ad96895`.  These common files are read-only during candidate exploration:

- `benchmarks/clean_slate_aer/manifest.neutrality-n16.json`
- `benchmarks/clean_slate_aer/fixtures/neutrality_n16_golden.json`
- `tb/clean/aer_clean_tb.sv`
- `scripts/run_clean_benchmark.sh`

A candidate may add its own replacement cell, stateless binding, filelist, and
runner.  Candidate-only capability profiles stay with the candidate.  An
adapter may not add storage, retry, arbitration, compression, or flow-control
functionality that is absent from the candidate.  A milestone is rejected when
the branch changes a frozen file instead of using a candidate-specific path.

## Stage gates

1. Research gate: primary sources, a concrete mechanism, state equations or a
   protocol, non-overlap, PPA risk, and predeclared rejection criteria.
2. Unit gate: synthesizable core primitive plus self-checking directed and
   adversarial tests.
3. Candidate gate: complete normalized candidate, loss/duplicate/ordering
   invariants, reset behavior, and a stateless binding.
4. Screening gate: all applicable frozen traces, fixed-window throughput,
   overrun, latency tail, demand-normalized fairness, phase recovery, and timing
   fidelity.  Unsupported capability is reported, never silently emulated.
5. Physical shortlist gate: only survivors receive the same Genus and Innovus
   flow.  The shared server flow is not run concurrently by exploration agents.

## Head comparison rules

- Any phantom, duplicate, corruption, accepted-event loss, source-local reorder,
  or drain failure rejects the candidate regardless of speed.
- A workload-specific win is a valid architectural strength.  Benchmark bias
  means omitted bottlenecks, changed traffic, unequal measurement boundaries,
  or free adapter functionality.
- Retire lane count, source count, pin count, and codec endpoint cost are always
  explicit.  Multi-lane designs are compared at equal K as well as at their
  maximum demonstrated K.
- Predictor accuracy, compression ratio, token utilization, or internal neural
  state is supporting evidence.  The outcome metrics remain event correctness,
  event/cycle, latency, overrun, fairness, energy/event, area, frequency, and
  event/pin-cycle.
- Results are kept as a per-bottleneck Pareto table.  No undocumented weighted
  score is introduced before the official contest weights are known.
- A mechanism that fails its own predeclared threshold is documented and
  stopped.  Its agent is then reassigned to falsification, simplification, or a
  clearly non-overlapping follow-up rather than left idle.

## Integration policy

No branch is merged merely because its RTL works.  After all eight screening
reports, the head selects independent Pareto winners.  At most two compatible
mechanisms are combined in a new integration branch, and that combined design
must repeat the entire correctness and 46-trace screening gates.  Negative
results remain valuable evidence and are preserved on their original branch.
