# MC-WTB Stage 3 implementation red-team — 2026-08-22

## Verdict

**`STAGE3_IMPLEMENTATION_REDTEAM_FAIL — 108 EVALUATION FORBIDDEN`**

The integrated tree at HEAD
`ed51e04bd88b58339d39c46ccca0e92b50972a0e` does not yet provide an
admissible execution path from the frozen neutral inputs through RG3, DSPB, or
SO3-PLL, through the independent synthetic oracle, and into the sealed
screen108 evaluator. The native unit tests are useful but do not close that
boundary. In particular, the current screen accepts unattested supplied rays,
cannot prove window reset or feedback effective-edge semantics, maps the
candidate decision edge onto the old occurrence edge, and cannot even encode
the frozen RG3 and SO3-PLL candidate IDs.

This is a source-only, synthetic-only review. No real dataset, external data,
candidate result, RTL, or PPA evidence was opened or executed. No source or
candidate implementation was changed.

## Authority and reviewed scope

The controlling design authority is the frozen Stage12 contract and candidate
architecture at Checkpoint A:

- baseline authority commit
  `2d7d3d128da2436b257ea1ce759bf8cb6c0b2466`;
- Checkpoint A content commit
  `4add865a1a3e46fbeb11bcfa49ffa48f0821712e`;
- source split plan SHA-256
  `654582131fe0d44ea047268163e928d53fd7120493292eda957b8c3180e14a6e`;
- Stage12 contract SHA-256
  `3b151649404b39557acc57b665d19e28e787368b46d0797ac7d37fad5d60409f`;
- Stage12 architecture SHA-256
  `86be810c63a3e4817af9611c24b6d02283f763c0616e02c12628a92cf4de1178`.

The implementation review covered:

- `benchmarks/redred_mc_wtb_predictor_stage3/framework.py`;
- `benchmarks/redred_mc_wtb_predictor_stage3/rg3.py`;
- `benchmarks/redred_mc_wtb_predictor_stage3/dspb.py`;
- `benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py`;
- `benchmarks/redred_mc_wtb_predictor_stage3/screen108.py` and its result
  schema;
- the independent oracle protocol, harness, scenarios, and tests under
  `tests/redred_mc_wtb_predictor_stage3_oracle/`;
- the common-framework, candidate-native, and screen108 synthetic tests; and
- the frozen pose recovery, cycle mapping, NEW108 adapter, and baseline
  evaluator seams used by Stage 3.

Severity means:

- **P0:** the current implementation can accept an unauthenticated,
  noncausal, identity-ambiguous, or incorrectly accounted 108 artifact, or no
  conforming actual candidate can traverse the claimed gate. It blocks
  `SYNTHETIC_PASS` and all 108 use.
- **P1:** a material contract/test/evidence gap that must be closed before an
  admissible Stage 3 result, but is dominated by the P0 stop conditions above.

## P0 findings

### P0-1 — No actual candidate traverses the common framework or independent oracle

The common framework requires a `CandidateModel` implementation
(`framework.py:395-429`, `786-787`). None of `RG3`, `DSPBModel`, or
`SO3PLLModel` subclasses or adapts that interface. RG3 exposes
`recover_rg3_cav()` (`rg3.py:352-378`); DSPB and PLL own separate mutable APIs
(`dspb.py:592-608`, `so3_pll.py:384-428`). There is no production adapter
between any of them and `run_candidate_neutral_predictor()`.

The independent oracle explicitly says only that a **future** wrapper may
implement `CandidateAdapter` (`tests/.../oracle/README.md:14-18`). Every
`OracleHarness.run()` invocation in `test_oracle.py` uses a synthetic test-local
adapter: `FallbackAdapter`, a numeric/nonfinite derivative, or a deliberately
bad citation/mutation adapter (`test_oracle.py:55-169`, `171-173`, `234`,
`254`, `274`, `290`, `307`, `320`, `329`, `343`, `357`, `374`, `383-390`,
`398-400`). It never imports or executes RG3, DSPB, or SO3-PLL.

