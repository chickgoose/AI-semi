#!/usr/bin/env python3
"""Verify the file closure asserted by an approved raw-summary flow receipt.

This repository-owned entry point gives qualification records a concrete flow
identity.  It deliberately does not synthesize or invent report contents; it
only verifies that the receipt's already-produced inputs, output, and success
sentinel are regular files and that the sentinel is asserted.
"""

from __future__ import annotations

import argparse
import stat
from pathlib import Path
from typing import Sequence


TOOL_NAME = "a8-approved-raw-summary-flow"
TOOL_VERSION = "1.0"


def _require_regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError(f"not a regular non-symlink file: {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--success-sentinel", required=True, type=Path)
    args = parser.parse_args(argv)

    for path in [*args.input, args.output, args.success_sentinel]:
        _require_regular(path)
    if args.success_sentinel.read_bytes() != b"FLOW_SUCCESS\n":
        raise ValueError("success sentinel is not asserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
