# Cluster2/CAV two-stream bridge

This standard-library-only package keeps source sidecars and AER transport
outcomes separate. A caller supplies the full lowercase SHA-256 of a canonical
manifest; the manifest hash-binds both JSONL streams, mapping, source-registry,
pose-stream, native transport/simulator receipt, and all RTL artifacts
(including `ganghee_cluster2_top`). Each authority is an opaque unique relative
path plus full SHA-256. It also binds `aer_clock_period_ps`,
`aer_cycle_zero_timestamp_ns`, the exact ceil mapping rule, and the v2 four-view
projection contract. There is no embedded or inferred latest Ganghee digest.

This scoped existing-CAV bridge accepts only whole-nanosecond AER clocks:
`aer_clock_period_ps % 1000 == 0`. The intended bridge demonstration uses
`aer_clock_period_ps = 2000`. Fractional-nanosecond AER clocks remain **HOLD
pending a picosecond-resolution CAV interface**, and are rejected at manifest
validation before any event is loaded.

The base bridge manifest's three opaque authorities are byte-integrity bindings
when that manifest is used alone.  The pinned native receipt/bundle and the
official functional runner described below add a separate, semantic replay path;
they do not silently promote arbitrary caller-supplied manifest authorities.

`source_event` contains only raw identity and CAV sidecar fields. Its sensor ray
must have unit norm within `1e-9`; `transform_guard_valid` is mandatory; and
`event_content_sha256` is recomputed from the exact current-CAV neutral preimage
before the source row is accepted. Ray components are converted to JSON floats
for that preimage, matching current-CAV `_unit_tuple` normalization even when
the captured JSON ray uses integer tokens such as `[0,0,1]`.
`transport_outcome` contains only event/source identity, occurrence cycle,
`DELIVERED|OVERRUN`, and discriminated retire fields. The loader rejects
unknown keys, so timestamp, polarity, ray, pose, and window data cannot enter
the transport stream.

The authoritative `cluster2_steal_buf` native boundary has two registered
row/bitmap outputs: `valid0,row0,col_mask0` and
`valid1,row1,col_mask1`. A delivered row records `retire_native_lane` (0 or 1)
plus the observed `retire_row` and `retire_col` (both 0 through 3), never a
normalized scoreboard slot. The identity check is
`source_index == retire_row * 4 + retire_col`. Native lane 0 may emit rows
0, 1, or 2; native lane 1 may emit rows 0, 2, or 3. All events in one
`(cycle,native_lane)` bitmap share one row, the two valid lanes cannot select
the same row in one cycle, and `(cycle,native_lane,col)` is unique. Thus the
two native bitmaps can represent up to eight delivered events per cycle after
expansion.

The bundle loader verifies canonical bytes and authority hashes, performs an
exact event-ID partition, checks source coordinates, the bound ceil timestamp
mapping, unique bitmap occurrence slots, `occurrence_cycle <= retire_cycle`,
same-row native lane/cycle bitmaps, unique native lane/cycle/column retirement,
per-source FIFO retirement, and integer-nanosecond CAV retire timestamps. It
imports no scorer or evaluator.

Projection produces exactly:

- `RAW4X4_ALL`: every source event;
- `RAW4X4_MATCHED`: source events whose outcome is `DELIVERED`;
- `AER_OCC`: occurrence cycle plus original timestamp and source sidecars;
- `AER_RET`: occurrence timestamp, physical cycle-derived retirement timestamp,
  and latency-injected timestamp retained as three distinct values, plus the
  same source sidecars and explicit transport latency.

Projection v2 never overloads a derived timestamp name. For a whole-nanosecond
clock period `P`, `physical_retire_timestamp_ns` is
`aer_cycle_zero_timestamp_ns + retire_cycle * P`, while
`latency_injected_timestamp_ns` is
`occurrence_timestamp_ns + (retire_cycle - occurrence_cycle) * P`.
`latency_cycles` and `latency_ns` record the latter interval separately. Every
`AER_RET` row binds this interpretation with the exact label
`TRANSPORT_LATENCY_INJECTION_NOT_PHYSICAL_REPLAY`. Native cycles are limited to
`0..2^63-1`; serialized timestamps and nanosecond latency values are limited to
`0..2^64-1`. The ambiguous v1 field `derived_retire_timestamp_ns` is absent.

