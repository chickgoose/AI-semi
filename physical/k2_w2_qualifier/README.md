# K2 physical W2 raw-report qualifier

This independent parser converts a hash-bound Genus/Innovus raw artifact bundle
into a canonical receipt. It targets the locally recorded server builds Genus
`23.14-s090_1` and Innovus `23.14-s088_1`.

Production PASS requires all declared sources, tool executables, commands,
constraints, technology inputs, raw reports, netlists, smoke evidence, and
clean markers to match their SHA-256 references as stable regular files. The
ordered RTL file list must equal the source closure and contain the declared
top. Tool commands and their environment are exact, and both tool exits must be
zero.

Raw reports must contain the following exact machine records in addition to
their normal human-readable text:

- `W2_DESIGN`: zero unresolved/blackbox/unmapped and nonzero mapped instances;
- `W2_COVERAGE`: exactly the six classes `unconstrained_paths`, `no_clock`,
  `no_input_delay`, `no_output_delay`, `no_drive`, and `no_load`, all zero;
- `W2_TIMING`: setup, hold, recovery, and removal with nonzero path coverage,
  zero violations, nonnegative WNS and TNS;
- `W2_SCAN_ICG` and `W2_ICG`: no scan/dangling/unrecognized objects and exact
  expected ICG inventory at both mapped and placed boundaries;
- mapped smoke: nonzero vectors/events, conservation, no mismatch or X/Z;
- Innovus placement, DRC, connectivity, and antenna summaries, all clean.

The separate clean marker must equal the final nonempty tool-log line and bind
the run ID and top. Error/fatal diagnostics anywhere in the text artifacts are
fatal. Warning lines are counted in the receipt but do not become physical
claims.

Run from the repository root:

```sh
python3 physical/k2_w2_qualifier/qualify_raw.py \
  --bundle-root /path/to/run-bundle \
  --manifest /path/to/run-bundle/manifest.json \
  --output /writable/new-result.json
```

The output path is exclusive: an existing file or symlink is never overwritten.
W2 qualifies raw Genus/Innovus structural/timing/implementation gates only.
Activity-annotated power, energy/event, signoff STA, and foundry signoff DRC
remain HOLD.
