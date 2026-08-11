#!/usr/bin/env python3
"""File-based model result reader; avoids inline shell Python."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: read_gate_status.py RESULT_JSON")
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print("GO" if report["go_gate"]["go"] else "HOLD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
