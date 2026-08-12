# A6 A2/A3 K2 and P6 same-flow digital audit

Status: local reproducible Yosys structural proxy **PASS**; functional and
physical qualification **HOLD**.

This A6-owned runner reads exact Git blobs and never reads candidate working
tree files. It compares two separate charged boundaries:

- `a2_k2` and `a3_k2`: the complete owner scheduler at the same atomic
  `pending/count/address/bundle_ready` normalized observation boundary. The
  small A6 wrappers add no storage. Candidate-only outputs remain charged
  until uniform optimization proves them irrelevant to this boundary.
- `a2_p6` and `a3_p6`: the complete integration tops, including scheduler,
  integration wrapper or elastic storage, atomic bundle frontend/adapter,
  launch/control, P6 TX, P6 RX, and retire observer. No endpoint state is
  excluded.

K2 and P6 numbers must not be compared across boundaries. P6 is the relevant
full digital endpoint comparison; K2 isolates the scheduler policy core.

The pinned flow is Yosys 0.52, `memory_map`, uniform flattening, `techmap`, and
`abc -g simple`. The runner records and pins Yosys and ABC hashes, every source
commit/blob hash, the closed reachable module inventory, generic and mapped
cells/state, combinational depth, fanout distribution, net count, and
sink-input connectivity proxy. These are not Liberty area, routed wire length,
STA, power, or physical PPA.

Run:

```sh
python3 audits/a6_a23_k2_p6_same_flow/run.py \
  --yosys /tmp/a7-toolchain/usr/bin/yosys \
  --output-dir /tmp/a6-a23-k2-p6-result
tests/a6_a23_k2_p6_same_flow/run_all.sh
```

Canonical mapped results are:

| Target | Cells | State bits | Depth | Fanout max/p95 | Nets | Sink-pin proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A2 K2 | 741 | 22 | 52 | 13 / 6 | 759 | 1450 |
| A3 K2 | 650 | 26 | 43 | 20 / 5 | 667 | 1235 |
| A2 + P6 | 983 | 73 | 50 | 22 / 6 | 1003 | 1932 |
| A3 + P6 | 733 | 66 | 47 | 31 / 5 | 753 | 1368 |

A3 is smaller/shallower in both like-for-like boundaries, while A2 retains the
lower K2 maximum-fanout proxy. No semantic equivalence or physical win follows
from these structural numbers.
