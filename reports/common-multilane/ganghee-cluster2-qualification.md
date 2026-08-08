# Ganghee cluster2 common multi-lane qualification

Date: 2026-08-08

## Frozen evidence for this run

- Candidate top: `aer_tx16_trad_rowcol_fovea_cluster2`
- Server source: `~/redred-faer/rtl/aer_tx16_trad_rowcol_fovea_cluster2.v`
- Candidate SHA-256: `97151241b642d5db1c5974233439dfcea14c4ec325b1d3e91c9caa9d4917c44a`
- Common manifest: `benchmarks/clean_slate_aer/manifest.multilane-n16.json`
- Manifest SHA-256: `0827ca7a03e81e03de4547e67df9161da7ddffef4eae40975dbbd10d6a5bd52c`
- Stateless binding: `tb/clean/native/aer_ganghee_cluster2_binding.sv`
- Binding SHA-256 on the server: `0711ba537b530321d06618ad593576a12693a4c23c6eb392dbcd02be59ce485b`
- Simulator: Xcelium 23.09-s013

The binding expands `valid0,row0,col_mask0` and
`valid1,row1,col_mask1` into at most eight normalized address completions. It
adds no queue, arbitration, grant state, or backpressure compensation and does
not modify Ganghee's RTL or legacy testbench.

## Correctness result

- Exact common traces: 18
- PASS logs: 18/18
- Generated: 46,200
- Accepted: 42,204
- Delivered: 42,204
- Source-overrun observations: 3,996
- Loss/duplicate/phantom/reorder errors after acceptance: 0

Results:

```text
results/common-multilane-candidates/*/ganghee-cluster2-n16-seed1/
results/common-multilane-candidates/ganghee-cluster2-n16-seed1.summary.csv
```

## Same-trace capacity comparison

| Candidate | Uniform offered load | Overrun ratio | Throughput (event/cycle) | Average E2E | Worst E2E |
|---|---:|---:|---:|---:|---:|
| DREC prefix K4 | 1.00 | 0.0000 | 0.9995 | 2.000 | 2 |
| Ganghee cluster2 | 1.00 | 0.0602 | 0.9393 | 2.000 | 2 |
| Ganghee fovea | 1.00 | 0.0602 | 0.9393 | 2.000 | 2 |
| Hyeonsu rotation-priority | 1.00 | 0.0000 | 0.9995 | 2.000 | 2 |
| DREC prefix K4 | 1.25 | 0.0000 | 1.2461 | 2.000 | 2 |
| Ganghee cluster2 | 1.25 | 0.0769 | 1.1502 | 2.063 | 3 |
| Ganghee fovea | 1.25 | 0.2014 | 0.9941 | 4.198 | 66 |
| Hyeonsu rotation-priority | 1.25 | 0.1969 | 0.9995 | 5.149 | 25 |
| DREC prefix K4 | 1.50 | 0.0000 | 1.4932 | 2.000 | 2 |
| Ganghee cluster2 | 1.50 | 0.0957 | 1.3503 | 2.110 | 3 |
| Ganghee fovea | 1.50 | 0.3294 | 0.9993 | 6.206 | 78 |
| Hyeonsu rotation-priority | 1.50 | 0.3292 | 0.9995 | 7.109 | 25 |
| DREC prefix K4 | 2.00 | 0.0000 | 1.9990 | 2.000 | 2 |
| Ganghee cluster2 | 2.00 | 0.1250 | 1.7490 | 2.160 | 3 |
| Ganghee fovea | 2.00 | 0.4985 | 0.9995 | 8.902 | 177 |
| Hyeonsu rotation-priority | 2.00 | 0.4984 | 0.9995 | 9.877 | 26 |

The complete four-candidate aggregate is stored on the server at:

```text
results/common-multilane/four-candidate.summary.csv
```

## Interpretation limits

- The 18 common traces are sink-always-ready. Cluster2 has no output-ready
  input, so it is not qualified for independent lane stalls or backpressure.
- Cluster2 accepts only after its registered native bitmap returns. Consecutive
  occurrences at one source can therefore overrun even below its aggregate
  output capacity; the overrun is reported rather than hidden.
- Cluster2 can emit up to eight events only when the chosen center and
  peripheral rows contain four active columns each. It is not an arbitrary
  eight-source selector.
- Cluster2 uses address-only row/bitmap semantics. DREC and the Hyeonsu design
  expose different payload, ready, buffering, and pin capabilities, so their
  current Genus areas are not directly comparable.
- Ganghee's source was uncommitted at qualification time. The SHA above is the
  run identity; a Git commit should be frozen before a team decision.
- Genus vectorless power is not workload energy/event, and Ganghee's current
  clock-gating P&R runs are under requalification. No post-route Fmax or final
  physical winner is claimed here.

## Reproduction

Create an absolute file list containing `arbiter2.v`, `arbiter4_tree.v`, and
`aer_tx16_trad_rowcol_fovea_cluster2.v`, then run:

```bash
cd ~/AI-semi/integration
setenv AER_GANGHEE_CLUSTER2_TOP aer_tx16_trad_rowcol_fovea_cluster2
setenv AER_GANGHEE_CLUSTER2_FILELIST /absolute/path/to/ganghee-cluster2.f
bash scripts/run_common_multilane_candidate.sh ganghee-cluster2
```
