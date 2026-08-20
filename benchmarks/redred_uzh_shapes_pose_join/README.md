# UZH shapes_rotation source-preserving pose join

This standard-library-only package imports one pinned public UZH
`shapes_rotation` text archive. It preserves all 1,100 original DAVIS240C
events in the exact `[41.321, 41.322)` second profile, the raw OpenCV pinhole
plus radtan calibration, and every raw JPL-order ground-truth quaternion.

Every selected event is bound to the deterministic latest pose at or before
its source timestamp and to the first strictly-future pose. The output records
the two source pose indices, exact timestamps, causal age, and integer bracket
fraction. It does not interpolate, undistort, warp, project to 4x4, or discard
an admitted event.

The successful status is:

```text
PUBLIC_UZH_SOURCE_POSE_JOIN_COMPLETE_UNQUALIFIED
```

Its promotion status is always:

```text
HOLD_MC_WTB_ADAPTER
```

The source archive is an official public UZH dataset, but the generated
artifact is neither official nor canonical REDRED traffic and is not a replay,
MC-WTB result, pure-rotation claim, or transport receipt. `shapes_rotation`
describes dominant motion; translation is present and retained.

## Inputs and exact-byte scope

The production input surface is only the archive, the separate license text,
the committed specification, and a new result directory. Extracted member
paths are deliberately not accepted.

The archive is copied once from a pinned no-follow descriptor into a private
staging directory while SHA-256 is computed. ZIP parsing consumes that private
captured copy. Each required member is streamed, parsed, and hashed in the same
pass. The 509 MB event member is never held as one byte array or event list.

The source URLs and exact archive/member/license identities are pinned in
`join_spec.json`. The upstream download basename is `shapes_rotation.zip`;
`uzh-shapes_rotation.zip` is only the required local input basename. They are
separate receipt fields and are never presented as the same authority fact.
The UZH format and camera-pose conventions are described at:

- <https://rpg.ifi.uzh.ch/davis_data.html>
- <https://arxiv.org/abs/1610.08336>

The data are released under CC BY-NC-SA 3.0. The deed URL is
<https://creativecommons.org/licenses/by-nc-sa/3.0/> while the exact bytes
hashed and copied into `LICENSE.txt` come from the distinct plain-text legal
code URL <https://creativecommons.org/licenses/by-nc-sa/3.0/legalcode.txt>.
The digest is a local exact-byte pin, not an upstream-published digest.

The importer accepts fixture specifications for independent tests, but only
the compiled production archive/member/license tuple can set
`official_uzh_source=true`. Generated artifacts always carry
`generated_artifact_official_uzh=false`, all REDRED official/canonical,
replay, warp, and pure-rotation claims remain false, and a remote server is
not authenticated by package inspection.

## Package

A completed directory contains exactly:

```text
LICENSE.txt
calibration.json
poses.jsonl
events_pose_join.jsonl
receipt.json
COMPLETE.json
```

Files are written and synced in a private sibling directory. `COMPLETE.json`
is written last, then the directory is published with Linux
`renameat2(RENAME_NOREPLACE)` relative to one pinned parent dirfd. Unsupported
platforms fail closed; there is no weaker production fallback. This provides
same-parent atomic visibility and no-overwrite, not protection from every
same-directory hostile-writer race or network-filesystem transaction.

## Run and inspect

```bash
python3 -m benchmarks.redred_uzh_shapes_pose_join.import_join \
  --archive /tmp/uzh-shapes_rotation.zip \
  --license /tmp/CC-BY-NC-SA-3.0.txt \
  --spec benchmarks/redred_uzh_shapes_pose_join/join_spec.json \
  --result-dir /tmp/uzh-shapes-rotation-pose-join

python3 -m benchmarks.redred_uzh_shapes_pose_join.import_join \
  --inspect \
  --spec benchmarks/redred_uzh_shapes_pose_join/join_spec.json \
  --result-dir /tmp/uzh-shapes-rotation-pose-join
```

Inspection validates the exact file inventory, every digest, strict JSON
shape, reconstructed source calibration/pose/selected-event bytes, join
invariants, and both conservation equations. It does not claim to re-fetch or
authenticate the remote server.