Consequences:

- native candidate tests and oracle tests can all pass while their integration
  conventions disagree;
- no actual candidate has been tested for the oracle's immutable snapshot,
  feedback, same-edge, citation, or fallback rules;
- no tested path produces the screen108 candidate-output schema; and
- `SYNTHETIC_PASS` cannot be issued to any current candidate.

This is not merely missing coverage. It is a disconnected qualification graph.
The required pre-108 evidence is an actual candidate adapter for each frozen
candidate identity, exercised through the independent harness and all required
mutants, followed by the same exact producer used to make its sealed screen
receipt.

### P0-2 — screen108 accepts supplied geometry without executing or reproducing the candidate

`screen108.py` states and implements that it never executes a candidate
(`1-7`, `808-846`). It hashes the files named by the caller as “executable” and
“config” (`842-843`) but has no evidence that those bytes generated the
candidate output. `seal_candidate_output()` is a public self-sealing helper
that hashes caller-supplied event rows (`274-311`). An output producer can
therefore bind any unrelated files after generating arbitrary rows.

For candidate-use events, the screen checks only that `world_ray` is unit
length and that cited pose IDs are members of the baseline occurrence snapshot
(`393-423`). It does not receive a candidate quaternion, replay candidate
state, recompute the candidate forecast, rotate the sensor ray independently,
or compare the supplied ray with RG3/DSPB/PLL output. `_window_losses()` feeds
the supplied `world_ray` directly into the causal reference bank
(`456-472`). A causally eligible pose citation does not prove that the ray was
derived from that pose or from the named candidate.

The result honestly records `candidate_executed_by_runner: false`
(`763-770`), and the screen test positively asserts nonexecution
(`test_screen108.py:340-372`). Consequently the status string
`SCREEN108_MEASURED_PROMOTION_NOT_AUTHORIZED` can describe metrics over an
authenticated event ledger but not measured output of the claimed candidate.
No favorable metric from this path is admissible as RG3/DSPB/PLL evidence.

Required closure: an independently reproducible producer receipt must bind the
exact candidate source/executable/config, neutral input, reset generation,
state/feedback chain, candidate quaternion, ray derivation, ordered event
decisions, and output digest. The independent verifier must rerun or otherwise
cryptographically bind that exact execution; a caller-created JSON seal is not
sufficient.

### P0-3 — The screen's decision edge is the occurrence edge, contrary to the frozen strict edge

Stage12 requires a distinct immutable decision edge `d` and a separately
mapped event record satisfying `occurrence_cycle < d`. The current candidate
output has `decision_cycle` but no `occurrence_cycle` field
(`screen108.py:61-71`). Validation then requires:

```text
candidate decision_cycle == baseline_decision.occurrence_cycle
```

at `screen108.py:380-382`. The NEW108 adapter and baseline cycle model use that
baseline field as the occurrence edge. Thus the accepted representation either
makes `d == occurrence_cycle`, directly violating the strict inequality, or
misnames a different edge without any receipt that proves the mapping. There
is no third field for event visibility/publication.

This also means same-edge pose checks are evaluated against the wrong or
unproven edge (`screen108.py:401-413`). The candidate-native APIs accept a
caller-provided event/decision cycle, but nothing binds that value to the
frozen raw-time-to-cycle adapter. Native same-edge tests therefore cannot close
the screen seam.

Required closure: freeze one adapter mapping that records raw sensor time,
occurrence cycle, immutable decision edge, pose commit edge, and rounding rule;
require `occurrence_cycle < decision_edge`; and use the same fields in the
candidate execution, independent oracle, candidate receipt, and screen
verification.

### P0-4 — Window reset, state content, and feedback publication are unverifiable

