# Result recording format

Generated run data lives under `results/runs/<run-id>/` and should normally be
kept out of Git. A run compares both designs with one configuration:

```text
results/runs/<run-id>/
├── baseline/{synth,sta,power}/   # STA/power appear when drivers are configured
├── improved/{synth,sta,power}/
├── manifest-comparison.tsv
├── summary.tsv
└── comparison.tsv
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

Record `N/A` rather than silently omitting an unavailable metric. Use the same
tool version, PDK/library, operating corner, RTL parameters, clock and I/O
constraints, activity workload, and power-analysis mode for baseline and
improved. Keep generated results, PDKs, standard-cell source, license files,
waveforms and credentials out of Git; commit only reviewed summary tables when
the team agrees.

Simulation writes one CSV per workload under `results/sim/`. Its columns cover
accepted/emitted/error counts, average and maximum latency, throughput, Jain's
fairness index, and maximum observed wait.

`summary.tsv` is a one-row-per-design comparison table:

```text
design  area_um2  wns_ns  tns_ns  fmax_mhz  total_power_mw  dynamic_power_mw  leakage_power_mw
baseline  ...
improved  ...
```

`manifest-comparison.tsv` must show `match=yes` for the shared integration
commit, config/SDC/Liberty hashes, parameters, corner, clock, driver and power
mode before metrics are compared. `comparison.tsv` records improved-minus-
baseline deltas and improvement percentages. Positive percentages mean better:
area/power are lower-is-better, fmax is higher-is-better, and TNS improvement
means reduced absolute violation. WNS is reported as a delta because a percent
around zero is misleading.

`fmax` is derived as `1000 / (clock_period_ns - WNS_ns)`. Report parsing emits
`N/A` when a Genus format is not recognized; inspect and update the extractor
instead of substituting a guessed value. Confirm the power unit printed by the
server report before publishing the normalized mW values.
