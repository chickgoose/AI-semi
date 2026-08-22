# MC-WTB Stage 3 CNCP Screen Policy — 2026-08-22

Status: independent B9 policy review; documentation only

Scope: model-only RG3, DSPB, and SO3-PLL candidates

Evidence boundary: no candidate implementation, candidate measurements, external data, RTL, synthesis, timing closure, area, power, or PPA evidence

## 1. Decision

The current `screen108` numeric-only CNCP input **does conflate model accuracy with implementation feasibility when it is used as a gate**.

The problem is not that the evaluator authorizes RTL or PPA. It does not: the result keeps `promotion_authorized=false` and `rtl_ppa_authorized=false`. The problem is narrower and still material:

1. `screen_metric_gate_pass` is the conjunction of accuracy checks and self-declared CNCP checks.
2. `hardware_estimate_boundary_met` can become true from a flat set of integers and an asserted risk class, without an auditable derivation.
3. `SCREEN108_MEASURED_PROMOTION_NOT_AUTHORIZED` can therefore describe a combined result whose accuracy portion was measured but whose feasibility portion was only asserted.
4. A fictional small cost can make a model-only candidate appear feasibility-ready; a fictional large cost can suppress an otherwise valid model result.

The flat numeric object may remain useful only as `DECLARED_UNVERIFIED` planning metadata. It is not evidence, must not satisfy a cost gate, must not affect model ranking, and must always produce `CNCP_HOLD_UNVERIFIED`.

Until a candidate has a complete, candidate-bound, auditable pre-RTL manifest, the honest representation for RG3, DSPB, and SO3-PLL is:

```text
model_evidence = independently evaluable
cncp_evidence = UNINSTANTIATED
cncp_verdict = HOLD
final_state_class = UNASSIGNED
final_compute_class = UNASSIGNED
final_pipeline_class = UNASSIGNED
numeric_risk = N3_PROVISIONAL
implementation_feasibility_go = false
ppa_status = NOT_EVALUATED
```

This HOLD is not a negative feasibility finding. It means feasibility has not yet been evidenced.

## 2. Audit basis and claim boundary

This policy applies the contracts in:

- `docs/MC_WTB_PREDICTOR_STAGE12_CONTRACT_20260822.md`
- `docs/MC_WTB_STAGE12_ARCHITECTURE_CANDIDATES_20260822.md`
- `docs/MC_WTB_STAGE3_COST_AND_INTEGRATION_REVIEW_20260822.md`
- `docs/MC_WTB_STAGE3_CAUSAL_REDTEAM_20260822.md`
- `benchmarks/redred_mc_wtb_predictor_stage3/screen108.py`
- `tests/redred_mc_wtb_predictor_stage3_screen/test_screen108.py`

The Stage 12 CNCP tuple remains the accounting target:

```text
(B_ff, B_sram, read_ports, write_ports,
 O_pose, O_event, II_event, critical_depth,
 pipeline_bits, max_wire_width, numeric_risk)
```

No field in that tuple is a PPA result. A complete tuple can support a bounded pre-RTL screening decision; it cannot support frequency, slack, area, power, energy, utilization, congestion, or timing-closure claims.

No numeric candidate outcome is inspected or introduced by this policy.

## 3. What the present numeric-only interface proves—and does not prove

### 3.1 What it currently validates

The current validator checks useful internal consistency conditions:

- fields are nonnegative integers where required;
- `endpoint_target_ns` equals the contractual target value;
- `event_lanes` equals two;
- `pipeline_bits <= B_ff`;
- the declared state class agrees with `B_ff + B_sram`;
- the declared compute class is not below a class inferred from aggregate operator categories;
- the pipeline class agrees with `critical_depth` as currently interpreted;
- the numeric-risk label belongs to N0–N3.

These checks can reject malformed declarations. They do not establish that the declarations describe a realizable candidate.

### 3.2 Missing evidence needed for a feasibility claim

The numeric-only input contains no auditable support for:

