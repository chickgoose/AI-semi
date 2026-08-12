# A8 independent A2/A3 K2 wrapper black-box suite

This A8-owned suite compiles owner RTL without editing either owner worktree.
It checks the externally visible atomic K2 contract and deliberately avoids
hierarchical peeks into policy state.

The A2 source is the exact Git blob at `d74ff962aaf07c5209f1a1d1c69832735c654a0d`.
The A3 scheduler is the exact Git blob at
`bd1c1ee955685fc077afe930116a03bc49a8218f`; its emerging normalization wrapper
is explicitly bound as an uncommitted worktree file by SHA-256. Therefore the
A3 result is executable development evidence, not release provenance, until
the owner commits that wrapper unchanged.

Checks cover exact flattened global address order for one full N16 cohort,
atomic held-offer stability, a legal later source-4 occurrence coexisting with
another pending source, reset of a held bundle, post-reset stale suppression,
acknowledgement bijection, and final drain. A3 has no native drain output, so
its drain check is honestly limited to black-box quiescence
`source_valid==0 && retire_valid==0`.

Six A8 observation mutants per owner must fail: lane swap, duplicate lane,
second acknowledgement drop, premature drain, stale replay, and reset leak.
These target failures that an unordered or purely aggregate common scoreboard
can miss. The mutation shim is outside the baseline owner hierarchy and is
never candidate functionality.

Run with the unlicensed local Verilator proxy:

```sh
tests/w8_k2_wrapper_blackbox/run_all.sh
```
