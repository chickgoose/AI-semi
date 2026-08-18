# A23 actual A2/A3 single-edge adversarial replay

This package is an independent digital qualification suite for the complete
A2 and A3 single-edge endpoints. It does not consume the P6 replay result,
P6 expected totals, P6 latency constants, P6 mutation hooks, or P6 publication
receipt. A PASS can only be created by compiling and executing the pinned
single-edge RTL.

## Required actual RTL integration

The replay names and pins these exact integration roots and every transitive
source expanded from their filelists:

- `rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge.f`
- `rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv`
- `rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge.f`
- `rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv`
- `rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv`
- `rtl/technology/single_edge/w2_single_edge_pair_tx.sv`
- `rtl/technology/single_edge/w2_single_edge_pair_rx.sv`

The hardened RTL bytes are additionally checked against source commit
`6fc5e167918fa4c54786c9a3abb5f60ecd8b991b` and integration commit
`a0a4eb38632245db8ff5937ea5b6c6e3f3839246`. Their complete repository trees
are pinned separately, and every replay RTL/filelist byte must match in both
commits. The hardened filelist includes
`rtl/technology/single_edge/w2_single_edge_error_latch.sv`.

Until all paths, literal mutation anchors, file SHA-256 values, and tools are
locked in `pins.json`, preflight exits 3 with
`A23_FULL_SINGLE_EDGE_HOLD_NOT_RUN`. It emits no result and never converts
missing RTL into PASS.

## Actual campaign

For each of exactly A2 and A3, the runner regenerates and SHA-checks every
generator-v4 `full50` trace, compiles the actual scheduler plus actual
single-edge endpoint, and launches all 50 traces as distinct simulator
processes. The required accounting is 100 actual full50 executions, two
reset/drain executions, two clean distinct-pair mutation-activation runs, and
eight separately compiled and executed literal RTL-source mutants. There is
no receipt-only execution and no `capacity22` sample inflation.

At each logical occurrence, the TB either occupies that source's one-entry
pending latch or records `source_overrun` if already occupied. An atomic
endpoint acceptance binds a TB-only event identity to the public ordered
address stream. One global FIFO then checks the actual retire stream for
phantom, duplicate, reorder, missing retirement, and exact-once behavior:

```text
generated = source_overrun + accepted
after bounded drain: accepted = retired
```

Drain requires endpoint idle, no protocol error, no source pending, and no
accepted FIFO residue, followed by four quiet cycles. The reset artifact must
explicitly report `pre_reset_clean_drain=1` before reset can be asserted; live
unrecorded input levels during reset and disjoint pre/post epochs then detect
reset escape. Reset may not erase accepted work or clear an error into PASS.

The four mutations are literal, exact-one-anchor rewrites of the actual
single-edge endpoint source, never TB or observation-wrapper edits:

- `drop`: suppress the second retirement of a committed pair;
- `duplicate`: retire lane zero twice;
- `reorder`: exchange the two retirement addresses;
- `reset_escape`: make retire visibility survive reset.

A mutant kill requires successful compilation, real execution, nonzero exit,
the exact first diagnostic, absence of the PASS sentinel, and recorded base,
anchor, mutant, build-log, and simulation-log hashes.

Run static checks with:

```sh
python3 -m unittest tests/a23_full_single_edge_replay/test_contract.py
```

Run the actual clean-tree campaign with:

```sh
tests/a23_full_single_edge_replay/run_all.sh
```

Only a completed campaign may emit `result.json` with single-edge digital RTL
`GO`. Physical, power, and CDC/RDC remain `HOLD`.
