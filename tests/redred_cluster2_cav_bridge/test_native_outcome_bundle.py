from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import benchmarks.redred_cluster2_cav_bridge.native_outcome_bundle as bundle_module
from benchmarks.redred_cluster2_cav_bridge.native_outcome_bundle import (
    SEALED_BUNDLE_RELATIVE_PATH,
    SEALED_RECEIPT_RELATIVE_PATH,
    NativeOutcome,
    NativeOutcomeBundleError,
    load_abaa094_native_outcomes,
    load_native_outcome_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = ROOT / SEALED_RECEIPT_RELATIVE_PATH
BUNDLE_PATH = ROOT / SEALED_BUNDLE_RELATIVE_PATH


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical_jsonl(rows):
    return b"".join(
        (json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ) + "\n").encode("ascii")
        for row in rows
    )


class BundleFixture:
    def __init__(self):
        self.receipt = json.loads(RECEIPT_PATH.read_text(encoding="ascii"))
        with tarfile.open(BUNDLE_PATH, mode="r:gz") as archive:
            self.names = []
            self.artifacts = {}
            for member in archive:
                self.names.append(member.name)
                self.artifacts[member.name] = archive.extractfile(member).read()
        self.member_types = {}
        self.member_pax_headers = {}

    def write(self, root):
        for field, member in bundle_module._DIGEST_MEMBER_BY_FIELD.items():
            self.receipt["artifact_digests"][field] = sha256(
                self.artifacts[member]
            )
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in self.names:
                info = tarfile.TarInfo(name)
                info.mtime = 0
                info.mode = 0o644
                payload = self.artifacts[name]
                info.size = len(payload)
                info.pax_headers = self.member_pax_headers.get(name, {})
                if name in self.member_types:
                    info.type = self.member_types[name]
                    info.linkname = "native_ledger.psv"
                    info.size = 0
                    archive.addfile(info)
                else:
                    archive.addfile(info, io.BytesIO(payload))
        bundle_payload = buffer.getvalue()
        self.receipt["artifact_bundle"]["sha256"] = sha256(bundle_payload)
        receipt_payload = (
            json.dumps(
                self.receipt,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ) + "\n"
        ).encode("ascii")
        receipt_path = root / SEALED_RECEIPT_RELATIVE_PATH
        bundle_path = root / SEALED_BUNDLE_RELATIVE_PATH
        receipt_path.parent.mkdir(parents=True)
        bundle_path.parent.mkdir(parents=True)
        receipt_path.write_bytes(receipt_payload)
        bundle_path.write_bytes(bundle_payload)
        return sha256(receipt_payload)


def transport_rows(fixture):
    return [
        json.loads(line)
        for line in fixture.artifacts["transport_outcomes.jsonl"].splitlines()
    ]


def set_transport_rows(fixture, rows):
    fixture.artifacts["transport_outcomes.jsonl"] = canonical_jsonl(rows)


def set_ledger_event(fixture, row):
    lines = fixture.artifacts["native_ledger.psv"].decode("ascii").splitlines()
    prefix = "EVENT|%d|" % row["event_id"]
    replacement = "EVENT|%d|%d|%d|%s|%d|%d|%d|%d" % (
        row["event_id"],
        row["source_index"],
        row["occurrence_cycle"],
        row["outcome"],
        row["retire_cycle"],
        row["retire_native_lane"],
        row["retire_row"],
        row["retire_col"],
    )
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError("fixture ledger event identity differs")
    lines[matches[0]] = replacement
    fixture.artifacts["native_ledger.psv"] = ("\n".join(lines) + "\n").encode(
        "ascii"
    )


