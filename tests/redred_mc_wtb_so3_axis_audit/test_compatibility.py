from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import subprocess
import sys
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit.compatibility import (
    OFFICIAL_RESULT_BODY_SHA256,
    Original24CompatibilityError,
    evaluate_original_24,
    load_original_24_neutral_inputs,
    verify_original_24_compatibility,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    CAVRegistryEvaluation,
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


ASSAY_DIR = Path("/tmp/mcwtb-stage4-official-assay-20260821-v3")
SEAL_DIR = Path("/tmp/mcwtb-stage4-official-seal-20260822-v1")
RESULT_PATH = Path("/tmp/mcwtb-stage4-official-score-20260822-v1.json")


def neutral_input_sha256(windows):
    registry_mapping = [window.registry.to_mapping() for window in windows]
    return canonical_sha256({
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": registry_mapping,
        "windows": [
            {
                "window_id": window.registry.window_id,
                "events": [
                    event.to_content_mapping() for event in window.input_events
                ],
                "poses": [pose.to_content_mapping() for pose in window.input_poses],
            }
            for window in windows
        ],
    })


@unittest.skipUnless(
    ASSAY_DIR.is_dir() and SEAL_DIR.is_dir() and RESULT_PATH.is_file(),
    "frozen original-24 Stage4 artifacts are not mounted",
)
class Original24CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evaluation = evaluate_original_24(ASSAY_DIR)

    def test_neutral_evaluator_exactly_reproduces_frozen_current_cav(self):
        report = verify_original_24_compatibility(
            self.evaluation,
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

    def test_float_tolerances_cannot_exceed_frozen_finite_caps(self):
        for value in (1.0e-11, math.nan, math.inf, -1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    Original24CompatibilityError, "frozen finite cap"
                ):
                    verify_original_24_compatibility(
                        self.evaluation,
                        seal_dir=SEAL_DIR,
                        result_path=RESULT_PATH,
                        float_rel_tol=value,
                    )

    def test_official_assay_root_digest_cannot_be_rebound(self):
        with self.assertRaisesRegex(
            Original24CompatibilityError, "cannot be overridden"
        ):
            load_original_24_neutral_inputs(
                ASSAY_DIR, expected_manifest_sha256="0" * 64
            )

    def test_mutated_neutral_input_digest_fails_before_compatibility(self):
        mutated = replace(self.evaluation)
        object.__setattr__(mutated, "neutral_input_sha256", "0" * 64)
        with self.assertRaisesRegex(
            Original24CompatibilityError, "neutral evaluation integrity"
        ):
            verify_original_24_compatibility(
                mutated,
                seal_dir=SEAL_DIR,
                result_path=RESULT_PATH,
            )

    def test_rebound_event_input_with_stale_outputs_fails_replay(self):
        original_window = self.evaluation.windows[0]
        event_index = next(
            index
            for index, event in enumerate(original_window.input_events)
            if event.is_query
        )
        original_event = original_window.input_events[event_index]
        changed_ray = tuple(-component for component in original_event.sensor_ray)
        changed_event_digest = canonical_event_content_sha256(
            original_event.event_id,
            original_event.timestamp_ns,
            original_event.polarity,
            original_event.is_query,
            changed_ray,
            original_event.causal_pose_source_index,
            original_event.transform_guard_valid,
        )
        changed_event = NeutralEventInput(
            original_event.event_id,
            original_event.timestamp_ns,
            original_event.polarity,
            original_event.is_query,
            changed_ray,
            original_event.causal_pose_source_index,
            changed_event_digest,
            original_event.transform_guard_valid,
        )
        changed_events = (
            original_window.input_events[:event_index]
            + (changed_event,)
            + original_window.input_events[event_index + 1:]
        )
        changed_window = replace(original_window, input_events=changed_events)
        changed_windows = (changed_window,) + self.evaluation.windows[1:]
        mutated = CAVRegistryEvaluation(
            self.evaluation.registry_sha256,
            neutral_input_sha256(changed_windows),
            changed_windows,
        )

        with self.assertRaises(Original24CompatibilityError) as raised:
            verify_original_24_compatibility(
                mutated,
                seal_dir=SEAL_DIR,
                result_path=RESULT_PATH,
            )
        self.assertRegex(str(raised.exception), "neutral evaluation integrity")
        self.assertIsInstance(raised.exception.__cause__, CurrentCAVEvaluationError)
        self.assertRegex(
            str(raised.exception.__cause__), "query event outputs.*retained-input replay"
        )

    def test_campaign_window_bounds_are_exact(self):
        original_window = self.evaluation.windows[0]
        registry = original_window.registry
        changed_registry = NeutralRegistryWindow(
            registry.window_id,
            registry.warmup_start_ns_inclusive + 1,
            registry.query_start_ns_inclusive,
            registry.query_end_ns_exclusive,
        )
        changed_window = replace(original_window, registry=changed_registry)
        changed_windows = (changed_window,) + self.evaluation.windows[1:]
        registry_mapping = [window.registry.to_mapping() for window in changed_windows]
        forged = CAVRegistryEvaluation(
            canonical_sha256(registry_mapping),
            neutral_input_sha256(changed_windows),
            changed_windows,
        )
        with self.assertRaisesRegex(
            Original24CompatibilityError,
            "neutral evaluation integrity|campaign registry digest|registry bounds",
        ):
            verify_original_24_compatibility(
                forged,
                seal_dir=SEAL_DIR,
                result_path=RESULT_PATH,
            )


class ImportGraphTests(unittest.TestCase):
    def test_compatibility_import_graph_never_loads_scoring(self):
        script = """
import importlib.abc
import sys

class RejectScoring(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if 'stage4_scoring' in fullname:
            raise RuntimeError('forbidden scoring import: ' + fullname)
        return None

sys.meta_path.insert(0, RejectScoring())
import benchmarks.redred_mc_wtb_so3_axis_audit.compatibility
loaded = sorted(name for name in sys.modules if 'stage4_scoring' in name)
if loaded:
    raise SystemExit('scoring modules loaded: ' + repr(loaded))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[2]),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
