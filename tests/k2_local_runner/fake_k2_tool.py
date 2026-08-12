#!/usr/bin/env python3
"""Deterministic fake compiler/suite driver for A1 K2 runner self-tests."""

from __future__ import annotations

import json
import os
import time
import sys
from pathlib import Path


def write_result(path: Path, suite: str, *, fabricated: bool = False,
                 sentinel: bool = False, stale: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sentinel:
        path.write_text("K2_DIRECTED_PASS\n", encoding="utf-8")
    else:
        document = {
            "schema_version": 1,
            "suite": suite,
            "status": "PASS",
            "stage_command_sha256": (
                "0" * 64 if fabricated else os.environ["K2_STAGE_COMMAND_SHA256"]
            ),
            "stage_input_sha256": os.environ["K2_STAGE_INPUT_SHA256"],
            "execution_challenge": os.environ["K2_STAGE_CHALLENGE"],
            "checks": [{
                "name": "conservation_and_order",
                "status": "PASS",
                "evidence": {"accepted": 4, "retired": 4, "errors": 0},
            }],
        }
        path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    if stale:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("fake-k2-tool 1.0")
        return 0
    if len(sys.argv) < 3:
        return 64
    mode = os.environ.get("FAKE_K2_MODE", "pass")
    operation, output = sys.argv[1], Path(sys.argv[2])
    if operation == "compile":
        print("compile invoked", " ".join(sys.argv[3:]))
        if mode == "hang":
            time.sleep(10)
        if mode == "compile_fail":
            return 17
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake simulator image\n")
        mutation = os.environ.get("FAKE_K2_MUTATE_PATH")
        if mutation:
            Path(mutation).write_text("changed during compile\n", encoding="utf-8")
        return 0
    if operation != "suite" or len(sys.argv) != 5:
        return 65
    suite = sys.argv[2]
    image = Path(sys.argv[3])
    output = Path(sys.argv[4])
    if not image.is_file():
        return 66
    print(f"{suite} execution completed")
    if mode == "run_fail" and suite == "directed_trace":
        return 23
    if mode == "partial" and suite == "reset_drain":
        return 0
    write_result(output, suite, fabricated=(mode == "fabricated"),
                 sentinel=(mode == "sentinel"), stale=(mode == "stale"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