- field widths, signedness, radix points, reset values, ranges, saturation, or rounding;
- state ownership, lifetime, multiplicity, banking, duplication, or baseline-versus-delta scope;
- memory depth, word width, port type, access schedule, or conflict proof;
- operator input/output widths, domain, sharing, replication, latency, or initiation interval;
- the pose-rate schedule and the authority for the minimum pose commit spacing;
- typed critical-path DAGs for the pose and event domains;
- actual register boundaries, per-stage payload, sidebands, lane duplication, or backpressure;
- fanout and wire-width derivation;
- bit-true range/error evidence or bounded nonlinear iteration;
- the implementation/configuration identity from which the counts were derived;
- an independent review receipt and a seal over the derivation.

The unit-test CNCP fixture demonstrates schema behavior, not candidate evidence. Passing that fixture proves the checker accepts a structurally consistent object; it does not convert the fixture values into hardware facts.

### 3.3 Specific category errors

The following current interpretations are not defensible as feasibility evidence:

- `endpoint_target_ns == 6.5` states a requirement; it does not show that a path meets it.
- `event_lanes == 2` states required topology; it does not show that every event-domain resource is correctly replicated or scheduled.
- `II_event == 1` is a claim unless supported by a worst-case resource/schedule proof independent of dataset cadence.
- Aggregate `add_compare`, `fixed_multiply`, and `nonlinear` counts omit widths, domains, sharing, latency, and replication.
- `B_ff + B_sram` alone cannot prove storage feasibility because ports, copies, banking, and lifetimes matter.
- Treating `critical_depth` as sufficient to derive a pipeline class conflates operator depth with registered pipeline stages.
- A self-declared N2 label is not a bit-true bounded-numeric argument.
- A single total without `delta_A` versus `total_endpoint` scope is not comparable between candidates.

Therefore, current numeric CNCP values can be linted but cannot make `hardware_estimate_boundary_met=true`.

## 4. Required separation of decisions

Every result must expose four independent verdicts. No combined pass bit is permitted.

| Track | Permitted evidence | Verdicts | May affect |
|---|---|---|---|
| Model screen | Sealed Stage 12 evaluator inputs and outputs | `MODEL_PASS`, `MODEL_HOLD`, `MODEL_FAIL` | Model ranking only |
| CNCP screen | Manifest defined below | `CNCP_PASS_BOUNDED`, `CNCP_HOLD_*`, `CNCP_FAIL_CEILING` | Eligibility for later implementation work only |
| Causal/integration | Frozen synthetic causal-mutation contract and later authorized integration evidence | `CAUSAL_PASS`, `CAUSAL_HOLD`, `CAUSAL_FAIL` | Whether model evaluation may be interpreted causally |
| PPA | RTL plus an authorized implementation flow, neither present here | `NOT_EVALUATED` | Nothing in Stage 3 model-only work |

Permitted combined envelope:

```text
model_screen.status
cncp_screen.status
causal_screen.status
ppa.status = NOT_EVALUATED
promotion_authorized = false
```

Forbidden combined envelope:

```text
screen_metric_gate_pass = accuracy_pass && asserted_cost_pass
```

A candidate may honestly be `MODEL_PASS / CNCP_HOLD_UNINSTANTIATED`. That is not contradictory and must not be rewritten as either an overall PASS or an overall FAIL.

## 5. CNCP evidence grades

Each manifest and each count within it has an evidence grade.

| Grade | Meaning | Gate effect |
|---|---|---|
| `UNINSTANTIATED` | Required object is known, but widths/counts/schedule are not instantiated | HOLD |
| `DECLARED_UNVERIFIED` | A number was supplied without a sealed derivation | HOLD; number ignored for ranking |
| `SYMBOLIC_BOUNDED` | A sealed expression and all variable bounds provide a conservative upper bound | Eligible for ceiling screening |
| `EXACT_PRE_RTL` | A sealed, fully enumerated pre-RTL inventory and schedule derive the number exactly for the identified model configuration | Eligible for ceiling screening |
| `MEASURED_RTL` | Requires RTL/implementation evidence | Prohibited in this model-only stage |

Rules:

1. A total inherits the weakest grade of any contributing row.
2. One unbounded or omitted acceptance-path row makes the corresponding total `UNINSTANTIATED` and the CNCP verdict HOLD.
3. A range is evaluated at its conservative upper bound.
4. A symbolic expression is admissible only when every variable has a sealed bound and the expression is reproducible.
5. Zero is evidence only when a sealed inventory proves the object is absent. Missing is `null`, never zero.
6. `EXACT_PRE_RTL` means exact accounting under a declared architecture, not accurate PPA.

