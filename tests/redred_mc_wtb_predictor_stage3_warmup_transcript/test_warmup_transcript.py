from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator
from referencing import Registry as SchemaRegistry, Resource

from benchmarks.redred_mc_wtb_causal_reference.reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    ReferenceObservation,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import warmup_transcript as transcript_module
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    timestamp_to_cycle,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.reference_prime import (
    ScoreFreeCausalReferenceBank,
)
from benchmarks.redred_mc_wtb_predictor_stage3.warmup_transcript import (
    EXTERNAL_PRODUCTION_HOLD,
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
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "benchmarks/redred_mc_wtb_predictor_stage3/warmup_transcript.py"
WINDOW_ID = "shapes_rotation/query_start_ns=50000000"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
EMPTY_DEPENDENCY_CHAIN = "6" * 64
DEPENDENCY_DOMAIN = "stage3/warmup/state_dependency_pose/v1"
LEAF_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_leaf/v1"
CHAIN_LINK_SCHEMA = "redred.mc_wtb_predictor_stage3.stream_chain_link/v1"


@dataclass(frozen=True)
class Registry:
    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int


@dataclass(frozen=True)
class NeutralEvent:
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: tuple
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool


@dataclass(frozen=True)
class NeutralPose:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: tuple
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool


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


def make_pose(pose_id, timestamp_ns):
    quaternion = (0.0, 0.0, 0.0, 1.0)
    return NeutralPose(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
        True,
        True,
    )


def make_event(event_id, timestamp_ns, polarity, causal_pose_id):
    sensor_ray = (1.0, 0.0, 0.0)
    is_query = timestamp_ns >= 50_000_000
    return NeutralEvent(
        event_id,
        timestamp_ns,
        polarity,
        is_query,
        sensor_ray,
        causal_pose_id,
        canonical_event_content_sha256(
            event_id,
            timestamp_ns,
            polarity,
            is_query,
            sensor_ray,
            causal_pose_id,
            True,
        ),
        True,
    )


def make_execution(event_ids=None, query_ids=None):
    if event_ids is None:
        event_ids = [90, 4, 80, 3, 70, 2, 60, 1, 50]
    if query_ids is None:
        query_ids = [100, 101]
    times = [100, 200, 200, 300, 600, 600, 600, 600, 900]
    causal = [0, 0, 0, 1, 2, 2, 2, 2, 2]
    events = [
        make_event(event_id, timestamp, ordinal % 2, pose_id)
        for ordinal, (event_id, timestamp, pose_id)
        in enumerate(zip(event_ids, times, causal))
    ]
    events.extend((
        make_event(query_ids[0], 50_000_100, 0, 2),
        make_event(query_ids[1], 50_000_200, 1, 2),
    ))
    registry = (Registry(WINDOW_ID, 0, 50_000_000, 51_000_000),)
    return build_stage3_execution_input(
        registry,
        {WINDOW_ID: tuple(events)},
        {WINDOW_ID: (
            make_pose(0, 0), make_pose(1, 200), make_pose(2, 500),
        )},
        source_events_authority={
            "source_events_path": "external/new108/events.txt",
            "source_events_sha256": "0" * 64,
            "source_events_size_bytes": 1234,
            "source_events_line_count": len(events),
        },
        repo_root=ROOT,
    )


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
        "reset_generation": 0,
        "reset_cycle": 0,
        "predictor_state_version": 0,
        "predictor_state_sha256": SHA_A,
        "dependency_pose_count": 0,
        "dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
        "last_dependency_pose_id": None,
        "authentication_status": EXTERNAL_PRODUCTION_HOLD,
    }, "reset_sha256")


def query_state(version=2, state_sha=SHA_C, count=2, chain=POSE2_CHAIN, last=2):
    return sealed({
        "schema": QUERY_START_STATE_SCHEMA,
        "predictor_state_version": version,
        "predictor_state_sha256": state_sha,
        "dependency_pose_count": count,
        "dependency_pose_chain_sha256": chain,
        "last_dependency_pose_id": last,
        "state_boundary": "at_query_start_before_first_query",
        "state_cycle": timestamp_to_cycle(50_000_000, 0),
        "authentication_status": EXTERNAL_PRODUCTION_HOLD,
    }, "query_start_state_receipt_sha256")


