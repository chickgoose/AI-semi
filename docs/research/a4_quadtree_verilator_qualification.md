# A4 frozen-46 Verilator RTL qualification

Status: local RTL qualification complete, `PENDING_HEAD_XCELIUM`, 2026-08-07

## Qualification boundary

Verilator 5.032 was reused from the non-server `/tmp/a7-sim-bin/verilator`
package. No common SSH/tmux pane or server file was touched. The frozen common
TB, assertions, generator, manifest, and golden file were not modified. A4 adds
only its candidate replacement binding, RTL/filelist, properties, and runner.

The actual top is the unchanged `aer_clean_tb`. The candidate runner compiles
the frozen interface/assertions/TB plus either the A4 candidate filelist or the
common one-slot flat RR mock. It builds once, then prepares and executes every
exact manifest trace:

```bash
python3 tests/a4/run_verilator_46.py \
  --design a4 \
  --verilator /tmp/a7-sim-bin/verilator \
  --output /tmp/a4-verilator-46 \
  --trace-dir /tmp/a4-verilator-traces

python3 tests/a4/run_verilator_46.py \
  --design flat \
  --verilator /tmp/a7-sim-bin/verilator \
  --output /tmp/a4-flat-verilator-46 \
  --trace-dir /tmp/a4-verilator-traces
```

The final A4 rerun used a fresh build after the phase-width cast cleanup. Its
four combined result checksums were bit-for-bit identical to the first run, and
the build log contained no Verilator warnings.

## Correctness result

| Candidate | Frozen runs | Scoreboard PASS | errors | accepted after drain | Event CSV rows |
| --- | ---: | ---: | ---: | --- | ---: |
| A4 quadtree RTL | 46 | 46 | 0 in every run | accepted=delivered in every run | 87,000 |
| flat one-slot RR RTL | 46 | 46 | 0 in every run | accepted=delivered in every run | 87,000 |

All A4 node properties passed: child ready is one-hot-or-zero, a full stalled
node acknowledges no child, and output valid/event/source/age remain stable
under stall. Common assertions additionally passed conservation, corruption,
duplicate, source-local ordering, drain, and quiet-guard checks.

The full A4 event CSV SHA-256 is
`0e125e6e58698fc5caf11c79a2a69be76b9f2fada2209ca940777cd0de033ce9`;
the flat event CSV SHA-256 is
`91695dd2f2f7fb2a0bc18862ad977a9b3dcec98d0446a4fc88336611c257c4f0`.
The committed gzip files decompress to those exact byte streams.

## RTL versus reference model

All 46 A4 runs and all 46 flat runs have exact agreement for:

- generated, overrun, accepted, and delivered counts;
- maximum request wait;
- trace identity and source-local ordering; and
- measurement throughput, within at most `5e-7` from six-decimal RTL CSV
  formatting.

RTL p95 and p99 occurrence-to-delivery latency are exactly one cycle greater
than the first-pass model on all 92 comparisons. The model injected and
handshook at one abstract cycle boundary, while the frozen TB records an
occurrence on the drive negedge and observes completion on its clocked
scoreboard boundary. Because transport counts and request wait agree exactly,
this is a measurement-origin offset, not an RTL pipeline error. All following
tables use RTL/common-TB numbers and supersede the model tables.

## Topology profit and loss from actual RTL

`epc` is fixed-window completion/cycle. Fairness is demand-normalized; `min
ratio` is the minimum source acceptance/offered ratio.

| Trace | Candidate | overrun | epc | p99 | max wait | fairness | min ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| matched local | A4 | 0 | 0.750000 | 6 | 0 | 1.00000 | 1.00000 |
| matched local | flat | 0 | 0.750000 | 5 | 3 | 1.00000 | 1.00000 |
| matched dispersed | A4 | 0 | 0.750000 | 6 | 2 | 1.00000 | 1.00000 |
| matched dispersed | flat | 0 | 0.750000 | 5 | 3 | 1.00000 | 1.00000 |
| local mirror | A4 | 0 | 0.750000 | 6 | 0 | 1.00000 | 1.00000 |
| local mirror | flat | 0 | 0.750000 | 5 | 3 | 1.00000 | 1.00000 |
| multi dispersed | A4 / flat | 0 / 0 | 0.888184 / 0.888672 | 3 / 2 | 0 / 0 | 1 / 1 | 1 / 1 |
| multi row | A4 / flat | 0 / 0 | 0.888184 / 0.888672 | 3 / 2 | 0 / 0 | 1 / 1 | 1 / 1 |
| multi column | A4 / flat | 0 / 0 | 0.888184 / 0.888672 | 3 / 2 | 0 / 0 | 1 / 1 | 1 / 1 |
| rotating victim identity | A4 | 150 | 0.991943 | 12 | 13 | 0.99983 | 0.93907 |
| rotating victim identity | flat | 215 | 0.976318 | 7 | 10 | 0.99980 | 0.92832 |
| rotating victim affine | A4 | 149 | 0.991943 | 12 | 12 | 0.99984 | 0.94318 |
| rotating victim affine | flat | 212 | 0.977295 | 7 | 7 | 0.99984 | 0.93156 |

Interpretation:

- Local and mirror placements save three cycles of acceptance wait versus flat,
  while the registered quadtree costs one p99 delivery cycle. Mirror is exactly
  neutral.
