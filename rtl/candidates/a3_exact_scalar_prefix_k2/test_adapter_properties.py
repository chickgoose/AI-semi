from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent
CROSS = HERE / "cross_validation"
sys.path.insert(0, str(CROSS))

import ordered_link_model as link_model  # noqa: E402
import run_adapter_properties as properties  # noqa: E402


class OrderedLinkReferenceTests(unittest.TestCase):
    def test_blocked_head_prevents_younger_bypass_and_overflow(self) -> None:
        model = link_model.OrderedLinkModel()
        model.step(
            rst=False, offer_count=2, offer_addr0=4,
            offer_addr1=11, retire_ready=0,
        )
        transition = model.step(
            rst=False, offer_count=2, offer_addr0=5,
            offer_addr1=10, retire_ready=2,
        )
        self.assertEqual(transition.outputs.retire_valid, 0b01)
        self.assertFalse(transition.outputs.offer_ready)
        self.assertEqual(transition.retired, ())
        self.assertEqual(transition.accepted, ())
        self.assertEqual(transition.after, (4, 11))

    def test_head_retire_and_single_refill_preserve_order(self) -> None:
        model = link_model.OrderedLinkModel()
        model.entries = (4, 11)
        transition = model.step(
            rst=False, offer_count=1, offer_addr0=5,
            offer_addr1=0, retire_ready=1,
        )
        self.assertEqual(transition.retired, (4,))
        self.assertEqual(transition.accepted, (5,))
        self.assertEqual(transition.after, (11, 5))

    def test_concrete_transition_space_is_exhaustive(self) -> None:
        result = properties.exhaustive_reference_properties()
        self.assertEqual(result["concrete_transition_cases"], 273 * 273 * 4)
        self.assertGreater(result["rejected_overflow_cases"], 0)
        self.assertGreater(result["simultaneous_retire_refill_cases"], 0)


class OrderedLinkRTLPropertyTests(unittest.TestCase):
    def test_fail_closed_when_tool_is_missing(self) -> None:
        with self.assertRaises(properties.PropertyGateError):
            properties.find_tool(
                "A3_K2_PROPERTIES_TEST_MISSING", (),
                (pathlib.Path("/definitely/not/a/tool"),),
            )

    def test_sv_lockstep_and_real_mutations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-k2-adapter-test-") as temporary:
            receipt = pathlib.Path(temporary) / "receipt.json"
            result = properties.execute(receipt)
            self.assertTrue(receipt.is_file())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["sv_lockstep"]["control_cross_product"], 36)
        self.assertEqual(result["sv_lockstep"]["logical_queue_states"], 273)
        self.assertEqual(set(result["mutations"]), set(properties.MUTATIONS))
        self.assertTrue(all(
            row["status"] == "EXPECTED_FAIL_CAUGHT"
            for row in result["mutations"].values()
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