def transitions():
    return [
        {
            "transition_ordinal": 0,
            "pose_id": 1,
            "pose_content_sha256": "2" * 64,
            "measurement_timestamp_ns": 200,
            "commit_cycle": timestamp_to_cycle(200, 0),
            "publication_cycle": timestamp_to_cycle(200, 0) + 1,
            "effective_cycle": timestamp_to_cycle(200, 0) + 1,
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
            "commit_cycle": timestamp_to_cycle(500, 0),
            "publication_cycle": timestamp_to_cycle(500, 0) + 1,
            "effective_cycle": timestamp_to_cycle(500, 0) + 1,
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


def occurrences(execution):
    result = []
    for ordinal, event in enumerate(execution["windows"][0]["events"][:9]):
        if event["timestamp_ns"] <= 200:
            state = (0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None)
        elif event["timestamp_ns"] < 600:
            state = (1, SHA_B, 1, POSE1_CHAIN, 1)
        else:
            state = (2, SHA_C, 2, POSE2_CHAIN, 2)
        decision_cycle = timestamp_to_cycle(event["timestamp_ns"], 0)
        result.append({
            "occurrence_ordinal": ordinal,
            "event_id": event["event_id"],
            "event_content_sha256": event["event_content_sha256"],
            "timestamp_ns": event["timestamp_ns"],
            "polarity": event["polarity"],
            "occurrence_cycle": decision_cycle - 1,
            "decision_cycle": decision_cycle,
            "service_cycle": decision_cycle,
            "predictor_state_version": state[0],
            "predictor_state_sha256": state[1],
            "state_dependency_pose_count": state[2],
            "state_dependency_pose_chain_sha256": state[3],
            "state_last_dependency_pose_id": state[4],
            "candidate_attempted": True,
            "candidate_used": True,
            "route": "CANDIDATE",
            "decision_sha256": canonical_sha256({
                "event_id": event["event_id"], "ordinal": ordinal,
            }),
            "world_ray": ray(ordinal * 0.01),
        })
    return result


def merge_snapshot(snapshot):
    values = snapshot["polarity_0"] + snapshot["polarity_1"]
    return sorted(values, key=lambda row: (
        row["timestamp_ns"], row["occurrence_ordinal"]
    ))


class WarmupTranscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.execution = make_execution()

    def arguments(self, execution=None, **overrides):
        if execution is None:
            execution = self.execution
        arguments = {
            "execution_input": execution,
            "window_id": WINDOW_ID,
            "repo_root": ROOT,
            "reference_prime_policy": reference_policy(),
            "transport_policy": transport_policy(),
            "reset": reset(),
            "query_start_state": query_state(),
            "warmup_occurrences": occurrences(execution),
            "state_transitions": transitions(),
        }
        arguments.update(overrides)
        arguments["warmup_occurrences"] = iter(arguments["warmup_occurrences"])
        arguments["state_transitions"] = iter(arguments["state_transitions"])
        return arguments

    def build(self, execution=None, **overrides):
        return build_warmup_transcript(**self.arguments(execution, **overrides))

    def verify(self, receipt, execution=None, **overrides):
        return verify_warmup_transcript(
            receipt, **self.arguments(execution, **overrides)
        )

    def test_verified_execution_derives_all_authority_and_holds(self):
        receipt = self.build()
        window = self.execution["windows"][0]
        binding = receipt["execution_binding"]
        boundary = receipt["boundary_authority"]
        replay = receipt["replay_receipt"]
        self.assertEqual(receipt["schema"], WARMUP_TRANSCRIPT_SCHEMA)
        self.assertEqual(binding["execution_input_aggregate_sha256"], self.execution["aggregate_sha256"])
        self.assertEqual(binding["consumer_dependency_aggregate_sha256"], self.execution["consumer_dependency_aggregate_sha256"])
        self.assertEqual(binding["window_events_sha256"], window["events_sha256"])
        self.assertEqual(binding["window_neutral_inputs_sha256"], window["neutral_inputs_sha256"])
        self.assertEqual(binding["ordered_warmup_event_ids_sha256"], window["ordered_warmup_event_ids_sha256"])
        self.assertEqual(binding["ordered_query_event_ids_sha256"], window["ordered_query_event_ids_sha256"])
        first_query = window["events"][9]
        self.assertEqual(boundary["first_query_event_id"], first_query["event_id"])
        self.assertEqual(boundary["first_query_event_content_sha256"], first_query["event_content_sha256"])
        self.assertEqual(boundary["query_end_ns_exclusive"], 51_000_000)
        self.assertEqual(boundary["first_query_decision_cycle"], timestamp_to_cycle(first_query["timestamp_ns"], 0))
        self.assertEqual(boundary["first_query_occurrence_cycle"], boundary["first_query_decision_cycle"] - 1)
        self.assertEqual(replay["logical_cycle_replay_authority"], self.execution["logical_cycle_replay_authority"])
        self.assertEqual(replay["logical_cycle_replay_authority_sha256"], self.execution["logical_cycle_replay_authority_sha256"])
        self.assertEqual(replay["native_candidate_replay_status"], EXTERNAL_PRODUCTION_HOLD)
        self.assertEqual(replay["candidate_state_payload_status"], EXTERNAL_PRODUCTION_HOLD)
        self.assertEqual(replay["pending_transition_payload_status"], EXTERNAL_PRODUCTION_HOLD)
        self.assertEqual(self.verify(receipt), receipt["receipt_sha256"])

    def test_public_apis_have_no_caller_authority_arguments(self):
        forbidden = {"bindings", "bounds", "boundary_close_authority", "boundary_authority"}
        for function in (
            begin_warmup_transcript,
            build_warmup_transcript,
            verify_warmup_transcript,
        ):
            self.assertTrue(forbidden.isdisjoint(inspect.signature(function).parameters))
        arguments = self.arguments()
        arguments["bindings"] = {"execution_input_aggregate_sha256": SHA_A}
        with self.assertRaises(TypeError):
            build_warmup_transcript(**arguments)

        re_iterable = self.arguments()
        re_iterable["warmup_occurrences"] = occurrences(self.execution)
        with self.assertRaisesRegex(WarmupTranscriptError, "one-shot iterators"):
            build_warmup_transcript(**re_iterable)

    def test_fabricated_reused_out_of_range_and_equal_split_boundaries_fail(self):
        pristine = self.build()
        attacks = []
        for field, value in (
            ("first_query_event_id", pristine["boundary_authority"]["last_warmup_event_id"]),
            ("first_query_event_content_sha256", "0" * 64),
            ("first_query_timestamp_ns", 51_000_000),
            ("first_query_timestamp_ns", pristine["boundary_authority"]["last_warmup_timestamp_ns"]),
            ("query_end_ns_exclusive", 52_000_000),
            ("ordered_query_event_ids_sha256", "1" * 64),
            ("query_event_count", 99),
            ("first_query_decision_cycle", 7),
        ):
            mutant = deepcopy(pristine)
            mutant["boundary_authority"][field] = value
            boundary_body = dict(mutant["boundary_authority"])
            boundary_body.pop("boundary_close_sha256")
            mutant["boundary_authority"]["boundary_close_sha256"] = canonical_sha256(boundary_body)
            receipt_body = dict(mutant)
            receipt_body.pop("receipt_sha256")
            mutant["receipt_sha256"] = canonical_sha256(receipt_body)
            attacks.append(mutant)
        for attack in attacks:
            with self.subTest(boundary=attack["boundary_authority"]):
                with self.assertRaises(WarmupTranscriptError):
                    self.verify(attack)

    def test_verified_input_mutations_cannot_be_resealed_into_authority(self):
        mutant = deepcopy(self.execution)
        mutant["windows"][0]["events"][9]["event_id"] = mutant["windows"][0]["events"][0]["event_id"]
        mutant["windows"][0]["events_sha256"] = canonical_sha256(mutant["windows"][0]["events"])
        mutant["windows_sha256"] = canonical_sha256(mutant["windows"])
        body = dict(mutant)
        body.pop("aggregate_sha256")
        mutant["aggregate_sha256"] = canonical_sha256(body)
        with self.assertRaisesRegex(WarmupTranscriptError, "verification failed"):
            self.build(execution=mutant, warmup_occurrences=occurrences(self.execution))

    def test_canonical_snapshot_closes_mutable_input_toctou(self):
        mutable = deepcopy(self.execution)
        source = occurrences(mutable)
        original_query_id = mutable["windows"][0]["events"][9]["event_id"]

        def mutate_caller_after_snapshot(snapshot, **unused):
            mutable["windows"][0]["events"][9]["event_id"] = 777_777
            return snapshot["aggregate_sha256"]

        with mock.patch.object(
            transcript_module,
            "verify_stage3_execution_input",
            side_effect=mutate_caller_after_snapshot,
        ):
            receipt = self.build(
                execution=mutable, warmup_occurrences=source
            )
        self.assertEqual(
            receipt["boundary_authority"]["first_query_event_id"],
            original_query_id,
        )
        self.assertEqual(mutable["windows"][0]["events"][9]["event_id"], 777_777)

    def test_source_chain_rejects_drop_reorder_duplicate_content_and_cycle(self):
        pristine = self.build()
        source = occurrences(self.execution)
        attacks = [source[:-1]]
        reordered = deepcopy(source)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        attacks.append(reordered)
        duplicate = deepcopy(source)
        duplicate[-1]["event_id"] = duplicate[0]["event_id"]
        attacks.append(duplicate)
        content = deepcopy(source)
        content[0]["event_content_sha256"] = "0" * 64
        attacks.append(content)
        cycle = deepcopy(source)
        cycle[0]["decision_cycle"] += 1
        cycle[0]["occurrence_cycle"] += 1
        cycle[0]["service_cycle"] += 1
        attacks.append(cycle)
        for attack in attacks:
            with self.subTest(attack=attack):
                with self.assertRaises(WarmupTranscriptError):
                    self.verify(pristine, warmup_occurrences=attack)

    def test_unique_numerically_decreasing_ids_pass_in_source_order(self):
        receipt = self.build()
        self.assertEqual(receipt["first_warmup_occurrence"]["event_id"], 90)
        self.assertEqual(receipt["last_warmup_occurrence"]["event_id"], 50)
        self.assertEqual(
            self.verify(
                receipt,
                warmup_occurrences=(row for row in occurrences(self.execution)),
            ),
            receipt["receipt_sha256"],
        )

    def test_same_edge_old_state_transition_and_future_pending(self):
        source = occurrences(self.execution)
        self.assertEqual(source[1]["decision_cycle"], transitions()[0]["commit_cycle"])
        self.assertEqual(source[1]["predictor_state_version"], 0)
        pending = {
            "transition_ordinal": 2,
            "pose_id": 3,
            "pose_content_sha256": "7" * 64,
            "measurement_timestamp_ns": 900,
            "commit_cycle": timestamp_to_cycle(900, 0),
            "publication_cycle": timestamp_to_cycle(50_000_100, 0) + 1,
            "effective_cycle": timestamp_to_cycle(50_000_100, 0) + 2,
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
        receipt = self.build(state_transitions=replay_transitions)
        self.assertEqual(receipt["pending_query_transition"]["effective_cycle"], pending["effective_cycle"])
        self.assertEqual(receipt["pending_query_transition"]["authentication_status"], EXTERNAL_PRODUCTION_HOLD)
        self.assertEqual(self.verify(receipt, state_transitions=replay_transitions), receipt["receipt_sha256"])

        gap_pending = deepcopy(pending)
        gap_pending["publication_cycle"] = timestamp_to_cycle(50_000_000, 0) + 1
        gap_pending["effective_cycle"] = timestamp_to_cycle(50_000_000, 0) + 1
        self.assertLess(
            gap_pending["effective_cycle"],
            timestamp_to_cycle(50_000_100, 0),
        )
        gap_transitions = transitions() + [gap_pending]
        gap_receipt = self.build(state_transitions=gap_transitions)
        self.assertEqual(
            gap_receipt["query_start_state"]["predictor_state_version"], 2
        )
        self.assertEqual(
            gap_receipt["pending_query_transition"]["effective_cycle"],
            gap_pending["effective_cycle"],
        )
        self.assertEqual(
            self.verify(gap_receipt, state_transitions=gap_transitions),
            gap_receipt["receipt_sha256"],
        )

    def test_transition_snapshot_and_hold_mutations_fail(self):
        pristine = self.build()
        transition_attacks = [transitions()[:-1]]
        wrong_commit = transitions()
        wrong_commit[0]["commit_cycle"] += 1
        transition_attacks.append(wrong_commit)
        wrong_pose = transitions()
        wrong_pose[0]["pose_content_sha256"] = "8" * 64
        transition_attacks.append(wrong_pose)
        for attack in transition_attacks:
            with self.subTest(transition=attack):
                with self.assertRaises(WarmupTranscriptError):
                    self.verify(pristine, state_transitions=attack)

        snapshot = deepcopy(pristine)
        bank = snapshot["query_start_reference_snapshot"]["polarity_0"]
        bank[0]["world_ray"] = ray(0.7)
        snapshot_body = snapshot["query_start_reference_snapshot"]
        snapshot_body["polarity_0_sha256"] = canonical_sha256({
            "domain": "stage3/warmup/reference_snapshot/polarity_0/v1",
            "observations": bank,
        })
        unsigned_snapshot = dict(snapshot_body)
        unsigned_snapshot.pop("snapshot_sha256")
        snapshot_body["snapshot_sha256"] = canonical_sha256(unsigned_snapshot)
        unsigned_receipt = dict(snapshot)
        unsigned_receipt.pop("receipt_sha256")
        snapshot["receipt_sha256"] = canonical_sha256(unsigned_receipt)
        with self.assertRaises(WarmupTranscriptError):
            self.verify(snapshot)

        false_go = deepcopy(pristine)
        false_go["replay_receipt"]["native_candidate_replay_status"] = "PASS"
        replay_body = dict(false_go["replay_receipt"])
        replay_body.pop("replay_receipt_sha256")
        false_go["replay_receipt"]["replay_receipt_sha256"] = canonical_sha256(replay_body)
        receipt_body = dict(false_go)
        receipt_body.pop("receipt_sha256")
        false_go["receipt_sha256"] = canonical_sha256(receipt_body)
        with self.assertRaises(WarmupTranscriptError):
            self.verify(false_go)

    def test_reset_and_exact_reference_policy_are_locked(self):
        for mutation, message in (
            (("reset_cycle", 1), "cycle-zero"),
            (("reset_generation", 1), "cycle-zero"),
            (("predictor_state_version", 1), "cycle-zero"),
            (("dependency_pose_count", 1), "cycle-zero"),
        ):
            bad = reset()
            bad[mutation[0]] = mutation[1]
            if mutation[0] == "dependency_pose_count":
                bad["last_dependency_pose_id"] = 1
            body = dict(bad)
            body.pop("reset_sha256")
            bad["reset_sha256"] = canonical_sha256(body)
            with self.subTest(field=mutation[0]):
                with self.assertRaisesRegex(WarmupTranscriptError, message):
                    self.build(reset=bad)
        for field, value in (("capacity_per_polarity", 257), ("max_age_ns", 1_999_999)):
            bad_policy = reference_policy()
            bad_policy[field] = value
            with self.assertRaisesRegex(WarmupTranscriptError, "fixed bounds"):
                self.build(reference_prime_policy=bad_policy)

    def test_snapshot_matches_actual_cluster_state_and_first_query_expires_gap(self):
        receipt = self.build()
        snapshot = receipt["query_start_reference_snapshot"]
        self.assertEqual(snapshot["last_warmup_timestamp_ns"], 900)
        self.assertGreater(snapshot["observation_count"], 0)
        for polarity in (0, 1):
            values = snapshot["polarity_%d" % polarity]
            self.assertEqual(
                [(row["timestamp_ns"], row["occurrence_ordinal"]) for row in values],
                sorted((row["timestamp_ns"], row["occurrence_ordinal"]) for row in values),
            )
            self.assertLessEqual(len(values), 256)

        source = occurrences(self.execution)
        warmup = tuple(ReferenceObservation(
            row["event_id"], row["timestamp_ns"], row["polarity"], tuple(row["world_ray"])
        ) for row in source)
        query = ReferenceObservation(100, 50_000_100, 0, tuple(ray(0.07)))
        config = CausalReferenceConfig(256, 2_000_000)
        legacy = CausalReferenceBank(config).process(warmup + (query,))[-1]
        primed = ScoreFreeCausalReferenceBank(config)
        primed.prime(tuple(ReferenceObservation(
            row["event_id"], row["timestamp_ns"], row["polarity"], tuple(row["world_ray"])
        ) for row in merge_snapshot(snapshot)))
        actual = primed.process((query,))[0]
        self.assertFalse(actual.reference_available)
        self.assertEqual(actual, legacy)

    def test_recursive_unknowns_and_locally_resealed_mutations_fail(self):
        pristine = self.build()
        paths = (
            ("execution_binding",),
            ("boundary_authority",),
            ("replay_receipt", "logical_cycle_replay_authority", "profile"),
            ("query_start_state",),
            ("query_start_reference_snapshot", "polarity_0", 0),
        )
        for path in paths:
            mutant = deepcopy(pristine)
            target = mutant
            for part in path:
                target = target[part]
            target["unknown"] = 1
            receipt_body = dict(mutant)
            receipt_body.pop("receipt_sha256")
            mutant["receipt_sha256"] = canonical_sha256(receipt_body)
            with self.subTest(path=path):
                with self.assertRaises(WarmupTranscriptError):
                    self.verify(mutant)

    def test_committed_schemas_are_closed_and_accept_receipt(self):
        receipt = self.build()
        schema_cases = {
            "stage3_warmup_transcript.schema.json": receipt,
            "stage3_warmup_boundary.schema.json": receipt["boundary_authority"],
            "stage3_warmup_replay_receipt.schema.json": receipt["replay_receipt"],
            "stage3_warmup_pending_transition.schema.json": self.build(
                state_transitions=transitions() + [{
                    "transition_ordinal": 2,
                    "pose_id": 3,
                    "pose_content_sha256": "7" * 64,
                    "measurement_timestamp_ns": 900,
                    "commit_cycle": timestamp_to_cycle(900, 0),
                    "publication_cycle": timestamp_to_cycle(50_000_100, 0) + 1,
                    "effective_cycle": timestamp_to_cycle(50_000_100, 0) + 2,
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
                }]
            )["pending_query_transition"],
            "stage3_warmup_query_start_state.schema.json": receipt["query_start_state"],
            "stage3_warmup_reference_snapshot.schema.json": receipt["query_start_reference_snapshot"],
        }
        schema_root = ROOT / "benchmarks/redred_mc_wtb_predictor_stage3"
        transcript_schema = json.loads((
            schema_root / "stage3_warmup_transcript.schema.json"
        ).read_text(encoding="utf-8"))
        registry = SchemaRegistry().with_resource(
            transcript_schema["$id"], Resource.from_contents(transcript_schema)
        )
        for filename, instance in schema_cases.items():
            schema = json.loads((schema_root / filename).read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema, registry=registry)
            with self.subTest(schema=filename):
                self.assertEqual(list(validator.iter_errors(instance)), [])
                mutant = deepcopy(instance)
                mutant["unknown"] = True
                self.assertNotEqual(list(validator.iter_errors(mutant)), [])
        top_validator = Draft202012Validator(transcript_schema, registry=registry)
        recursive = deepcopy(receipt)
        recursive["replay_receipt"]["logical_cycle_replay_authority"]["profile"]["unknown"] = 1
        self.assertNotEqual(list(top_validator.iter_errors(recursive)), [])

    def test_20k_generator_retains_no_rich_rows_or_unbounded_id_set(self):
        warmup_events = []
        for ordinal in range(20_000):
            warmup_events.append(make_event(
                100_000 - ordinal, 1 + ordinal * 1_000, ordinal % 2, 0
            ))
        query_event = make_event(200_000, 50_000_100, 0, 0)
        all_events = tuple(warmup_events) + (query_event,)
        expanded = build_stage3_execution_input(
            (Registry(WINDOW_ID, 0, 50_000_000, 51_000_000),),
            {WINDOW_ID: all_events},
            {WINDOW_ID: (make_pose(0, 0),)},
            source_events_authority={
                "source_events_path": "external/new108/events.txt",
                "source_events_sha256": "0" * 64,
                "source_events_size_bytes": 1,
                "source_events_line_count": len(all_events),
            },
            repo_root=ROOT,
        )
        builder = begin_warmup_transcript(
            execution_input=expanded,
            window_id=WINDOW_ID,
            repo_root=ROOT,
            reference_prime_policy=reference_policy(),
            transport_policy=transport_policy(8),
            reset=reset(),
        )
        del expanded
        del warmup_events
        del all_events

        def stream():
            for ordinal in range(20_000):
                timestamp = 1 + ordinal * 1_000
                decision_cycle = timestamp_to_cycle(timestamp, 0)
                event_id = 100_000 - ordinal
                polarity = ordinal % 2
                sensor_ray = (1.0, 0.0, 0.0)
                yield {
                    "occurrence_ordinal": ordinal,
                    "event_id": event_id,
                    "event_content_sha256": canonical_event_content_sha256(
                        event_id, timestamp, polarity, False, sensor_ray, 0, True
                    ),
                    "timestamp_ns": timestamp,
                    "polarity": polarity,
                    "occurrence_cycle": decision_cycle - 1,
                    "decision_cycle": decision_cycle,
                    "service_cycle": decision_cycle,
                    "predictor_state_version": 0,
                    "predictor_state_sha256": SHA_A,
                    "state_dependency_pose_count": 0,
                    "state_dependency_pose_chain_sha256": EMPTY_DEPENDENCY_CHAIN,
                    "state_last_dependency_pose_id": None,
                    "candidate_attempted": False,
                    "candidate_used": False,
                    "route": "FALLBACK",
                    "decision_sha256": canonical_sha256({"ordinal": ordinal}),
                    "world_ray": ray(ordinal * 0.00001),
                }

        for row in stream():
            builder.update_occurrence(row)
        retained = builder.retained_state_counts()
        self.assertEqual(retained["rich_occurrence_rows"], 0)
        self.assertEqual(retained["rich_transition_rows"], 0)
        self.assertLessEqual(retained["reference_polarity_0"], 256)
        self.assertLessEqual(retained["reference_polarity_1"], 256)
        for value in builder.__dict__.values():
            if isinstance(value, (list, tuple, set, dict, frozenset, bytes, bytearray)):
                self.assertLessEqual(len(value), 512)
        receipt = builder.finalize(query_state(0, SHA_A, 0, EMPTY_DEPENDENCY_CHAIN, None))
        self.assertEqual(receipt["warmup_occurrence_count"], 20_000)
        self.assertNotIn("warmup_occurrences", receipt)

    def test_python38_grammar_and_clean_import(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        ast.parse(source, filename=str(MODULE_PATH), feature_version=(3, 8))
        script = (
            "import json,sys; import benchmarks.redred_mc_wtb_predictor_stage3.warmup_transcript; "
            "bad=[m for m in sys.modules if (m.endswith('.selector') or "
            "m.endswith('.evaluator') or m.endswith('.screen108') or "
            "m.endswith('.scorer') or 'causal_reference' in m)]; "
            "print(json.dumps(sorted(bad)))"
        )
        completed = subprocess.run(
            [sys.executable, "-S", "-c", script], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [])


if __name__ == "__main__":
    unittest.main()
