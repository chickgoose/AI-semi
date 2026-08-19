"""Command-line entry point for the MC-WTB Stage-1 analysis model."""

from __future__ import annotations

import argparse
import json
import sys

from .model import InterfaceError, analyze_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare sensor-fixed and supplied-rotation reference-tile occupancy "
            "without implementing a transport codec."
        )
    )
    parser.add_argument("--events", required=True, help="strict MC-WTB event JSONL")
    parser.add_argument("--intrinsics", required=True, help="known-motion intrinsics JSON")
    parser.add_argument("--poses", required=True, help="known-motion pose JSONL")
    parser.add_argument("--tile-width", required=True, type=int)
    parser.add_argument("--tile-height", required=True, type=int)
    parser.add_argument("--time-bin-ns", required=True, type=int)
    parser.add_argument("--max-pose-age-ns", required=True, type=int)
    parser.add_argument("--output", required=True, help="deterministic analysis JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = analyze_files(
            args.events,
            args.intrinsics,
            args.poses,
            args.output,
            tile_width=args.tile_width,
            tile_height=args.tile_height,
            time_bin_ns=args.time_bin_ns,
            max_pose_age_ns=args.max_pose_age_ns,
        )
    except (InterfaceError, OSError) as exc:
        print(f"mc-wtb-stage1: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
