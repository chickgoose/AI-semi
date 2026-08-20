# Phase-4 occurrence baseline qualification

This directory qualifies the fixed UZH `shapes_rotation` window with a
synthesizable six-lane, sixteen-source, depth-three occurrence boundary.  The
qualification is deliberately a 102-bit wide correctness baseline, not the
phase-5 codec or a PPA candidate.

Prepare the immutable source-bound stimulus from repository root:

```sh
python3 tests/mc_wtb_occurrence_baseline/prepare.py \
  --pose-join /path/to/qualified-pose-join \
  --join-spec benchmarks/redred_uzh_shapes_pose_join/join_spec.json \
  --a23-archive tests/a23_full_single_edge_replay/public_projected_export.tar.gz \
  --output-dir /new/empty/stimulus-directory
```

The server Xcelium run uses a 6.5 ns clock and `-timescale 1ps/1ps`.  Events
are admitted on `ceil((timestamp-start)/6.5ns)`, so none is presented before
its original integer-nanosecond timestamp.  The raw transcript is independently
checked against a separate A2 scheduler/queue model; shifting ingress, accept,
or retire cycles does not pass by merely rehashing the log.

```sh
python3 tests/mc_wtb_occurrence_baseline/inspect_run.py \
  --source-records /path/source_records.jsonl \
  --stimulus /path/stimulus.txt \
  --stimulus-manifest /path/stimulus_manifest.json \
  --raw-log /path/raw.log --status /path/status.txt \
  --simulator-log /path/xrun.log \
  --implementation-commit FULL_40_HEX_COMMIT --run-id UNIQUE_RUN_ID \
  --output-dir /new/empty/inspection-directory
```

`corner_tb.sv` separately exercises depth-three fill, full-bank same-edge pop
credit, old-before-new ordering, clean drain, and explicit no-pop overflow.
`genus_elaborate.tcl` is only an HDL/library elaboration smoke.  Neither result
is mapped timing, area, power, Innovus, codec, wire-width, or novelty evidence.