## 6. Exact HOLD-safe manifest

The canonical manifest is a versioned, candidate-bound object. Ordering, integer encoding, null encoding, and digest procedure must be fixed by the manifest schema before candidate entries are produced.

```yaml
schema: mc_wtb_stage3_cncp_manifest_v1
candidate:
  candidate_id: RG3 | DSPB | SO3_PLL
  model_config_sha256: <required or null>
  executable_sha256: <required or null>
  cncp_derivation_sha256: <required or null>
  baseline_id: A
  scope:
    delta_A: required
    total_endpoint: required

basis:
  stage: MODEL_ONLY_NO_RTL
  evidence_grade: UNINSTANTIATED | DECLARED_UNVERIFIED |
                  SYMBOLIC_BOUNDED | EXACT_PRE_RTL
  ppa_status: NOT_EVALUATED
  no_ppa_claim: true

authority:
  stage12_contract_sha256: <required>
  architecture_contract_sha256: <required>
  cncp_policy_sha256: <self-excluded seal rule required>
  frozen_pose_spacing_authority_sha256: <required or null>

numeric_format_rows:
  - field: <semantic field name>
    domain: pose | event | control
    width_bits: <integer, bounded expression, or null>
    signed: <boolean or null>
    integer_bits: <integer or null>
    fractional_bits: <integer or null>
    reset: <exact value or null>
    range: <sealed interval or null>
    rounding: <mode or null>
    overflow: saturate | wrap | reject | null
    evidence_grade: <row grade>

state_rows:
  - name: <semantic state>
    owner: baseline_A | candidate_delta
    domain: pose | event | control
    count: <integer, bounded expression, or null>
    bits_per_item: <integer, bounded expression, or null>
    storage: ff | sram | rom | unresolved
    lifetime: <defined interval or null>
    copies_or_banks: <integer, bounded expression, or null>
    lane_replication: <integer, bounded expression, or null>
    included_in_pipeline_bits: <boolean or null>
    evidence_grade: <row grade>

memory_rows:
  - name: <semantic memory>
    owner: baseline_A | candidate_delta
    depth: <integer, bounded expression, or null>
    width_bits: <integer, bounded expression, or null>
    copies: <integer, bounded expression, or null>
    read_ports: <integer, bounded expression, or null>
    write_ports: <integer, bounded expression, or null>
    read_latency_cycles: <integer, bounded expression, or null>
    worst_case_access_schedule: <sealed reference or null>
    conflict_free_proof_sha256: <required or null>
    evidence_grade: <row grade>

operator_rows:
  - name: <semantic operation>
    operator_type: add | compare | multiply | divide | normalize |
                   exp | log | trig | lookup | other
    domain: pose | event | control
    input_widths: <list, bounded expressions, or null>
    output_width: <integer, bounded expression, or null>
    quantity: <integer, bounded expression, or null>
    latency_cycles: <integer, bounded expression, or null>
    initiation_interval: <integer, bounded expression, or null>
    sharing_group: <identifier, none, or null>
    lane_replication: <integer, bounded expression, or null>
    bounded_iteration_count: <integer or null>
    numeric_mode: fixed | integer | unresolved
    evidence_grade: <row grade>

schedule:
  pose_domain:
    minimum_commit_spacing_cycles: <bounded value or null>
    authority_sha256: <required or null>
    worst_case_schedule_sha256: <required or null>
  event_domain:
    required_lanes: 2
    required_ii: 1
    proven_lanes: <integer or null>
    proven_worst_case_ii: <integer or null>
    worst_case_schedule_sha256: <required or null>
  dataset_cadence_used_as_proof: false

critical_path_dags:
  pose:
    dag_sha256: <required or null>
    longest_typed_operator_depth: <bounded value or null>
  event:
    dag_sha256: <required or null>
    longest_typed_operator_depth: <bounded value or null>
  timing_ns_claim: null

pipeline_rows:
  - stage_id: <stable identifier>
    domain: pose | event | control
    payload_bits: <integer, bounded expression, or null>
    sideband_bits: <integer, bounded expression, or null>
    copies: <integer, bounded expression, or null>
    lane_replication: <integer, bounded expression, or null>
    backpressure_behavior: <defined behavior or null>
    evidence_grade: <row grade>

wiring_rows:
  - signal_group: <semantic group>
    width_bits: <integer, bounded expression, or null>
    fanout: <integer, bounded expression, or null>
    domain_crossing: <description or null>
    evidence_grade: <row grade>

numeric_risk:
  provisional_class: N3
  hazards: <complete list or null>
  range_proof_sha256: <required or null>
  bit_true_receipt_sha256: <required for N2 or lower, otherwise null>
  fixed_iteration_proof_sha256: <required when applicable or null>
  final_class: UNASSIGNED | N0 | N1 | N2 | N3

totals:
  delta_A:
    B_ff: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    B_sram: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    read_ports: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    write_ports: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    O_pose: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    O_event: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    II_event: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    critical_depth: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    pipeline_bits: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
    max_wire_width: {value_or_upper_bound: null, evidence_grade: UNINSTANTIATED}
  total_endpoint: <same required fields and rules>
  state_class: UNASSIGNED
  compute_class: UNASSIGNED
  pipeline_class: UNASSIGNED

review:
  completeness_receipt_sha256: <required or null>
  independent_reviewer_id: <required or null>
  unresolved_items: <complete list>
  manifest_sha256: <required>

verdict:
  status: CNCP_HOLD_UNINSTANTIATED
  implementation_feasibility_go: false
  hardware_estimate_boundary_met: false
  promotion_authorized: false
  rtl_ppa_authorized: false
```