The screen receipt stores only a scalar `predictor_state_version`; it omits the
state hash, reset/window generation, parent state, effective edge, publication
edge, transition reason, and DSPB/PLL immutable forecast binding
(`screen108.py:61-71`). Validation only requires nondecreasing version numbers
inside a window and equality within an equal-timestamp cluster
(`369-391`). The verifier local variables reset between windows, but it neither
requires an initial version nor proves that the producer reset model content at
the 50 ms pre-roll start. A producer may carry trained DSPB/PLL state across
windows, assign monotonically plausible local version numbers, and pass.

The common framework initializes once per function call (`framework.py:802-811`)
but has no window/reset generation in its input or result. Its “behaviorally
stateless” requirement is prose (`395-401`), not enforced: the common test's
`RecordingModel` mutates `self.predict_calls` and `self.commit_calls`
(`test_framework.py:29-34`, `46-60`) and passes. Candidate state outside the
hashed payload can therefore influence later events without appearing in the
state chain.

Candidate reset support is inconsistent: PLL has `reset()`
(`so3_pll.py:421-428`), RG3 is call-stateless, and DSPB has neither the common
state-payload interface nor an explicit reset. Re-instantiation could be the
DSPB reset rule, but no integrated producer or receipt proves that it occurs
exactly at each pre-roll start.

This is a direct adaptive leakage path and invalidates stateful window metrics.
Required closure is a sealed reset-generation receipt per window, initial
state/config hash, every state parent/effective edge, every feedback forecast
source/target/publication binding, and replay proving that window permutation
does not change canonically restored results.

### P0-5 — RG3 and SO3-PLL identities cannot pass the screen schema

The screen candidate identifier permits only `[A-Za-z0-9_.-]`
(`screen108.py:59-60`, `129-132`). The frozen implementation identities are:

- RG3: `redred.mc_wtb_predictor_stage3.rg3_cav/.../v1`, containing `/`
  (`rg3.py:93-104`);
- DSPB: `DSPB-A4-E0E1E2E3-V1`, accepted (`dspb.py:220-260`); and
- SO3-PLL: a parameter-complete colon/comma-separated string, containing `:`
  and `,` (`so3_pll.py:243-270`).

A direct source probe against the actual `_IDENTIFIER` regex returned
`False`, `True`, `False` respectively. Therefore two of the three actual
candidate IDs cannot be sealed or evaluated. Renaming them only in the output
would sever configuration identity; changing the IDs is a material candidate
change requiring a new freeze.

Required closure: one canonical candidate-ID encoding, fixed before results,
that round-trips byte-identically through candidate code, oracle, producer,
screen validator, result schema, and registry.

### P0-6 — Source/config identity is self-referential and not tied to executed code

`_verify_freeze()` verifies the three paths against hashes read from the same
mutable receipt (`screen108.py:585-616`). It does not pin the receipt hash, the
three expected artifact hashes, `content_commit`, or
`implementation_baseline_commit` to compiled/external authority values. A
coordinated edit of a frozen document and its receipt entry therefore satisfies
this function. There is also no dirty-tree rejection.

Candidate identity is weaker still:

- the caller chooses any candidate executable and config paths;
- the runner hashes their bytes but never executes or parses them
  (`screen108.py:839-846`);
- the top-level candidate ID is not derived from either hash;
- event `model_id` is not required to equal the top-level candidate ID
  (`392-430`); and
- no screen field binds the common framework source, candidate module source,
  oracle adapter, fallback implementation, or output producer.

For DSPB, the internal `DSPBConfig.sha256` is canonical and frozen, but the
screen's `candidate_config_sha256` is merely the raw hash of an arbitrary file;
there is no equality bridge. RG3 has policy constants and an ID but no screen
config serialization. PLL allows material configurations with distinct IDs,
but the current screen rejects those IDs and still does not parse the config.

Required closure: pin authority values independently; bind Git/content or
runtime-tree hashes and a canonical parsed config to the exact candidate ID;
reject dirty or unindexed candidate/runtime bytes; and bind the producer and
oracle adapter used in the same execution receipt.

