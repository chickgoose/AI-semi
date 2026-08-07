# A7 radix-4 segmented K2 rescue result

Date: 2026-08-07

## Decision: rescue failed

The N=16/K=2 rescue is rejected.  Radix-4 segmentation reduces generic gates
from original prefix 4,299 and replicated 3,733 to 3,307, but increases generic
depth from 133/133 to 149.  It therefore fails the predeclared requirement to
beat both references simultaneously in area and depth.  Maximum cell-input
fanout is also 131 versus original 85 and replicated 131.  Equal register state
is preserved at 62 bits for all three.

This is a useful negative result: moving wide prefix work to four-source local
segments successfully attacks area, but the dependency
`local count -> segment prefix -> rank match -> index mux` adds critical depth.
The K2 failure is not repaired by its 23.1% gate reduction versus original.

## Three-way structural result

All rows use isolated source lists, identical flattened top ports, the same
`proc; flatten; opt; techmap; opt` Yosys 0.52 flow, and `ltp -noff`.  Isolating
the frozen tops reproduces the second-round gates/depth/register numbers rather
than allowing unused experimental modules to perturb optimizer ordering.

| N | K | implementation | gates | depth | max/p95 fanout | register bits |
| ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 16 | 2 | original | 4,299 | 133 | 85 / 4 | 62 |
| 16 | 2 | segmented | 3,307 | 149 | 131 / 4 | 62 |
| 16 | 2 | replicated | 3,733 | 133 | 131 / 3 | 62 |
| 16 | 4 | original | 5,592 | 139 | 104 / 5 | 104 |
| 16 | 4 | segmented | 4,784 | 165 | 133 / 4 | 104 |
| 16 | 4 | replicated | 6,729 | 248 | 154 / 5 | 104 |
| 32 | 2 | original | 10,855 | 222 | 194 / 4 | 81 |
| 32 | 2 | segmented | 7,282 | 237 | 259 / 4 | 81 |
| 32 | 2 | replicated | 12,111 | 234 | 259 / 3 | 81 |
| 32 | 4 | original | 13,436 | 228 | 194 / 4 | 125 |
| 32 | 4 | segmented | 10,011 | 253 | 261 / 4 | 125 |
| 32 | 4 | replicated | 20,983 | 447 | 298 / 5 | 125 |
| 64 | 2 | original | 27,914 | 392 | 447 / 4 | 116 |
| 64 | 2 | segmented | 17,452 | 407 | 515 / 4 | 116 |
| 64 | 2 | replicated | 42,895 | 429 | 515 / 3 | 116 |
| 64 | 4 | original | 33,105 | 398 | 445 / 4 | 162 |
| 64 | 4 | segmented | 22,731 | 423 | 517 / 4 | 162 |
| 64 | 4 | replicated | 72,845 | 836 | 586 / 5 | 162 |

Fanout counts post-techmap cell input connections per JSON net bit.  It is a
structural wiring proxy, not buffered physical fanout.  Complete operator
proxies are retained in `radix4-rescue-structural.csv`.  No server, Liberty,
placement, or routing flow was run.

At every N, segmented K2 is smaller but deeper than original.  At N=32 it is
also three levels deeper than replicated; at N=64 it beats replicated depth
but remains 15 levels deeper than original.  Therefore increasing N does not
satisfy the strict three-way K2 rescue criterion.

## Equivalence

Verilator exhaustively checked all 65,536 N=16 bitmaps at every one of 16
rotation bases.  For K=2/4 it checked selected valid count, cyclic ordering,
and each exact selected index; prefix totals and every per-source inclusive
count also matched original.  The existing K=1 case was retained as a guard.

Full candidate equivalence then ran original, segmented, and replicated in
lockstep for N=16/32/64 and K=2/4, 2,048 cycles per configuration.  Stimulus
included deterministic random requests, independent random lane ready,
permanent lane-0 stall, alternating backpressure, refill, and final drain.
All six configurations matched cycle-by-cycle on source ready, retire valid,
event, and source index.

## Application condition

For the frozen N=16 problem, shared-prefix compaction is allowed only at K>=4.
K=2 must use neither original nor segmented A7 when the equal-state replicated
selector is the break-even reference.

At K=4, select the original prefix when depth is primary.  Select radix-4
segmented only when gate reduction is primary and the larger generic-depth
budget is acceptable: at N=16 it saves 14.4% gates versus original while depth
increases 18.7%, and it still beats replicated in both gates and depth.  The
same conditional pattern holds at N=32/64.  No K2 result may be generalized
from the K4 area benefit.

## Reproduction

```bash
PATH=/path/with/verilator:$PATH AER_SIMULATOR=verilator \
  tests/a7_parallel_event_compactor/run_unit.sh

PATH=/path/with/verilator:$PATH \
  tests/a7_parallel_event_compactor/run_rescue_equivalence.sh

LD_LIBRARY_PATH=/path/to/yosys-libs \
  tests/a7_parallel_event_compactor/structural_rescue_compare.py \
  --yosys /path/to/yosys \
  --output reports/a7-parallel-event-compactor/radix4-rescue-structural.csv

git diff 2219040 -- \
  rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv \
  tests/a7_parallel_event_compactor/a7_structural_wrappers.sv \
  tests/a7_parallel_event_compactor/structural_compare.py \
  tests/a7_parallel_event_compactor/run_adversarial.sh

git diff ad96895 -- scripts/run_clean_benchmark.sh tb/clean/aer_clean_tb.sv
```

Both boundary commands produce no output.  The structural CSV SHA-256 is
`1fc53e50d57d3d813f3fdd5e76ae1f167f97bc15a48a8abcd1d6ab0f5d626d13`.
