# A23 full scheduler + actual P6 digital replay

This additions-only package executes the exact generator-v4 `full50` traces
against the existing A2, A3, and A4 integrated scheduler-plus-P6 RTL tops.
`capacity22` is an exact name-and-trace-SHA-checked subset view of those same
executions; it contributes zero additional samples. Each owner also runs the
common reset/drain scenario and five separately compiled real-RTL mutations:
drop, duplicate, ordered-pair swap, policy microstep corruption, and reset
phantom.

The three owner wrappers are combinational observers with zero state bits.
They reconstruct source acceptance only from the actual public atomic bundle
commit, committed count, and ordered grant addresses. They never use a
source-ready level as acceptance and never instantiate the ordered-link
adapter. The shared testbench globally queues those accepts and scores the
actual P6 `retire_valid` and addresses for conservation, ordering, phantom,
duplicate, reset, occurrence-to-accept latency, accept-to-retire latency, and
fixed-window retirement.

## Immutable publication

Publication uses two commits because a SHA-256 manifest cannot contain its
own byte hash without a fixed-point construction. Commit 1 contains this
package, including `pins.json`; the runner refuses a dirty or untracked package
and records the exact commit-1 Git identity plus the SHA-256 of `pins.json` in
the result. `pins.json` pins every actual owner, integrated top, P6 RTL source,
file list, observation wrapper, common TB, generator, preparer, manifests,
runner, launcher, and both Verilator executables. Commit 2 adds only the
byte-reproducible result and publication receipt. Thus the committed result
pins the complete immutable package, while the package manifest pins every
executable input without claiming the impossible self-hash.

Run from a clean commit with:

```sh
tests/a23_full_p6_replay/run_all.sh
```

The result deliberately keeps physical implementation and CDC/RDC at `HOLD`.
