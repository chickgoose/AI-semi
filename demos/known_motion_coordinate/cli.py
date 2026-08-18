"""Command-line entry point for the known-motion coordinate demo."""

from __future__ import annotations

import argparse
import json
import sys

from .model import InterfaceError, transform_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Warp provenance-bound retired AER events with supplied camera rotation."
    )
    parser.add_argument("--events", required=True, help="retired-event JSONL input")
    parser.add_argument("--intrinsics", required=True, help="camera-intrinsics JSON input")
    parser.add_argument("--poses", required=True, help="known-pose JSONL input")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("world-to-sensor", "sensor-to-world"),
        help="direction in which input pixels are transformed",
    )
    parser.add_argument("--output", required=True, help="transformed-result JSONL output")
    parser.add_argument("--summary", required=True, help="summary JSON output")
    parser.add_argument(
        "--max-pose-age-ns",
        type=int,
        default=None,
        help="optional fail-closed maximum age for zero-order-held or explicit poses",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        summary = transform_files(
            events_path=args.events,
            intrinsics_path=args.intrinsics,
            poses_path=args.poses,
            output_path=args.output,
            summary_path=args.summary,
            mode=args.mode,
            max_pose_age_ns=args.max_pose_age_ns,
        )
    except (InterfaceError, OSError) as exc:
        print(f"known-motion-coordinate: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
