# Result recording format

Generated run data lives under `results/runs/<run-id>/` and should normally be
kept out of Git. The active flow measures the selected baseline:

```text
results/runs/<run-id>/
├── baseline/{synth,sta,power}/   # STA/power appear when drivers are configured
└── summary.tsv
```

Each stage directory contains:

- `manifest.txt`: commit, top, file list, SDC, corner, clock, and activity input.
- `tool.log`: complete stdout/stderr from the EDA wrapper.
- `metrics.tsv`: normalized values produced by the site-specific wrapper.
- Native reports/netlists: use descriptive names; do not commit licensed data.

`metrics.tsv` has exactly three tab-separated columns:

```text
metric	value	unit
cell_area	1234.50	um2
wns	0.125	ns
tns	0.000	ns
fmax	222.22	MHz
total_power	1.234	mW
dynamic_power	0.900	mW
leakage_power	0.334	mW
```

Record `N/A` rather than silently omitting an unavailable metric. Keep the tool
version, PDK/library, operating corner, RTL parameters, clock and I/O
constraints, activity workload, and power-analysis mode fixed between runs.
Keep generated results, PDKs, standard-cell source, license files,
waveforms and credentials out of Git; commit only reviewed summary tables when
the team agrees.

Simulation writes one CSV per workload under `results/sim/`. Its columns cover
accepted/emitted/error counts, average and maximum latency, throughput, Jain's
fairness index, and maximum observed wait.

`summary.tsv` contains the selected baseline metrics:

```text
design  area_um2  wns_ns  tns_ns  fmax_mhz  total_power_mw  dynamic_power_mw  leakage_power_mw
baseline  ...
```

The rejected buffered round-robin comparison is preserved in
`docs/tasks/a2.md`, the `a2` branch, and Git history.

`fmax` is derived as `1000 / (clock_period_ns - WNS_ns)`. Report parsing emits
`N/A` when a Genus format is not recognized; inspect and update the extractor
instead of substituting a guessed value. Confirm the power unit printed by the
server report before publishing the normalized mW values.

For the current GPDK045 Genus reports, QoR timing values are parsed as ps and
converted to ns. The power `Subtotal` columns are parsed as Leakage, Internal,
Switching, and Total in W, then converted to mW; dynamic power is
`Internal + Switching`. Summary generation fails if any required metric is
missing, `N/A`, duplicated, or non-numeric.
