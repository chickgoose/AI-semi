# Architecture-Neutral AER Physical PPA/Fmax Contract

Status: candidate-neutral comparison freeze candidate, 2026-08-07

## Purpose and evidence boundary

This contract compares physical implementations of candidates that implement the
same logical AER job. It does not select an RTL base and it does not turn an
external candidate's exploratory run into an official team score.

The intended final-candidate set is Ganghee's fovea design, Hyeonsu's final
design, and Junyoung's new clean-slate design. A23 and its baseline/A2/A3
experiments are historical evidence only: their scripts, fixed throughput
constants, and earlier Genus table are not the final comparison pipeline. A
newly written minimal conventional AER may be reported as a reference, but it is
not one of the three team candidates.

Pre-layout synthesis and post-route qualification answer different questions:

- **Genus screening** estimates area, power, and timing early enough to reject
  clearly uncompetitive structures. Its slack and nominal target are not final
  achieved-frequency evidence.
- **Innovus final qualification** must include placement, CTS, routing, parasitic
  extraction, and post-route timing at the declared corners. Only this stage can
  establish the reported physical Fmax bracket.

The observed Ganghee flow illustrates why this separation matters. Genus used a
1.2 ns target (833.3 MHz), while post-route observations were:

| Period | Frequency | Setup WNS | Setup-only observation |
| ---: | ---: | ---: | --- |
| 2.0 ns | 500 MHz | +0.007 ns | SETUP PASS |
| 1.5 ns | 666.7 MHz | -0.066 ns | SETUP FAIL |

These setup observations support only a **provisional setup-only fixed-netlist
interval `[500, 666.7) MHz`**. They do not establish 500 MHz as a qualified
demonstrated lower bound or establish a formal Fmax bracket. Qualification
requires the original reports to confirm hold WNS >= 0, successful detailed
route completion, and zero unconstrained paths at the declared corner. The runs
changed the period SDC while reusing the same existing netlist, so even after
those checks this remains a fixed-netlist diagnostic, not a per-target optimized
or absolute Fmax result.

The repository fixture preserves those period/setup observations only as a tool
example. The numeric hold WNS was not present in the supplied observation; the
fixture uses zero solely as a placeholder that lets the bracket utility exercise
the provisional setup-only interval. Neither that placeholder nor the utility's
fixture output is qualification evidence. They must not be cited as measured
hold evidence, a qualified demonstrated lower bound, a formal Fmax bracket, an
official external-candidate score, or a decision to adopt that candidate.

## Candidate-equivalence contract

A comparison is valid only when every candidate is charged for the same complete
logical service and physical boundary. Freeze and record all of the following:

1. logical event semantics, source count, and accepted/delivered-event rules;
2. normalized logical retire width, or all serializer/deserializer cycles, pins,
   buffering, and codec logic required to reach that width;
3. standard-cell and memory libraries, voltage/temperature process corner, and
   derates;
4. extraction technology and RC corner;
5. clock definition, uncertainty, generated clocks, input/output delays, output
   loads, false/multicycle paths, and every other SDC exception;
6. target utilization, aspect ratio, core margin, macro placement/blockages, and
   power-grid assumptions;
7. placement, CTS, routing, extraction, SI/OCV analysis settings, tool versions,
   and optimization effort;
8. identical event activity trace, annotation format, simulation interval,
   activity window, clock/reset treatment, and vectorless fallback policy.

`corner` in the CSV is an opaque qualification key for the complete PVT+RC
analysis corner, not merely a temperature label. Reports must also carry the
flow/configuration identity outside the minimal CSV when any item above differs.
Numbers with mismatched boundaries may be shown side by side as diagnostics but
must not be ranked as a controlled architecture comparison.

## Immutable candidate registry

Do not start an official run from a moving personal worktree. Copy or check out
an immutable candidate bundle and freeze one registry row before simulation or
synthesis. At minimum, each row records:

