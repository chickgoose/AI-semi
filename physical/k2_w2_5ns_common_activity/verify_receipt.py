#!/usr/bin/env python3
"""Independently verify an already-produced W2 5 ns activity campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import activity_lib as lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_root", type=Path)
    args = parser.parse_args()
    root = args.campaign_root.resolve(strict=True)
    receipt_path = root / "campaign-receipt.json"
    lib.require_sealed_receipt(receipt_path)
    receipt = json.loads(lib.stable_bytes(receipt_path))
    lib.verify_campaign_receipt(receipt, root)
    sentinel_path = root / "campaign.success"
    lib.require_sealed_receipt(sentinel_path)
    sentinel = lib.stable_bytes(sentinel_path).decode("utf-8")
    expected = f"W2_5NS_COMMON_ACTIVITY_SUCCESS receipt_sha256={lib.digest(receipt_path)}\n"
    if sentinel != expected or not re.fullmatch(
        r"W2_5NS_COMMON_ACTIVITY_SUCCESS receipt_sha256=[0-9a-f]{64}\n",
        sentinel,
    ):
        raise lib.ActivityError("campaign success sentinel mismatch")
    print(f"W2_5NS_COMMON_ACTIVITY_RECEIPT_PASS root={root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (lib.ActivityError, json.JSONDecodeError) as exc:
        print(f"W2_5NS_COMMON_ACTIVITY_RECEIPT_FAIL error={exc}", file=sys.stderr)
        raise SystemExit(1)