`AER_RET` rows are deterministic native retirement order:
`(retire_cycle, retire_native_lane, retire_col, event_id)`. RAW diagnostics and
`AER_OCC` remain in source ordinal order.

The implementation asserts exact `(event_id, source_index)` equivalence between
`RAW4X4_MATCHED` and `AER_OCC` before returning the views.

`AER_OCC` and `AER_RET` are observational joins for a later adapter. Every row
and the manifest projection contract carries the exact label
`SOURCE_EVENT_OBSERVATIONAL_JOIN_NOT_AER_PAYLOAD`. They are not asserted to be
wire-complete AER payloads or direct CAV inputs.

## Pinned Ganghee native observation

`ganghee_cluster2_native_authority.json` is canonical JSON for the public
Ganghee repository `https://github.com/GangHeeJo/AI-SEMI` at commit
`5ac1f0e3c0e6991558afa699e64680f708ff625d`. It binds the full SHA-256 and
repository-relative path of the unmodified Cluster2 steal-buffer RTL,
`arbiter2`, `arbiter4_tree`, the strict native phantom-debug TB, the UZH
cyclemask converter, and the tracked UZH cyclemask. It also binds the actual
native boundary: 16-bit `arrival`/`overrun` and two registered
1/2/4-bit `valid,row,col_mask` lanes with up to eight delivered events per
cycle. The authority also records the exact single-valid row sets and all six
legal two-valid row pairs; the TB and independent parser reject broader
lane-row combinations that the pinned RTL cannot produce.
The tracked cyclemask has two explicitly accepted raw byte encodings: LF SHA-256
`850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea`
and CRLF SHA-256
`a50866f95430e3fe8d8af775c2e9692353e1e6bc9a1ecfedfed620143be48313`.
Both bind the one canonical semantic-LF SHA-256 `850049...25ea`. Mixed or
malformed line endings are rejected. The raw accepted bytes are snapshotted
unchanged; semantic LF conversion is used only for parsing and never silently
rewrites provenance. Code files remain exact-single-SHA authorities. The
package pins the authority JSON's own canonical-byte SHA-256 as
`90e659358423368ce6a27850cdffa36a0eb85cea508babc66e72ecafb8e70530`.

The standalone observational TB at
`tests/redred_cluster2_cav_bridge/redred_cluster2_native_observational_tb.sv`
assigns zero-based event IDs in cycle-then-source order, samples `overrun`
before the admission edge, and keeps a TB-only two-entry FIFO per source. The
IDs and FIFO never drive the DUT. Each output bitmap pops the oldest observed
identity and emits a pipe-separated native ledger. The TB asserts conservation,
no phantom retirement, legal/different lane rows, and an empty bounded drain.
Its one-cycle cyclemask occurrences use the native pulse contract: a sampled
native `overrun` is terminal for that occurrence. It is not common held-valid
retry evidence and must not be renamed or counted as common `source_overrun`.

`native_ledger.py` independently reparses the cyclemask and re-derives every
event ID. It rejects malformed bytes, ID/source/cycle differences,
non-deterministic ledger order, false pre-edge overrun decisions, FIFO reorder,
phantom or incomplete drain, illegal/duplicate native coordinates, more than
eight retirements per cycle, and count/conservation differences. Its only
product is `transport_outcome/v1`; this integration does not generate
`source_event`, projections, or CAV results.

