# W4 A2 independent A4 moving-block qualification

This candidate-external harness pins A4 commit `850fbcf` and materializes the
exact verified RTL blob from that read-only Git object into W4 temporary
storage.  It also instantiates the same RTL with
`MAX_ADVANCE=1` as the fixed reference.  Both instances are checked every
cycle against an independently implemented Python model over exact
generator-v4 full50 and capacity22 traces.

The adapter contains no state or behavioral process.  It wires each native
event input to its mandatory source address and exposes every raw retirement
without gating.  Occurrence IDs/timestamps remain TB-only causal credits and
never enter the DUT.  Consequently source ordering means oldest unconsumed
accepted credit for that source; address-only pins cannot distinguish two
same-address occurrences internally.

Run with an executable Verilator:

```bash
W4_VERILATOR=/absolute/path/to/verilator \
W4_COMMON_ROOT=/home/chickgoose/projects/a1 \
W4_A4_ROOT=/home/chickgoose/projects/a4 \
bash w4_a4_moving_block/run_w4.sh
```

Missing tools, commit drift, input/RTL SHA drift, compilation failure, missing
PASS sentinel, any cycle mismatch, phantom/duplicate, conservation error,
source-credit order error, or drain failure terminates nonzero.
