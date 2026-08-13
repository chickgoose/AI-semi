#!/usr/bin/env python3
"""Launch the exact three tech-staged complete-composition goal rows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import run_genus


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = args.repo_root.resolve(strict=True)
    if repo_root != run_genus.ROOT.resolve(strict=True):
        raise run_genus.FlowError(
            "launcher entrypoint and repository root identity mismatch")
    registry = run_genus.load_registry(repo_root)
    runner = Path(run_genus.__file__).resolve(strict=True)
    rows = []
    for index, key in enumerate(registry["goal_order"], start=1):
        design = registry["designs"][key]
        attempt = f"{args.attempt_prefix}-{index:02d}-{key}"
        command = [
            sys.executable, "-B", str(runner),
            "--repo-root", str(args.repo_root.resolve()),
            "--design", key,
            "--genus", str(args.genus.resolve()),
            "--library", str(args.library.resolve()),
            "--hold-library", str(args.hold_library.resolve()),
            "--cell-lef", str(args.cell_lef.resolve()),
            "--shared-qrc", str(args.shared_qrc.resolve()),
            "--golden-archive", str(args.golden_archive.resolve()),
            "--raw-golden-archive", str(args.raw_golden_archive.resolve()),
            "--functional-loss-archive", str(args.functional_loss_archive.resolve()),
            "--server-environment-receipt",
            str(args.server_environment_receipt.resolve()),
            "--mapped-functional-hook", str(args.mapped_functional_hook.resolve()),
            "--output-root", str(args.output_root.resolve()),
            "--attempt", attempt,
        ]
        for model in args.functional_model:
            command.extend(["--functional-model", str(model.resolve())])
        rows.append({
            "ordinal": index,
            "design": key,
            "top": design["top"],
            "boundary_cohort": design["boundary_cohort"],
            "source_origin": design["source_origin"],
            "attempt": attempt,
            "command": command,
        })
    return {
        "schema": "k2_w2_genus_exact_three_endpoint_launch_plan_v2",
        "goal_order": registry["goal_order"],
        "ranking_policy": registry["ranking_policy"],
        "generic_or_native_substitution": "FORBIDDEN",
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=run_genus.ROOT)
    parser.add_argument("--genus", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--hold-library", type=Path, required=True)
    parser.add_argument("--cell-lef", type=Path, required=True)
    parser.add_argument("--shared-qrc", type=Path, required=True)
    parser.add_argument("--golden-archive", type=Path, required=True)
    parser.add_argument("--raw-golden-archive", type=Path, required=True)
    parser.add_argument("--functional-loss-archive", type=Path, required=True)
    parser.add_argument("--server-environment-receipt", type=Path, required=True)
    parser.add_argument("--mapped-functional-hook", type=Path, required=True)
    parser.add_argument("--functional-model", type=Path, action="append",
                        required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt-prefix", required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if not run_genus.SAFE_ATTEMPT.fullmatch(args.attempt_prefix):
        print("K2_W2_GOAL_FAIL invalid attempt prefix", file=sys.stderr)
        return 2
    try:
        plan = build_plan(args)
        if args.plan_only:
            sys.stdout.buffer.write(run_genus.canonical(plan))
            return 0
        output = args.output_root.resolve()
        output.mkdir(parents=False, exist_ok=False)
        run_genus.write_exclusive(output / "launch-plan.json", run_genus.canonical(plan))
        receipts = []
        for row in plan["rows"]:
            result = subprocess.run(
                row["command"], cwd=args.repo_root.resolve(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            log_path = output / f"{row['attempt']}.launcher.log"
            run_genus.write_exclusive(log_path, result.stdout)
            if result.returncode:
                raise run_genus.FlowError(
                    f"goal row failed: {row['design']} exit={result.returncode}")
            receipt_path = output / row["attempt"] / "receipt.json"
            receipt_payload = run_genus.stable_read(receipt_path)
            receipt = json.loads(receipt_payload)
            if (receipt.get("status") !=
                    "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD" or
                    receipt.get("design") != row["design"] or
                    receipt.get("top") != row["top"] or
                    receipt.get("boundary_cohort") != row["boundary_cohort"] or
                    receipt.get("ranking_policy") != plan["ranking_policy"]):
                raise run_genus.FlowError(
                    f"goal receipt identity mismatch: {row['design']}")
            receipts.append({
                "design": row["design"],
                "top": row["top"],
                "boundary_cohort": row["boundary_cohort"],
                "receipt": str(receipt_path.relative_to(output)),
                "receipt_sha256": run_genus.sha256_bytes(receipt_payload),
            })
        publication = {
            "schema": "k2_w2_genus_exact_three_endpoint_publication_v2",
            "status": "PASS_EXACT_THREE_TECH_STAGED_ENDPOINTS",
            "goal_order": plan["goal_order"],
            "ranking_policy": plan["ranking_policy"],
            "generic_or_native_substitution": "FORBIDDEN",
            "launch_plan_sha256": run_genus.sha256_bytes(
                run_genus.stable_read(output / "launch-plan.json")),
            "receipts": receipts,
        }
        publication_path = output / "goal-publication.json"
        run_genus.write_exclusive(publication_path, run_genus.canonical(publication))
    except (OSError, ValueError, json.JSONDecodeError,
            run_genus.FlowError, subprocess.SubprocessError) as error:
        print(f"K2_W2_GOAL_FAIL {error}", file=sys.stderr)
        return 2
    print(f"K2_W2_GOAL_PASS publication={publication_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
