#!/usr/bin/env python3
"""Fail if Ganghee bindings reconstruct retire identity from TB metadata."""

from __future__ import annotations

import re
import sys
from pathlib import Path


class DerivationError(ValueError):
    pass


def _code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def check_native(text: str) -> None:
    code = _code(text)
    if "bench.source_event" in code:
        raise DerivationError("native binding reads pending source_event metadata")
    assignments = re.findall(r"bench\.retire_event\s*\[0\]\s*=\s*([^;]+);", code)
    if assignments != ["ADDR_WIDTH'(native_addr)"]:
        raise DerivationError(
            f"native retire_event must derive only from native_addr: {assignments}"
        )


def check_cluster2_direct_harness(text: str) -> None:
    match = re.search(
        r"AER_CLUSTER2_DIRECT_BEGIN(.*?)AER_CLUSTER2_DIRECT_END", text, re.DOTALL
    )
    if match is None:
        raise DerivationError("cluster2 direct-native harness markers are missing")
    code = _code(match.group(1))
    if "source_event" in code:
        raise DerivationError("cluster2 monitor reads pending source_event metadata")
    forbidden = ("always_ff", "always_latch", "retire_ready", "ack_mask",
                 "fifo", "queue", "grant")
    found = [token for token in forbidden if token in code.lower()]
    if found:
        raise DerivationError(
            f"cluster2 direct monitor adds forbidden state/control: {found}"
        )
    if not re.search(r"assign\s+cluster2_req\s*=\s*bench\.source_valid\s*;", code):
        raise DerivationError("cluster2 req is not driven directly from source_valid")
    if "raw_cluster2_dut" not in code:
        raise DerivationError("raw cluster2 DUT is not instantiated by the common TB")
    assignments = re.findall(
        r"bench\.retire_event\s*\[[^]]+\]\s*=\s*([^;]+);", code
    )
    assignments = [assignment for assignment in assignments if assignment != "'0"]
    if assignments != ["ADDR_WIDTH'(cluster2_source)",
                       "ADDR_WIDTH'(cluster2_source)"]:
        raise DerivationError(f"cluster2 retire_event is not address-derived: {assignments}")
    source_defs = re.findall(r"source\s*=\s*([^;]+);", code)
    expected = {
        "(integer'(cluster2_row0) * 4) + cluster2_col",
        "(integer'(cluster2_row1) * 4) + cluster2_col",
    }
    if set(source_defs) != expected:
        raise DerivationError(
            f"cluster2 decoded source must derive from native row/column: {source_defs}"
        )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: lint_address_derivation.py NATIVE_BINDING COMMON_TB", file=sys.stderr)
        return 2
    native, common_tb = (Path(name) for name in argv[1:])
    try:
        check_native(native.read_text(encoding="utf-8"))
        check_cluster2_direct_harness(common_tb.read_text(encoding="utf-8"))
    except (OSError, DerivationError) as error:
        print(f"ANTI_RECONSTRUCTION_LINT_FAIL {error}", file=sys.stderr)
        return 1
    print("ANTI_RECONSTRUCTION_LINT_PASS native=addr cluster2=direct-row-col")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
