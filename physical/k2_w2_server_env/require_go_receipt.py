#!/usr/bin/env python3
"""Fail closed unless a hash-bound K2 W2 server-environment GO receipt is valid."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from preflight import (PreflightError, load_json, sha_bytes, stable_read,
                       validate_contract, verify_go_document)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_json(args.contract)
        validate_contract(contract)
        contract_sha = sha_bytes(stable_read(args.contract)[0])
        receipt = load_json(args.receipt)
        verify_go_document(receipt, contract_sha)
    except (PreflightError, OSError) as error:
        print(f"K2_W2_SERVER_ENV_RECEIPT_FAIL: {error}", file=sys.stderr)
        return 2
    print(f"K2_W2_SERVER_ENV_RECEIPT_GO sha256={receipt['receipt_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
