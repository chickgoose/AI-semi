#!/usr/bin/env python3
"""Create one no-overwrite execution sidecar from immutable attempt evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import common_suite_receipt as receipt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(attempt_root: Path, run_manifest: Path, trace: Path, result: Path,
          analyzer: Path | None) -> dict:
    attempt_path = attempt_root / "attempt.json"
    try:
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        metadata = json.loads(run_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise receipt.ReceiptError(f"cannot read sidecar input: {exc}") from exc
    run = metadata.get("run")
    if not isinstance(run, dict):
        raise receipt.ReceiptError("run manifest has no run object")
    name = receipt._string(run.get("name"), "run name")
    workload = receipt._string(run.get("workload"), "workload")
    if metadata.get("trace_file") != trace.name or metadata.get("trace_sha256") != _sha(trace):
        raise receipt.ReceiptError("trace does not match run manifest")
    needs_analyzer = workload in receipt.ANALYZER_WORKLOADS
    if needs_analyzer != (analyzer is not None):
        raise receipt.ReceiptError("analyzer presence does not match workload schema")
    tools = attempt.get("tools")
    if not isinstance(tools, dict) or "runner" not in tools or (needs_analyzer and workload not in tools):
        raise receipt.ReceiptError("attempt lacks required tool identity")
    tool_binding = {}
    for key in (["runner", workload] if needs_analyzer else ["runner"]):
        row = tools[key]
        tool_binding[key] = {
            "identity": receipt._string(row.get("identity"), f"tool {key} identity"),
            "sha256": receipt._sha(row.get("sha256"), f"tool {key} sha256"),
        }
    return {
        "schema_version": receipt.SIDECAR_SCHEMA_VERSION,
        "suite": receipt._string(attempt.get("suite"), "attempt suite"),
        "attempt_id": receipt._string(attempt.get("attempt_id"), "attempt id"),
        "candidate": receipt._string(attempt.get("candidate"), "attempt candidate"),
        "run_name": name,
        "trace_sha256": metadata["trace_sha256"],
        "run_manifest_sha256": _sha(run_manifest),
        "candidate_manifest_sha256": receipt._sha(
            attempt.get("candidate_manifest", {}).get("sha256"), "candidate manifest sha256"),
        "tools": tool_binding,
        "result_sha256": _sha(result),
        "analyzer_sha256": _sha(analyzer) if analyzer else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--analyzer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        root = args.attempt_root.resolve()
        output = args.output.resolve(strict=False)
        if root not in output.parents:
            raise receipt.ReceiptError("sidecar output must reside in attempt root")
        payload = (json.dumps(build(root, args.run_manifest, args.trace, args.result, args.analyzer),
                              indent=2, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(args.output, payload)
    except (OSError, receipt.ReceiptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"PASS sidecar={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