An uninstantiated manifest must still enumerate all known semantic rows. It must not replace unknown widths, quantities, schedules, or totals with guessed values.

## 7. Candidate-specific minimum inventories

These inventories name what must be accounted. They deliberately assign no widths, counts, sharing factors, or PPA.

### 7.1 RG3

At minimum, the manifest must expose separate rows for:

- the additional pose/history sample and its time or interval metadata;
- rate and acceleration state;
- the three gates and their state, if any;
- transport, composition, logarithm, division/reciprocal, and clamping operations;
- event-domain extrapolation and any lane-local copies;
- fallback state and all pipeline/sideband state.

Until those rows have bounded formats, operator mappings, and schedules, RG3 remains `CNCP_HOLD_UNINSTANTIATED`, with S/C/P unassigned and N3 provisional.

### 7.2 DSPB

At minimum, the manifest must expose separate rows for:

- every expert state and expert-specific history;
- immutable forecast snapshots used for delayed credit;
- all credit state and update arithmetic;
- winner selection, hysteresis, and switching state;
- per-expert forecast operators;
- shared versus replicated expert engines and the worst-case pose schedule;
- event-domain selected forecast state and lane-local copies;
- fallback state and all pipeline/sideband state.

An assertion that experts are shared is not a schedule proof. An assertion that they are parallel is not an operator count. Until the choice is fixed and bounded, DSPB remains `CNCP_HOLD_UNINSTANTIATED`, with S/C/P unassigned and N3 provisional.

### 7.3 SO3-PLL

At minimum, the manifest must expose separate rows for:

- angular-rate and integral/controller state;
- pose/time snapshots and any double-buffered publication state;
- gain, lock, guard, and reset state;
- residual logarithm, proportional, integral, and update operations;
- normalization and nonlinear operations with fixed iteration bounds;
- pose-to-event publication/CDC payload and lane-local event state;
- fallback state and all pipeline/sideband state.

Until formats, nonlinear bounds, publication semantics, and schedules are sealed, SO3-PLL remains `CNCP_HOLD_UNINSTANTIATED`, with S/C/P unassigned and N3 provisional.

## 8. Promotion and HOLD gates

The gates are evaluated independently and in order. A later gate cannot repair an earlier HOLD.

### G0 — identity and causal contract

PASS requires:

- candidate ID, model configuration, executable, and derivation identities are sealed;
- the candidate matches the frozen architecture/configuration being evaluated;
- the applicable synthetic causal-mutation receipt is present before any interpretation of the 108-window model result.