def bind_cyclemask_authority(fixture):
    payload = fixture.artifacts[bundle_module._CYCLEMASK_MEMBER]
    if b"\r\n" in payload:
        line_endings = "CRLF"
        canonical_lf = payload.replace(b"\r\n", b"\n")
    else:
        line_endings = "LF"
        canonical_lf = payload
    cyclemask = fixture.receipt["input_authority"]["cyclemask"]
    cyclemask["line_endings"] = line_endings
    cyclemask["raw_sha256"] = sha256(payload)
    cyclemask["canonical_semantic_lf_sha256"] = sha256(canonical_lf)


class SealedBundlePositiveTests(unittest.TestCase):
    def test_abaa094_bundle_returns_exact_native_cycle_latency_rows(self):
        outcomes = load_abaa094_native_outcomes(ROOT)

        self.assertEqual(len(outcomes), 8503)
        self.assertTrue(all(type(row) is NativeOutcome for row in outcomes))
        self.assertEqual(
            outcomes[0], NativeOutcome(0, 6, 4101, 4102, 1)
        )
        self.assertEqual(
            outcomes[1], NativeOutcome(1, 10, 4106, 4107, 1)
        )
        self.assertEqual(
            outcomes[-1], NativeOutcome(8502, 14, 59424, 59425, 1)
        )
        self.assertEqual(outcomes[0].source, outcomes[0].source_index)
        self.assertEqual(outcomes[0].latency, outcomes[0].latency_cycles)
        self.assertEqual(outcomes[0].to_mapping(), {
            "event_id": 0,
            "source": 6,
            "occurrence_cycle": 4101,
            "retire_cycle": 4102,
            "latency": 1,
        })
        self.assertEqual(
            {row.latency_cycles for row in outcomes}, {1, 2, 3}
        )
        self.assertTrue(all(
            row.latency_cycles == row.retire_cycle - row.occurrence_cycle
            for row in outcomes
        ))

    def test_generic_loader_accepts_digest_authorized_equivalent_bundle(self):
        fixture = BundleFixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = fixture.write(root)
            outcomes = load_native_outcome_bundle(
                root, SEALED_RECEIPT_RELATIVE_PATH, authority
            )
        self.assertEqual(len(outcomes), 8503)
        self.assertEqual(outcomes[0].source_index, 6)