| Field | Required meaning |
| --- | --- |
| `candidate` | Stable result key, independent of directory nickname |
| `owner` | Ganghee, Hyeonsu, or Junyoung |
| `repo_url` and `commit_sha` | Repository identity and full immutable commit |
| `bundle_sha256` | Hash of the archived source bundle when the source is copied |
| `top` and `filelist` | Exact synthesizable top and ordered RTL/filelist inputs |
| `parameters` | Complete elaboration parameter map, including source count and widths |
| `defines` and `include_dirs` | Compile-time choices that can change hardware |
| `clock_reset` | Clock/reset ports, edge, polarity, and reset timing assumptions |
| `native_interface` | Protocol, physical input/output widths, and retire-lane count |
| `capability_profile` | Frozen common-benchmark capability profile and checksum |
| `normalization_rtl` | Any synthesizable codec/serializer/decoder charged to PPA |
| `tool_config_sha256` | Hash of common SDC, library, Genus, and Innovus configuration |

The first official comparison uses `N=16`, because all three candidates must be
compared at a source count supported by the fixed-N fovea implementation. Scale
studies at other source counts are separate evidence and must not replace the
N=16 table. A missing SHA, top, filelist, parameter map, or physical-boundary
declaration makes the candidate `NOT_FROZEN`, not zero-scoring.

The registry contains exactly these final-candidate identities once their rows
are complete:

| Candidate key | Role before freeze |
| --- | --- |
| `ganghee_fovea_final` | final candidate; immutable identity pending registry |
| `hyeonsu_final` | final candidate; immutable identity pending registry |
| `junyoung_clean_slate` | final candidate; register only after new RTL exists |

No compatibility wrapper may silently select obsolete RTL. A wrapper is allowed
only when its source and cost are frozen in `normalization_rtl`; behavioral
testbench pin mapping remains outside PPA only when it adds no storage, retry,
arbitration, coding, or protocol capability.

## Evaluation stages

All candidates advance through the same ordered stages:

1. **Common-TB eligibility gate:** run the unchanged architecture-neutral
   conformance and workload contract through the candidate's native binding.
   Accepted events must drain without loss, duplication, corruption, or phantom
   output. Unsupported optional capabilities remain explicit SKIPs.
2. **Genus screening:** synthesize with the common libraries, PVT, SDC, effort,
   clock-gating policy, loads, and reset exceptions. This stage finds structural
   errors and removes clearly infeasible targets; it does not demonstrate Fmax.
3. **Innovus fixed-netlist diagnostic:** reuse one mapped netlist across a period
   sweep to locate likely timing limits cheaply. Keep these results diagnostic.
4. **Per-target resynthesis final P&R:** rerun Genus and the complete Innovus flow
   independently for every candidate and every target period. Only this stage
   supplies the final structural comparison and demonstrated Fmax bracket.

A candidate must not receive extra optimization effort, a different
clock-gating setting, or a hand-tuned exception unless the same declared policy
is available to every candidate. Candidate-specific syntax needed to elaborate
equivalent hardware is configuration, not permission to change the contract.

## Common functional metrics used by PPA

Throughput is never a manifest constant or a value inferred from RTL structure.
Import it from the common deterministic workload result for the frozen candidate
and configuration. Retain the workload version, trace hash, seed, source count,
stimulus interval, measurement interval, drain rule, and result checksum beside
each PPA row.

At minimum, use these measured values:

- sustainable completed logical events/cycle and the saturation knee;
- occurrence-to-delivery and acceptance-to-delivery average, p95, p99, and
  maximum latency in cycles;
- maximum request wait, fairness, source overrun, and timing-error metrics;
- the number of completed logical events in the activity-power window.

The throughput numerator counts normalized completed logical events, not output
words. Packed or multi-lane output may therefore exceed one event/cycle. The
denominator is the frozen measurement window; reset, warm-up, and drain cycles
must either be excluded identically or reported as a separate end-to-end view.
Do not substitute the historical A23 table's hardcoded `0.5` or `1.0` values.

