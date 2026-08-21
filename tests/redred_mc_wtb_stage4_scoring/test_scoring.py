from __future__ import annotations

from dataclasses import replace
import math
import unittest

from benchmarks.redred_mc_wtb_stage4_contract import (
    DecisionRecord,
    canonical_sha256,
    load_comparison_contract,
    validate_decision_records,
)
from benchmarks.redred_mc_wtb_stage4_scoring import (
    EventLoss,
    RayEvent,
    ScoreBoundaryEvidence,
    ScoreInputManifest,
    ScoreFreeAccounting,
    ScoringError,
    ShadowRay,
    WindowMetrics,
    aggregate_arm,
    finalize_disposition,
    is_positive_window,
    nearest_rank_latency,
    score_window,
    validate_complete_comparison,
    verify_prescore_binding,
)
from benchmarks.redred_mc_wtb_stage4_contract.receipt import DECISION_ARMS
from benchmarks.redred_mc_wtb_stage4_contract.receipt import ARM_LABELS
from benchmarks.redred_mc_wtb_stage4_scoring.scoring import (
    _validate_shadow_provenance,
)


HASH = "1" * 64
WINDOW = "synthetic_window"
ARM = "zoh_freshness"


def ray(degrees):
    angle = math.radians(degrees)
    return (math.cos(angle), math.sin(angle), 0.0)


def shadow(
    arm,
    value,
    transform,
    ids=(1,),
    timestamps=(0,),
    commits=(0,),
    hashes=(HASH,),
):
    return ShadowRay(arm, value, transform, ids, timestamps, commits, hashes)


def shadows(value, *, cav=None):
    by_arm = {
        "zoh_freshness": shadow("zoh_freshness", value, "occurrence_zoh"),
        "causal_cav": cav or shadow("causal_cav", value, "occurrence_zoh"),
        "delayed_exact": shadow(
            "delayed_exact",
            value,
            "delayed_slerp",
            (1, 2),
            (0, 1),
            (0, 1),
            (HASH, "2" * 64),
        ),
        "oracle_resampled_groundtruth_1khz": shadow(
            "oracle_resampled_groundtruth_1khz", value, "oracle_prefix"
        ),
    }
    return tuple(by_arm[arm] for arm in sorted(DECISION_ARMS))


def decision(event_id, timestamp, occurrence, retire, enabled, arm=ARM):
    return DecisionRecord(
        window_id=WINDOW,
        event_id=event_id,
        event_timestamp_ns=timestamp,
        arm=arm,
        arm_semantic_label=ARM_LABELS[arm],
        occurrence_cycle=occurrence,
        retire_cycle=retire,
        occurrence_pose_ids=(1,),
        occurrence_pose_timestamps_ns=(0,),
        occurrence_pose_commit_cycles=(0,),
        occurrence_pose_sha256=(HASH,),
        used_pose_ids=(1,),
        used_pose_timestamps_ns=(0,),
        used_pose_commit_cycles=(0,),
        used_pose_sha256=(HASH,),
        intentional_future_pose_use=False,
        pose_age_ns=timestamp,
        disposition="corrected_world_ray" if enabled else "raw_bypass",
        disposition_reason="fresh_pose" if enabled else "freshness_veto",
        queue_cycles=retire - occurrence,
    )


def delayed_record(*, enabled, occurrence=True):
    occurrence_ids = (10, 11) if occurrence else ()
    occurrence_timestamps = (50, 100) if occurrence else ()
    occurrence_commits = (0, 1) if occurrence else ()
    occurrence_hashes = ("a" * 64, "b" * 64) if occurrence else ()
    if enabled:
        used_ids = (11, 12)
        used_timestamps = (100, 200)
        used_commits = (1, 20)
        used_hashes = ("b" * 64, "c" * 64)
    else:
        used_ids = (11,) if occurrence else ()
        used_timestamps = (100,) if occurrence else ()
        used_commits = (1,) if occurrence else ()
        used_hashes = ("b" * 64,) if occurrence else ()
    return DecisionRecord(
        window_id=WINDOW,
        event_id=70,
        event_timestamp_ns=150,
        arm="delayed_exact",
        arm_semantic_label=ARM_LABELS["delayed_exact"],
        occurrence_cycle=3,
        retire_cycle=30 if enabled else 10,
        occurrence_pose_ids=occurrence_ids,
        occurrence_pose_timestamps_ns=occurrence_timestamps,
        occurrence_pose_commit_cycles=occurrence_commits,
        occurrence_pose_sha256=occurrence_hashes,
        used_pose_ids=used_ids,
        used_pose_timestamps_ns=used_timestamps,
        used_pose_commit_cycles=used_commits,
        used_pose_sha256=used_hashes,
        intentional_future_pose_use=enabled,
        pose_age_ns=-50 if enabled else (50 if occurrence else None),
        disposition="corrected_world_ray" if enabled else "raw_bypass",
        disposition_reason=(
            "bracket_interpolation" if enabled else "deadline_timeout"
        ),
        queue_cycles=27 if enabled else 7,
    )


