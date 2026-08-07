#!/usr/bin/env python3
"""Check same-K aggregate equivalence between prefix and replicated A7."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def normalized(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, value) for key, value in row.items()
                        if key != "candidate"))


def load(path: Path) -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    rows: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            candidate = row["candidate"]
            try:
                k = candidate.rsplit("_k", 1)[1]
            except (IndexError, ValueError) as error:
                raise SystemExit(f"cannot infer K from candidate {candidate!r}") from error
            key = (k, row["test"] + ":" + row["load_pct"])
            if key in rows:
                raise SystemExit(f"duplicate aggregate key {key} in {path}")
            rows[key] = normalized(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("replicated", type=Path)
    args = parser.parse_args()
    prefix = load(args.prefix)
    replicated = load(args.replicated)
    if prefix.keys() != replicated.keys():
        missing = sorted(prefix.keys() - replicated.keys())
        extra = sorted(replicated.keys() - prefix.keys())
        raise SystemExit(f"key mismatch missing={missing} extra={extra}")
    differences = [key for key in prefix if prefix[key] != replicated[key]]
    if differences:
        raise SystemExit(f"metric mismatch count={len(differences)} keys={differences}")
    print(f"A7_46_EQUIVALENT grouped_rows={len(prefix)} same_k=1,2,4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
