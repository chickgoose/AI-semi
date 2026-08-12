from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import oracle
import run


class OracleTests(unittest.TestCase):
    def test_persistent_ratio(self) -> None:
        result = oracle.persistent_probe(120)
        self.assertEqual(result["row_opportunities_0_1_2_3"], [20, 100, 100, 20])
        self.assertEqual(result["address_grants"], 240)

    def test_second_grant_is_literal_scalar_fold(self) -> None:
        state = oracle.PolicyState()
        pair, final_state = oracle.scalar_prefix_k2(0xFFFF, state)
        first, after_first = oracle.scalar_step(0xFFFF, state)
        self.assertEqual(pair[0], first)
        second, after_second = oracle.scalar_step(0xFFFF & ~(1 << first), after_first)
        self.assertEqual(pair[1], second)
        self.assertEqual(final_state, after_second)

    def test_atomic_stall_holds(self) -> None:
        model = oracle.AtomicK2Model()
        model.step(rst=True, ready=False, pending=0)
        model.step(rst=False, ready=True, pending=0xFFFF)
        before = (model.grants, model.state, model.post_state)
        for pending in (0xFFFF, 0xF11F, 0x7117):
            self.assertEqual(model.step(rst=False, ready=False, pending=pending), ())
            self.assertEqual((model.grants, model.state, model.post_state), before)


class QualificationTests(unittest.TestCase):
    def test_pinned_a1_common_tb_provenance(self) -> None:
        frozen = run.verify_frozen()
        self.assertEqual(frozen["common_tb_sha256"], run.EXPECTED[run.COMMON_TB])
        self.assertEqual(frozen["common_tb_git_blob_sha1"],
                         run.COMMON_TB_BLOB_SHA1)
        self.assertEqual(frozen["common_tb_source_commit"],
                         run.COMMON_TB_SOURCE_COMMIT)

    def test_common_occurrence_precedes_same_cycle_fire(self) -> None:
        self.assertEqual(run.qualify_common_semantic_probe(),
                         run.COMMON_SEMANTIC_EXPECTED)
        with self.assertRaisesRegex(run.GateError, "COMMON_SEMANTIC_MISMATCH"):
            run.qualify_common_semantic_probe(
                mutate_fire_before_occurrence=True
            )

    def test_fail_closed_bad_tool_override(self) -> None:
        with self.assertRaises(run.GateError):
            run.find_tool("A3_K2_TEST_MISSING", (), (Path("/definitely/missing"),))

    def test_full_local_qualification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-k2-test-") as temporary:
            result = run.execute(Path(temporary) / "receipt.json")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["execution"]["frozen_lockstep_runs"], 72)
        self.assertEqual(result["persistent_probe"]["row_opportunities_0_1_2_3"],
                         [20, 100, 100, 20])
        self.assertEqual(set(result["mutations"]),
                         {"A3_K2_MUT_STALE", "A3_K2_MUT_DUP",
                          "A3_K2_MUT_STATE_ADV",
                          "A3_K2_MUT_FIRE_BEFORE_OCCURRENCE"})


if __name__ == "__main__":
    unittest.main()
