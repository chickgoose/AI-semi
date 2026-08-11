#!/usr/bin/env python3
"""Independent loss/duplicate diff check against exact flat-scan outcomes."""

from __future__ import annotations

import itertools
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_directory_model import ActivityDirectory, Event, FlatScan, mutation, simulate


def main() -> int:
    checked = 0
    modes = (None, "false_empty", "out_of_range", "duplicate_hot",
             "false_overflow_clear", "rotating_corrupt", "stale_valid")
    # Each source occurs at most once, so arbitration latency cannot change
    # source-overrun.  Candidate and exact flat scan must therefore deliver the
    # identical input multiset even though their service order may differ.
    for sequence in itertools.product(range(4), repeat=3):
        events = [Event(source, source, cycle) for source, cycle in enumerate(sequence)]
        flat = simulate("flat-diff", events, 3, 4, FlatScan(3), drain_limit=64)
        expected = sorted(flat.delivered_ids)
        for mode in modes:
            directory = ActivityDirectory(
                3, 2, watchdog_limit=2,
                mutation=mutation(mode) if mode is not None else None)
            observed = simulate("directory-diff", events, 3, 4, directory,
                                drain_limit=64)
            if sorted(observed.delivered_ids) != expected:
                raise SystemExit(
                    f"DIFF_FAIL sequence={sequence} mutation={mode} "
                    f"expected={expected} observed={sorted(observed.delivered_ids)}")
            if observed.accepted != observed.delivered:
                raise SystemExit(f"DIFF_FAIL accepted/delivered mutation={mode}")
            checked += 1
    print(f"A5_ACTIVITY_DIRECTORY_DIFF_PASS cases={checked} "
          "truth=exact_pending hint=advisory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
