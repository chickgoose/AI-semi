# Architecture-Neutral AER Physical PPA/Fmax Contract

Status: discussion freeze candidate, 2026-08-06

## Purpose and evidence boundary

This contract compares physical implementations of candidates that implement the
same logical AER job. It does not select an RTL base and it does not turn an
external candidate's exploratory run into an official team score.

Pre-layout synthesis and post-route qualification answer different questions:

- **Genus screening** estimates area, power, and timing early enough to reject
  clearly uncompetitive structures. Its slack and nominal target are not final
  achieved-frequency evidence.
- **Innovus final qualification** must include placement, CTS, routing, parasitic
  extraction, and post-route timing at the declared corners. Only this stage can
  establish the reported physical Fmax bracket.

The observed Ganghee flow illustrates why this separation matters. Genus used a
1.2 ns target (833.3 MHz), while post-route observations were:

| Period | Frequency | Setup WNS | Observed result |
| ---: | ---: | ---: | --- |
| 2.0 ns | 500 MHz | +0.007 ns | PASS |
| 1.5 ns | 666.7 MHz | -0.066 ns | FAIL |

The supported conclusion is therefore a **demonstrated 500 MHz lower bound with
Fmax bracket `[500, 666.7) MHz`**, not an assertion that the exact Fmax is 500
MHz. Those runs changed the period SDC while reusing the same existing netlist,
so this is a fixed-netlist post-route achieved-Fmax diagnostic. It is not an
absolute Fmax obtained by resynthesizing independently for every target.

The repository fixture preserves those period/setup observations only as a tool
example. The numeric hold WNS was not present in the supplied observation; the
fixture uses zero solely to encode the stated overall PASS/FAIL boundary. It must
not be cited as measured hold evidence, an official external-candidate score, or
a decision to adopt that candidate.

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
- [ ] the report states the last-PASS/first-FAIL bracket and does not replace it
      with an unsupported exact Fmax;
- [ ] any external result remains calibration evidence, not an official score or
      an architecture-base decision.
