#!/usr/bin/env python3
"""Check one normalized K2 wrapper trace with the flattened-order oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oracle import BindingViolation, TraceContractError, validate_trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--stimulus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--expect-diagnostic")
    args = parser.parse_args()
    try:
        report = validate_trace(
            json.loads(args.stimulus.read_text(encoding="utf-8")),
            json.loads(args.observations.read_text(encoding="utf-8")),
            args.binding,
        )
    except (OSError, json.JSONDecodeError, TraceContractError) as error:
        print(f"A21_K2_TRACE_ERROR {error}")
        return 2
    except BindingViolation as error:
        if args.expect_diagnostic == error.code:
            print(f"A21_K2_EXPECTED_KILL binding={args.binding} diagnostic={error.code} cycle={error.cycle}")
            return 0
        print(f"A21_K2_BINDING_FAIL binding={args.binding} {error}")
        return 1
    if args.expect_diagnostic:
        print(
            f"A21_K2_MUTANT_SURVIVED binding={args.binding} "
            f"expected={args.expect_diagnostic}"
        )
        return 1
    print(
        f"A21_K2_BINDING_PASS binding={report.binding} cycles={report.cycles} "
        f"accepted={report.accepted} retired={report.retired} order=flattened-global"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
