# Hardened A2/A3 preserved-run exporter

This package is a separate, fail-closed exporter for a preserved synthetic
single-edge replay run. It does not import, edit, or regenerate the pinned
`a23_full_single_edge_replay` producer or any public projection.

The requested binding is exact:

- result SHA-256 `e21e714e4c4ebbeba4caf63ad5656b2b29fc05881ebb74ea6d93114c5f7d8cf4`;
- hardened source commit `6fc5e167918fa4c54786c9a3abb5f60ecd8b991b`;
- integration commit `a0a4eb38632245db8ff5937ea5b6c6e3f3839246`;
- pins SHA-256 `0daba2132010272a78b56ec2a1541f30f7cb5d2b0d8562102cb70cf9e098d8e0`.

On a matching root, the exporter independently recomputes trace/prepared/event,
summary, reset, activation, mutation, and conservation claims. Its closed
payload contains `result.json`, all 50 generated JSONL traces and manifests,
all 50 prepared traces, generator/preparer/build logs, A2/A3 full50 event CSVs,
summary CSVs and simulator logs, reset/activation artifacts and logs, all eight
mutation logs, and all eight mutated RX sources. Reproducible compiler scratch
below `work/build/` is scanned for unsafe filesystem objects and summarized but
is deliberately excluded from the evidence payload.

Both source-tree and archive validation reject symlinks, hardlinks, path
escapes, duplicate members, unexpected evidence files, and hash/size drift.
Missing receipt-bound evidence produces `HOLD`; unsafe or contradictory
evidence is rejected. The exporter never fills a gap from Git or by rerunning
the producer.

The preserved `/tmp/a23-full-single-edge-replay.IdAjj6` root is a `HOLD`: its
`result.json` is the earlier `7286913/4ce4836` run and hashes to `0df2de9c...`,
not the requested hardened result. Therefore no compressed export is published.

Run the focused tests with:

```sh
tests/a23_single_edge_synthetic_export/run_all.sh
```

Re-evaluate the preserved root into fresh output paths with:

```sh
python3 tests/a23_single_edge_synthetic_export/export_preserved.py \
  --run-root /tmp/a23-full-single-edge-replay.IdAjj6 \
  --status-output /tmp/a23-single-edge-export-status.json \
  --archive-output /tmp/a23-single-edge-export.tar.gz
```