### P0-7 — Candidate-output fallback semantics collapse CAV, ZOH, and bypass

Stage12 requires the exact chain candidate -> current CAV -> fresh ZOH at age
at most 1 ms -> sensor-fixed bypass, with every actual route and reason counted.
The common framework represents all four routes, but its `current_cav` hook is
arbitrary and is checked only for type, unit quaternion, and visible pose IDs
(`framework.py:670-680`, `695-756`); it is never independently recomputed as
the frozen CAV.

The screen representation is more severe. Whenever `candidate_used` is false,
it requires `model_id == "CURRENT_CAV"`, `world_ray == null`, and any nonempty
caller-supplied fallback string (`screen108.py:424-430`). It has no fallback
route field and does not compare the supplied reason with the baseline
decision. `_window_losses()` silently substitutes `A.policy_loss`
(`483-497`). A true fresh-ZOH or sensor-fixed bypass is therefore reported as
“CURRENT_CAV,” and arbitrary reasons are accepted. Candidate-use/fallback
counts remain numerically partitioned, but exact route availability, attempt
eligibility, reason counts, and recovery behavior are not auditable.

This also masks a candidate boundary mismatch. DSPB selects E1/E2/E3 and emits
candidate geometry without first requiring that current CAV is valid
(`dspb.py:1138-1174`); it calls the baseline only after candidate failure
(`1175-1186`). The screen separately rejects candidate use when baseline A is
invalid (`screen108.py:416-423`). With no integrated adapter, it is undefined
whether DSPB output is honored or rewritten at this seam.

Required closure: the producer must record `candidate_attempted`, candidate
failure, and the actual fallback route (`CURRENT_CAV`, `FRESH_ZOH`, or
`SENSOR_FIXED`) with exact used poses/output/reason. The verifier must
independently recompute the full chain and reject any mismatch.

## P1 findings

### P1-1 — Native APIs bypass the framework's identity-free input projection

The common framework deliberately strips event and pose identity before a
candidate call. DSPB instead receives an `EventRecord` containing `event_id`
and `SuppliedPose` containing `pose_id` (`dspb.py:263-324`) and owns receipt and
state history using those identities. PLL receives `pose_id` directly
(`so3_pll.py:535-543`). Current prediction arithmetic does not visibly branch
on the DSPB event ID, but the public candidate boundary permits it and no
independent adapter proves stripping. The implementation should not receive a
forbidden field merely because current code happens not to use it for one
formula.

### P1-2 — Signed pre-roll cycle semantics differ across components

The frozen pose-recovery/cycle model explicitly supports signed pose commit
cycles before a window origin. DSPB and PLL accept signed pose commit cycles.
The common framework's `_nonnegative_int` is used for event occurrence,
decision, pose commit, state effective, and receipt cycles
(`framework.py:36-39`, `97-105`, `126-130`, `333-337`). The independent oracle
also initializes state at effective cycle zero and restricts synthetic input to
at most one pose commit per cycle (`oracle.py:249-267`, `304-307`). No adapter
test establishes the exact reset-boundary treatment of an authoritative pose
whose commit is before the pre-roll origin. This can produce different initial
CAV/history availability between native candidates, framework, oracle, and
screen.

### P1-3 — Positive-window counting has an extra undocumented epsilon

The contract asks for positive-window counts. `screen108.py` defines
`POSITIVE_WINDOW_THRESHOLD = 1.0e-6` (`48`) and counts a window positive only
when its effect exceeds that epsilon (`517-518`). The promotion gates use
strict `> 0` (`680-684`). Thus a window with a small positive effect is positive
for the gate's sign interpretation but negative for the reported count. This
is metric-definition drift unless the epsilon is separately frozen in the
authority.

### P1-4 — CNCP values are caller assertions and do not implement the frozen integration record

