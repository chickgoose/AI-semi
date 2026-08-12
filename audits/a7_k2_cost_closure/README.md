# A7 K2 normalized plus charged-P6 cost closure

This A7-owned audit combines five committed generic structural receipts: the
A2/A3 normalized scheduler receipts from `89f8eb6` and the full A2/A3 plus P6
receipts reproduced from integration commits `e1d5598` and `599f24c`, plus an
isolated P6 seam receipt from A7 commit `747db00`.

Both normalized candidates use one boundary and both integrated candidates use
one full-link boundary. All four use the same Yosys recipe and executable. The
full-link boundary observes P6 pins, final retire lanes, protocol error, and
drain, so the endpoint and required A2 elastic adapter cannot be optimized out.
A2 charges 11 adapter state bits plus P6's 40; A3 charges a zero-state admission
seam plus the same 40-bit P6 endpoint.

The report keeps three costs distinct: normalized common-seam metrics, the
isolated P6 seam, and the full composition. Adapter state is closed by
`full - normalized - isolated P6` and must equal 11 bits for A2 and zero for
A3 in both generic and mapped state. Combinational cells are deliberately not
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
A7_K2_COST_CLOSURE_TEST_PASS receipts=5 physical=HOLD
```

Physical area, power, energy/event, Fmax, CDC/RDC, half-cycle timing, and P&R
remain **HOLD** because none is present in a qualified receipt.
