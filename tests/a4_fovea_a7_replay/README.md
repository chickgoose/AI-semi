# A4 W6 scalar Fovea → A7 replay projection

Status: **LOCAL_MODEL**. This is not RTL or common-suite qualification.

The harness consumes exact generator-v4 traces plus the current common-TB event CSVs from a
scalar Ganghee fovea run. A fovea `delivered` row is the A7 admission. The model requires at
most one such row per cycle, preserves its event ID and four-bit logical-source address, and
places the synchronous always-ready consumer observation exactly two cycles later. It creates
no queue and performs no rescheduling. `source_overrun`, `accepted`, and `pending` rows remain
explicit counts and are never converted into successful A7 admissions.

The `+2` projection is bound to corrected A7 W5 owner commit
`42377ca81340951bfcd453b3bd664e673091f9f3`.

The frozen inputs are A1 generator-v4 SHA-256
`59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50`, official-suite
specification SHA-256 `7e1ec861ed901f4501e07104d3f34ae3992cbb6c392d52143a91968dd7f78e33`, and the official
full50/capacity22 manifest and per-trace hashes. The unit regression regenerates and validates
exactly 50 and 22 runs.

Example after an official fovea run has produced one event CSV per trace:

```sh
python3 tests/a4_fovea_a7_replay/replay_projection.py \
  --suite full50 \
  --trace-root /path/to/attempt/traces \
  --results-root /path/to/fovea/event-results \
  --result-pattern '{name}/trace.events.csv' \
  --official-manifest /home/chickgoose/projects/a1/benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  --generator /home/chickgoose/projects/a1/benchmarks/clean_slate_aer/generate_trace.py \
  --official-spec /home/chickgoose/projects/a1/scripts/common_suite_official.py \
  --output /tmp/fovea-a7-full50.local-model.json
```

The input result pattern must resolve to one private regular file per official stem. Existing
outputs are never overwritten. Any missing/duplicate event, candidate/trial mismatch, trace
hash mismatch, non-address-only metadata, or multi-event scalar cycle fails closed.
