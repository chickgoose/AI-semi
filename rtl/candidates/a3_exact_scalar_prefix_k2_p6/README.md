# A3 exact scalar-prefix K2 to P6 integrated digital top

Status: **digital RTL only**. Physical implementation, characterized DDR/ICG
mapping, CDC/RDC, timing, power, and PPA remain **HOLD**.

`a3_exact_scalar_prefix_k2_p6_top` connects the byte-identical A3 owner
scheduler to the scheduler-neutral atomic bundle adapter and its P6 frontend
and endpoint. The owner emits a registered ordered bundle of count zero, one,
or two. At this seam, count zero is the owner's empty/invalid state; it does
not create an artificial no-op commit or a physical link cell. Counts one and
two commit atomically and `policy_microsteps_o` equals the committed count.

`link_enable_i` is a combinational admission control used to exercise and
support whole-bundle stalls. It gates valid and count together at the P6
frontend while feeding ready low to the owner. The held offer lives only in
the owner's already-charged output register. This top contains no FIFO, skid
buffer, free queue, independent retire-lane ready, or combinational feedback
path. The P6 retire boundary remains always-ready.

Integrated `drain_idle_o` is true only when both the P6 endpoint is drained and
the owner has no registered offer. `source_pending_i` is external level state,
not accepted internal work. Reset must be asserted only after drain; the owner
uses its pinned synchronous active-high reset while P6 uses its pinned
asynchronous active-low reset.

The imported owner RTL, profile, and owner file list are pinned in
`provenance.json`. Reproduce the digital checks with:

```sh
VERILATOR=/path/to/verilator YOSYS=/path/to/yosys \
  tests/a3_exact_scalar_prefix_k2_p6/run_all.sh
```