For physical-interface efficiency, define `functional_pin_bits` as every
non-clock, non-reset, non-power signal bit crossing the frozen candidate PPA
boundary, including address/data, request/valid, ready/acknowledge, lane, type,
and other required controls. Count a bidirectional bit once and disclose the
input/output split. Then:

```text
events_per_pin_cycle = completed_events /
                       (measurement_cycles * functional_pin_bits)
```

If the competition later fixes a particular off-chip link boundary, report its
link-only pin metric additionally; do not replace or silently redefine the
whole-boundary metric after candidate results are known.

## Physical PPA report fields

Frequency is only one axis. For every qualified implementation, retain at least:

- mapped and post-route standard-cell area, separated into sequential,
  combinational, clock-tree, buffer/inverter, and filler/physical-only cells when
  the tools expose them;
- core and die dimensions, achieved utilization, macro area, and routed pin count;
- post-route leakage, internal, switching, and total power with units, activity
  coverage, annotation source, and exact measurement window;
- the timing bracket and setup/hold evidence defined below.

Activity-annotated post-route power is the comparison result. Vectorless power is
screening evidence and must remain visibly labeled. Do not combine area, power,
frequency, or events/pin-cycle into a single PPA score unless the weighting and
normalization rule was frozen before seeing candidate results.

Use the identical deterministic trace and identical cycle-indexed activity
window for all candidates. Report at least one sparse operating point and one
near-saturation operating point; idle-heavy sparse power must not hide saturated
transport cost, and saturation-only power must not hide normal AER efficiency.
For each window, compute energy from delivered logical work:

```text
energy_per_event = average_power * elapsed_window_time / completed_events
energy_nJ_per_event = power_mW / (clock_MHz * events_per_cycle)
```

The second form is valid only when power and measured events/cycle refer to the
same window and clock. A window with zero completed events reports energy/event
as undefined, never zero.

## Required final report views

Publish two separate tables; neither may be reconstructed by mixing rows from
different netlists or flow modes.

### Same-frequency efficiency table

Choose and freeze one frequency that all three candidates pass under
`per_target_resynthesis` (provisionally 200 MHz until the controlled sweep says
otherwise). Use the same workload points and activity windows. Required columns
are:

| Identity/timing | Function | Physical efficiency |
| --- | --- | --- |
| candidate, SHA, frequency, corner, setup/hold WNS | correctness, measured events/cycle, saturation knee, average/p95/p99/max latency | post-route area, total/dynamic/leakage power, energy/event, functional pins, events/pin-cycle |

This is the primary low-area/low-power efficiency comparison because clock rate
is held constant. Report sparse and near-saturation power/energy as distinct
rows or distinct labeled column groups.

### Maximum-demonstrated bracket table

Use only `per_target_resynthesis` bracket points. Required columns are:

| Provenance | Demonstrated timing | Useful-rate lower bound |
| --- | --- | --- |
| candidate, SHA, corner, flow-config hash | last-PASS period/frequency, first higher-frequency FAIL, setup/hold/route/unconstrained/DRC/antenna evidence | measured events/cycle, demonstrated logical Mevents/s, events/pin-cycle |

Compute the useful-rate lower bound as:

```text
demonstrated_Mevents_per_s = measured_events_per_cycle *
                             last_PASS_frequency_MHz
```

This is a lower bound tied to the named workload and clean last-PASS
implementation, not an exact application rate. If there is no higher-frequency
FAIL, show `>= last_PASS` rather than inventing a closed bracket.

## Two required sweep modes

### Fixed-netlist post-route diagnostic

Synthesize once, then reuse that netlist while changing the period SDC for
Innovus runs. This cheaply probes the timing margin of one mapped structure and
is useful for debugging constraints or locating a bracket. Label it
`synthesis_mode=fixed_netlist`.

It does not allow synthesis to restructure, resize, buffer, or remap logic for a
tighter target. Its result is therefore specific to that netlist and must not be
presented as per-target optimized or absolute Fmax.

### Signoff-style per-target resynthesis sweep

