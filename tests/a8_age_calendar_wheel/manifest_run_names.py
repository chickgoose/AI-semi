#!/usr/bin/env python3
"""Print one validated run name per line from an AER manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        parser.error("manifest must contain a non-empty runs array")
    for run in runs:
        name = run.get("name") if isinstance(run, dict) else None
        if not isinstance(name, str) or not name or "\n" in name:
            parser.error("every run must have a non-empty single-line name")
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
