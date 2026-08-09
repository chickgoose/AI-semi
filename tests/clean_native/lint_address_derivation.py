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


def check_cluster2_binding(text: str) -> None:
    match = re.search(
        r"AER_CLUSTER2_BINDING_BEGIN(.*?)AER_CLUSTER2_BINDING_END", text, re.DOTALL
    )
    if match is None:
        raise DerivationError("cluster2 binding markers are missing")
    code = _code(match.group(1))
    if "source_event" in code:
        raise DerivationError("cluster2 monitor reads pending source_event metadata")
    forbidden = ("always_ff", "always_latch", "retire_ready",
                 "fifo", "queue", "grant")
    found = [token for token in forbidden if token in code.lower()]
    if found:
        raise DerivationError(
            f"cluster2 binding adds forbidden state/control: {found}"
        )
    if not re.search(
        r"assign\s+cluster2_req\s*=\s*bench\.source_valid\s*&\s*"
        r"~cluster2_current_result_mask\s*;", code
    ):
        raise DerivationError(
            "cluster2 req must use only current-result acknowledgement masking"
        )
    if not re.search(
        r"bench\.source_ready\s*=\s*bench\.source_valid\s*&\s*"
        r"cluster2_current_result_mask\s*;", code
    ):
        raise DerivationError("cluster2 source_ready must acknowledge only live results")
    if "raw_cluster2_dut" not in code:
        raise DerivationError("raw cluster2 DUT is not instantiated by the binding")
    assignments = re.findall(
        r"bench\.retire_event\s*\[[^]]+\]\s*=\s*([^;]+);", code
    )
    assignments = [assignment for assignment in assignments if assignment != "'0"]
    if assignments != ["ADDR_WIDTH'(cluster2_source)",
                       "ADDR_WIDTH'(cluster2_source)"]:
        raise DerivationError(f"cluster2 retire_event is not address-derived: {assignments}")
    raw_match = re.search(
        r"AER_CLUSTER2_RAW_OBSERVATION_BEGIN(.*?)"
        r"AER_CLUSTER2_RAW_OBSERVATION_END", match.group(1), re.DOTALL
    )
    if raw_match is None:
        raise DerivationError("cluster2 unmasked raw-observation markers are missing")
    raw_code = _code(raw_match.group(1))
    raw_forbidden = ("source_valid", "current_result_mask", "cluster2_req")
    raw_found = [token for token in raw_forbidden if token in raw_code]
    if raw_found:
        raise DerivationError(
            f"cluster2 raw retirement is masked by request state: {raw_found}"
        )
    source_defs = re.findall(r"cluster2_source\s*=\s*([^;]+);", code)
    expected = {
        "(integer'(cluster2_row0) * 4) + cluster2_col",
        "(integer'(cluster2_row1) * 4) + cluster2_col",
    }
    if set(source_defs) != expected:
        raise DerivationError(
            f"cluster2 decoded source must derive from native row/column: {source_defs}"
        )


def check_cluster2_common_tb(text: str) -> None:
    code = _code(text)
    if "aer_ganghee_cluster2_binding" not in code:
        raise DerivationError("common TB does not instantiate the cluster2 binding")
    if "raw_cluster2_dut" in code or "AER_CLUSTER2_RAW_OBSERVATION_BEGIN" in code:
        raise DerivationError("production common TB contains inline cluster2 protocol logic")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: lint_address_derivation.py NATIVE_BINDING CLUSTER2_BINDING COMMON_TB",
            file=sys.stderr,
        )
        return 2
    native, cluster2, common_tb = (Path(name) for name in argv[1:])
    try:
        check_native(native.read_text(encoding="utf-8"))
        check_cluster2_binding(cluster2.read_text(encoding="utf-8"))
        check_cluster2_common_tb(common_tb.read_text(encoding="utf-8"))
    except (OSError, DerivationError) as error:
        print(f"ANTI_RECONSTRUCTION_LINT_FAIL {error}", file=sys.stderr)
        return 1
    print("ANTI_RECONSTRUCTION_LINT_PASS native=addr cluster2=binding-raw-row-col")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
