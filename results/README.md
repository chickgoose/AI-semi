# Result recording format

Generated run data lives under `results/runs/<run-id>/` and should normally be
kept out of Git. A run compares both designs with one configuration:

```text
results/runs/<run-id>/
├── baseline/{synth,sta,power}/
├── improved/{synth,sta,power}/
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

Record `N/A` rather than silently omitting an unavailable metric. Use the same
tool version, PDK/library, operating corner, RTL parameters, clock and I/O
constraints, activity workload, and power-analysis mode for baseline and
improved. Keep generated results, PDKs, standard-cell source, license files,
waveforms and credentials out of Git; commit only reviewed summary tables when
the team agrees.

Simulation writes one CSV per workload under `results/sim/`. Its columns cover
accepted/emitted/error counts, average and maximum latency, throughput, Jain's
fairness index, and maximum observed wait.
