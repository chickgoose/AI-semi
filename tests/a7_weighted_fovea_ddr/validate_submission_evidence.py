#!/usr/bin/env python3
"""Bind every W7 result address/timing row to its exhaustive input bitmap."""

import argparse
import csv
from pathlib import Path


SENTINEL = "A7_W7_N16_BITMAP_EXHAUSTIVE_PASS bitmaps=65536 nonempty=65535 accepted=65535 retired=65535"
FIELDS = ["bitmap", "logical_source", "retire_addr", "accept_cycle",
          "output_cycle", "consumer_cycle"]


def fail(kind: str, detail: str) -> None:
    raise SystemExit(f"A7_W7_EVIDENCE_{kind}_CAUGHT: {detail}")


def integer(row: dict[str, str], field: str, line: int) -> int:
    try:
        value = int(row[field], 10)
    except (KeyError, TypeError, ValueError):
        fail("SCHEMA", f"line={line} field={field} is not a decimal integer")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-csv", required=True, type=Path)
    parser.add_argument("--run-log", required=True, type=Path)
    parser.add_argument("--expected-rows", type=int, default=65535)
    args = parser.parse_args()

    if not args.events_csv.is_file() or args.events_csv.stat().st_size == 0:
        fail("CSV_MISSING", f"missing/nonregular/empty artifact: {args.events_csv}")
    if not args.run_log.is_file() or args.run_log.stat().st_size == 0:
        fail("SENTINEL_MISSING", f"missing/nonregular/empty run log: {args.run_log}")
    lines = args.run_log.read_text(encoding="utf-8", errors="strict").splitlines()
    if lines.count(SENTINEL) != 1:
        fail("SENTINEL_MISSING", f"exact sentinel count={lines.count(SENTINEL)} expected=1")

    with args.events_csv.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            fail("SCHEMA", f"header={reader.fieldnames!r} expected={FIELDS!r}")
        rows = list(reader)
    if len(rows) != args.expected_rows:
        fail("ROW_COUNT", f"rows={len(rows)} expected={args.expected_rows}")

    previous_accept = -1
    for index, row in enumerate(rows, start=1):
        bitmap = integer(row, "bitmap", index + 1)
        logical_source = integer(row, "logical_source", index + 1)
        retire_addr = integer(row, "retire_addr", index + 1)
        accept_cycle = integer(row, "accept_cycle", index + 1)
        output_cycle = integer(row, "output_cycle", index + 1)
        consumer_cycle = integer(row, "consumer_cycle", index + 1)
        if bitmap != index or not 1 <= bitmap <= 0xFFFF:
            fail("BITMAP_ORDER", f"line={index + 1} bitmap={bitmap} expected={index}")
        if not 0 <= logical_source < 16 or not (bitmap & (1 << logical_source)):
            fail("SOURCE_NOT_LIVE", f"line={index + 1} bitmap={bitmap} logical_source={logical_source}")
        if retire_addr != logical_source:
            fail("ADDRESS_MISMATCH", f"line={index + 1} logical_source={logical_source} retire_addr={retire_addr}")
        if output_cycle != accept_cycle + 1 or consumer_cycle != accept_cycle + 2:
            fail("TIMING", f"line={index + 1} accept={accept_cycle} output={output_cycle} consumer={consumer_cycle}")
        if accept_cycle <= previous_accept:
            fail("ORDER", f"line={index + 1} accept={accept_cycle} previous={previous_accept}")
        previous_accept = accept_cycle
    print(f"A7_W7_EVIDENCE_VALIDATION_PASS rows={len(rows)} address_bound=1 exact_sentinel=1")


if __name__ == "__main__":
    main()
