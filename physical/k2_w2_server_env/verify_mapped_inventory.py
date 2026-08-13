#!/usr/bin/env python3
"""Post-Genus mapped inventory gate, bound to a server-environment GO receipt."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from preflight import (PreflightError, canonical, inspect_mapped_inventory,
                       load_json, sha_bytes, stable_read, validate_contract,
                       verify_go_document, write_result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--mapped-netlist", type=Path, required=True)
    parser.add_argument("--expected-netlist-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema": "k2_w2_mapped_inventory_receipt_v1",
        "qualification_status": "FAIL",
    }
    try:
        contract = load_json(args.contract)
        validate_contract(contract)
        contract_sha = sha_bytes(stable_read(args.contract)[0])
        environment = load_json(args.environment_receipt)
        verify_go_document(environment, contract_sha)
        netlist, _ = stable_read(args.mapped_netlist)
        netlist_sha = sha_bytes(netlist)
        if netlist_sha != args.expected_netlist_sha256:
            raise PreflightError("mapped netlist SHA mismatch")
        result.update({
            "qualification_status": "PROVEN_MAPPED_INVENTORY",
            "contract_sha256": contract_sha,
            "environment_receipt_sha256": environment["receipt_sha256"],
            "mapped_netlist": {"path": str(args.mapped_netlist),
                               "sha256": netlist_sha},
            "inventory": inspect_mapped_inventory(
                netlist, contract["technology"]["mapped_inventory"]),
        })
    except (PreflightError, OSError, UnicodeError) as error:
        result["failure"] = str(error)
    result["receipt_sha256"] = sha_bytes(canonical(result))
    write_result(args.output, result,
                 exclusive=result["qualification_status"] == "PROVEN_MAPPED_INVENTORY")
    if result["qualification_status"] != "PROVEN_MAPPED_INVENTORY":
        print(f"K2_W2_MAPPED_INVENTORY_FAIL: {result['failure']}", file=sys.stderr)
        return 2
    print(f"K2_W2_MAPPED_INVENTORY_GO sha256={result['receipt_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