def delayed_event(delayed):
    by_arm = dict((row.arm, row) for row in shadows(ray(20)))
    by_arm["delayed_exact"] = delayed
    return RayEvent(
        WINDOW,
        70,
        150,
        0,
        True,
        ray(10),
        tuple(by_arm[arm] for arm in sorted(DECISION_ARMS)),
    )


def sealed_inputs(
    contract, records, events, *, freshness=(), invalid=(), operational=()
):
    ids = tuple(record.event_id for record in records)
    receipt = validate_decision_records(
        contract,
        ids,
        records,
        expected_window_id=WINDOW,
        expected_arm=records[0].arm,
    )
    enabled = tuple(
        record.event_id for record in records
        if record.disposition == "corrected_world_ray"
    )
    accounting = ScoreFreeAccounting(
        WINDOW,
        records[0].arm,
        tuple((record.event_id, record.occurrence_cycle + 1) for record in records),
        enabled + tuple(operational),
        tuple(freshness),
        tuple(invalid),
        tuple(operational),
        0,
        0,
        0,
        192_000,
        102_000,
        1_024,
    )
    artifacts = dict((name, HASH) for name in (
        "protocol",
        "registry",
        "arm_parameters",
        "generator",
        "cycle_model",
        "scorer",
        "sources",
        "runtime",
    ))
    artifacts["protocol"] = contract.canonical_sha256
    artifacts["registry"] = contract.registry["sha256"]
    manifest = ScoreInputManifest(
        WINDOW,
        records[0].arm,
        receipt.canonical_sha256(),
        accounting.canonical_sha256(),
        canonical_sha256([event.to_mapping() for event in events]),
        "3" * 64,
        "4" * 64,
        "5" * 64,
        receipt.decision_records_sha256,
        tuple(artifacts.items()),
    )
    evidence = ScoreBoundaryEvidence(
        manifest.assay_authoritative_input_manifest_sha256,
        manifest.full_cycle_result_sha256,
        manifest.cycle_receipts_sha256,
        manifest.query_projection_sha256,
    )
    return receipt, accounting, manifest, evidence


class FrameSafeWindowTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_comparison_contract()

    def score(self, records, events, **categories):
        receipt, accounting, manifest, evidence = sealed_inputs(
            self.contract, records, events, **categories
        )
        return score_window(
            self.contract,
            receipt,
            tuple(records),
            tuple(events),
            accounting,
            manifest,
            evidence,
            expected_manifest_sha256=manifest.canonical_sha256(),
            expected_receipt_sha256=receipt.canonical_sha256(),
            expected_accounting_sha256=accounting.canonical_sha256(),
        )

    def test_receipt_digest_is_checked_before_any_ray_validation(self):
        records = (decision(10, 100, 3, 4, True),)
        valid_events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0))),
            RayEvent(WINDOW, 10, 100, 0, True, ray(1), shadows(ray(1))),
        )
        receipt, accounting, manifest, evidence = sealed_inputs(
            self.contract, records, valid_events
        )
        with self.assertRaisesRegex(ScoringError, "receipt digest"):
            score_window(
                self.contract,
                receipt,
                records,
                (object(),),
                accounting,
                manifest,
                evidence,
                expected_manifest_sha256=manifest.canonical_sha256(),
                expected_receipt_sha256="0" * 64,
                expected_accounting_sha256=accounting.canonical_sha256(),
            )

    def test_manifest_digest_is_checked_before_any_ray_or_loss_join(self):
        records = (decision(11, 100, 3, 4, True),)
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0))),
            RayEvent(WINDOW, 11, 100, 0, True, ray(1), shadows(ray(1))),
        )
        receipt, accounting, manifest, evidence = sealed_inputs(
            self.contract, records, events
        )
        with self.assertRaisesRegex(ScoringError, "manifest digest"):
            score_window(
                self.contract,
                receipt,
                records,
                (object(),),
                accounting,
                manifest,
                evidence,
                expected_manifest_sha256="0" * 64,
                expected_receipt_sha256=receipt.canonical_sha256(),
                expected_accounting_sha256=accounting.canonical_sha256(),
            )
        mutated_events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(2), shadows(ray(2))),
            events[1],
        )
        with self.assertRaisesRegex(ScoringError, "pre-frozen manifest"):
            score_window(
                self.contract,
                receipt,
                records,
                mutated_events,
                accounting,
                manifest,
                evidence,
                expected_manifest_sha256=manifest.canonical_sha256(),
                expected_receipt_sha256=receipt.canonical_sha256(),
                expected_accounting_sha256=accounting.canonical_sha256(),
            )

    def test_full_stream_boundary_digests_precede_loss_join(self):
        records = (decision(12, 100, 3, 4, True),)
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0))),
            RayEvent(WINDOW, 12, 100, 0, True, ray(1), shadows(ray(1))),
        )
        receipt, accounting, manifest, evidence = sealed_inputs(
            self.contract, records, events
        )
        expected = {
            "expected_manifest_sha256": manifest.canonical_sha256(),
            "expected_receipt_sha256": receipt.canonical_sha256(),
            "expected_accounting_sha256": accounting.canonical_sha256(),
        }
        verify_prescore_binding(
            self.contract,
            receipt,
            records,
            accounting,
            manifest,
            evidence,
            **expected
        )
        for field, value in (
            ("assay_authoritative_input_manifest_sha256", "6" * 64),
            ("full_cycle_result_sha256", "7" * 64),
            ("cycle_receipts_sha256", "8" * 64),
            ("query_projection_sha256", "9" * 64),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ScoringError, "full-stream boundary"
            ):
                verify_prescore_binding(
                    self.contract,
                    receipt,
                    records,
                    accounting,
                    manifest,
                    replace(evidence, **{field: value}),
                    **expected
                )

        wrong_projection = replace(manifest, query_projection_sha256="a" * 64)
        wrong_evidence = replace(evidence, query_projection_sha256="a" * 64)
        with self.assertRaisesRegex(ScoringError, "query projection"):
            verify_prescore_binding(
                self.contract,
                receipt,
                records,
                accounting,
                wrong_projection,
                wrong_evidence,
                expected_manifest_sha256=wrong_projection.canonical_sha256(),
                expected_receipt_sha256=receipt.canonical_sha256(),
                expected_accounting_sha256=accounting.canonical_sha256(),
            )

    def test_shadow_commit_cycles_allow_signed_relative_time(self):
        before_window = shadow(
            ARM,
            ray(0),
            "occurrence_zoh",
            commits=(-1,),
        )
        self.assertEqual(before_window.pose_commit_cycles, (-1,))
        with self.assertRaisesRegex(ScoringError, "signed integer"):
            shadow(ARM, ray(0), "occurrence_zoh", commits=(True,))

    def test_delayed_raw_bypass_accepts_sealed_score_only_bracket(self):
        record = delayed_record(enabled=False)
        score_only = shadow(
            "delayed_exact",
            ray(20),
            "delayed_slerp",
            (11, 12),
            (100, 200),
            (1, 40),
            ("b" * 64, "c" * 64),
        )
        event = delayed_event(score_only)
        before = record.to_mapping()

        _validate_shadow_provenance(event, record)

        self.assertEqual(record.to_mapping(), before)
        self.assertNotEqual(score_only.provenance_rows(), tuple(zip(
            record.used_pose_ids,
            record.used_pose_timestamps_ns,
            record.used_pose_commit_cycles,
            record.used_pose_sha256,
        )))

    def test_delayed_shadow_requires_authoritative_complete_ordered_bracket(self):
        record = delayed_record(enabled=False)
        bad_shadows = (
            (
                shadow(
                    "delayed_exact", ray(20), "delayed_slerp",
                    (10, 12), (50, 200), (0, 40), ("a" * 64, "c" * 64),
                ),
                "latest occurrence pose",
            ),
            (
                shadow(
                    "delayed_exact", ray(20), "delayed_slerp",
                    (11, 12), (100, 150), (1, 40), ("b" * 64, "c" * 64),
                ),
                "strict right bracket",
            ),
            (
                shadow(
                    "delayed_exact", ray(20), "delayed_slerp",
                    (11, 12), (100, 200), (1, 40), ("b" * 64, "b" * 64),
                ),
                "distinct pose hashes",
            ),
        )
        for delayed, message in bad_shadows:
            with self.subTest(message=message), self.assertRaisesRegex(
                ScoringError, message
            ):
                _validate_shadow_provenance(delayed_event(delayed), record)

        with self.assertRaisesRegex(ScoringError, "wrong pose arity"):
            shadow(
                "delayed_exact", ray(20), "delayed_slerp",
                (11,), (100,), (1,), ("b" * 64,),
            )
        with self.assertRaisesRegex(ScoringError, "IDs must be strictly increasing"):
            shadow(
                "delayed_exact", ray(20), "delayed_slerp",
                (12, 11), (100, 200), (1, 40), ("b" * 64, "c" * 64),
            )
        with self.assertRaisesRegex(
            ScoringError, "timestamps must be strictly increasing"
        ):
            shadow(
                "delayed_exact", ray(20), "delayed_slerp",
                (11, 12), (200, 100), (1, 40), ("b" * 64, "c" * 64),
            )
        no_left = delayed_record(enabled=False, occurrence=False)
        complete = shadow(
            "delayed_exact", ray(20), "delayed_slerp",
            (11, 12), (100, 200), (1, 40), ("b" * 64, "c" * 64),
        )
        with self.assertRaisesRegex(ScoringError, "latest occurrence pose"):
            _validate_shadow_provenance(delayed_event(complete), no_left)

    def test_corrected_delayed_shadow_still_equals_runtime_used_pose(self):
        record = delayed_record(enabled=True)
        matching = shadow(
            "delayed_exact", ray(20), "delayed_slerp",
            (11, 12), (100, 200), (1, 20), ("b" * 64, "c" * 64),
        )
        _validate_shadow_provenance(delayed_event(matching), record)

        distinct = replace(matching, pose_commit_cycles=(1, 21))
        with self.assertRaisesRegex(ScoringError, "runtime used pose"):
            _validate_shadow_provenance(delayed_event(distinct), record)

    def test_equal_timestamp_cluster_scores_before_insert_and_separates_polarity(self):
        records = (
            decision(20, 100, 3, 4, True),
            decision(21, 100, 3, 4, True),
        )
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(90))),
            RayEvent(WINDOW, 2, 0, 1, False, ray(180), shadows(ray(270))),
            RayEvent(WINDOW, 20, 100, 0, True, ray(30), shadows(ray(120))),
            RayEvent(WINDOW, 21, 100, 0, True, ray(60), shadows(ray(150))),
        )
        metrics = self.score(records, events)
        by_id = dict((row.event_id, row) for row in metrics.event_losses)
        self.assertEqual(by_id[20].sensor_reference_event_id, 1)
        self.assertEqual(by_id[21].sensor_reference_event_id, 1)
        self.assertEqual(by_id[20].world_reference_event_id, 1)
        self.assertEqual(by_id[21].world_reference_event_id, 1)
        self.assertAlmostEqual(by_id[20].sensor_loss, math.radians(30), places=12)
        self.assertAlmostEqual(by_id[20].world_shadow_loss, math.radians(30), places=12)

    def test_bypassed_world_shadow_still_supplies_later_density(self):
        records = (
            decision(30, 100, 3, 4, False),
            decision(31, 200, 5, 6, True),
        )
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(90))),
            RayEvent(WINDOW, 30, 100, 0, True, ray(20), shadows(ray(120))),
            RayEvent(WINDOW, 31, 200, 0, True, ray(40), shadows(ray(120))),
        )
        metrics = self.score(records, events, freshness=(30,))
        later = metrics.event_losses[1]
        self.assertEqual(later.world_reference_event_id, 30)
        self.assertLess(later.world_shadow_loss, 2.0e-8)
        self.assertEqual(later.sensor_reference_event_id, 30)
        self.assertAlmostEqual(later.sensor_loss, math.radians(20), places=12)
        self.assertAlmostEqual(metrics.all_event_effect, 0.5, places=7)

    def test_quality_waste_counts_ties_and_effects_use_all_events(self):
        records = (decision(40, 100, 3, 4, True),)
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(90))),
            RayEvent(WINDOW, 40, 100, 0, True, ray(30), shadows(ray(120))),
        )
        metrics = self.score(records, events)
        self.assertEqual(metrics.quality_waste_events, 1)
        self.assertEqual(metrics.quality_waste_rate, 1.0)
        self.assertEqual(metrics.all_event_effect, 0.0)
        self.assertEqual(metrics.enabled_only_effect, 0.0)
        self.assertFalse(metrics.positive_window)

    def test_missing_query_reference_and_incomplete_arm_shadow_fail(self):
        with self.assertRaisesRegex(ScoringError, "every arm"):
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0))[:-1])
        records = (decision(50, 100, 3, 4, True),)
        events = (RayEvent(WINDOW, 50, 100, 1, True, ray(0), shadows(ray(90))),)
        with self.assertRaisesRegex(ScoringError, "sensor reference"):
            self.score(records, events)

    def test_cav_bypass_shadow_is_latest_occurrence_zoh_outside_horizon(self):
        record = DecisionRecord(
            window_id=WINDOW,
            event_id=55,
            event_timestamp_ns=1_000,
            arm="causal_cav",
            arm_semantic_label=ARM_LABELS["causal_cav"],
            occurrence_cycle=3,
            retire_cycle=4,
            occurrence_pose_ids=(1, 2),
            occurrence_pose_timestamps_ns=(0, 100),
            occurrence_pose_commit_cycles=(0, 1),
            occurrence_pose_sha256=(HASH, "2" * 64),
            used_pose_ids=(2,),
            used_pose_timestamps_ns=(100,),
            used_pose_commit_cycles=(1,),
            used_pose_sha256=("2" * 64,),
            intentional_future_pose_use=False,
            pose_age_ns=900,
            disposition="raw_bypass",
            disposition_reason="freshness_veto",
            queue_cycles=1,
        )
        warmup = RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0)))
        bad_cav = shadow(
            "causal_cav",
            ray(20),
            "occurrence_cav",
            (1, 2),
            (0, 100),
            (0, 1),
            (HASH, "2" * 64),
        )
        bad = (
            warmup,
            RayEvent(WINDOW, 55, 1_000, 0, True, ray(10), shadows(ray(20), cav=bad_cav)),
        )
        with self.assertRaisesRegex(ScoringError, "latest occurrence-snapshot ZOH"):
            self.score((record,), bad, freshness=(55,))

        latest_zoh = shadow(
            "causal_cav",
            ray(20),
            "occurrence_zoh",
            (2,),
            (100,),
            (1,),
            ("2" * 64,),
        )
        good = (
            warmup,
            RayEvent(WINDOW, 55, 1_000, 0, True, ray(10), shadows(ray(20), cav=latest_zoh)),
        )
        metrics = self.score((record,), good, freshness=(55,))
        self.assertEqual(metrics.accepted_events, 1)
        self.assertEqual(metrics.enabled_events, 0)

    def test_score_free_rate_partition_is_exhaustive(self):
        records = (
            decision(60, 100, 3, 4, False),
            decision(61, 200, 5, 6, True),
        )
        events = (
            RayEvent(WINDOW, 1, 0, 0, False, ray(0), shadows(ray(0))),
            RayEvent(WINDOW, 60, 100, 0, True, ray(10), shadows(ray(5))),
            RayEvent(WINDOW, 61, 200, 0, True, ray(20), shadows(ray(10))),
        )
        receipt, accounting, manifest, evidence = sealed_inputs(
            self.contract, records, events
        )
        with self.assertRaisesRegex(ScoringError, "not exhaustive"):
            score_window(
                self.contract,
                receipt,
                records,
                events,
                accounting,
                manifest,
                evidence,
                expected_manifest_sha256=manifest.canonical_sha256(),
                expected_receipt_sha256=receipt.canonical_sha256(),
                expected_accounting_sha256=accounting.canonical_sha256(),
            )


