"""Independent contract tests for the UZH raw/cyclemask crosswalk."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge import source_crosswalk as crosswalk_module
from benchmarks.redred_cluster2_cav_bridge.source_crosswalk import (
    BIN_NS,
    SourceCrosswalkError,
    derive_official_uzh_source_crosswalk_files,
    derive_source_crosswalk,
    derive_source_crosswalk_files,
)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def crosswalk(raw, cyclemask):
    return derive_source_crosswalk(raw, cyclemask, digest(raw), digest(cyclemask))


class SourceCrosswalkTests(unittest.TestCase):
    def test_true_raw_fields_and_cycle_then_source_identity(self):
        raw = (
            b"4.101999999 113 88 0\n"
            b"4.100999999 111 85 1\n"
            b"4.100000001 110 85 0\n"
            b"4.100500000 109 85 1\n"  # outside the raw 4x4 patch
        )
        rows = crosswalk(raw, b"4100 0003\n4101 8000\n")

        self.assertEqual(BIN_NS, 1_000_000)
        self.assertEqual(
            [
                (
                    row.event_id,
                    row.timestamp_ns,
                    row.x,
                    row.y,
                    row.polarity,
                    row.source_index,
                    row.occurrence_cycle,
                )
                for row in rows
            ],
            [
                (0, 4_100_000_001, 110, 85, 0, 0, 4100),
                (1, 4_100_999_999, 111, 85, 1, 1, 4100),
                (2, 4_101_999_999, 113, 88, 0, 15, 4101),
            ],
        )

    def test_bin_uses_the_pinned_float_division_expression(self):
        timestamp = "0.009000000"
        expected = int(float(timestamp) / 0.001)
        raw = (timestamp + " 110 85 1\n").encode("ascii")
        rows = crosswalk(raw, ("%d 0001\n" % expected).encode("ascii"))
        self.assertEqual(rows[0].occurrence_cycle, expected)
        self.assertEqual(rows[0].timestamp_ns, 9_000_000)

    def test_same_cycle_same_source_collision_fails(self):
        raw = b"1.000000001 110 85 0\n1.000999999 110 85 1\n"
        with self.assertRaisesRegex(SourceCrosswalkError, "collide"):
            crosswalk(raw, b"1000 0001\n")

    def test_cyclemask_slot_set_must_be_exactly_equal(self):
        raw = b"1.000000001 110 85 0\n1.001000001 111 85 1\n"
        for cyclemask in (
            b"1000 0001\n",
            b"1000 0001\n1001 0006\n",
            b"1000 0002\n1001 0002\n",
        ):
            with self.subTest(cyclemask=cyclemask):
                with self.assertRaisesRegex(SourceCrosswalkError, "slot sets differ"):
                    crosswalk(raw, cyclemask)

    def test_caller_sha_authorities_are_mandatory_and_exact(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\n"
        bad = "0" * 64
        with self.assertRaisesRegex(SourceCrosswalkError, "raw events bytes differ"):
            derive_source_crosswalk(raw, cyclemask, bad, digest(cyclemask))
        with self.assertRaisesRegex(SourceCrosswalkError, "cyclemask bytes differ"):
            derive_source_crosswalk(raw, cyclemask, digest(raw), bad)
        with self.assertRaisesRegex(SourceCrosswalkError, "caller SHA-256"):
            derive_source_crosswalk(raw, cyclemask, "A" * 64, digest(cyclemask))

    def test_raw_format_and_sensor_values_fail_closed(self):
        invalid_rows = (
            b"1.0 110 85 0\n",
            b"1.000000001 240 85 0\n",
            b"1.000000001 110 180 0\n",
            b"1.000000001 110 85 2\n",
            b"1.000000001 110 85 0",
            b"1.000000001 110 85 0\r\n",
        )
        for raw in invalid_rows:
            with self.subTest(raw=raw):
                with self.assertRaises(SourceCrosswalkError):
                    crosswalk(raw, b"1000 0001\n")

    def test_invalid_cyclemask_is_wrapped_as_crosswalk_failure(self):
        raw = b"1.000000001 110 85 0\n"
        with self.assertRaisesRegex(SourceCrosswalkError, "invalid cyclemask"):
            crosswalk(raw, b"1000 0000\n")

    def test_crlf_cyclemask_preserves_raw_authority_and_semantics(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\r\n"
        rows = crosswalk(raw, cyclemask)
        self.assertEqual(
            [(row.event_id, row.source_index, row.occurrence_cycle) for row in rows],
            [(0, 0, 1000)],
        )

    def test_official_authorities_are_exact_internal_constants(self):
        self.assertEqual(crosswalk_module._OFFICIAL_UZH_EVENTS_SIZE_BYTES, 509_907_771)
        self.assertEqual(
            crosswalk_module._OFFICIAL_UZH_EVENTS_SHA256,
            "d0b66503613354d1d274c56c979dfd89ba80b256c31eaba459a52adb7d03ffda",
        )
        self.assertEqual(
            crosswalk_module._OFFICIAL_CYCLEMASK_SHA256,
            (
                ("LF", "850049ea794fa80295ca9c0023d5549f2b7a8557776f37355b277aaccfde25ea"),
                ("CRLF", "a50866f95430e3fe8d8af775c2e9692353e1e6bc9a1ecfedfed620143be48313"),
            ),
        )

    def test_official_wrapper_rejects_caller_self_authorized_fixture(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            cyclemask_path.write_bytes(cyclemask)
            with self.assertRaisesRegex(SourceCrosswalkError, "accepted SHA-256"):
                derive_official_uzh_source_crosswalk_files(raw_path, cyclemask_path)
            with mock.patch.object(
                crosswalk_module,
                "_OFFICIAL_CYCLEMASK_SHA256",
                (("synthetic-test-only", digest(cyclemask)),),
            ):
                with self.assertRaisesRegex(SourceCrosswalkError, "official size"):
                    derive_official_uzh_source_crosswalk_files(raw_path, cyclemask_path)

    def test_official_wrapper_accepts_only_its_internal_lf_crlf_allowlist(self):
        raw = b"1.000000001 110 85 0\n"
        encodings = (b"1000 0001\n", b"1000 0001\r\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            with mock.patch.object(
                crosswalk_module, "_OFFICIAL_UZH_EVENTS_SIZE_BYTES", len(raw)
            ), mock.patch.object(
                crosswalk_module, "_OFFICIAL_UZH_EVENTS_SHA256", digest(raw)
            ), mock.patch.object(
                crosswalk_module,
                "_OFFICIAL_CYCLEMASK_SHA256",
                (("LF", digest(encodings[0])), ("CRLF", digest(encodings[1]))),
            ):
                for cyclemask in encodings:
                    with self.subTest(cyclemask=cyclemask):
                        cyclemask_path.write_bytes(cyclemask)
                        rows = derive_official_uzh_source_crosswalk_files(
                            raw_path, cyclemask_path
                        )
                        self.assertEqual(len(rows), 1)

    def test_bytes_apis_reject_byte_limits_before_hash_or_parse(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\n"
        with mock.patch.object(crosswalk_module, "MAX_RAW_EVENTS_BYTES", len(raw) - 1):
            with self.assertRaisesRegex(SourceCrosswalkError, "raw events exceeds"):
                crosswalk(raw, cyclemask)
        with mock.patch.object(
            crosswalk_module, "MAX_CYCLEMASK_BYTES", len(cyclemask) - 1
        ):
            with self.assertRaisesRegex(SourceCrosswalkError, "cyclemask exceeds"):
                crosswalk(raw, cyclemask)

    def test_file_apis_reject_stat_size_before_stream_hash(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            cyclemask_path.write_bytes(cyclemask)
            with mock.patch.object(
                crosswalk_module, "MAX_RAW_EVENTS_BYTES", len(raw) - 1
            ), mock.patch.object(
                crosswalk_module, "_scan_authenticated_raw_stream"
            ) as raw_scanner:
                with self.assertRaisesRegex(SourceCrosswalkError, "raw events exceeds"):
                    derive_source_crosswalk_files(
                        raw_path, cyclemask_path, digest(raw), digest(cyclemask)
                    )
                raw_scanner.assert_not_called()

            with mock.patch.object(
                crosswalk_module, "MAX_CYCLEMASK_BYTES", len(cyclemask) - 1
            ):
                with self.assertRaisesRegex(SourceCrosswalkError, "cyclemask exceeds"):
                    derive_source_crosswalk_files(
                        raw_path, cyclemask_path, digest(raw), digest(cyclemask)
                    )

    def test_patch_slot_cap_precedes_set_construction(self):
        raw = b"1.000000001 110 85 0\n1.001000001 111 85 0\n"
        cyclemask = b"1000 0001\n1001 0002\n"
        with mock.patch.object(crosswalk_module, "MAX_PATCH_SLOTS", 1):
            with self.assertRaisesRegex(SourceCrosswalkError, "slot count exceeds"):
                crosswalk(raw, cyclemask)

        one_raw_slot = b"1.000000001 110 85 0\n"
        with mock.patch.object(crosswalk_module, "MAX_PATCH_SLOTS", 1):
            with self.assertRaisesRegex(
                SourceCrosswalkError, "cyclemask patch slot count exceeds"
            ):
                crosswalk(one_raw_slot, cyclemask)

    def test_same_size_raw_overwrite_cannot_substitute_parsed_bytes(self):
        raw = b"1.000000001 110 85 0\n"
        mutated = b"1.000000001 110 85 1\n"
        cyclemask = b"1000 0001\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            cyclemask_path.write_bytes(cyclemask)

            real_open_regular = crosswalk_module._open_regular
            mutation = {"performed": False}

            class MutatingRawStream:
                def __init__(self, stream):
                    self.stream = stream

                def read(self, maximum=-1):
                    payload = self.stream.read(maximum)
                    if payload and not mutation["performed"]:
                        raw_path.write_bytes(mutated)
                        mutation["performed"] = True
                    return payload

                def fileno(self):
                    return self.stream.fileno()

                def close(self):
                    self.stream.close()

            def open_then_overwrite(path, where):
                stream, identity = real_open_regular(path, where)
                if where == "raw events":
                    stream = MutatingRawStream(stream)
                return stream, identity

            def stable_identity(value):
                return (value.st_dev, value.st_ino, value.st_mode, value.st_size)

            with mock.patch.object(
                crosswalk_module, "_open_regular", side_effect=open_then_overwrite
            ), mock.patch.object(
                crosswalk_module, "_file_identity", side_effect=stable_identity
            ):
                rows = derive_source_crosswalk_files(
                    raw_path, cyclemask_path, digest(raw), digest(cyclemask)
                )
            self.assertTrue(mutation["performed"])
            self.assertEqual(rows[0].polarity, 0)

    def test_cyclemask_uses_its_authenticated_captured_payload(self):
        raw = b"1.000000001 110 85 0\n"
        cyclemask = b"1000 0001\n"
        mutated = b"1000 0002\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            cyclemask_path.write_bytes(cyclemask)
            real_open_regular = crosswalk_module._open_regular

            class MutatingCyclemaskStream:
                def __init__(self, stream):
                    self.stream = stream

                def read(self, maximum):
                    payload = self.stream.read(maximum)
                    cyclemask_path.write_bytes(mutated)
                    return payload

                def fileno(self):
                    return self.stream.fileno()

                def close(self):
                    self.stream.close()

            def open_then_mutate(path, where):
                stream, identity = real_open_regular(path, where)
                if where == "cyclemask":
                    stream = MutatingCyclemaskStream(stream)
                return stream, identity

            with mock.patch.object(
                crosswalk_module, "_open_regular", side_effect=open_then_mutate
            ), mock.patch.object(
                crosswalk_module,
                "_file_identity",
                side_effect=lambda value: (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_size,
                ),
            ):
                rows = derive_source_crosswalk_files(
                    raw_path, cyclemask_path, digest(raw), digest(cyclemask)
                )
            self.assertEqual(
                [(row.source_index, row.occurrence_cycle) for row in rows],
                [(0, 1000)],
            )

    def test_file_api_preserves_authority_before_raw_syntax_errors(self):
        malformed = b"1.0 110 85 0\n"
        cyclemask = b"1000 0001\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            mask_path = root / "trace.cyclemask"
            raw_path.write_bytes(malformed)
            mask_path.write_bytes(cyclemask)
            with self.assertRaisesRegex(SourceCrosswalkError, "accepted SHA-256"):
                derive_source_crosswalk_files(
                    raw_path, mask_path, "0" * 64, digest(cyclemask)
                )
            with self.assertRaisesRegex(SourceCrosswalkError, "not canonical"):
                derive_source_crosswalk_files(
                    raw_path, mask_path, digest(malformed), digest(cyclemask)
                )

    def test_authenticated_raw_scanner_is_single_pass_and_chunk_bounded(self):
        raw = b"1.000000001 110 85 0\n1.001000001 111 85 1\n"

        class NoSeekShortReads:
            def __init__(self, payload):
                self.payload = payload
                self.offset = 0
                self.read_sizes = []

            def read(self, maximum):
                self.read_sizes.append(maximum)
                chunk = self.payload[self.offset:self.offset + 7]
                self.offset += len(chunk)
                return chunk

            def seek(self, *args):
                raise AssertionError("scanner must not seek")

        stream = NoSeekShortReads(raw)
        slots, actual, error = crosswalk_module._scan_authenticated_raw_stream(stream)
        self.assertIsNone(error)
        self.assertEqual(actual, digest(raw))
        self.assertEqual(len(slots), 2)
        self.assertTrue(all(size <= 1024 * 1024 for size in stream.read_sizes))

    def test_streaming_file_api_uses_the_same_caller_bound_contract(self):
        raw = b"1.000000001 110 85 0\n1.001000001 113 88 1\n"
        cyclemask = b"1000 0001\n1001 8000\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "events.txt"
            cyclemask_path = root / "trace.cyclemask"
            raw_path.write_bytes(raw)
            cyclemask_path.write_bytes(cyclemask)
            rows = derive_source_crosswalk_files(
                raw_path, cyclemask_path, digest(raw), digest(cyclemask)
            )
        self.assertEqual(
            [(row.event_id, row.source_index) for row in rows], [(0, 0), (1, 15)]
        )


if __name__ == "__main__":
    unittest.main()