For each tested period, run Genus again with that target, then run the complete
Innovus placement-through-extraction flow on the resulting netlist. Label it
`synthesis_mode=per_target_resynthesis`. Use the same frozen comparison contract
at every point and for every candidate.

Final structural comparisons require this mode. A fixed-netlist sweep can select
promising periods, but cannot replace the per-target resynthesis sweep.

## Qualification and bracket rule

A tested period is a qualified timing PASS only when all of these are true:

- post-route setup WNS is at least 0 ns at the declared setup views;
- post-route hold WNS is at least 0 ns at the declared hold views;
- detailed routing completed successfully;
- the timing report contains zero unconstrained paths.

The run record should additionally retain failing endpoints, TNS, transition,
capacitance, fanout, LVS/connectivity, and tool-error summaries when available.
DRC and antenna counts are mandatory signoff disclosures, but remain distinct
from the timing-PASS boolean: report `CLEAN`, `VIOLATIONS:n`, or `NOT_REPORTED`.
Never imply physical signoff merely because setup/hold passed.

Convert period to frequency as `F_MHz = 1000 / period_ns`. The highest-frequency
qualified PASS is the demonstrated lower bound. The nearest tested FAIL at a
higher frequency is the exclusive upper bound. Report `[last PASS, first FAIL)`;
if no higher-frequency FAIL exists, report a lower bound only. A FAIL at a lower
frequency followed by a PASS at a higher frequency is non-monotonic and requires
flow investigation rather than a trusted bracket.

Never combine different `candidate`, `synthesis_mode`, or `corner` values in one
bracket. A Genus screening point is not an Innovus bracket point.

## CSV tool

[`bracket_fmax.py`](../../benchmarks/physical_ppa/bracket_fmax.py) uses only the
Python standard library. Its required schema is:

```text
candidate,period_ns,synthesis_mode,corner,setup_wns_ns,hold_wns_ns,route_ok,unconstrained_paths
```

Optional `drc_violations` and `antenna_violations` columns are disclosed at the
demonstrated point but do not silently change timing PASS. Extra provenance
columns are allowed.

```sh
python3 benchmarks/physical_ppa/bracket_fmax.py \
  benchmarks/physical_ppa/fixtures/ganghee_fixed_netlist_example.csv

python3 benchmarks/physical_ppa/bracket_fmax.py \
  --format json --output /tmp/fmax-brackets.json results-*.csv
```

## Saturday discussion freeze checklist

Before combining physical results with the existing clean benchmark:

- [ ] final candidate registry rows contain immutable SHA/bundle hash, top,
      filelist, parameters, native interface, normalization RTL, and flow hash;
- [ ] conformance tests pass and accepted events fully drain without corruption;
- [ ] workload, seed, source count, offered load, and activity window match;
- [ ] logical retire width matches, or serializer/decoder costs and pin-cycle
      normalization are included;
- [ ] saturation knee, throughput, latency tail, request wait, timing error, and
      fairness come from the common architecture-neutral result aggregator;
- [ ] the PPA boundary contains all synthesizable arbitration, buffering, coding,
      serialization, and output normalization required by the candidate;
- [ ] libraries/PVT, RC corner, SDC I/O assumptions, floorplan/utilization, and
      physical-flow settings satisfy the equivalence contract above;
- [ ] Genus screening numbers and Innovus final-qualification numbers are labeled
      separately;
- [ ] structural ranking uses `per_target_resynthesis`, with fixed-netlist results
      retained only as diagnostics;
- [ ] setup/hold/route/unconstrained-path evidence is complete at every bracket
      point, while DRC and antenna status are explicitly disclosed;
- [ ] power uses the same trace and measurement window, with vectorless estimates
      visibly separated from activity-annotated results;
- [ ] throughput and latency are imported from measured common-TB results, not
      hardcoded architecture expectations;
- [ ] same-frequency efficiency and maximum-demonstrated bracket results are
      published as separate tables;
- [ ] the report states the last-PASS/first-FAIL bracket and does not replace it
      with an unsupported exact Fmax;
- [ ] any external result remains calibration evidence, not an official score or
      an architecture-base decision.
