# A9 W4 tournament

`w4_tournament.py` performs an analytical, exact-input-commit, read-only
full50/cap22 cycle tournament.  It is not an executed A4-RTL→A7-RTL composition
and does not model the composed reset path.  `REPORT.md` records the model
contract, complete metrics, boundary failure, and Pareto decision;
`A9_W4_FIX_SUMMARY.md` records the address-only audit correction.
`test_w4_tournament.py` locks the A4 replay
anchors, conservation across the link, zero-buffer rule, non-free DDR costs,
and R=2/4 fail-closed behavior.

The script writes only its explicit output and secure `/tmp` materializations.
It refuses to replace an existing output.  It never checks out or modifies A4,
A7, A1, common manifests/TB, or existing candidate RTL.
