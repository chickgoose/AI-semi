#!/usr/bin/env python3
"""Prepared-trace tool double for A1 K2 orchestrator tests."""

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--addr-width", required=True)
    args = parser.parse_args()
    mode = os.environ.get("FAKE_PREPARER_MODE", "success")
    if mode == "fail":
        print("fake preparer failure")
        return 8
    args.output.write_text("4 1 4 16 1000 always 0 0 1\n0 0 0 0 0\n", encoding="utf-8")
    if mode == "error_zero":
        print("ERROR: fake preparer diagnostic")
    else:
        print(f"TRACE_PREPARED name={args.trace.stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