- Dispersed placement saves only one acceptance-wait cycle and still pays the
  one-cycle delivery cost. It is weaker than local placement, as expected from
  leaf contention and root serialization.
- Frozen row, column, and dispersed moving-hotspot traces do not build backlog;
  topology provides no capacity benefit and A4 loses one latency cycle plus a
  two-delivery measurement-window edge effect.
- Under rotating-victim overload, distributed slots improve accepted work:
  identity overrun falls 215 to 150 and affine 212 to 149. The price is p99 +5
  cycles and maximum wait +3/+5. Minimum service ratio improves in both maps.
- Elephant/mouse and retrigger identity/affine pairs are lossless and identical
  within each candidate; A4 only pays its one-cycle latency stage.

The identity/affine and identity/mirror results do not justify address-dependent
rotation or a per-level phase offset. The existing transfer-driven local RR is
retained. No A7 compactor, A9 token/ring, or other forbidden mechanism was
introduced.

## Rate shape and uniform sweep

For rate-shape burst sizes 1/4/16, both candidates preserve every event. A4
p99 is 3/6/18 versus flat 2/5/17; A4 maximum acceptance wait is 0/2/12 versus
flat 0/3/15. Thus distributed capture reduces burst admission wait, while the
additional tree level remains visible in delivery latency.

Three-seed uniform means:

| load | candidate | overrun | epc | p99 | fairness | min ratio |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 0.125 | A4 / flat | 0 / 0 | 0.120931 / 0.120931 | 3 / 2 | 1 / 1 | 1 / 1 |
| 0.5 | A4 / flat | 0 / 0 | 0.496419 / 0.496582 | 3 / 2 | 1 / 1 | 1 / 1 |
| 0.9 | A4 / flat | 0 / 0 | 0.903320 / 0.903808 | 3 / 2 | 1 / 1 | 1 / 1 |
| 1.0 | A4 / flat | 0 / 0 | 0.999023 / 0.999512 | 3 / 2 | 1 / 1 | 1 / 1 |
| 1.25 | A4 / flat | 500.7 / 502.7 | 0.999023 / 0.999512 | 19 / 11.3 | 0.99812 / 0.99890 | 0.74129 / 0.75867 |
| 1.5 | A4 / flat | 1003.7 / 1005.7 | 0.999023 / 0.999512 | 21 / 14 | 0.99780 / 0.99824 | 0.61749 / 0.62642 |
| 2.0 | A4 / flat | 2037 / 2040 | 0.999023 / 0.999512 | 21 / 16 | 0.99783 / 0.99827 | 0.46274 / 0.47215 |

The one-lane root fixes the same saturation knee. A4 saves only 2--3 overruns
per seed above saturation and has worse overload tail latency/fairness. Its
stronger result is rotating-victim and timing-pair admission, not uniform
throughput.

## N=16/64 structural scaling

These are deterministic structural proxies generated by
`tests/a4/quadtree_scaling.py`, not synthesized N=64 measurements. The committed
top is frozen at N=16; N=64 applies the same radix-4 node/state contract for one
additional level. Grid wire length is Manhattan distance between hierarchical
cluster centers.

| N | structure | levels / registered stages | merge nodes | state bits | fan-in | balanced mux depth per stage | longest local wire | control bit-grid | full-channel bit-grid |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | A4 | 2 / 2 | 5 | 155 | 4 | 2 | 2 | 48 | 720 |
| 16 | flat | 0 / 1 | 1 | 25 | 16 | 4 | 3 | 64 | 704 |
| 64 | A4 | 3 / 3 | 21 | 693 | 4 | 2 | 4 | 224 | 3584 |
| 64 | flat | 0 / 1 | 1 | 29 | 64 | 6 | 7 | 512 | 6144 |

At N=16, A4 control wire is 25% lower but full-channel proxy is 2.3% higher
because payload crosses two registered levels. At N=64, control proxy is 56.3%
lower and full-channel proxy 41.7% lower, with maximum local span 4 rather than
7 and fixed four-way merge depth. A4 pays 693 versus 29 state bits and three
cycles of sparse registered transport. This is the physical scaling hypothesis
that later Genus/Innovus must validate.

## Evidence files

- [A4 per-trace RTL metrics](results/a4_verilator_46.csv)
- [flat per-trace RTL metrics](results/a4_flat_verilator_46.csv)
- [A4-vs-flat 46-trace join](results/a4_vs_flat_verilator_46.csv)
- [A4 RTL/model deltas](results/a4_rtl_model_comparison.csv)
- [flat RTL/model deltas](results/a4_flat_rtl_model_comparison.csv)
- [N=16/64 scaling CSV](results/a4_scaling_n16_n64.csv)
- [A4 common aggregate](results/a4_verilator_aggregate.csv)
- [A4 common event-run metrics](results/a4_verilator_event_runs.csv)
- [A4 combined summary](results/a4_verilator_summary_all.csv)
- [A4 87,000-row event CSV, gzip](results/a4_verilator_events_all.csv.gz)
- [A4 per-run trace/result checksums](results/a4_verilator_run_manifest.csv)
- [flat 87,000-row event CSV, gzip](results/a4_flat_verilator_events_all.csv.gz)

Xcelium remains `PENDING_HEAD_XCELIUM`. Verilator establishes local RTL/common-TB
eligibility; it does not authorize server simulation, Genus, or Innovus.
