"""Independent contract tests for the UZH raw/cyclemask crosswalk."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from benchmarks.redred_cluster2_cav_bridge.source_crosswalk import (
    BIN_NS,
    SourceCrosswalkError,
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