class FailClosedBundleTests(unittest.TestCase):
    def assert_fixture_rejected(self, fixture):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = fixture.write(root)
            with self.assertRaises(NativeOutcomeBundleError):
                load_native_outcome_bundle(
                    root, SEALED_RECEIPT_RELATIVE_PATH, authority
                )

    def test_receipt_and_bundle_digest_authorities_are_mandatory(self):
        fixture = BundleFixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = fixture.write(root)
            with self.assertRaises(NativeOutcomeBundleError):
                load_native_outcome_bundle(
                    root, SEALED_RECEIPT_RELATIVE_PATH, "0" * 64
                )

            bundle_path = root / SEALED_BUNDLE_RELATIVE_PATH
            payload = bytearray(bundle_path.read_bytes())
            payload[-1] ^= 1
            bundle_path.write_bytes(bytes(payload))
            with self.assertRaises(NativeOutcomeBundleError):
                load_native_outcome_bundle(
                    root, SEALED_RECEIPT_RELATIVE_PATH, authority
                )

    def test_transport_types_canonicality_and_identity_fail_closed(self):
        mutations = []

        def boolean_source(fixture):
            rows = [
                json.loads(line)
                for line in fixture.artifacts["transport_outcomes.jsonl"].splitlines()
            ]
            rows[0]["source_index"] = True
            fixture.artifacts["transport_outcomes.jsonl"] = canonical_jsonl(rows)

        mutations.append(boolean_source)

        def changed_retire(fixture):
            rows = [
                json.loads(line)
                for line in fixture.artifacts["transport_outcomes.jsonl"].splitlines()
            ]
            rows[0]["retire_cycle"] += 1
            fixture.artifacts["transport_outcomes.jsonl"] = canonical_jsonl(rows)

        mutations.append(changed_retire)

        def noncanonical_jsonl(fixture):
            lines = fixture.artifacts["transport_outcomes.jsonl"].splitlines()
            first = json.loads(lines[0])
            lines[0] = json.dumps(first, sort_keys=False).encode("ascii")
            fixture.artifacts["transport_outcomes.jsonl"] = b"\n".join(lines) + b"\n"

        mutations.append(noncanonical_jsonl)

        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                fixture = BundleFixture()
                mutate(fixture)
                root = Path(temporary)
                authority = fixture.write(root)
                with self.assertRaises(NativeOutcomeBundleError):
                    load_native_outcome_bundle(
                        root, SEALED_RECEIPT_RELATIVE_PATH, authority
                    )

    def test_cyclemask_ledger_and_receipt_semantics_fail_closed(self):
        def changed_cyclemask(fixture):
            member = (
                "faer_snapshot/common_traces_uzh/"
                "uzh_shapes_rotation_patch.cyclemask.txt"
            )
            fixture.artifacts[member] = fixture.artifacts[member].replace(
                b"4101 0040", b"4101 0080", 1
            )

        def changed_ledger(fixture):
            fixture.artifacts["native_ledger.psv"] = fixture.artifacts[
                "native_ledger.psv"
            ].replace(
                b"EVENT|0|6|4101|DELIVERED|4102|0|1|2",
                b"EVENT|0|6|4101|DELIVERED|4103|0|1|2",
                1,
            )

        def false_invariant(fixture):
            fixture.receipt["invariants"]["no_phantom_retirement"] = False

        for mutate in (changed_cyclemask, changed_ledger, false_invariant):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                fixture = BundleFixture()
                mutate(fixture)
                root = Path(temporary)
                authority = fixture.write(root)
                with self.assertRaises(NativeOutcomeBundleError):
                    load_native_outcome_bundle(
                        root, SEALED_RECEIPT_RELATIVE_PATH, authority
                    )

    def test_redteam_cyclemask_member_must_match_exact_receipt_authority(self):
        lf_reencoding = BundleFixture()
        member = bundle_module._CYCLEMASK_MEMBER
        lf_reencoding.artifacts[member] = lf_reencoding.artifacts[member].replace(
            b"\r\n", b"\n"
        )

        wrong_raw = BundleFixture()
        wrong_raw.receipt["input_authority"]["cyclemask"]["raw_sha256"] = "0" * 64

        wrong_semantic = BundleFixture()
        wrong_semantic.receipt["input_authority"]["cyclemask"][
            "canonical_semantic_lf_sha256"
        ] = "0" * 64

        contaminated_schema = BundleFixture()
        contaminated_schema.receipt["input_authority"]["cyclemask"][
            "unbound_encoding_hint"
        ] = "CRLF"

        for fixture in (
            lf_reencoding, wrong_raw, wrong_semantic, contaminated_schema,
        ):
            with self.subTest(fixture=fixture):
                self.assert_fixture_rejected(fixture)

    def test_redteam_ledger_order_and_native_row_rules_are_replayed(self):
        reordered = BundleFixture()
        lines = reordered.artifacts["native_ledger.psv"].splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        reordered.artifacts["native_ledger.psv"] = b"\n".join(lines) + b"\n"

        same_lane_two_rows = BundleFixture()
        rows = transport_rows(same_lane_two_rows)
        rows[12]["retire_native_lane"] = 0
        set_transport_rows(same_lane_two_rows, rows)
        set_ledger_event(same_lane_two_rows, rows[12])

        illegal_two_lane_pair = BundleFixture()
        rows = transport_rows(illegal_two_lane_pair)
        rows[12]["retire_native_lane"] = 0
        rows[13]["retire_native_lane"] = 1
        set_transport_rows(illegal_two_lane_pair, rows)
        set_ledger_event(illegal_two_lane_pair, rows[12])
        set_ledger_event(illegal_two_lane_pair, rows[13])

        illegal_single_lane_row = BundleFixture()
        rows = transport_rows(illegal_single_lane_row)
        rows[1]["retire_native_lane"] = 1
        set_transport_rows(illegal_single_lane_row, rows)
        set_ledger_event(illegal_single_lane_row, rows[1])

        for fixture in (
            reordered,
            same_lane_two_rows,
            illegal_two_lane_pair,
            illegal_single_lane_row,
        ):
            with self.subTest(fixture=fixture):
                self.assert_fixture_rejected(fixture)

    def test_redteam_depth_two_preedge_fifo_and_bounded_drain_are_replayed(self):
        depth_two_overflow = BundleFixture()
        depth_two_overflow.artifacts[bundle_module._CYCLEMASK_MEMBER] = (
            b"0 0001\r\n1 0001\r\n2 0001\r\n"
        )
        rows = [{
            "schema": bundle_module.TRANSPORT_OUTCOME_SCHEMA,
            "event_id": event_id,
            "source_index": 0,
            "occurrence_cycle": event_id,
            "outcome": "DELIVERED",
            "retire_cycle": event_id + 3,
            "retire_native_lane": 1,
            "retire_row": 0,
            "retire_col": 0,
        } for event_id in range(3)]
        set_transport_rows(depth_two_overflow, rows)
        depth_two_overflow.artifacts["native_ledger.psv"] = (
            "SCHEMA|%s\n"
            "EVENT|0|0|0|DELIVERED|3|1|0|0\n"
            "EVENT|1|0|1|DELIVERED|4|1|0|0\n"
            "EVENT|2|0|2|DELIVERED|5|1|0|0\n"
            "SUMMARY|3|3|0\n" % bundle_module.LEDGER_SCHEMA
        ).encode("ascii")
        depth_two_overflow.receipt["counts"].update({
            "delivered": 3,
            "generated": 3,
            "native_ledger_lines": 5,
            "overrun": 0,
            "transport_outcome_rows": 3,
        })
        bind_cyclemask_authority(depth_two_overflow)

        unbounded_drain = BundleFixture()
        rows = transport_rows(unbounded_drain)
        rows[-1]["retire_cycle"] = 59424 + 100001
        set_transport_rows(unbounded_drain, rows)
        set_ledger_event(unbounded_drain, rows[-1])

        for fixture in (depth_two_overflow, unbounded_drain):
            with self.subTest(fixture=fixture):
                self.assert_fixture_rejected(fixture)

    def test_tar_member_order_and_regular_file_type_are_exact(self):
        fixtures = []
        reordered = BundleFixture()
        reordered.names[0], reordered.names[1] = reordered.names[1], reordered.names[0]
        fixtures.append(reordered)

        linked = BundleFixture()
        linked.member_types["transport_outcomes.jsonl"] = tarfile.SYMTYPE
        fixtures.append(linked)

        extra = BundleFixture()
        extra.names.append("unexpected-extra-member.txt")
        extra.artifacts["unexpected-extra-member.txt"] = b"extra\n"
        fixtures.append(extra)

        oversized_header = BundleFixture()
        oversized_header.member_pax_headers["native_ledger.psv"] = {
            "comment": "x" * (bundle_module.MAX_TAR_HEADER_BYTES + 1),
        }
        fixtures.append(oversized_header)

        for fixture in fixtures:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                authority = fixture.write(root)
                with self.assertRaises(NativeOutcomeBundleError):
                    load_native_outcome_bundle(
                        root, SEALED_RECEIPT_RELATIVE_PATH, authority
                    )

    def test_parser_uses_bounded_streaming_tar_iteration(self):
        parser = (
            ROOT
            / "benchmarks/redred_cluster2_cav_bridge/native_outcome_bundle.py"
        ).read_text(encoding="ascii")
        self.assertNotIn("getmembers", parser)
        self.assertIn('mode="r|gz"', parser)
        self.assertIn("MAX_TAR_HEADER_BYTES", parser)


if __name__ == "__main__":
    unittest.main()
