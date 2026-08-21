from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_existing_registry,
    validate_registry,
)


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_comparison_contract()

    def test_frozen_contract_and_existing_registry_validate(self) -> None:
        self.assertEqual(
            self.contract.canonical_sha256,
            "f6350c8fe24a2ee36d988aedaba265ef2a81f19539bd2cb0a4b70b0765d9d1bd",
        )
        receipt = validate_existing_registry(self.contract)
        self.assertEqual(receipt.window_count, 24)
        self.assertEqual(receipt.canonical_sha256, self.contract.registry["sha256"])
        self.assertEqual(
            receipt.forbidden_interval_ns, (43320750000, 43322000000)
        )

    def test_contract_rejects_duplicate_key_extra_field_and_wrong_type(self) -> None:
        original = self.contract.as_dict()
        cases = []
        cases.append(
            '{"schema":"a","schema":"b"}'
        )
        extra = copy.deepcopy(original)
        extra["unfrozen"] = 1
        cases.append(json.dumps(extra))
        wrong_type = copy.deepcopy(original)
        wrong_type["registry"]["window_count"] = True
        cases.append(json.dumps(wrong_type))
        for index, payload in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "contract.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ContractError):
                    load_comparison_contract(path)

    def test_registry_rejects_hash_mutation_and_forbidden_overlap(self) -> None:
        rows = [dict(row) for row in window_registry()]
        changed = copy.deepcopy(rows)
        changed[0]["query_start_ns_inclusive"] += 1
        with self.assertRaisesRegex(ContractError, "hash"):
            validate_registry(self.contract, changed)

        overlap = copy.deepcopy(rows)
        overlap[18] = {
            "window_id": overlap[18]["window_id"],
            "warmup_start_ns_inclusive": 43320750000,
            "query_start_ns_inclusive": 43321000000,
            "query_end_ns_exclusive": 43322000000,
        }
        with self.assertRaisesRegex(ContractError, "forbidden"):
            validate_registry(self.contract, overlap)

    def test_canonical_json_is_order_independent_for_objects_and_ordered_for_arrays(self) -> None:
        left = {"z": 1, "a": [2, 3]}
        right = {"a": [2, 3], "z": 1}
        self.assertEqual(canonical_json_bytes(left), b'{"a":[2,3],"z":1}\n')
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))
        self.assertNotEqual(
            canonical_sha256({"a": [2, 3]}), canonical_sha256({"a": [3, 2]})
        )
        with self.assertRaises(ContractError):
            canonical_json_bytes({"not_finite": float("nan")})


if __name__ == "__main__":
    unittest.main()
