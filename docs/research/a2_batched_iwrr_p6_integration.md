# A2 Batched-IWRR K2 to P6 digital integration

Status: **digital RTL PASS; physical implementation and CDC signoff HOLD**.

## Pinned ownership and frozen boundaries

The scheduler RTL is an exact byte-for-byte import from A2 owner commit
`d74ff962aaf07c5209f1a1d1c69832735c654a0d`, Git blob
`8ea7be42b4fe4fbcb414ff1947ddeabbcbf9ec85`, SHA-256
`800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d`.
The import is not edited or wrapped with replacement policy logic.

The integration reuses the committed A7 P6 exact-pair endpoint and its atomic
bundle frontend.  No common RTL, common testbench, constraint, or physical
asset is changed.

## Digital seam and charged state

`a2_batched_iwrr_p6_top` places one explicit elastic register between A2 and
P6.  Its 11 charged state bits are bundle valid, two-bit count, and two ordered
four-bit addresses.  When the endpoint consumes an old record, the register can
capture the next whole A2 bundle on the same edge.  `link_enable_i` provides a
digital quiesce/stall control; while low, a full register backpressures A2 and
P6 sees an invalid count-zero input.

The scheduler commit is exactly `nonzero grant && buffer capacity`.  The P6
side only observes registered valid/count/addresses.  Consequently endpoint
ready cannot depend on the scheduler's current combinational grant, and there
is no scheduler-to-link-to-scheduler combinational loop.  There is no hidden
testbench queue or uncharged synthesizable state.  Combined drain requires the
A2 pending/held state, elastic register, and P6 endpoint all to be idle.

## RTL qualification

Run `tests/a2_batched_iwrr_p6/run_all.sh`.  The directed RTL test covers
continuous K2 traffic, count zero/one/two, an extended link stall, drain,
reset/rearm, ordered pair retirement, and acceptance/retirement conservation.
It also checks the persistent-demand IWRR row count `[1,5,5,1]` over one full
12-token calendar and verifies the pinned owner SHA-256 before compiling.

This is digital phase-related RTL evidence only.  The P6 clock/data boundary
still requires characterized ICG/DDR cells, implementation timing, reset
recovery/removal analysis, and CDC signoff.  No physical readiness, PPA, or CDC
closure claim is made here.
