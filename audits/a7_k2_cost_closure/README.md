# A7 K2 normalized plus charged-P6 cost closure

This A7-owned audit combines seven committed generic structural receipts: the
A2/A3/A4 normalized scheduler receipts from `89f8eb6`, the full A2/A3/A4 plus
P6 receipts reproduced from integration commits `e1d5598`, `599f24c`, and
`602d24b`, plus an isolated P6 seam receipt from A7 commit `747db00`.

All three normalized candidates use one boundary and all three integrated
candidates use one full-link boundary. All six use the same Yosys recipe and
executable. The full-link boundary observes P6 pins, final retire lanes,
protocol error, and drain, so the endpoint and required A2 elastic adapter
cannot be optimized out. A2 charges 11 adapter state bits plus P6's 40; A3 and
A4 each charge a zero-state admission seam plus the same 40-bit P6 endpoint.

The report keeps three costs distinct: normalized common-seam metrics, the
isolated P6 seam, and the full composition. Adapter state is closed by
`full - normalized - isolated P6` and must equal 11 bits for A2 and zero for
A3 and A4 in both generic and mapped state. Combinational cells are deliberately not
split by subtraction because whole-cone ABC rewriting is not additive.

`generate_report.py` refuses an input unless it is tracked and byte-identical to
the current repository HEAD. It also rejects differing flow/tool/top boundaries,
missing or uncharged adapter/P6 components, missing charged state in the full
composition, and any area, power, energy, or Fmax value masquerading as generic
Yosys evidence. The reported full-minus-normalized values are whole-cone
structural deltas, not additive physical area or power.

Reproduce and test with:

```sh
A7_K2_COST_TEST_OUT=/tmp/a7-k2-cost-closure-new \
  tests/a7_k2_cost_closure/run_all.sh
```

Expected final marker:

```text
A7_K2_COST_CLOSURE_TEST_PASS receipts=7 physical=HOLD
```

Physical area, power, energy/event, Fmax, CDC/RDC, half-cycle timing, and P&R
remain **HOLD** because none is present in a qualified receipt.

## Committed structural result

`result.json` is generated only after all seven input receipts are committed and
byte-identical to `HEAD`.

| Boundary | A2 mapped cells/state/depth | A3 mapped cells/state/depth | A4 mapped cells/state/depth |
| --- | ---: | ---: | ---: |
| normalized common seam | 720 / 22 / 52 | 644 / 26 / 42 | 1832 / 49 / 101 |
| full scheduler + adapter + P6 | 778 / 73 / 55 | 728 / 66 / 43 | 1863 / 89 / 108 |
| adapter state residual | 11 bits | 0 bits | 0 bits |

The shared isolated P6 seam is 95 mapped cells, 40 state bits, and depth 5.
Both A2 and A3 remain on the selected structural Pareto set. At the normalized
common seam, A2 has lower mapped state (22 < 26). At the full-P6 boundary,
generic metrics favor A2 for cells (304 < 361) and state (73 < 74).
Full-P6 mapped/Pareto metrics favor A2 for maximum fanout (15 < 31) and nets
with fanout at least 16 (0 < 3); they favor A3 for mapped cells (728 < 778),
mapped state (66 < 73), depth (43 < 55), p95 fanout (5 < 6), and wire proxy
(1372 < 1514). A3 strictly dominates A4 across every listed mapped/Pareto
metric in both normalized and full-P6 cohorts, but their policy semantic grades
remain non-equivalent. No physical ranking is inferred.
