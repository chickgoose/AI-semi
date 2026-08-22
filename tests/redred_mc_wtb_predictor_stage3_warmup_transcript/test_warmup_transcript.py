from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from benchmarks.redred_mc_wtb_causal_reference.reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    ReferenceObservation,
)
from benchmarks.redred_mc_wtb_predictor_stage3.reference_prime import (
    ScoreFreeCausalReferenceBank,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import SO3PLLConfig
from benchmarks.redred_mc_wtb_predictor_stage3.warmup_transcript import (
    BOUNDARY_CLOSE_SCHEMA,
    NATIVE_REPLAY_AUTHORITY_SHA256,
    PENDING_TRANSITION_SCHEMA,
    QUERY_START_STATE_SCHEMA,
    REFERENCE_PRIME_POLICY_SCHEMA,
    RESET_SCHEMA,
    TRANSPORT_POLICY_SCHEMA,
    WARMUP_TRANSCRIPT_SCHEMA,
    WarmupTranscriptError,
    begin_warmup_transcript,
    build_warmup_transcript,
    verify_warmup_transcript,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
MODULE = "benchmarks.redred_mc_wtb_predictor_stage3.warmup_transcript"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
DEPENDENCY_DOMAIN = "stage3/warmup/state_dependency_pose/v1"
LEAF_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_leaf/v1"
CHAIN_LINK_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_chain_link/v1"
EMPTY_DEPENDENCY_CHAIN = "6" * 64


def ray(angle):
    return [math.sin(angle), 0.0, math.cos(angle)]


def sealed(body, field):
    return dict(body, **{field: canonical_sha256(body)})


def dependency_link(prior, count, pose_id, pose_sha256):
    leaf_sha256 = canonical_sha256({
        "domain": DEPENDENCY_DOMAIN,
        "leaf": {"pose_content_sha256": pose_sha256, "pose_id": pose_id},
        "schema": LEAF_SCHEMA,
    })
    return canonical_sha256({
        "domain": DEPENDENCY_DOMAIN,
        "leaf_sha256": leaf_sha256,
        "ordinal": count,
        "prior_sha256": prior,
        "schema": CHAIN_LINK_SCHEMA,
    })


POSE1_CHAIN = dependency_link(EMPTY_DEPENDENCY_CHAIN, 0, 1, "2" * 64)
POSE2_CHAIN = dependency_link(POSE1_CHAIN, 1, 2, "4" * 64)
POSE3_CHAIN = dependency_link(POSE2_CHAIN, 2, 3, "7" * 64)


def bindings(event_ids=None):
    if event_ids is None:
        event_ids = list(range(10, 19))
    return {
        "candidate_id": "TEST_CANDIDATE",
        "candidate_config_sha256": SHA_A,
        "candidate_dependency_aggregate_sha256": SHA_B,
        "logical_ingress_profile_sha256": SHA_C,
        "execution_input_aggregate_sha256": SHA_D,
        "neutral_input_sha256": SHA_E,
        "window_input_sha256": SHA_F,
        "reference_prime_implementation_sha256": "1" * 64,
        "native_replay_authority_sha256": NATIVE_REPLAY_AUTHORITY_SHA256,
        "ordered_warmup_event_ids_sha256": canonical_sha256(list(event_ids)),
    }


def bounds():
    return {
        "window_id": "w0",
        "warmup_start_ns_inclusive": 0,
        "query_start_ns_inclusive": 1_000,
        "query_end_ns_exclusive": 2_000,
        "query_start_decision_cycle": 10,
    }


def reference_policy(capacity=256, max_age=2_000_000):
    return {
        "schema": REFERENCE_PRIME_POLICY_SCHEMA,
        "capacity_per_polarity": capacity,
        "max_age_ns": max_age,
        "expiration_rule": "drop_timestamp_lt_cluster_timestamp_minus_max_age_ns",
        "selection_rule": "minimum_angular_distance_then_timestamp_then_event_id",
        "equal_timestamp_rule": "complete_cluster_before_insert",
        "snapshot_boundary_rule": "after_last_warmup_cluster_without_query_start_expiry",
        "warmup_mode": "prime_without_metrics",
    }


def transport_policy(capacity=3):
    return {
        "schema": TRANSPORT_POLICY_SCHEMA,
        "capacity_per_cycle": capacity,
        "violation_rule": "observed_occurrences_gt_capacity",
        "service_rule": "record_without_deferral",
    }


def reset():
    return sealed({
        "schema": RESET_SCHEMA,
        "reset_generation": 7,
        "reset_cycle": 0,
        "predictor_state_version": 0,
        "predictor_state_sha256": SHA_A,
        "dependency_pose_count": 0,
        "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
        "last_dependency_pose_id": None,
    }, "reset_sha256")


def query_state():
    return sealed({
        "schema": QUERY_START_STATE_SCHEMA,
        "predictor_state_version": 2,
        "predictor_state_sha256": SHA_C,
        "dependency_pose_count": 2,
        "dependency_pose_chain_sha256": POSE2_CHAIN,
        "last_dependency_pose_id": 2,
    }, "query_start_state_receipt_sha256")


def boundary_close(
    source=None, *, transition_rows=None, bound=None, binding=None,
    first_query_event_id=100, first_query_timestamp_ns=1_100,
):
    if source is None:
        source = occurrences()
    if bound is None:
        bound = bounds()
    if binding is None:
        binding = bindings([row["event_id"] for row in source])
    if transition_rows is None:
        transition_rows = transitions()
    last = source[-1]
    last_transition_timestamp = max(
        (row["measurement_timestamp_ns"] for row in transition_rows), default=None
    )
    last_cluster_timestamp = max(
        last["timestamp_ns"],
        last_transition_timestamp
        if last_transition_timestamp is not None else last["timestamp_ns"],
    )
    return sealed({
        "schema": BOUNDARY_CLOSE_SCHEMA,
        "window_id": bound["window_id"],
        "query_start_ns_inclusive": bound["query_start_ns_inclusive"],
        "last_warmup_occurrence_ordinal": last["occurrence_ordinal"],
        "last_warmup_event_id": last["event_id"],
        "last_warmup_timestamp_ns": last["timestamp_ns"],
        "warmup_transition_count": len(transition_rows),
        "last_warmup_transition_measurement_timestamp_ns": last_transition_timestamp,
        "last_warmup_cluster_timestamp_ns": last_cluster_timestamp,
        "first_query_source_ordinal": len(source),
        "first_query_event_id": first_query_event_id,
        "first_query_timestamp_ns": first_query_timestamp_ns,
        "execution_input_aggregate_sha256": binding["execution_input_aggregate_sha256"],
        "ordered_warmup_event_ids_sha256": binding["ordered_warmup_event_ids_sha256"],
        "native_replay_authority_sha256": binding["native_replay_authority_sha256"],
    }, "boundary_close_sha256")


def transitions():
    return [
        {
            "transition_ordinal": 0,
            "pose_id": 1,
            "pose_content_sha256": "2" * 64,
            "measurement_timestamp_ns": 200,
            "commit_cycle": 2,
            "publication_cycle": 3,
            "effective_cycle": 3,
            "state_changed": True,
            "prior_state_version": 0,
            "prior_state_sha256": SHA_A,
            "prior_dependency_pose_count": 0,
            "prior_dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
            "prior_last_dependency_pose_id": None,
            "next_state_version": 1,
            "next_state_sha256": SHA_B,
            "next_dependency_pose_count": 1,
            "next_dependency_pose_chain_sha256": POSE1_CHAIN,
            "next_last_dependency_pose_id": 1,
            "native_transition_sha256": "3" * 64,
        },
        {
            "transition_ordinal": 1,
            "pose_id": 2,
            "pose_content_sha256": "4" * 64,
            "measurement_timestamp_ns": 500,
            "commit_cycle": 5,
            "publication_cycle": 6,
            "effective_cycle": 6,
            "state_changed": True,
            "prior_state_version": 1,
            "prior_state_sha256": SHA_B,
            "prior_dependency_pose_count": 1,
            "prior_dependency_pose_chain_sha256": POSE1_CHAIN,
            "prior_last_dependency_pose_id": 1,
            "next_state_version": 2,
            "next_state_sha256": SHA_C,
            "next_dependency_pose_count": 2,
            "next_dependency_pose_chain_sha256": POSE2_CHAIN,
            "next_last_dependency_pose_id": 2,
            "native_transition_sha256": "5" * 64,
        },
    ]


def occurrence(
    ordinal, event_id, timestamp_ns, decision_cycle, state_version, state_sha,
    dependency_count, dependency_chain, last_dependency_pose_id,
    polarity=None, angle=None,
):
    if polarity is None:
        polarity = ordinal % 2
    if angle is None:
        angle = ordinal * 0.01
    return {
        "occurrence_ordinal": ordinal,
        "event_id": event_id,
        "event_content_sha256": ("%x" % ((ordinal % 6) + 10)) * 64,
        "timestamp_ns": timestamp_ns,
        "polarity": polarity,
        "occurrence_cycle": decision_cycle - 1,
        "decision_cycle": decision_cycle,
        "service_cycle": decision_cycle,
        "predictor_state_version": state_version,
        "predictor_state_sha256": state_sha,
        "state_dependency_pose_count": dependency_count,
        "state_dependency_pose_chain_sha256": dependency_chain,
        "state_last_dependency_pose_id": last_dependency_pose_id,
        "candidate_attempted": True,
        "candidate_used": True,
        "route": "CANDIDATE",
        "decision_sha256": ("%x" % ((ordinal % 6) + 1)) * 64,
        "world_ray": ray(angle),
    }


def occurrences():
    result = [
        occurrence(0, 10, 100, 1, 0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None),
        occurrence(1, 11, 200, 2, 0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None),
        occurrence(2, 12, 200, 2, 0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None),
        occurrence(3, 13, 300, 3, 1, SHA_B, 1, POSE1_CHAIN, 1),
    ]
    for offset in range(4):
        result.append(occurrence(
            4 + offset, 14 + offset, 600, 6, 2, SHA_C, 2, POSE2_CHAIN, 2
        ))
    result.append(occurrence(8, 18, 900, 9, 2, SHA_C, 2, POSE2_CHAIN, 2))
    return result


def build(**overrides):
    arguments = {
        "bindings": bindings(),
        "bounds": bounds(),
        "reference_prime_policy": reference_policy(),
        "transport_policy": transport_policy(),
        "reset": reset(),
        "query_start_state": query_state(),
        "boundary_close_authority": boundary_close(),
        "warmup_occurrences": occurrences(),
        "state_transitions": transitions(),
    }
    arguments.update(overrides)
    return build_warmup_transcript(**arguments)


def verify(receipt, **overrides):
    arguments = {
        "bindings": bindings(),
        "bounds": bounds(),
        "reference_prime_policy": reference_policy(),
        "transport_policy": transport_policy(),
        "reset": reset(),
        "query_start_state": query_state(),
        "boundary_close_authority": boundary_close(),
        "warmup_occurrences": occurrences(),
        "state_transitions": transitions(),
    }
    arguments.update(overrides)
    return verify_warmup_transcript(receipt, **arguments)


def merge_snapshot(snapshot):
    values = snapshot["polarity_0"] + snapshot["polarity_1"]
    return sorted(values, key=lambda item: (item["timestamp_ns"], item["occurrence_ordinal"]))


class WarmupTranscriptTests(unittest.TestCase):
    def test_compact_closed_receipt_binds_all_stream_domains(self):
        receipt = build()
        self.assertEqual(receipt["schema"], WARMUP_TRANSCRIPT_SCHEMA)
        self.assertEqual(receipt["warmup_occurrence_count"], 9)
        self.assertEqual(receipt["same_edge_cluster_count"], 5)
        self.assertEqual(receipt["state_transition_count"], 2)
        self.assertEqual(receipt["transport_cycle_violation_count"], 1)
        self.assertEqual(receipt["first_warmup_occurrence"], {
            "occurrence_ordinal": 0, "event_id": 10, "timestamp_ns": 100,
        })
        self.assertEqual(receipt["last_warmup_occurrence"], {
            "occurrence_ordinal": 8, "event_id": 18, "timestamp_ns": 900,
        })
        self.assertIsNone(receipt["pending_query_transition"])
        authority = receipt["native_replay_authority"]
        self.assertEqual(authority["authority_sha256"], NATIVE_REPLAY_AUTHORITY_SHA256)
        self.assertEqual(
            authority["integrity_scope"],
            "transcript_chains_are_not_independent_authentication",
        )
        self.assertEqual(
            authority["ordered_warmup_event_ids_sha256"],
            bindings()["ordered_warmup_event_ids_sha256"],
        )
        self.assertEqual(verify(receipt), receipt["receipt_sha256"])

        serialized = json.dumps(receipt, sort_keys=True)
        for forbidden in (
            "warmup_occurrences", "state_transitions", "selector", "label",
            "loss", "score",
        ):
            self.assertNotIn(forbidden, serialized.lower())
        self.assertLessEqual(
            receipt["query_start_reference_snapshot"]["occupancy"][0], 256
        )
        self.assertLessEqual(
            receipt["query_start_reference_snapshot"]["occupancy"][1], 256
        )
        chains = {
            receipt["warmup_occurrence_chain_sha256"],
            receipt["same_edge_cluster_chain_sha256"],
            receipt["state_transition_chain_sha256"],
            receipt["transport_cycle_violation_chain_sha256"],
        }
        self.assertEqual(len(chains), 4)

    def test_snapshot_is_oldest_to_newest_and_reproduces_query_scores(self):
        policy = reference_policy()
        source = occurrences()
        receipt = build(reference_prime_policy=policy)
        snapshot = receipt["query_start_reference_snapshot"]
        for polarity in (0, 1):
            values = snapshot["polarity_%d" % polarity]
            self.assertEqual(
                [(row["timestamp_ns"], row["occurrence_ordinal"]) for row in values],
                sorted((row["timestamp_ns"], row["occurrence_ordinal"]) for row in values),
            )
            self.assertLessEqual(len(values), 256)

        config = CausalReferenceConfig(256, 2_000_000)
        warmup = tuple(ReferenceObservation(
            row["event_id"], row["timestamp_ns"], row["polarity"],
            tuple(row["world_ray"]),
        ) for row in source)
        query = (
            ReferenceObservation(100, 1_100, 0, tuple(ray(0.07))),
            ReferenceObservation(101, 1_100, 1, tuple(ray(0.08))),
        )
        legacy = CausalReferenceBank(config).process(warmup + query)[len(warmup):]
        primed = ScoreFreeCausalReferenceBank(config)
        primed.prime(tuple(ReferenceObservation(
            row["event_id"], row["timestamp_ns"], row["polarity"],
            tuple(row["world_ray"]),
        ) for row in merge_snapshot(snapshot)))
        self.assertEqual(primed.process(query), legacy)

    def test_sparse_gap_snapshot_waits_for_actual_first_query_expiry(self):
        sparse_bounds = bounds()
        sparse_bounds["query_start_ns_inclusive"] = 3_000_000
        sparse_bounds["query_end_ns_exclusive"] = 6_000_000
        sparse_source = [occurrence(
            0, 10, 100, 1, 0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None,
            polarity=0,
        )]
        empty_query = sealed({
            "schema": QUERY_START_STATE_SCHEMA,
            "predictor_state_version": 0,
            "predictor_state_sha256": SHA_A,
            "dependency_pose_count": 0,
            "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
            "last_dependency_pose_id": None,
        }, "query_start_state_receipt_sha256")
        receipt = build(
            bindings=bindings([10]),
            bounds=sparse_bounds,
            boundary_close_authority=boundary_close(
                sparse_source, transition_rows=[], bound=sparse_bounds,
                binding=bindings([10]),
                first_query_event_id=20, first_query_timestamp_ns=5_000_000,
            ),
            reference_prime_policy=reference_policy(),
            query_start_state=empty_query,
            warmup_occurrences=sparse_source,
            state_transitions=[],
        )
        snapshot = receipt["query_start_reference_snapshot"]
        self.assertEqual(snapshot["last_warmup_timestamp_ns"], 100)
        self.assertEqual([row["event_id"] for row in snapshot["polarity_0"]], [10])

        query = ReferenceObservation(20, 5_000_000, 0, tuple(ray(0.1)))
        config = CausalReferenceConfig(256, 2_000_000)
        legacy = CausalReferenceBank(config).process((
            ReferenceObservation(10, 100, 0, tuple(sparse_source[0]["world_ray"])),
            query,
        ))[-1]
        primed = ScoreFreeCausalReferenceBank(config)
        primed.prime((ReferenceObservation(
            10, 100, 0, tuple(snapshot["polarity_0"][0]["world_ray"]),
        ),))
        actual = primed.process((query,))[0]
        self.assertFalse(actual.reference_available)
        self.assertEqual(actual, legacy)

    def test_authenticated_boundary_rejects_equal_timestamp_cluster_split(self):
        split = boundary_close(first_query_timestamp_ns=900)
        with self.assertRaisesRegex(
            WarmupTranscriptError, "equal-timestamp warmup/query cluster",
        ):
            build(boundary_close_authority=split)

        builder = begin_warmup_transcript(
            bindings=bindings([10]), bounds=bounds(),
            reference_prime_policy=reference_policy(),
            transport_policy=transport_policy(), reset=reset(),
        )
        builder.update_occurrence(occurrences()[0])
        empty_query = sealed({
            "schema": QUERY_START_STATE_SCHEMA,
            "predictor_state_version": 0,
            "predictor_state_sha256": SHA_A,
            "dependency_pose_count": 0,
            "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
            "last_dependency_pose_id": None,
        }, "query_start_state_receipt_sha256")
        with self.assertRaisesRegex(WarmupTranscriptError, "field schema"):
            builder.finalize(empty_query)

        wrong_source_boundary = boundary_close()
        wrong_source_boundary["first_query_source_ordinal"] += 1
        unsigned = dict(wrong_source_boundary)
        unsigned.pop("boundary_close_sha256")
        wrong_source_boundary["boundary_close_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(WarmupTranscriptError, "source order"):
            build(boundary_close_authority=wrong_source_boundary)

        omitted_transition = boundary_close()
        omitted_transition["warmup_transition_count"] -= 1
        omitted_transition_body = dict(omitted_transition)
        omitted_transition_body.pop("boundary_close_sha256")
        omitted_transition["boundary_close_sha256"] = canonical_sha256(
            omitted_transition_body
        )
        with self.assertRaisesRegex(WarmupTranscriptError, "transition endpoint"):
            build(boundary_close_authority=omitted_transition)

    def test_production_window_and_native_candidate_identifiers_are_accepted(self):
        production_bounds = bounds()
        production_bounds["window_id"] = "shapes_rotation/query_start_ns=41590000000"
        for candidate_id in (RG3_POLICY.candidate_id, SO3PLLConfig().candidate_id):
            production_bindings = bindings()
            production_bindings["candidate_id"] = candidate_id
            with self.subTest(candidate_id=candidate_id):
                receipt = build(
                    bindings=production_bindings,
                    bounds=production_bounds,
                    boundary_close_authority=boundary_close(
                        bound=production_bounds, binding=production_bindings,
                    ),
                )
                self.assertEqual(receipt["bounds"]["window_id"], production_bounds["window_id"])
                self.assertEqual(receipt["bindings"]["candidate_id"], candidate_id)

    def test_receipt_mutations_are_rejected_even_when_locally_resealed(self):
        pristine = build()
        mutations = []

        config = deepcopy(pristine)
        config["bindings"]["candidate_config_sha256"] = "0" * 64
        config["bindings_sha256"] = canonical_sha256(config["bindings"])
        mutations.append(config)

        snapshot = deepcopy(pristine)
        snapshot_body = snapshot["query_start_reference_snapshot"]
        snapshot_body["polarity_0"][0]["world_ray"] = ray(0.9)
        snapshot_body["polarity_0_sha256"] = canonical_sha256({
            "domain": "stage3/warmup/reference_snapshot/polarity_0/v1",
            "observations": snapshot_body["polarity_0"],
        })
        unsigned_snapshot = dict(snapshot_body)
        unsigned_snapshot.pop("snapshot_sha256")
        snapshot_body["snapshot_sha256"] = canonical_sha256(unsigned_snapshot)
        mutations.append(snapshot)

        boundary = deepcopy(pristine)
        boundary["last_warmup_occurrence"]["timestamp_ns"] = 1_000
        mutations.append(boundary)

        for chain_field in (
            "warmup_occurrence_chain_sha256",
            "same_edge_cluster_chain_sha256",
            "state_transition_chain_sha256",
            "transport_cycle_violation_chain_sha256",
        ):
            chain = deepcopy(pristine)
            chain[chain_field] = "0" * 64
            mutations.append(chain)

        for mutant in mutations:
            body = dict(mutant)
            body.pop("receipt_sha256")
            mutant["receipt_sha256"] = canonical_sha256(body)
            with self.subTest(mutant=mutant):
                with self.assertRaises(WarmupTranscriptError):
                    verify(mutant)

    def test_replay_rejects_drop_reorder_deferral_state_and_pose_mutations(self):
        pristine = build()
        source = occurrences()
        attacks = []
        attacks.append(source[:-1])
        reordered = deepcopy(source)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        attacks.append(reordered)
        deferred = deepcopy(source)
        deferred[3]["service_cycle"] += 1
        attacks.append(deferred)
        state = deepcopy(source)
        state[3]["predictor_state_sha256"] = SHA_A
        attacks.append(state)
        pose = deepcopy(source)
        pose[3]["state_dependency_pose_count"] = 0
        pose[3]["state_dependency_pose_chain_sha256"] = EMPTY_DEPENDENCY_CHAIN
        pose[3]["state_last_dependency_pose_id"] = None
        attacks.append(pose)

        for attack in attacks:
            with self.subTest(attack=attack):
                with self.assertRaises(WarmupTranscriptError):
                    verify(pristine, warmup_occurrences=attack)

        transition_attack = transitions()
        transition_attack[0]["effective_cycle"] = 2
        with self.assertRaises(WarmupTranscriptError):
            verify(pristine, state_transitions=transition_attack)

    def test_boundary_policy_and_query_state_attacks_fail_closed(self):
        query_event = occurrences()
        query_event[-1]["timestamp_ns"] = 1_000
        with self.assertRaisesRegex(WarmupTranscriptError, "boundary"):
            build(warmup_occurrences=query_event)

        wrong_query = query_state()
        wrong_query["predictor_state_version"] = 1
        query_body = dict(wrong_query)
        query_body.pop("query_start_state_receipt_sha256")
        wrong_query["query_start_state_receipt_sha256"] = canonical_sha256(query_body)
        with self.assertRaisesRegex(WarmupTranscriptError, "query-start state"):
            build(query_start_state=wrong_query)

        wrong_policy = reference_policy()
        wrong_policy["capacity_per_polarity"] = 257
        with self.assertRaisesRegex(WarmupTranscriptError, "fixed bounds"):
            build(reference_prime_policy=wrong_policy)

        wrong_age = reference_policy()
        wrong_age["max_age_ns"] -= 1
        with self.assertRaisesRegex(WarmupTranscriptError, "fixed bounds"):
            build(reference_prime_policy=wrong_age)

        wrong_semantics = reference_policy()
        wrong_semantics["selection_rule"] = "minimum_angular_distance_only"
        with self.assertRaisesRegex(WarmupTranscriptError, "semantics"):
            build(reference_prime_policy=wrong_semantics)

        late_reset = reset()
        late_reset["reset_cycle"] = 1
        reset_body = dict(late_reset)
        reset_body.pop("reset_sha256")
        late_reset["reset_sha256"] = canonical_sha256(reset_body)
        with self.assertRaisesRegex(WarmupTranscriptError, "exactly zero"):
            build(reset=late_reset)

    def test_event_ids_follow_authoritative_source_order_not_numeric_order(self):
        source = occurrences()
        replacement_ids = [90, 4, 80, 3, 70, 2, 60, 1, 50]
        for row, event_id in zip(source, replacement_ids):
            row["event_id"] = event_id
        source_bindings = bindings(replacement_ids)
        source_boundary = boundary_close(source, binding=source_bindings)
        receipt = build(
            bindings=source_bindings,
            boundary_close_authority=source_boundary,
            warmup_occurrences=source,
        )
        self.assertEqual(receipt["first_warmup_occurrence"]["event_id"], 90)
        self.assertEqual(receipt["last_warmup_occurrence"]["event_id"], 50)
        self.assertEqual(
            verify(
                receipt, bindings=source_bindings,
                boundary_close_authority=source_boundary,
                warmup_occurrences=(row for row in source),
            ),
            receipt["receipt_sha256"],
        )

        duplicate = deepcopy(source)
        duplicate[-1]["event_id"] = duplicate[0]["event_id"]
        with self.assertRaises(WarmupTranscriptError):
            verify(
                receipt, bindings=source_bindings,
                boundary_close_authority=source_boundary,
                warmup_occurrences=duplicate,
            )

    def test_fixed_native_replay_authority_is_required_and_resealed_mutations_fail(self):
        bad_bindings = bindings()
        bad_bindings["native_replay_authority_sha256"] = "0" * 64
        with self.assertRaisesRegex(WarmupTranscriptError, "authority"):
            build(bindings=bad_bindings)

        pristine = build()
        mutant = deepcopy(pristine)
        authority = mutant["native_replay_authority"]
        authority["integrity_scope"] = "rows_are_independently_authenticated"
        authority_body = dict(authority)
        authority_body.pop("authority_receipt_sha256")
        authority["authority_receipt_sha256"] = canonical_sha256(authority_body)
        receipt_body = dict(mutant)
        receipt_body.pop("receipt_sha256")
        mutant["receipt_sha256"] = canonical_sha256(receipt_body)
        with self.assertRaisesRegex(WarmupTranscriptError, "authority"):
            verify(mutant)

    def test_future_effective_transition_is_preserved_for_query_continuation(self):
        pending = {
            "transition_ordinal": 2,
            "pose_id": 3,
            "pose_content_sha256": "7" * 64,
            "measurement_timestamp_ns": 900,
            "commit_cycle": 9,
            "publication_cycle": 11,
            "effective_cycle": 12,
            "state_changed": True,
            "prior_state_version": 2,
            "prior_state_sha256": SHA_C,
            "prior_dependency_pose_count": 2,
            "prior_dependency_pose_chain_sha256": POSE2_CHAIN,
            "prior_last_dependency_pose_id": 2,
            "next_state_version": 3,
            "next_state_sha256": SHA_D,
            "next_dependency_pose_count": 3,
            "next_dependency_pose_chain_sha256": POSE3_CHAIN,
            "next_last_dependency_pose_id": 3,
            "native_transition_sha256": "9" * 64,
        }
        replay_transitions = transitions() + [pending]
        receipt = build(
            boundary_close_authority=boundary_close(
                transition_rows=replay_transitions,
            ),
            state_transitions=replay_transitions,
        )
        preserved = receipt["pending_query_transition"]
        self.assertEqual(preserved["schema"], PENDING_TRANSITION_SCHEMA)
        self.assertEqual(preserved["effective_cycle"], 12)
        self.assertEqual(preserved["next_state_sha256"], SHA_D)
        self.assertEqual(
            verify(
                receipt,
                boundary_close_authority=boundary_close(
                    transition_rows=replay_transitions,
                ),
                state_transitions=replay_transitions,
            ),
            receipt["receipt_sha256"],
        )

        removed = deepcopy(receipt)
        removed["pending_query_transition"] = None
        removed_body = dict(removed)
        removed_body.pop("receipt_sha256")
        removed["receipt_sha256"] = canonical_sha256(removed_body)
        with self.assertRaises(WarmupTranscriptError):
            verify(
                removed,
                boundary_close_authority=boundary_close(
                    transition_rows=replay_transitions,
                ),
                state_transitions=replay_transitions,
            )

    def test_snapshot_never_exceeds_256_per_polarity(self):
        large = []
        for ordinal in range(600):
            large.append(occurrence(
                ordinal,
                10_000 + ordinal,
                300 + ordinal,
                1 + ordinal,
                0,
                SHA_A,
                0,
                EMPTY_DEPENDENCY_CHAIN,
                None,
                polarity=ordinal % 2,
                angle=ordinal * 0.0001,
            ))
        large_bounds = bounds()
        large_bounds["query_start_ns_inclusive"] = 2_000
        large_bounds["query_end_ns_exclusive"] = 3_000
        large_bounds["query_start_decision_cycle"] = 700
        empty_query = sealed({
            "schema": QUERY_START_STATE_SCHEMA,
            "predictor_state_version": 0,
            "predictor_state_sha256": SHA_A,
            "dependency_pose_count": 0,
            "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
            "last_dependency_pose_id": None,
        }, "query_start_state_receipt_sha256")
        receipt = build(
            bindings=bindings([row["event_id"] for row in large]),
            bounds=large_bounds,
            boundary_close_authority=boundary_close(
                large, transition_rows=[], bound=large_bounds,
                binding=bindings([row["event_id"] for row in large]),
                first_query_event_id=99_999,
                first_query_timestamp_ns=2_000,
            ),
            reference_prime_policy=reference_policy(),
            transport_policy=transport_policy(capacity=1),
            query_start_state=empty_query,
            warmup_occurrences=large,
            state_transitions=[],
        )
        snapshot = receipt["query_start_reference_snapshot"]
        self.assertEqual(receipt["warmup_occurrence_count"], 600)
        self.assertEqual(snapshot["occupancy"], [256, 256])
        self.assertEqual(snapshot["polarity_0"][0]["occurrence_ordinal"], 88)
        self.assertEqual(snapshot["polarity_1"][0]["occurrence_ordinal"], 89)
        self.assertNotIn("warmup_occurrences", receipt)

    def test_incremental_generator_retains_only_bounded_reference_state(self):
        stream_bounds = bounds()
        stream_bounds["query_start_ns_inclusive"] = 5_000
        stream_bounds["query_end_ns_exclusive"] = 6_000
        stream_bounds["query_start_decision_cycle"] = 3_000
        builder = begin_warmup_transcript(
            bindings=bindings(range(20_000, 22_000)),
            bounds=stream_bounds,
            reference_prime_policy=reference_policy(),
            transport_policy=transport_policy(capacity=6),
            reset=reset(),
        )

        def occurrence_stream():
            for ordinal in range(2_000):
                yield occurrence(
                    ordinal, 20_000 + ordinal, 1 + ordinal, 1 + ordinal,
                    0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None,
                    polarity=ordinal % 2, angle=ordinal * 0.00001,
                )

        for item in occurrence_stream():
            builder.update_occurrence(item)
            retained = builder.retained_state_counts()
            self.assertEqual(retained["rich_occurrence_rows"], 0)
            self.assertEqual(retained["rich_transition_rows"], 0)
            self.assertLessEqual(retained["reference_polarity_0"], 256)
            self.assertLessEqual(retained["reference_polarity_1"], 256)

        empty_query = sealed({
            "schema": QUERY_START_STATE_SCHEMA,
            "predictor_state_version": 0,
            "predictor_state_sha256": SHA_A,
            "dependency_pose_count": 0,
            "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
            "last_dependency_pose_id": None,
        }, "query_start_state_receipt_sha256")
        stream_boundary = sealed({
            "schema": BOUNDARY_CLOSE_SCHEMA,
            "window_id": stream_bounds["window_id"],
            "query_start_ns_inclusive": stream_bounds["query_start_ns_inclusive"],
            "last_warmup_occurrence_ordinal": 1_999,
            "last_warmup_event_id": 21_999,
            "last_warmup_timestamp_ns": 2_000,
            "warmup_transition_count": 0,
            "last_warmup_transition_measurement_timestamp_ns": None,
            "last_warmup_cluster_timestamp_ns": 2_000,
            "first_query_source_ordinal": 2_000,
            "first_query_event_id": 30_000,
            "first_query_timestamp_ns": 5_000,
            "execution_input_aggregate_sha256": bindings()["execution_input_aggregate_sha256"],
            "ordered_warmup_event_ids_sha256": bindings(range(20_000, 22_000))["ordered_warmup_event_ids_sha256"],
            "native_replay_authority_sha256": NATIVE_REPLAY_AUTHORITY_SHA256,
        }, "boundary_close_sha256")
        receipt = builder.finalize(empty_query, stream_boundary)
        self.assertEqual(receipt["warmup_occurrence_count"], 2_000)
        self.assertEqual(
            receipt["query_start_reference_snapshot"]["occupancy"], [256, 256]
        )

    def test_clean_import_is_python38_syntax_and_loads_no_label_or_metric_modules(self):
        script = (
            "import json,sys; import " + MODULE + "; "
            "bad=[m for m in sys.modules if (m.endswith('.selector') or "
            "m.endswith('.evaluator') or m.endswith('.screen108'))]; "
            "print(json.dumps(sorted(bad)))"
        )
        completed = subprocess.run(
            [sys.executable, "-S", "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [])
        completed = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROOT / "benchmarks/redred_mc_wtb_predictor_stage3/warmup_transcript.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
