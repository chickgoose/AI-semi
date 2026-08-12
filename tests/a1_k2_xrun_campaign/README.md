# A1 local Xcelium K2 campaign

`scripts/a1_k2_xrun_campaign.py` is an A1-owned, non-destructive local
orchestrator. It does not log in to or launch anything on the shared server.
The caller supplies the local `xrun` executable and every candidate-facing
compile input explicitly.

Example (paths are illustrative):

```sh
python3 scripts/a1_k2_xrun_campaign.py \
  --candidate a3-k2 \
  --top aer_clean_tb \
  --candidate-filelist /path/to/a3-k2-candidate.f \
  --tb-filelist tb/clean/files.f \
  --define AER_CLEAN_A3_K2 \
  --param aer_clean_tb.NUM_SOURCES=16 \
  --param aer_clean_tb.ADDR_WIDTH=16 \
  --param aer_clean_tb.RETIRE_LANES=2 \
  --suite full50 --suite capacity22 \
  --xrun /path/to/xrun \
  --generator benchmarks/clean_slate_aer/generate_trace.py \
  --preparer benchmarks/clean_slate_aer/prepare_sv_trace.py \
  --full50-manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  --capacity22-manifest benchmarks/clean_slate_aer/manifest.multilane-n16.json \
  --project-root "$PWD" \
  --output-root /path/to/new/local-results
```

Use `--no-defines` instead of `--define` when the candidate intentionally has
no preprocessor defines. Filelists are deliberately restricted to source paths
and nested `-f PATH` entries. Compile options, defines, and parameters hidden in
a filelist are rejected. Relative filelist entries resolve from
`--project-root`, matching the repository's filelists.

For each invocation the script creates a random, never-reused attempt path. It
generates and verifies the selected frozen generator-v4 suites, prepares every
trace, elaborates exactly one snapshot, runs `basic_reset_drain` first, and then
runs all selected traces from that snapshot. Every run has a separate directory
and `run.log`; compilation has separate `compile.log` and console evidence.

`campaign.receipt.json` is published only after exact PASS-marker checks,
Xcelium fatal/error scans, nonempty private result checks, freshness checks, and
post-run input SHA verification. `campaign.receipt.sha256` binds the receipt
itself. A failed attempt is retained with `campaign.failure.json`, but never a
PASS receipt.

The unit tests use only committed fake tools; they do not invoke Xcelium:

```sh
python3 -m unittest discover -s tests/a1_k2_xrun_campaign -p 'test_*.py' -v
```
