#!/usr/bin/env python3
"""File-based model result reader; avoids inline shell Python."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_report(report: object) -> tuple[str, str]:
    if not isinstance(report, dict):
        raise ValueError("model result must be a JSON object")
    if report.get("research_complete") is not True:
        raise ValueError("research_complete must be true")
    gate = report.get("go_gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("go"), bool):
        raise ValueError("go_gate.go must be boolean")
    decision = report.get("decision")
    if decision not in {"GO", "HOLD"}:
        raise ValueError("decision must be GO or HOLD")
    expected_decision = "GO" if gate["go"] else "HOLD"
    if decision != expected_decision:
        raise ValueError("decision disagrees with go_gate.go")
    sentinel = report.get("completion_sentinel")
    expected_sentinel = f"A8_NCF_RESEARCH_COMPLETE_{decision}"
    if sentinel != expected_sentinel:
        raise ValueError("completion sentinel disagrees with decision")
    return decision, sentinel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_json", type=Path)
    parser.add_argument("--require-go", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.result_json.read_text(encoding="utf-8"))
    decision, sentinel = validate_report(report)
    print(f"A8_NCF_MACHINE_DECISION={decision}")
    print(f"A8_NCF_COMPLETION_SENTINEL={sentinel}")
    print("A8_NCF_RESEARCH_COMPLETION=PASS")
    if args.require_go and decision != "GO":
        print("A8_NCF_REQUIRE_GO=FAIL")
        return 2
    if args.require_go:
        print("A8_NCF_REQUIRE_GO=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
