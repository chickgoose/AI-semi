#!/usr/bin/env python3
"""Fail-closed checks for the REDRED single-edge GPDK045 legality matrix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = Path(__file__).with_name("legality_matrix.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    require(
        matrix.get("schema") == "redred_single_edge_gpdk045_legality_matrix_v1",
        "schema mismatch",
    )

    rule = matrix["decision_rule"]
    require(rule["operator"] == "ALL", "release decision must use ALL gates")
    absence = rule["absence_policy"].lower()
    require("never organizer approval" in absence, "absence/approval rule was weakened")

    gates = matrix["gates"]
    by_id = {gate["id"]: gate for gate in gates}
    require(len(by_id) == len(gates), "duplicate gate id")
    require(set(by_id) == set(rule["required_gate_ids"]), "required gate set mismatch")
    require(all(gate["status"] in {"HOLD", "GO"} for gate in gates), "bad gate state")
    expected_decision = "GO" if all(gate["status"] == "GO" for gate in gates) else "HOLD"
    require(matrix["decision"] == expected_decision, "aggregate decision is not fail-closed")
    require(matrix["decision"] == "HOLD", "current repository must remain HOLD")
    require(by_id["G01_ORGANIZER_PRIMARY_RULE"]["status"] == "HOLD", "organizer gate promoted")

    for row in matrix["repository_evidence"]:
        path = ROOT / row["path"]
        require(path.is_file(), f"missing repository evidence: {row['path']}")
        require(sha256(path) == row["sha256"], f"evidence hash mismatch: {row['path']}")

    external_hashes = {row["sha256"] for row in matrix["expected_external_artifacts"]}
    require(len(external_hashes) == len(matrix["expected_external_artifacts"]), "duplicate external hash")
    for row in matrix["expected_external_artifacts"]:
        require(row["present_in_checkout"] is False, f"external state must be false: {row['id']}")

    for row in matrix["local_test_doubles"]:
        path = ROOT / row["path"]
        require(path.is_file(), f"missing declared test double: {row['path']}")
        actual = sha256(path)
        require(actual == row["sha256"], f"test-double hash mismatch: {row['path']}")
        require(actual not in external_hashes, f"test double aliases real artifact: {row['path']}")

    cell_rules = matrix["recorded_real_cell_contracts"]
    require(
        "does not satisfy organizer approval" in cell_rules["forbidden_absence_rule"],
        "forbidden primitive absence was allowed to imply approval",
    )
    require(
        matrix["team_profiles_not_organizer_rules"]["inherited_complete_endpoint_6p5ns"]["output_load_pf"]
        == 0.01,
        "recorded team load changed",
    )

    print("PASS: REDRED single-edge GPDK045 legality matrix is fail-closed (decision=HOLD)")


if __name__ == "__main__":
    main()