Missing identity or causal receipt: `CAUSAL_HOLD`; do not consume the 108 result for a causal claim.

### G1 — model accuracy

Evaluate only the frozen Stage 12 accuracy, robustness, and waste rules. CNCP fields are not inputs to this verdict.

- All model criteria met: `MODEL_PASS`.
- Required evidence absent or invalid: `MODEL_HOLD`.
- A frozen criterion is violated: `MODEL_FAIL`.

G1 never establishes implementation feasibility.

### G2 — CNCP manifest presence and scope

PASS requires:

- both `delta_A` and `total_endpoint` scopes;
- complete semantic row inventories;
- required hashes and independent completeness receipt;
- no `MEASURED_RTL` or PPA language.

Any missing scope, inventory, or seal: `CNCP_HOLD_INCOMPLETE`.

### G3 — bound completeness

PASS requires every acceptance-path state, memory, operator, schedule, pipeline, wire, and numeric item to be `SYMBOLIC_BOUNDED` or `EXACT_PRE_RTL`.

Any `UNINSTANTIATED`, `DECLARED_UNVERIFIED`, omitted, or unbounded item: `CNCP_HOLD_UNBOUNDED`.

### G4 — architectural ceilings

Use conservative upper bounds and the Stage 12 class definitions. PASS requires:

- state upper bound no greater than S2;
- compute upper bound no greater than C2;
- pipeline upper bound no greater than P2;
- two event lanes proven, not merely requested;
- worst-case event II of one proven without dataset-cadence assumptions;
- fixed-latency/backpressure behavior defined;
- pose-rate nonlinear work scheduled against a frozen minimum commit spacing;
- both candidate delta and total endpoint fit the applicable budgets.

Exceeding a ceiling: `CNCP_FAIL_CEILING`. Missing proof: HOLD, not FAIL.

No result of G4 is a 6.5 ns timing claim. `endpoint_target_ns` remains a requirement label only.

### G5 — numeric boundary

N3 is mandatory while acceleration/adaptive feedback hazards lack a sealed bit-true range/error argument. N3 produces `CNCP_HOLD_NUMERIC` for implementation feasibility even if G2–G4 otherwise pass.

N2 or lower requires:

- complete formats and intermediate ranges;
- explicit rounding, saturation, reset, and exceptional-value behavior;
- bounded nonlinear iterations;
- a sealed bit-true receipt against the frozen model semantics.

This policy does not create that receipt. Therefore the present model-only RG3, DSPB, and SO3-PLL disposition is HOLD.

### G6 — implementation and PPA boundary

Stage 3 model-only work cannot pass this gate. The only valid result is:

```text
ppa.status = NOT_EVALUATED
rtl_ppa_authorized = false
```

Terms such as “meets 6.5 ns,” “low area,” “lower power,” “cheap,” or any comparative PPA claim are forbidden.

## 9. Ranking policy

### 9.1 Model ranking

Model ranking is produced only among candidates that satisfy the frozen causal prerequisite and Stage 12 model gate. It uses the frozen Stage 12 accuracy and waste ordering. CNCP values, evidence grades, classes, and HOLD reasons do not enter a weighted score and do not change measured model metrics.

The output may therefore state:

```text
model_rank = 1
cncp_status = CNCP_HOLD_UNINSTANTIATED
overall_promotion = HOLD
```

It must not state that a lower-ranked candidate is the model winner merely because someone declared smaller CNCP numbers.

### 9.2 Feasibility ordering

CNCP is an eligibility filter, not a model-quality score.

1. Candidates failing a proven ceiling are ineligible for implementation promotion but retain their model rank.
2. Candidates on CNCP HOLD remain unranked for feasibility.
3. Only candidates with the same manifest schema, scope, baseline, evidence grade, ceiling authority, and numeric boundary may be compared.
4. For comparable manifests, use conservative upper bounds and Pareto dominance over the full CNCP tuple. Do not collapse unlike resources into an arbitrary weighted scalar.
5. If neither candidate Pareto-dominates, report `CNCP_NONDOMINATED_TIE`; do not invent exchange rates between bits, ports, operators, depth, and wires.
6. If a frozen decision requires the Stage 12 “smaller actual candidate state” tie-break, apply it only when both `delta_A` state totals are comparably evidenced. Otherwise report `MODEL_RANK_TIE_CNCP_HOLD`; do not guess state.
7. Apply frozen candidate ID only after all preceding, evidentially available tie-breaks are exactly equal. It must not conceal missing state evidence.

