from __future__ import annotations

from pathlib import Path
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit.compatibility import (
    OFFICIAL_RESULT_BODY_SHA256,
    evaluate_original_24,
    verify_original_24_compatibility,
)


ASSAY_DIR = Path("/tmp/mcwtb-stage4-official-assay-20260821-v3")
SEAL_DIR = Path("/tmp/mcwtb-stage4-official-seal-20260822-v1")
RESULT_PATH = Path("/tmp/mcwtb-stage4-official-score-20260822-v1.json")


@unittest.skipUnless(
    ASSAY_DIR.is_dir() and SEAL_DIR.is_dir() and RESULT_PATH.is_file(),
    "frozen original-24 Stage4 artifacts are not mounted",
)
class Original24CompatibilityTests(unittest.TestCase):
    def test_neutral_evaluator_exactly_reproduces_frozen_current_cav(self):
        evaluation = evaluate_original_24(ASSAY_DIR)
        report = verify_original_24_compatibility(
            evaluation,
            seal_dir=SEAL_DIR,
            result_path=RESULT_PATH,
        )

        self.assertEqual(report.window_count, 24)
        self.assertEqual(report.event_count, 8_914)
        self.assertEqual(report.exact_decision_count, 8_914)
        self.assertEqual(report.exact_reference_identity_count, 17_828)
        self.assertEqual(report.float_loss_count, 26_742)
        self.assertEqual(report.positive_windows, 21)
        self.assertAlmostEqual(report.all_event_effect, 0.04559204357832747)
        self.assertEqual(report.result_body_sha256, OFFICIAL_RESULT_BODY_SHA256)


if __name__ == "__main__":
    unittest.main()