class MetricRuleTests(unittest.TestCase):
    def test_nearest_rank_sorts_by_latency_then_event_id(self):
        summary = nearest_rank_latency(((9, 4), (3, 1), (8, 3), (1, 2)))
        self.assertEqual(summary.mean_cycles, 2.5)
        self.assertEqual(summary.p50_cycles, 2)
        self.assertEqual(summary.p95_cycles, 4)
        self.assertEqual(summary.p99_cycles, 4)
        self.assertEqual(summary.max_cycles, 4)

    def test_positive_window_threshold_is_strict(self):
        self.assertFalse(is_positive_window(1.0e-6))
        self.assertTrue(is_positive_window(math.nextafter(1.0e-6, math.inf)))
        base = EventLoss(1, 1.0, 0.5, 1.0, False, False, 0, 0, 1, 0)
        equal = WindowMetrics(
            "w", ARM, HASH, HASH, HASH, (base,), 1, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        )
        self.assertFalse(equal.positive_window)
        above_event = EventLoss(1, 1.0, 0.5, 0.5, True, False, 0, 0, 1, 0)
        above = replace(
            equal,
            event_losses=(above_event,),
            freshness_veto_events=0,
            attempted_corrections=1,
        )
        self.assertTrue(above.positive_window)

    def test_loss_reduction_uses_fsum_after_event_id_ordering(self):
        epsilon = 2.0 ** -53
        rows = (
            EventLoss(3, epsilon, 0.0, epsilon, False, False, 0, 0, 1, 0),
            EventLoss(1, 1.0, 0.0, 1.0, False, False, 0, 0, 1, 0),
            EventLoss(2, epsilon, 0.0, epsilon, False, False, 0, 0, 1, 0),
        )
        metrics = WindowMetrics(
            "w", ARM, HASH, HASH, HASH, rows, 3, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        )
        self.assertEqual(metrics.sensor_loss_sum, math.fsum((1.0, epsilon, epsilon)))
        self.assertEqual(metrics.sensor_loss_sum, 1.0000000000000002)

    def test_diagnostic_labels_never_become_epoch_integration_go(self):
        self.assertEqual(
            finalize_disposition("delayed_exact", "GO_NUMERIC"),
            "DIAGNOSTIC_UPPER_BOUND",
        )
        self.assertEqual(
            finalize_disposition("oracle_resampled_groundtruth_1khz", "GO_NUMERIC"),
            "INTERFACE_VALUE_ONLY",
        )
        self.assertEqual(
            finalize_disposition("causal_cav", "GO_NUMERIC"),
            "GO_TO_EPOCH_INTEGRATION",
        )
        self.assertEqual(finalize_disposition("delayed_exact", "STOP"), "STOP")

    def test_normative_aggregate_go_and_cost_stop(self):
        contract = load_comparison_contract()
        total = int(contract.registry["query_event_count"])
        window_count = int(contract.registry["window_count"])
        per_window, remainder = divmod(total, window_count)
        windows = []
        event_id = 0
        for index in range(window_count):
            count = per_window + (1 if index < remainder else 0)
            losses = []
            for _ in range(count):
                losses.append(EventLoss(
                    event_id, 1.0, 0.5, 0.5, True, False, 90_000 + index,
                    80_000 + index, 1, 0,
                ))
                event_id += 1
            windows.append(WindowMetrics(
                "w%02d" % index,
                ARM,
                HASH,
                HASH,
                HASH,
                tuple(losses),
                0,
                0,
                count,
                0,
                0,
                0,
                0,
                192_000,
                102_000,
                1_024,
                0,
                0,
                0,
                0,
            ))
        aggregate = aggregate_arm(contract, tuple(windows))
        self.assertEqual(aggregate.accepted_events, 8_914)
        self.assertEqual(aggregate.positive_windows, 24)
        self.assertEqual(aggregate.all_event_effect, 0.5)
        self.assertEqual(aggregate.enabled_only_effect, 0.5)
        self.assertEqual(aggregate.numeric_disposition, "GO_NUMERIC")
        self.assertEqual(aggregate.final_disposition, "GO_TO_EPOCH_INTEGRATION")

        too_large = list(windows)
        too_large[0] = replace(too_large[0], peak_buffer_entries=1_025)
        stopped = aggregate_arm(contract, tuple(too_large))
        self.assertEqual(stopped.numeric_disposition, "STOP")
        self.assertEqual(stopped.final_disposition, "STOP")

        aggregates = []
        for arm in sorted(DECISION_ARMS):
            arm_windows = tuple(replace(window, arm=arm) for window in windows)
            aggregates.append(aggregate_arm(contract, arm_windows))
        complete = validate_complete_comparison(tuple(aggregates))
        by_arm = dict((item.arm, item.final_disposition) for item in complete)
        self.assertEqual(by_arm["delayed_exact"], "DIAGNOSTIC_UPPER_BOUND")
        self.assertEqual(
            by_arm["oracle_resampled_groundtruth_1khz"], "INTERFACE_VALUE_ONLY"
        )
        self.assertEqual(by_arm["causal_cav"], "GO_TO_EPOCH_INTEGRATION")


if __name__ == "__main__":
    unittest.main()