The runner accepts an absolute, normalized, symlink-free caller FAER root and
one normalized path relative to that root. For this pinned
integration the relative path must be
`common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt`. Before looking up
or invoking a simulator, default `FILE_BYTES_AUTHORITY` verifies the exact five
code-file hashes and one of the two explicit raw trace hashes plus its canonical
semantic digest. This mode deliberately does not require the server checkout's
HEAD or object database: `git_commit=5ac1f0e...` records the provenance of the
canonical scoped content and is not a claim about the stale server repository
state. Optional `CLEAN_GIT_AUTHORITY` additionally requires the exact commit,
origin identity (allowing Git's conventional trailing `.git` spelling), and a
clean tracked checkout. The runner configures no output path under the Ganghee
checkout, but it does not claim host-wide zero-write coverage. Exact verified
raw bytes are hash-checked again into a private temporary snapshot before
compilation. The observational TB is likewise checked against the runner-pinned
full SHA-256 and compiled only from its exact private snapshot.
Every simulator subprocess runs with its working directory and `TMPDIR` under
the output root. The caller must select a system temporary directory outside
both repositories; the runner does not claim global zero-write coverage for an
arbitrary host. Pre/post Git-status snapshots detect status changes in the
bridge worktree and, when the caller root exposes usable Git status, the FAER
worktree. The six caller-authority byte identities are independently reverified
after the run. Invoke
the server-compatible mode from this repository root as:

```text
python3 tests/redred_cluster2_cav_bridge/run_native_observational.py \
  /path/to/AI-SEMI \
  common_traces_uzh/uzh_shapes_rotation_patch.cyclemask.txt \
  --authority-mode FILE_BYTES_AUTHORITY --simulator xrun
```

The runner auto-detects Cadence Xcelium `xrun` (including the server installation
at `/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun`), then Verilator, then
Icarus Verilog plus `vvp`; each can also be explicitly selected. For an external
system temporary directory, all compile, snapshot, log, ledger, and outcome
paths are outside the caller FAER root. If no
supported simulator is available it returns status 2 with `NATIVE_OBSERVATIONAL_SKIP
simulator_unavailable`; it does not emit a fabricated run. Logs, the native
ledger, and validated transport outcomes go to a newly created temporary
directory printed on PASS or failure.

A runner PASS by itself is an unsealed local observational result, not a native
release authority. The scoped server execution at bridge commit `ca446aa...` is
separately preserved by `server_native_observation_receipt.json` and its
hash-bound evidence bundle. That receipt remains native-observational evidence,
not common-seam, CAV, or PPA evidence. Likewise, the pinned converter plus
tracked cyclemask bind
only those repository bytes; no official UZH archive/member or source-to-
cyclemask reproduction receipt is bound or claimed here.
The selected polarity-v1 track is separately bound by
`polarity_release_authority.json`. Its observational TB emits only raw
per-cycle lane, overrun, column-mask, and polarity-mask records; it assigns no
event IDs. `polarity_native_ledger.py` independently rebuilds the two-entry
per-source FIFO from the pinned address/polarity trace. Only `pol_mask` bits
selected by `col_mask` represent retired events; other row-slice polarity bits
are intentionally ignored. The sealed Xcelium 23.09 replay observed 8,503
generated and delivered events, zero overrun, zero phantom or duplicate
retirements, and an empty bounded drain. The release gate reports **GO** for
this native polarity transport scope while explicitly declining event-ID order
independence and any common-seam, CAV RTL, physical-signoff, or PPA claim.

## Official UZH-to-world functional replay

`official_functional_run.py` binds the exact official UZH events,
ground-truth poses and calibration; the accepted LF/CRLF cyclemask; the sealed
Xcelium native receipt and outcome bundle; and the 30 repository Python files
loaded by this execution path.  The canonical result is committed at
`results/official_uzh_cluster2_cav_result.json`.  Its public validator always
rehashes the repository authorities, including when the caller omits an
explicit repository root.

For the pinned input, the runner reproduces an exact 8,503-event native join
with zero native overrun.  Software CAV produces 8,420 WORLD rays; 83 events
take the explicit SENSOR_FIXED bypass.  The 512 by 256 world grid contains
8,420 quantized events in 821 unique cells.  Original occurrence timestamps
drive geometry in all three comparison views.  Native retirement contributes
only the separately labelled observational latency sidecar: 6,393 events at
one cycle, 2,077 at two cycles and 33 at three cycles.

This is scoped functional extension and interface-compatibility evidence.  It
is not a claim that scalability is guaranteed, that native scheduling improves
CAV accuracy, or that CAV ran in RTL.  Wire-complete CAV/world RTL, physical
latency-quality replay, and CAV/world synthesis, STA, power and P&R remain
explicit **HOLD** items.  Any separate PPA evidence for the native Ganghee AER
endpoint is outside this software-CAV result.
