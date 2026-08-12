# A6 A2/A3/A4 K2 and P6 same-flow extension

Status: reproducible local Yosys structural proxy **PASS**; functional and
physical qualification **HOLD**.

This additions-only follow-up preserves every A2/A3 K2 and P6 result byte from
the earlier A6 audit and adds A4 Paired-Cortical-Column-K2 commit
`0e613b6933f1bb92e9b2f75b79a50663187f17d3` at the identical normalized
atomic K2 boundary and identical pinned Yosys recipe.

The A4 source inventory contains the exact Git blob
`rtl/candidates/a4_paired_cortical_column_k2/a4_paired_cortical_column_k2.sv`,
SHA-256 `56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185`.
Reachable hierarchy must close to that scheduler and the zero-state A6
observation wrapper only. A4's active-low reset inverter remains charged.

| K2 target | Generic cells/state | Mapped cells/state | Depth | Fanout max/p95 | Nets | Sink-pin proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A2 | 210 / 22 | 741 / 22 | 52 | 13 / 6 | 759 | 1450 |
| A3 | 283 / 34 | 650 / 26 | 43 | 20 / 5 | 667 | 1235 |
| A4 | 629 / 49 | 1794 / 49 | 102 | 33 / 6 | 1812 | 3612 |

The A4 numbers are produced by this A6 flow, not copied from the earlier A7
audit that used a different pass ordering. Existing A2/A3 numbers remain
byte-identical.

A2 and A3 retain their complete committed P6 results. There is no committed
A4+P6 integration top, so `a4_p6` is exactly JSON `null` with status
`HOLD_NO_A4_INTEGRATED_P6_TOP`. No A4 P6 cost is estimated or invented.

Run:

```sh
python3 audits/a6_a234_k2_p6_same_flow/run.py \
  --yosys /tmp/a7-toolchain/usr/bin/yosys \
  --output-dir /tmp/a6-a234-k2-p6-result
tests/a6_a234_k2_p6_same_flow/run_all.sh
```

Cells, state, depth, fanout, net count, and sink-pin count are generic digital
connectivity proxies. Liberty area, STA, routed wire, power, and all physical
PPA remain `HOLD_GENERIC_YOSYS_ONLY`.
