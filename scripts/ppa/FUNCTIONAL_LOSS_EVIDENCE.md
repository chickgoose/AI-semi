# Functional loss evidence boundary

The yZr1 functional server result is a workspace-diff, non-official receipt.
It may be used only to compare workload loss; it is not physical evidence and
must never supply area, power, timing, Fmax, PPA or physical-ranking values.

The authoritative archive is `eval-fovea-cluster2.yZr1kmYL.tar.gz`, SHA-256
`22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39`.
Its original attempt path is recorded inside `provenance.txt` as
`/tmp/aer-eval-47e1f2f/eval-fovea-cluster2.yZr1kmYL`. The locally staged archive
defaults to `/tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz`; an identical relocated
archive can be selected with `W2_GANGHEE_FUNCTIONAL_ARCHIVE`.

Only the archive, its internal `provenance.txt`, `fovea-run.log`,
`cluster2-run.log`, `result-artifacts.sha256`, workload stem lists and ledgered
result files are accepted. The external `eval-driver-final.log` is stale,
references the old `0FfaT8kp` attempt, is not part of the archive and is
explicitly excluded.

The internal ledger validates 338 of 338 artifacts. Both candidates have 50 of
50 workload runs passing, reset-drain passing and pairwise status 0. Full50
loss accounting is:

- Fovea: generated 106416, accepted/delivered 78229, overrun 28187.
- Cluster2: generated 106416, accepted/delivered 94157, overrun 12259.

Capacity22 is a 22-stem subset of full50, not an additional workload. Its
standalone subset totals are Fovea 65616/42163/23453 and Cluster2
65616/57802/7814 for generated/accepted/overrun respectively. Tests recompute
these quantities from candidate log metrics associated with each `RUN_PASS`;
they do not trust aggregate file presence or double-count capacity22.
