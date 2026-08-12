#!/usr/bin/env python3
"""Small deterministic generator double for A1 K2 orchestrator tests."""

import argparse
import hashlib
import json
import os
from pathlib import Path


TRACE_PAYLOAD = b'{"tb_only_event_id":0}\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("FAKE_GENERATOR_MODE") == "fail":
        print("fake generator failure")
        return 7
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for run in manifest["runs"]:
        name = run["name"]
        trace_name = f"{name}.events.jsonl"
        trace_sha = hashlib.sha256(TRACE_PAYLOAD).hexdigest()
        (args.output_dir / trace_name).write_bytes(TRACE_PAYLOAD)
        row = {
            "schema_version": 1,
            "generator_version": "4.0",
            "run": {
                "name": name,
                "workload": run["workload"],
                "seed": run["seed"],
                "geometry": run["geometry"],
                "load": str(run["load"]),
                "stim_cycles": run["stim_cycles"],
                "parameters": run.get("parameters", {}),
                "sink": run.get("sink", {"mode": "always"}),
            },
            "report_group": name,
            "trace_file": trace_name,
            "trace_sha256": trace_sha,
            "event_count": 1,
            "event_identity_mode": "address_only",
            "dut_address_fields": ["logical_source"],
            "dut_payload_fields": [],
        }
        (args.output_dir / f"{name}.manifest.json").write_text(
            json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        rows.append(row)
    if os.environ.get("FAKE_GENERATOR_MODE") == "partial":
        rows = []
    index = {
        "schema_version": 1,
        "generator_version": "4.0",
        "input_manifest": args.manifest.name,
        "runs": rows,
    }
    (args.output_dir / "generation-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"generated fake runs={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
