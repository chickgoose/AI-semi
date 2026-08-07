# A7 DREC N=16/K=4 qualification

Date: 2026-08-07

## Decision boundary

The candidate is the original shared-prefix N=16/K=4 implementation.  The only
fair screening reference is the N=16/K=4 replicated selector with the same
native ports, four one-entry retire registers, 104 registered state bits,
rotation policy, and independent-ready behavior.  K=1, K=2, fixed priority,
or a design with fewer output endpoints is not a physical comparison baseline.

The candidate remains a physical-screening hypothesis until same-library Genus
and, if that passes, Innovus P&R.  Generic mapping is not standard-cell PPA.

## Reproduced functional evidence

The following were rerun from the integration branch:

- exhaustive request-bitmap qualification for K=1/2/4: PASS for all 65,536
  N=16 request masks at every K;
- independent-ready directed qualification at K=4: prefix and replicated both
  PASS, with 671 other-lane completions while lane 0 remained stalled;
- randomized simultaneous lockstep at K=4: PASS for 1,223 cycles, 3,761
  accepted and 3,761 delivered events, six drain cycles;
- the lockstep monitor checks exact cycle-by-cycle source-ready, retire-valid,
  retire-event, and retire-source equality, continuous stalled valid/payload,
  source-local FIFO order, phantom/duplicate detection, conservation, and
  persistent-service coverage.

The frozen 46-trace evidence remains 138/138 correct across K=1/2/4.  Same-K
prefix and replicated aggregate rows are identical.  The new lockstep test
closes the earlier gap where aggregate equivalence alone could have hidden
cycle-level differences.

## Reproduced generic structural evidence

Yosys structural mapping was rerun.  The decisive N=16 rows reproduced exactly:

| K | shared prefix gates / depth | replicated gates / depth | state bits | decision |
| ---: | ---: | ---: | ---: | --- |
| 1 | 3,689 / 130 | 2,304 / 67 | 41 | reject |
| 2 | 4,299 / 133 | 3,733 / 133 | 62 | reject |
| 4 | 5,592 / 139 | 6,729 / 248 | 104 | physical screening eligible |

At K=4 the generic proxy is 16.9% smaller and 44.0% shallower than this exact
replicated reference.  This is a crossover, not proof of area or Fmax.

As a mapper-sensitivity check, a separate Yosys `abc -fast` generic flow was
also run at N=16/K=4.  It produced 5,272 combinational cells and depth 113 for
the shared prefix versus 5,542 and depth 161 for the replicated reference:
4.9% fewer combinational cells and 29.8% lower generic depth.  The benefit is
weaker than the first proxy but retains the same direction.  This check reduces
the chance of a single-mapper artifact; it is still not a standard-cell result.

```bash
tests/a7_parallel_event_compactor/abc_sensitivity_compare.py \
  --yosys /absolute/path/to/yosys \
  --output reports/a7-parallel-event-compactor/abc-fast-sensitivity.csv
```

## Physical bundle

Build an immutable comparison bundle only from a committed tree:

```bash
scripts/ppa/build_a7_k4_physical_bundle.sh /tmp/a7-k4-genus-bundle
```

On the assigned server, after the normal Cadence environment is sourced:

```csh
cd /path/to/a7-k4-genus-bundle
setenv AER_LIBRARY_FILE /home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
setenv AER_COMPARISON_RUN_ID drec-n16-k4-5ns
setenv AER_CLOCK_PERIOD_NS 5.000
./run_comparison.sh
```

Every result records commit, top, filelist, source hash, SDC hash, Liberty hash,
Tcl hash, port/state boundary, clock period, and flow effort.  Vectorless Genus
power is labeled screening-only.  No hardcoded throughput number is converted
into an efficiency claim.

## Stop/go rule

- STOP on any correctness, unresolved-reference, latch, or timing-metric failure.
- STOP if the standard-cell comparison removes the shared-prefix benefit without
  a compensating and material timing benefit.
- Only a meaningful same-frequency Genus area/power or timing advantage allows
  period-by-period resynthesis and Innovus P&R.
- P&R must charge all four endpoints and 88 retire signals.  If global prefix
  wiring removes the crossover, reject DREC and use the documented fallback
  gate rather than optimizing indefinitely.
