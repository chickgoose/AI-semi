# Frozen common activity for the canonical staged three

This directory supplies only TB bindings and activity production.  It does not
modify `aer_clean_tb`, the official manifests, or any candidate/staged RTL.

The three bindings instantiate the canonical staged tops directly:

- `w2_fovea_r1_physical_staging_top`
- `w2_a2_p6_physical_staging_top`
- `w2_a3_p6_physical_staging_top`

All use N16, 16-bit address-only scoreboard fields, two retire lanes, FIFO0,
an always-ready sink, a 10 ns ref clock, and a phase-related 10 ns sample clock
whose first rise is 7.5 ns.  The binding contains no request/retire queue.

`run_xcelium_activity.sh` accepts an exact generated JSONL trace and its run
manifest.  The frozen TB's `measurement_active` edges define the VCD window;
reset/warm-up and candidate-dependent drain are excluded.  It emits the common
summary/event CSV, raw and zero-rebased DUT-scope VCD, per-net SAIF, clock/window
text, and SHA-256 inventory covering the trace, manifests, frozen TB, binding,
staged source closure, and activity artifacts.

The frozen TB holds `measurement_active` through the service edge after the
last offered occurrence.  Consequently the activity interval is exactly 4097
reference periods for the official 4096-cycle trace.  The rebasing tool records
both counts and fails unless `activity_window_ref_cycles` equals the common
summary's `measurement_cycles + 1`; it does not relabel the longer power window
as a 4096-cycle interval.

`run_three_xcelium_activity.sh NEW_OUTPUT_ROOT` generates the official full50
trace set and uses the exact `mixed_phase_always_ready_identity` v4 trace for
all three candidates.  Set `AER_STAGED_ROOT` only when the staged RTL worktree
is not `/tmp/k2-phys-w2-techmap`; the selected staged filelist and every source
are resolved, checked as regular non-symlink files, and hashed into the output.

The server environment receipts in `physical/k2_w2_server_env` are inputs to a
later campaign and are not regenerated or changed here.

`scale_vcd_timestamps.py` is the offline, no-EDA conversion boundary for a
validated 10 ns common-activity VCD.  It streams an exact `1/2` timestamp
transform to a new 5 ns VCD, rejects nonintegral or malformed timelines and
unsafe filesystem inputs, and emits a deterministic JSON receipt binding the
upstream `activity.sha256.txt` validation, input bytes, output bytes, converter,
and fixed scale.  It never rewrites the validated source VCD in place.
