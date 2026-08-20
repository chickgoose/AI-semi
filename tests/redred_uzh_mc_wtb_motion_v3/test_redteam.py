"""Adversarial unit tests for architecture-neutral metric-v3 contracts."""

from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.redteam import (
    Finding,
    RedTeamContractError,
    ReferenceIdentity,
    audit_event_denominators,
    audit_fractional_phase_bias,
    audit_negative_control_order,
    audit_reference_identity,
    audit_window_selection,
    merge_findings,
    require_clean,
)


def codes(findings):
    return {finding.code for finding in findings}


class EventDenominatorAuditTests(unittest.TestCase):
    def setUp(self):
        self.expected = (100, 101, 102, 103)
        self.arms = {
            "SENSOR_FIXED": self.expected,
            "MC_CORRECT": self.expected,
            "MC_WRONG": self.expected,
            "MC_DELAYED": self.expected,
        }

    def test_pristine_common_denominator_passes(self):
        self.assertEqual(
            audit_event_denominators(
                self.expected, self.arms, oof_event_ids=(102,)
            ),
            (),
        )

    def test_oof_deletion_is_never_a_valid_filter(self):
        arms = dict(self.arms)
        arms["MC_CORRECT"] = (100, 101, 103)
        findings = audit_event_denominators(
            self.expected, arms, oof_event_ids=(102,)
        )
        self.assertIn("OOF_DELETION", codes(findings))
        self.assertIn("EVENT_DROP", codes(findings))
        self.assertIn("ARM_LOCAL_DENOMINATOR", codes(findings))

    def test_arm_local_denominator_swap_is_rejected(self):
        arms = dict(self.arms)
        arms["MC_WRONG"] = (100, 101, 102, 999)
        findings = audit_event_denominators(self.expected, arms)
        self.assertIn("EVENT_DROP", codes(findings))
        self.assertIn("UNEXPECTED_EVENT", codes(findings))
        self.assertIn("ARM_LOCAL_DENOMINATOR", codes(findings))

    def test_duplicate_and_drop_cannot_cancel_in_the_count(self):
        arms = dict(self.arms)
        arms["MC_DELAYED"] = (100, 101, 101, 103)
        findings = audit_event_denominators(self.expected, arms)
        self.assertIn("EVENT_DUPLICATE", codes(findings))
        self.assertIn("EVENT_DROP", codes(findings))
        self.assertIn("ARM_LOCAL_DENOMINATOR", codes(findings))

    def test_reordered_full_cohort_is_not_canonical(self):
        arms = dict(self.arms)
        arms["MC_CORRECT"] = (100, 102, 101, 103)
        findings = audit_event_denominators(self.expected, arms)
        self.assertEqual(codes(findings), {"EVENT_ORDER_MISMATCH"})


class NegativeControlAuditTests(unittest.TestCase):
    def test_correct_pose_strictly_beats_wrong_and_delayed(self):
        self.assertEqual(
            audit_negative_control_order(
                {"MC_CORRECT": 1.0, "MC_WRONG": 2.0, "MC_DELAYED": 3.0}
            ),
            (),
        )

    def test_wrong_pose_favored_is_rejected(self):
        findings = audit_negative_control_order(
            {"MC_CORRECT": 1.0, "MC_WRONG": 0.9, "MC_DELAYED": 3.0}
        )
        self.assertEqual(codes(findings), {"NEGATIVE_CONTROL_FAVORED"})
        self.assertEqual(findings[0].subject, "MC_WRONG")

    def test_delayed_pose_favored_is_rejected(self):
        findings = audit_negative_control_order(
            {"MC_CORRECT": 1.0, "MC_WRONG": 2.0, "MC_DELAYED": 0.5}
        )
        self.assertEqual(codes(findings), {"NEGATIVE_CONTROL_FAVORED"})
        self.assertEqual(findings[0].subject, "MC_DELAYED")

    def test_equal_control_is_nonseparated_not_a_pass(self):
        findings = audit_negative_control_order(
            {"MC_CORRECT": 1.0, "MC_WRONG": 1.0, "MC_DELAYED": 2.0}
        )
        self.assertEqual(codes(findings), {"NEGATIVE_CONTROL_NOT_SEPARATED"})


class FractionalPhaseAuditTests(unittest.TestCase):
    POINTS = ((0.0, 0.0), (3.0, 4.0), (8.0, 1.0))

    @staticmethod
    def pairwise_self_energy(points):
        return sum(
            math.dist(points[left], points[right]) ** 2
            for left in range(len(points))
            for right in range(left)
        )

    @staticmethod
    def integer_biased_self_energy(points):
        return sum(
            1.0
            if x.is_integer() and y.is_integer()
            else (math.modf(x)[0] ** 2 + math.modf(y)[0] ** 2)
            for x, y in points
        )

    def test_translation_invariant_energy_passes_fractional_probe(self):
        audit = audit_fractional_phase_bias(
            self.POINTS, self.pairwise_self_energy
        )
        self.assertEqual(audit.findings, ())
        self.assertEqual(len(audit.measurements), 4)

    def test_integer_vs_fractional_self_energy_bias_is_rejected(self):
        audit = audit_fractional_phase_bias(
            self.POINTS, self.integer_biased_self_energy
        )
        self.assertEqual(
            codes(audit.findings), {"FRACTIONAL_PHASE_SELF_ENERGY_BIAS"}
        )

    def test_crashing_energy_fails_closed(self):
        def broken(_points):
            raise RuntimeError("deliberate")

        audit = audit_fractional_phase_bias(self.POINTS, broken)
        self.assertEqual(codes(audit.findings), {"PHASE_ENERGY_EXCEPTION"})
        self.assertEqual(audit.measurements, ())


