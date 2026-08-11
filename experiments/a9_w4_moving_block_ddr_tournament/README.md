# A9 W4 tournament

`w4_tournament.py` performs the exact-commit, read-only full50/cap22 cycle
tournament.  `REPORT.md` records the model contract, complete metrics, boundary
failure, and Pareto decision.  `test_w4_tournament.py` locks the A4 replay
anchors, conservation across the link, zero-buffer rule, non-free DDR costs,
and R=2/4 fail-closed behavior.

The script writes only its explicit output and secure `/tmp` materializations.
It refuses to replace an existing output.  It never checks out or modifies A4,
A7, A1, common manifests/TB, or existing candidate RTL.
