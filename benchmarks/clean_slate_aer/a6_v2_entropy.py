#!/usr/bin/env python3
"""Entropy and non-expanding block-bypass bounds for the A6 v2 decision gate."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean

from a6_codec import encode


def entropy(counts: Counter) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total)
                for count in counts.values())


def transition_entropy(addresses: list[int]) -> float:
    contexts: dict[int, Counter] = defaultdict(Counter)
    for previous, current in zip(addresses, addresses[1:]):
        contexts[previous][current] += 1
    transitions = max(0, len(addresses) - 1)
    if transitions == 0:
        return 0.0
    return sum(sum(counts.values()) * entropy(counts)
               for counts in contexts.values()) / transitions


def runs(addresses: list[int]) -> list[int]:
    lengths: list[int] = []
    for address in addresses:
        if not lengths or address != addresses[sum(lengths) - 1]:
            lengths.append(1)
        else:
            lengths[-1] += 1
    return lengths


def block_result(addresses: list[int], block_size: int) -> dict[str, float | int]:
    transmitted = 0
    compressed_blocks = 0
    raw_blocks = 0
    token_blocks = 0
    dictionary_blocks = 0
    saved = 0
    previous = None
    blocks = 0
    for start in range(0, len(addresses), block_size):
        block = addresses[start:start + block_size]
        candidate = encode(block, initial_previous=previous)
        raw_length = 4 * len(block)
        token_payload = 1 + len(candidate.bits)  # compressed subtype 0
        dictionary = list(dict.fromkeys(block))
        index_width = (len(dictionary) - 1).bit_length()
        dictionary_payload = 1 + 4 + 4 * len(dictionary) + index_width * len(block)
        # Compressed blocks end in [0..3 zero pad bits][2-bit pad count] and
        # are forced to length == 1 (mod 4). Raw blocks are exactly 4*n bits.
        # The receiver therefore distinguishes modes from framed data length.
        def framed_length(payload_length: int) -> int:
            pad = (1 - (payload_length + 2)) % 4
            return payload_length + pad + 2

        token_length = framed_length(token_payload)
        # Dictionary blocks imply exactly BLOCK_SIZE events. Partial flushes
        # use token/raw mode, avoiding an event-count header.
        dictionary_length = (framed_length(dictionary_payload)
                             if len(block) == block_size else raw_length + 1)
        # External block framing makes length itself the mode.  Compressed mode
        # is selected only for a strict saving; otherwise raw identity is sent.
        selected = min(raw_length, token_length, dictionary_length)
        if selected > raw_length:
            raise AssertionError("raw-bypass expansion")
        if selected < raw_length:
            compressed_blocks += 1
            saved += raw_length - selected
            if token_length <= dictionary_length:
                token_blocks += 1
            else:
                dictionary_blocks += 1
        else:
            raw_blocks += 1
        transmitted += selected
        blocks += 1
        previous = block[-1]
    return {
        "block_size": block_size,
        "blocks": blocks,
        "compressed_blocks": compressed_blocks,
        "raw_blocks": raw_blocks,
        "token_blocks": token_blocks,
        "dictionary_blocks": dictionary_blocks,
        "raw_bypass_ratio": raw_blocks / blocks if blocks else 0.0,
        "bits": transmitted,
        "bits_per_event": transmitted / len(addresses) if addresses else 0.0,
        "compression_ratio": 4 * len(addresses) / transmitted if transmitted else 0.0,
        "saved_bits": saved,
        "mode_header_lower_bound_bits": blocks,
        "inband_header_bits_per_event": blocks / len(addresses) if addresses else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    parser.add_argument("--block-sizes", default="4,8,16,32")
    args = parser.parse_args()
    block_sizes = [int(value) for value in args.block_sizes.split(",")]
    if any(value <= 0 for value in block_sizes):
        raise ValueError("block sizes must be positive")

    output = []
    for manifest_path in sorted(args.trace_dir.glob("*.manifest.json")):
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = metadata["run"]["name"]
        trace_path = args.trace_dir / metadata["trace_file"]
        events = [json.loads(line) for line in trace_path.read_text().splitlines()]
        addresses = [event["logical_source"] for event in events]
        run_lengths = []
        if addresses:
            current = addresses[0]
            length = 0
            for address in addresses:
                if address != current:
                    run_lengths.append(length)
                    current = address
                    length = 0
                length += 1
            run_lengths.append(length)
        base = {
            "name": name,
            "workload": metadata["run"]["workload"],
            "events": len(addresses),
            "zero_order_entropy": entropy(Counter(addresses)),
            "transition_entropy": transition_entropy(addresses),
            "repeat_fraction": 1 - (len(run_lengths) / len(addresses)) if addresses else 0.0,
            "mean_run_length": fmean(run_lengths) if run_lengths else 0.0,
            "max_run_length": max(run_lengths, default=0),
            "run_length_entropy": entropy(Counter(run_lengths)),
        }
        for block_size in block_sizes:
            output.append(base | block_result(addresses, block_size))

    if len({row["name"] for row in output}) != 46:
        raise ValueError("expected all 46 frozen traces")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)

    summary = {"runs": 46, "block_sizes": {}}
    for block_size in block_sizes:
        selected = [row for row in output if row["block_size"] == block_size]
        total_events = sum(row["events"] for row in selected)
        total_bits = sum(row["bits"] for row in selected)
        total_blocks = sum(row["blocks"] for row in selected)
        families = {}
        for family in sorted({row["workload"] for row in selected}):
            members = [row for row in selected if row["workload"] == family]
            families[family] = {
                "runs": len(members),
                "mean_zero_order_entropy": fmean(row["zero_order_entropy"] for row in members),
                "mean_transition_entropy": fmean(row["transition_entropy"] for row in members),
                "mean_repeat_fraction": fmean(row["repeat_fraction"] for row in members),
                "mean_bits_per_event": fmean(row["bits_per_event"] for row in members),
                "mean_raw_bypass_ratio": fmean(row["raw_bypass_ratio"] for row in members),
                "mean_inband_header_bits_per_event": fmean(
                    row["inband_header_bits_per_event"] for row in members),
            }
        summary["block_sizes"][str(block_size)] = {
            "weighted_bits_per_event_without_framing": total_bits / total_events,
            "weighted_compression_ratio_without_framing": 4 * total_events / total_bits,
            "raw_bypass_ratio": sum(row["raw_blocks"] for row in selected) / total_blocks,
            "inband_mode_header_bits_per_event": total_blocks / total_events,
            "families": families,
        }
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"A6_V2_ENTROPY_PASS runs=46 blocks={','.join(map(str, block_sizes))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