class WindowSelectionAuditTests(unittest.TestCase):
    WINDOWS = ("w0", "w1", "w2")

    def test_all_frozen_windows_and_frozen_primary_pass(self):
        self.assertEqual(
            audit_window_selection(
                self.WINDOWS,
                self.WINDOWS,
                self.WINDOWS,
                frozen_primary_window="w1",
                reported_primary_window="w1",
            ),
            (),
        )

    def test_best_window_only_publication_is_rejected(self):
        findings = audit_window_selection(
            self.WINDOWS,
            self.WINDOWS,
            ("w2",),
            frozen_primary_window=None,
            reported_primary_window="w2",
        )
        self.assertIn("POST_HOC_WINDOW_SUBSET", codes(findings))
        self.assertIn("POST_HOC_PRIMARY_WINDOW", codes(findings))

    def test_primary_cannot_change_after_scores_are_seen(self):
        findings = audit_window_selection(
            self.WINDOWS,
            self.WINDOWS,
            self.WINDOWS,
            frozen_primary_window="w0",
            reported_primary_window="w2",
        )
        self.assertEqual(codes(findings), {"POST_HOC_PRIMARY_WINDOW"})

    def test_best_of_rule_cannot_replace_frozen_all_window_rule(self):
        findings = audit_window_selection(
            self.WINDOWS,
            self.WINDOWS,
            self.WINDOWS,
            frozen_selection_rule="MEAN_ALL_WINDOWS",
            reported_selection_rule="BEST_OBSERVED_WINDOW",
        )
        self.assertEqual(codes(findings), {"POST_HOC_WINDOW_SELECTION_RULE"})


class ReferenceIdentityAuditTests(unittest.TestCase):
    def setUp(self):
        self.reference = ReferenceIdentity(
            frame_id="reference-camera-at-t0",
            epoch_ns=41_321_000_000,
            anchor_sha256="a" * 64,
            projection_model_id="radtan-v1",
        )
        self.arms = {
            "SENSOR_FIXED": self.reference,
            "MC_CORRECT": self.reference,
            "MC_WRONG": self.reference,
            "MC_DELAYED": self.reference,
        }

    def test_common_reference_passes(self):
        self.assertEqual(
            audit_reference_identity(
                self.arms, required_arms=tuple(self.arms)
            ),
            (),
        )

    def test_reference_epoch_mismatch_is_rejected(self):
        arms = dict(self.arms)
        arms["MC_CORRECT"] = ReferenceIdentity(
            frame_id=self.reference.frame_id,
            epoch_ns=self.reference.epoch_ns + 1,
            anchor_sha256=self.reference.anchor_sha256,
            projection_model_id=self.reference.projection_model_id,
        )
        findings = audit_reference_identity(
            arms, required_arms=tuple(self.arms)
        )
        self.assertEqual(codes(findings), {"REFERENCE_MISMATCH"})
        self.assertIn("epoch_ns", findings[0].message)

    def test_arm_cannot_omit_reference_identity(self):
        arms = dict(self.arms)
        del arms["MC_DELAYED"]
        findings = audit_reference_identity(
            arms, required_arms=tuple(self.arms)
        )
        self.assertEqual(codes(findings), {"REFERENCE_MISSING"})

    def test_nonbaseline_reference_digest_must_be_canonical(self):
        arms = dict(self.arms)
        arms["MC_WRONG"] = ReferenceIdentity(
            frame_id=self.reference.frame_id,
            epoch_ns=self.reference.epoch_ns,
            anchor_sha256="NOT-A-DIGEST",
            projection_model_id=self.reference.projection_model_id,
        )
        findings = audit_reference_identity(
            arms, required_arms=tuple(self.arms)
        )
        self.assertIn("REFERENCE_IDENTITY_INVALID", codes(findings))
        self.assertIn("REFERENCE_MISMATCH", codes(findings))


class CompositionTests(unittest.TestCase):
    def test_multiple_audits_compose_and_reject_together(self):
        denominator = audit_event_denominators(
            (1, 2),
            {"MC_CORRECT": (1,), "MC_WRONG": (1, 2)},
            oof_event_ids=(2,),
        )
        controls = audit_negative_control_order(
            {"MC_CORRECT": 2.0, "MC_WRONG": 1.0, "MC_DELAYED": 3.0}
        )
        combined = merge_findings(denominator, controls, denominator)
        self.assertIn("OOF_DELETION", codes(combined))
        self.assertIn("NEGATIVE_CONTROL_FAVORED", codes(combined))
        self.assertEqual(len(combined), len(set(combined)))
        with self.assertRaises(RedTeamContractError) as context:
            require_clean(combined)
        self.assertEqual(context.exception.findings, tuple(sorted(combined)))

    def test_require_clean_accepts_no_findings(self):
        self.assertIsNone(require_clean(()))
        self.assertEqual(
            merge_findings(
                (Finding("X", "a", "b"),),
                (Finding("X", "a", "b"),),
            ),
            (Finding("X", "a", "b"),),
        )


if __name__ == "__main__":
    unittest.main()
