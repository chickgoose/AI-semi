#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import pathlib
import re


def fields(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise ValueError("malformed or duplicate window field")
        result[key] = value
    required = {"candidate", "start_tick_1ps", "end_tick_1ps",
                "ref_period_ps", "sample_period_ps",
                "sample_first_rise_ps", "scope"}
    if set(result) != required:
        raise ValueError("window field set mismatch")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--window", type=pathlib.Path, required=True)
    parser.add_argument("--summary", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--sha-output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.sha_output.exists():
        raise SystemExit("refusing to overwrite activity artifacts")
    meta = fields(args.window)
    start, end = int(meta["start_tick_1ps"]), int(meta["end_tick_1ps"])
    if start < 0 or end <= start or (end-start) % int(meta["ref_period_ps"]):
        raise SystemExit("invalid measurement window")
    with args.summary.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1 or not rows[0].get("measurement_cycles"):
        raise SystemExit("summary must contain exactly one measurement_cycles row")
    benchmark_cycles = int(rows[0]["measurement_cycles"])
    activity_cycles = (end-start)//int(meta["ref_period_ps"])
    if activity_cycles != benchmark_cycles+1:
        raise SystemExit("activity window does not match frozen TB service-edge contract")
    raw = args.input.read_text()
    pieces = re.split(r"(?m)(?=^#\d+$)", raw)
    header = pieces[0]
    kept: list[str] = []
    for block in pieces[1:]:
        match = re.match(r"#(\d+)", block)
        if match and start <= int(match.group(1)) <= end:
            kept.append(re.sub(r"^#\d+", f"#{int(match.group(1))-start}",
                               block, count=1))
    if not kept:
        raise SystemExit("VCD has no changes inside declared window")
    output = header + "".join(kept)
    rebased = [int(value) for value in re.findall(r"(?m)^#(\d+)$", output)]
    duration = end-start
    if min(rebased) != 0:
        marker = "$enddefinitions $end"
        if marker not in output:
            raise SystemExit("VCD enddefinitions missing")
        output = output.replace(marker, marker + "\n#0", 1)
    if max(rebased) != duration:
        output += "" if output.endswith("\n") else "\n"
        output += f"#{duration}\n"
    args.output.write_text(output)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.sha_output.write_text(
        f"candidate={meta['candidate']}\n"
        f"vcd_sha256={digest}\n"
        f"window_start_tick_1ps={start}\n"
        f"window_end_tick_1ps={end}\n"
        f"duration_tick_1ps={duration}\n"
        f"benchmark_measurement_cycles={benchmark_cycles}\n"
        f"activity_window_ref_cycles={activity_cycles}\n"
        f"window_contract=frozen_measurement_active_edges_plus_final_service\n"
        f"scope={meta['scope']}\n"
    )


if __name__ == "__main__":
    main()
