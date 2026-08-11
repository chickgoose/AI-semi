# A5 W3: Limited-Pointer Activity Directory with Exact Bitmap Fallback

Status: **HOLD at the pre-RTL gate; no SystemVerilog was created**

Date: 2026-08-11

## Scope and invariant

This experiment implements only a limited-pointer activity-directory model. It
does not add prediction, pregrant, caching, a circuit lease, or a new event
encoding. The committed official full50 and capacity22 manifests and the common
TB/RTL were read but not modified.

The source-latch pending bitmap is the sole authoritative truth. Directory
pointer tags, valid bits, and overflow are advisory and can only alter which
selection path is attempted. A hinted source is accepted only after its exact
pending bit is checked. An invalid or false-empty hint is detected by an exact
nonempty OR guard and enters a registered exact scan. A watchdog forces an
exact scan after 16 hint services, so a corrupt but continuously valid hot hint
cannot permanently starve another pending source.

The model enforces:

```text
generated = accepted + source_overrun
accepted multiset = delivered multiset
directory mutation cannot write the pending bitmap or the exact RR pointer
```

Exact fallback has a one-cycle entry penalty and then models one exact service
per cycle. This is deliberately less optimistic than a zero-latency fallback.
It also avoids claiming that the N-wide scan disappears: the N-wide exact OR
guard and registered fallback scan remain in the design.

## Model configurations and state proxy

The directory has L=1/2/4/8 pointers. State includes the L valid/tag entries,
overflow and fallback mode, exact RR pointer, 16-service watchdog, and an N-bit
previous-pending shadow used only to update the advisory directory.

| N | flat | L1 | L2 | L4 | L8 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16 state bits | 4 | 32 | 37 | 47 | 67 |
| 64 state bits | 6 | 84 | 91 | 105 | 133 |
| N64 hint-depth proxy | 6 | 1 | 2 | 3 | 4 |
| N64 pipelined-fallback depth proxy | 6 | 4 | 4 | 4 | 4 |

The N64 result is a source-ID expansion proxy: each official N16 source is
mapped to source `4*s`. It is not an official N64 workload result and does not
increase offered spatial diversity.

## Official full50 and capacity22 results

The runner validates the exact committed manifest byte hashes, ordered names,
and all 50 trace SHA-256 values before simulation. Capacity22 is selected as the
exact committed 22-name subset of full50.

### Outcome metrics

| Suite/config | Fixed-window event/cycle | Avg wait | weighted p95 wait | max wait | overrun |
| --- | ---: | ---: | ---: | ---: | ---: |
| full50 flat | 0.71528 | 2.445 | 5.338 | 15 | 23,375 |
| full50 L1 | 0.68989 | 3.147 | 7.004 | 16 | 26,306 |
| full50 L2 | 0.69025 | 3.117 | 6.986 | 35 | 26,260 |
| full50 L4 | 0.68647 | 3.019 | 7.659 | 49 | 26,707 |
| full50 L8 | 0.68232 | 3.124 | 11.639 | 123 | 27,179 |
| capacity22 flat | 0.76705 | 4.402 | 9.438 | 15 | 22,931 |
| capacity22 L1 | 0.74941 | 4.980 | 10.316 | 16 | 23,906 |
| capacity22 L2 | 0.74221 | 5.072 | 10.722 | 30 | 24,303 |
| capacity22 L4 | 0.72672 | 5.303 | 11.451 | 49 | 25,165 |
| capacity22 L8 | 0.71682 | 5.562 | 19.303 | 123 | 25,705 |

The directory does not add retire capacity. Its registered fallback entry
bubbles reduce fixed-window delivery and increase source-latch overrun. L8's
apparently modest average-wait change must not be read as a win: more events are
censored at the source and its p95/max wait regress sharply.

### Hit, update, overflow, and fallback behavior

| full50 config | hit services | updates | update overflow | fallback entries | overflow entries | hit wait | fallback wait | overflow wait | update-to-hit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L1 | 24,256 | 33,916 | 46,194 | 6,965 | 5,695 | 0.506 | 4.293 | 4.376 | 0.130 |
| L2 | 36,294 | 49,135 | 31,021 | 5,963 | 3,986 | 0.679 | 5.134 | 5.318 | 0.312 |
| L4 | 51,320 | 64,110 | 15,599 | 5,030 | 2,026 | 0.890 | 6.867 | 7.409 | 0.492 |
| L8 | 64,465 | 73,020 | 6,217 | 5,516 | 1,725 | 1.696 | 9.352 | 11.066 | 0.719 |

Clean official traces produced no invalid-pointer miss; their fallback entries
were overflow or watchdog entries. Mutation tests force false-empty,
out-of-range, duplicate-hot, false-overflow-clear, rotating-corrupt, and
stale-valid hints. Invalid/false-empty mutation recovery is exactly one cycle.
Official overflow recovery is also exactly one cycle for every L.

### Selection and activity proxies