No accuracy-to-cost ratio, normalized composite, hand-selected weight, or post-outcome threshold is allowed.

### 9.3 Selection consequence

If the model winner is on CNCP HOLD, the program result is `MODEL_WINNER_CNCP_HOLD`. Do not silently substitute a runner-up and do not inspect outcomes to design a substitution rule. A future fallback-selection rule would have to be frozen before the relevant outcomes are inspected and would require comparable CNCP evidence.

## 10. Backward-compatible treatment of the current flat input

If the current flat CNCP object must be accepted by an evaluator for compatibility, its treatment is fixed:

```text
input_evidence_grade = DECLARED_UNVERIFIED
cncp_status = CNCP_HOLD_UNVERIFIED
hardware_estimate_boundary_met = false
implementation_feasibility_go = false
promotion_authorized = false
rtl_ppa_authorized = false
```

The evaluator may report lint failures in the declaration, but a lint success must not be reported as a feasibility pass. The numeric object must not be conjoined with accuracy, used in ranking, or labeled measured evidence.

A future screen result should carry only the CNCP manifest digest and independent CNCP verdict alongside the model result. It should not re-derive feasibility from naked values inside `screen108`.

## 11. Immediate stop rules

Stop and return HOLD before promotion or ranking if any of the following occurs:

- a missing width, count, port, schedule, stage, copy, or lane factor is encoded as zero;
- a total is labeled exact while any contributing row is missing, unbounded, or only declared;
- final S/C/P classes are assigned from model equations without a sealed mapping and schedule;
- N2 or lower is asserted without sealed bit-true range/error evidence;
- the 6.5 ns target is reported as met from a `critical_depth` integer;
- typical, average, or observed dataset cadence is used to prove worst-case II or resource sharing;
- pose-rate nonlinear sharing lacks a frozen minimum commit-spacing authority;
- event-lane replication, memory copies, snapshot copies, fallback state, or pipeline sidebands are hidden;
- `delta_A` and `total_endpoint` are absent or mixed;
- a CNCP manifest changes without a new digest and a candidate/configuration binding check;
- CNCP is changed after model outcomes are inspected to alter the winner or threshold;
- an accuracy result is failed, passed, or re-ranked because of unverified CNCP;
- a CNCP HOLD is presented as a feasibility FAIL or PASS;
- model-only evidence is described as RTL, timing, area, power, or PPA evidence;
- the synthetic causal prerequisite is missing or was defined after inspecting the protected outcomes.

On any stop, preserve the independent model result, record the precise HOLD reason, keep promotion false, and require a newly sealed receipt before reevaluation.

## 12. Final disposition for this review

| Candidate | Model evidence disposition | CNCP evidence disposition | S/C/P | Numeric risk | Implementation/PPA disposition |
|---|---|---|---|---|---|
| RG3 | Separate Stage 12 track; no outcome considered here | `CNCP_HOLD_UNINSTANTIATED` | `UNASSIGNED/UNASSIGNED/UNASSIGNED` | N3 provisional | HOLD / `NOT_EVALUATED` |
| DSPB | Separate Stage 12 track; no outcome considered here | `CNCP_HOLD_UNINSTANTIATED` | `UNASSIGNED/UNASSIGNED/UNASSIGNED` | N3 provisional | HOLD / `NOT_EVALUATED` |
| SO3-PLL | Separate Stage 12 track; no outcome considered here | `CNCP_HOLD_UNINSTANTIATED` | `UNASSIGNED/UNASSIGNED/UNASSIGNED` | N3 provisional | HOLD / `NOT_EVALUATED` |

The policy permits honest pre-RTL progress: semantic inventories, sealed bounds, resource schedules, critical DAGs, and bit-true receipts can mature independently. It does not permit model accuracy to stand in for feasibility, or unverified integers to stand in for hardware evidence.
