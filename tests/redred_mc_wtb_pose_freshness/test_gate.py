from __future__ import annotations

from dataclasses import replace
import unittest

from benchmarks.redred_mc_wtb_pose_freshness import (
    UINT128_MAX,
    UINT64_MAX,
    FreshnessAction,
    FreshnessContractError,
    FreshnessProfile,
    PoseEpochEvidence,
    PoseFreshnessConfig,
    PoseSampleMetadata,
    ReasonCode,
    ceil_div,
    config_digest,
    evidence_digest,
    qualify_pose_freshness,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def config(profile=FreshnessProfile.AGE_TIMES_RATE, **changes):
    values = dict(
        profile=profile,
        fractional_bits=8,
        hard_max_cover_age_ns=2_000_000,
        max_rate_sample_interval_ns=1_500_000,
        max_pixel_error_q=200,
        pixel_rate_floor_q_per_second=50,
        static_error_margin_q=3,
        rate_growth_num=2,
        rate_growth_den=1,
        pixel_gain_q_per_rad=200 * (1 << 8),
        expected_timebase_id="camera-ns-epoch-0",
        expected_timebase_sha256=SHA_E,
        expected_pose_stream_id="pose-stream-v1",
        expected_calibration_id="calibration-v1",
        expected_calibration_sha256=SHA_A,
        expected_pixel_gain_profile_sha256=SHA_B,
        profile_id="freshness-profile-v1",
    )
    values.update(changes)
    return PoseFreshnessConfig(**values)


def pose(pose_id, sequence, timestamp_ns, **changes):
    values = dict(
        pose_id=pose_id,
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        timebase_id="camera-ns-epoch-0",
        pose_sha256=SHA_C if sequence == 10 else SHA_D,
        value_valid=True,
    )
    values.update(changes)
    return PoseSampleMetadata(**values)


def evidence(**changes):
    previous = pose("p10", 10, 0)
    latest = pose("p11", 11, 1_000_000)
    values = dict(
        epoch_id=7,
        epoch_start_ns=1_500_000,
        epoch_end_ns=2_000_000,
        decision_timestamp_ns=1_500_000,
        timebase_id="camera-ns-epoch-0",
        timebase_sha256=SHA_E,
        clock_alignment_valid=True,
        pose_stream_id="pose-stream-v1",
        pose_snapshot_id="snapshot-7",
        pose_snapshot_sha256=SHA_D,
        previous_pose=previous,
        latest_pose=latest,
        latest_available_pose_id="p11",
        latest_available_pose_sequence=11,
        poses_are_immediate_predecessors=True,
        relative_angle_upper_urad=1_000,
        calibration_id="calibration-v1",
        calibration_sha256=SHA_A,
        pixel_gain_profile_sha256=SHA_B,
        rate_bound_assumption_authorized=True,
    )
    values.update(changes)
    return PoseEpochEvidence(**values)


class CeilingDivisionTests(unittest.TestCase):
    def test_exact_ceiling_division(self):
        self.assertEqual(ceil_div(0, 7), 0)
        self.assertEqual(ceil_div(14, 7), 2)
        self.assertEqual(ceil_div(15, 7), 3)
        self.assertEqual(ceil_div((1 << 128) - 1, UINT64_MAX), (1 << 64) + 1)
        self.assertEqual(ceil_div(UINT128_MAX, UINT128_MAX), 1)
        self.assertEqual(ceil_div(UINT64_MAX + 2, UINT64_MAX + 1), 2)

    def test_invalid_ceiling_division_fails(self):
        with self.assertRaises(FreshnessContractError):
            ceil_div(1, 0)
        with self.assertRaises(FreshnessContractError):
            ceil_div(True, 1)


class FreshnessGateTests(unittest.TestCase):
    def test_age_only_passes_without_rate_authorization(self):
        sample = evidence(rate_bound_assumption_authorized=False)
        result = qualify_pose_freshness(sample, config(FreshnessProfile.AGE_ONLY))
        self.assertTrue(result.pose_reliable)
        self.assertEqual(result.action, FreshnessAction.POSE_QUALIFIED)
        self.assertEqual(result.reason_codes, ())
        self.assertEqual(result.pose_age_at_start_ns, 500_000)
        self.assertEqual(result.cover_age_ns, 1_000_000)
        self.assertIsNone(result.total_pixel_error_q)

    def test_complete_epoch_cover_age_not_start_age_controls_gate(self):
        sample = evidence(epoch_end_ns=2_600_000)
        policy = config(FreshnessProfile.AGE_ONLY, hard_max_cover_age_ns=1_500_000)
        result = qualify_pose_freshness(sample, policy)
        self.assertEqual(result.pose_age_at_start_ns, 500_000)
        self.assertEqual(result.cover_age_ns, 1_600_000)
        self.assertFalse(result.pose_reliable)
        self.assertIn(ReasonCode.HARD_COVER_AGE_EXCEEDED.value, result.reason_codes)
        self.assertEqual(result.action, FreshnessAction.UNRELIABLE_SENSOR_FIXED_BYPASS)

    def test_age_times_rate_uses_exact_integer_equations(self):
        result = qualify_pose_freshness(evidence(), config())
        self.assertTrue(result.pose_reliable)
        # ceil(51200 * 1000 / 1e6) = 52 Q8 pixels.
        self.assertEqual(result.recent_displacement_q, 52)
        # ceil(2 * 52 * 1e6 / 1e6) = 104.
        self.assertEqual(result.recent_rate_error_q, 104)
        # ceil(50 * 1e6 / 1e9) = 1; max(104,1)+3 = 107.
        self.assertEqual(result.rate_floor_error_q, 1)
        self.assertEqual(result.total_pixel_error_q, 107)

    def test_pixel_error_limit_is_inclusive_and_fail_closed(self):
        self.assertTrue(
            qualify_pose_freshness(evidence(), config(max_pixel_error_q=107)).pose_reliable
        )
        failed = qualify_pose_freshness(evidence(), config(max_pixel_error_q=106))
        self.assertFalse(failed.pose_reliable)
        self.assertIn(ReasonCode.PIXEL_ERROR_LIMIT_EXCEEDED.value, failed.reason_codes)

    def test_immediate_predecessor_and_latest_available_are_mandatory(self):
        sample = evidence(
            latest_pose=pose("p12", 12, 1_000_000),
            poses_are_immediate_predecessors=False,
        )
        result = qualify_pose_freshness(sample, config())
        self.assertIn(ReasonCode.NON_IMMEDIATE_PREDECESSOR.value, result.reason_codes)
        self.assertIn(
            ReasonCode.LATEST_POSE_NOT_LATEST_AVAILABLE.value,
            result.reason_codes,
        )
        self.assertFalse(result.pose_reliable)

    def test_timebase_stream_and_hash_mismatches_are_explicit(self):
        sample = evidence(
            previous_pose=pose("p10", 10, 0, timebase_id="other-timebase"),
            timebase_sha256=SHA_C,
            pose_stream_id="other-stream",
            calibration_id="other-calibration",
            calibration_sha256=SHA_C,
            pixel_gain_profile_sha256=SHA_D,
        )
        result = qualify_pose_freshness(sample, config())
        expected = {
            ReasonCode.TIMEBASE_MISMATCH.value,
            ReasonCode.TIMEBASE_HASH_MISMATCH.value,
            ReasonCode.POSE_STREAM_MISMATCH.value,
            ReasonCode.CALIBRATION_ID_MISMATCH.value,
            ReasonCode.CALIBRATION_HASH_MISMATCH.value,
            ReasonCode.PIXEL_GAIN_PROFILE_HASH_MISMATCH.value,
        }
        self.assertTrue(expected.issubset(set(result.reason_codes)))
        self.assertFalse(result.pose_reliable)

    def test_past_only_clock_and_pose_validity_checks(self):
        sample = evidence(
            clock_alignment_valid=False,
            latest_pose=pose("p11", 11, 1_500_001, value_valid=False),
        )
        result = qualify_pose_freshness(sample, config())
        self.assertIn(ReasonCode.CLOCK_ALIGNMENT_INVALID.value, result.reason_codes)
        self.assertIn(ReasonCode.POSE_VALUE_INVALID.value, result.reason_codes)
        self.assertIn(ReasonCode.LATEST_POSE_FROM_FUTURE.value, result.reason_codes)

    def test_rate_interval_and_authorization_fail_closed(self):
        sample = evidence(
            previous_pose=pose("p10", 10, 0),
            latest_pose=pose("p11", 11, 2_000_000),
            epoch_start_ns=2_000_000,
            epoch_end_ns=2_500_000,
            decision_timestamp_ns=2_000_000,
            rate_bound_assumption_authorized=False,
        )
        result = qualify_pose_freshness(
            sample,
            config(max_rate_sample_interval_ns=1_000_000),
        )
        self.assertIn(
            ReasonCode.RATE_SAMPLE_INTERVAL_TOO_LARGE.value,
            result.reason_codes,
        )
        self.assertIn(ReasonCode.RATE_BOUND_UNAUTHORIZED.value, result.reason_codes)
        self.assertFalse(result.pose_reliable)

    def test_exact_rate_division_accepts_unsigned_128_bit_denominator(self):
        result = qualify_pose_freshness(
            evidence(),
            config(rate_growth_den=UINT64_MAX),
        )
        self.assertTrue(result.pose_reliable)
        self.assertEqual(result.recent_rate_error_q, 1)
        self.assertEqual(result.total_pixel_error_q, 4)

    def test_unsigned_128_bit_intermediate_overflow_rejects(self):
        sample = evidence(
            epoch_start_ns=UINT64_MAX - 1,
            epoch_end_ns=UINT64_MAX,
            decision_timestamp_ns=UINT64_MAX - 1,
            relative_angle_upper_urad=UINT64_MAX,
        )
        policy = config(
            hard_max_cover_age_ns=UINT64_MAX,
            max_rate_sample_interval_ns=UINT64_MAX,
            pixel_gain_q_per_rad=UINT64_MAX,
            rate_growth_num=UINT64_MAX,
            max_pixel_error_q=UINT64_MAX,
        )
        result = qualify_pose_freshness(sample, policy)
        self.assertFalse(result.pose_reliable)
        self.assertIn(ReasonCode.ARITHMETIC_OVERFLOW.value, result.reason_codes)
        self.assertIsNone(result.total_pixel_error_q)

    def test_digests_are_deterministic_and_cover_metadata_and_config(self):
        first = evidence()
        second = evidence()
        self.assertEqual(evidence_digest(first), evidence_digest(second))
        self.assertNotEqual(
            evidence_digest(first),
            evidence_digest(replace(second, epoch_id=8)),
        )
        first_config = config()
        self.assertEqual(config_digest(first_config), config_digest(config()))
        self.assertNotEqual(
            config_digest(first_config),
            config_digest(config(max_pixel_error_q=201)),
        )

    def test_typed_metadata_and_config_reject_bool_and_bad_hash(self):
        with self.assertRaises(FreshnessContractError):
            config(fractional_bits=True)
        with self.assertRaises(FreshnessContractError):
            evidence(epoch_id=True)
        with self.assertRaises(FreshnessContractError):
            pose("p", 1, 1, pose_sha256="not-a-digest")


if __name__ == "__main__":
    unittest.main()