| Suite/config | select bits/cycle | exact guard bits/cycle | state toggles/event | total selector activity/event | examined reduction vs flat |
| --- | ---: | ---: | ---: | ---: | ---: |
| N16 full50 flat | 16.016 | 0 | 1.848 | 24.214 | 0% |
| N16 full50 L1 | 7.915 | 4.004 | 7.818 | 26.072 | 50.6% |
| N16 full50 L2 | 6.392 | 4.136 | 8.584 | 25.817 | 60.1% |
| N16 full50 L4 | 4.469 | 4.326 | 9.292 | 26.087 | 72.1% |
| N16 full50 L8 | 2.775 | 4.325 | 10.105 | 28.496 | 82.7% |
| N64 proxy flat | 64.062 | 0 | 1.905 | 91.369 | 0% |
| N64 proxy L1 | 31.034 | 16.015 | 7.934 | 77.043 | 51.6% |
| N64 proxy L2 | 24.547 | 16.545 | 8.695 | 70.146 | 61.7% |
| N64 proxy L4 | 16.219 | 17.303 | 9.370 | 62.142 | 74.7% |
| N64 proxy L8 | 8.889 | 17.301 | 10.186 | 56.516 | 86.1% |

`select bits` excludes the separately reported exact nonempty guard. `total
selector activity` additionally includes tag comparisons and directory-state
toggles. Thus examined-bit reduction alone exaggerates the PPA opportunity. At
N16 every L increases the combined activity proxy. The N64 proxy decreases it
by about 15.7% (L1), 23.2% (L2), 32.0% (L4), and 38.1% (L8), but those estimates
do not include physical pointer routing or post-layout fanout.

## Adversarial L+1 cycling

Each case starts L+1 sources simultaneously and then retriggers them cyclically.
Flat accepts every generated event. The directory's fallback-entry bubbles cause
source overrun.

| Config | flat accepted/overrun | directory accepted/overrun | directory fixed-window throughput | directory max wait |
| --- | ---: | ---: | ---: | ---: |
| L1 | 509 / 0 | 457 / 52 | 0.8906 | 2 |
| L2 | 510 / 0 | 480 / 30 | 0.9336 | 3 |
| L4 | 512 / 0 | 480 / 32 | 0.9336 | 5 |
| L8 | 516 / 0 | 480 / 36 | 0.9336 | 9 |

N64 has the same event outcomes because this adversary activates only L+1
sources; its examined-bit proxy is lower but that does not repair the capacity
loss.

## Corruption proof scope

The exhaustive test covers all `8^4 = 4096` N=3 four-cycle arrival-mask
sequences under six persistent corruptions, for 24,576 mutated sequences. It
checks source-latch conservation, accepted/delivered equality, and exact event-ID
multisets. Additional directed tests cover false-empty recovery, duplicate
pointers, and a stale valid hot pointer versus a pending victim.

The independent diff check uses 64 three-source occurrence schedules under clean
and six corrupt modes, 448 cases total. Each source occurs once, eliminating
source-overrun as a confounder; flat and directory must deliver identical input
event multisets.

This is a bounded model proof, not formal proof of an unimplemented RTL. The
watchdog bound under arbitrary hint corruption is at most one fallback-entry
cycle plus exact service progress; a continuously stale hot hint is interrupted
after at most 16 hint services.

## GO gate and verdict

The predeclared gate requires:

1. all correctness checks pass;
2. capacity22 fixed-window throughput is at least 99% of flat; and
3. N64 select examined-bit reduction is at least 40%.

All L values pass correctness and the N64 examined-bit criterion. None passes
capacity22 throughput:

| L | capacity22 throughput / flat | N64 examined reduction | verdict |
| ---: | ---: | ---: | --- |
| 1 | 97.70% | 51.6% | HOLD |
| 2 | 96.76% | 61.7% | HOLD |
| 4 | 94.74% | 74.7% | HOLD |
| 8 | 93.45% | 86.1% | HOLD |

Final verdict: **HOLD / no RTL**. The exact fallback needed for correctness
erases enough event-level performance that an N64 timing proxy alone does not
justify SystemVerilog. Per the task gate, no candidate RTL, binding, or lockstep
TB was created.

## Reproduction

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/a5_activity_directory -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/a5_activity_directory/run_diff_check.py
PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/a5_activity_directory/run_activity_directory_sweep.py \
  --output /tmp/a5-w3-activity-directory
# CI/adoption gate: the committed HOLD returns 3 after publishing evidence.
PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/a5_activity_directory/run_activity_directory_sweep.py \
  --require-go --output /tmp/a5-w3-activity-directory-require-go
git diff --check
```

The sweep output contains per-run CSV, aggregate CSV, L+1 adversarial CSV, the
trace-generation log, and the machine-readable GO-gate JSON. Generated traces
and results remain under `/tmp`; existing user result directories are untouched.
The normal evidence run emits the decision-specific
`A5_ACTIVITY_DIRECTORY_SWEEP_HOLD` sentinel and exits zero only to permit HOLD
artifact collection. It never emits a PASS sentinel. Automation requiring an
adoptable candidate must use `--require-go`; the current HOLD then emits
`A5_ACTIVITY_DIRECTORY_REQUIRE_GO_FAILED` and exits 3.

The gate JSON does not infer correctness from `accepted == delivered` alone.
Each candidate explicitly binds generated accounting, accepted/delivered count,
event-ID multiset equality, no loss, no duplicate, no phantom, and source-local
order evidence. `correctness` is the conjunction of all seven fields.