`validate_cncp()` performs useful internal consistency checks, but it accepts a
caller-supplied single record and cannot derive resource/operator counts from
the candidate. It does not require the separately mandated `CNCP_delta_A` and
`CNCP_total_endpoint`, estimate confidence, widths, schedules, or candidate
family's mandatory `N3` classification. A floating adaptive candidate can
supply a syntactically valid `N2` record. The screen may then set
`hardware_estimate_boundary_met` from those assertions (`screen108.py:690-701`)
without independent cost evidence. This flag must not be used as feasibility
evidence.

### P1-5 — Required mutation evidence is absent even where positive tests exist

The native suites contain directed failure cases, but they do not demonstrate
that deliberately modified actual candidate implementations are killed by an
independent verifier. The independent oracle has synthetic bad adapters, not
RG3/DSPB/PLL mutants. Missing actual-candidate mutations include at least:

- occurrence-edge substituted for decision edge;
- same-edge feedback publication;
- cross-window DSPB/PLL state carry;
- PLL commit-time anchoring;
- RG3 transport removal/reversal;
- DSPB post-pose hindcast and stale-winner retention;
- current-CAV/ZOH/bypass route relabeling;
- candidate output ray unrelated to its quaternion;
- candidate/config/source-ID substitution; and
- fallback-event deletion or outcome-aware retry.

Until those mutants are killed through the same adapter/producer/oracle path,
native pass results are not `SYNTHETIC_PASS`.

### P1-6 — The screen receipt omits required query-start state diagnostics

The result reports candidate-use and fallback aggregates, but not each
window's state-valid/lock status at query start, pre-roll fallback partition,
reset receipt, convergence/lock history, or the fixed 25/100 ms non-ranking
initialization sensitivities. Those omissions prevent interpretation of
MID/HIGH gains from a stateful candidate even after the P0 execution seam is
fixed.

## Test audit

The following source-only synthetic suites were run with bytecode writes
disabled:

| Suite | Tests | Result | What it actually establishes |
|---|---:|---|---|
| Independent oracle | 21 | PASS | Harness behavior against test-local synthetic adapters only |
| Common framework | 10 | PASS | Framework behavior against `RecordingModel` only |
| RG3 native | 11 | PASS | Direct RG3 geometry/fallback unit behavior |
| DSPB native | 17 | PASS | Direct DSPB state/expert/fallback unit behavior |
| SO3-PLL native | 19 | PASS | Direct PLL geometry/state/fallback unit behavior |
| screen108 | 8 | PASS | Fake sealed-output validation and metric envelope behavior |

All **86/86** tests passed. This does not reduce any P0 severity: the suites
prove six disconnected local contracts. Repository-wide call-site inspection
found no `OracleHarness.run()` using an actual candidate, no actual candidate
implementing `CandidateModel`, and no screen candidate-output producer for any
of the three models.

## Mandatory gate before any 108 execution

The next admissible action is not a 108 run. One synthetic integration gate
must first demonstrate, for each unchanged frozen candidate:

1. a canonical candidate ID accepted byte-identically by every component;
2. exact source/executable/config/adapter/producer/oracle/fallback identity;
3. a frozen occurrence-to-decision-edge mapping with
   `occurrence_cycle < decision_edge`;
4. exact reset at every 50 ms pre-roll start and no cross-window state;
5. immutable state/feedback versions and next-edge-only publication;
6. candidate quaternion and independently recomputed world ray;
7. exact current-CAV, fresh-ZOH, and sensor-fixed fallback routes and reasons;
8. actual RG3/DSPB/PLL execution through the independent oracle; and
9. all required actual-candidate mutations killed with retained receipts.

Any surviving mutation, missing identity, unattested ray, reset ambiguity,
decision-edge ambiguity, fallback-route collapse, or actual-candidate oracle
gap remains `STAGE3_IMPLEMENTATION_REDTEAM_FAIL`. Accuracy, waste, MID/HIGH
effects, native unit-test success, or self-reported CNCP cannot compensate for
those failures.
