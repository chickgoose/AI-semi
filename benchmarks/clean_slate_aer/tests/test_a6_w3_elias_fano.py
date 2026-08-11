#!/usr/bin/env python3

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARK_DIR))

import a6_w3_elias_fano as codec  # noqa: E402
import a6_w3_cycle_oracle as cycle_oracle  # noqa: E402
import a6_w3_evaluate as evaluate  # noqa: E402


class EliasFanoCodecTests(unittest.TestCase):
    def test_cycle_oracle_matches_batch_visible_and_k_slot_contract(self) -> None:
        rows = cycle_oracle.generate_rows()
        self.assertEqual([row.cycle for row in rows if row.accepted], [0, 20, 40, 79])
        # Three one-bit beats are exactly the EF markers.  The third waits from
        # encoder acceptance at 40 until the same-edge pop creates K free slots.
        self.assertEqual(
            [(row.cycle, row.link_data) for row in rows if row.link_count == 1],
            [(1, 2), (21, 2), (60, 2)],
        )
        self.assertFalse(any(row.decoded_valid for row in rows[:19]))
        self.assertEqual(rows[19].decoded_address, 0)
        self.assertEqual(sum(row.retired for row in rows), 51)

    def test_cap22_manifest_digest_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text('{"runs": []}\n', encoding="utf-8")
            with self.assertRaisesRegex(codec.CodecError, "digest"):
                evaluate.evaluate_cap22(manifest, Path(directory), max_batch=16)

    def test_known_monotone_batch_uses_real_elias_fano(self) -> None:
        sources = (0, 1, 2, 3, 8, 9, 10, 11)
        frame = codec.encode_batch(
            sources, num_sources=16, max_batch=16, force_mode="elias_fano"
        )
        self.assertEqual(frame.mode, "elias_fano")
        self.assertEqual(frame.low_width, 1)
        self.assertGreater(frame.high_bits, len(sources))
        self.assertEqual(
            codec.decode_beats(frame.beats, num_sources=16, max_batch=16),
            list(sources),
        )

    def test_random_round_trip_n16_n64_all_cardinalities(self) -> None:
        rng = random.Random(6003)
        for num_sources in (16, 64):
            for k in range(17):
                for _ in range(25):
                    sources = tuple(sorted(rng.sample(range(num_sources), k)))
                    frame = codec.encode_batch(
                        sources, num_sources=num_sources, max_batch=16
                    )
                    decoded = codec.decode_beats(
                        frame.beats, num_sources=num_sources, max_batch=16
                    )
                    self.assertEqual(decoded, list(sources))

    def test_raw_escape_has_no_header(self) -> None:
        frame = codec.encode_batch((15,), num_sources=16, max_batch=16)
        self.assertEqual(frame.mode, "raw")
        self.assertEqual(frame.framing_bits, 0)
        self.assertEqual(frame.valid_bits, 4)
        self.assertEqual(frame.link_cycles, 2)

    def test_empty_batch_is_explicit_and_decodes_to_no_occurrence(self) -> None:
        frame = codec.encode_batch((), num_sources=16, max_batch=16)
        self.assertEqual(frame.mode, "elias_fano")
        self.assertEqual(
            codec.decode_beats(frame.beats, num_sources=16, max_batch=16), []
        )

    def test_encoder_rejects_duplicate_unsorted_and_out_of_range(self) -> None:
        bad = ((1, 1), (2, 1), (-1,), (16,))
        for sources in bad:
            with self.subTest(sources=sources), self.assertRaises(codec.CodecError):
                codec.encode_batch(sources, num_sources=16, max_batch=16)

    def test_decoder_fails_closed_on_bad_marker_and_truncation(self) -> None:
        decoder = codec.StreamDecoder(num_sources=16, max_batch=16)
        with self.assertRaises(codec.CodecError):
            decoder.feed(codec.Beat("0"))

        frame = codec.encode_batch(
            (0, 2, 4, 6, 8, 10, 12, 14),
            num_sources=16, max_batch=16, force_mode="elias_fano",
        )
        with self.assertRaises(codec.CodecError):
            codec.decode_beats(frame.beats[:-1], num_sources=16, max_batch=16)

    def test_refire_closes_batch_and_partial_timeout_is_bounded(self) -> None:
        events = [
            codec.Event(10, 0, 3), codec.Event(10, 1, 1),
            codec.Event(11, 2, 3), codec.Event(12, 3, 2),
        ]
        batches = codec.batch_events(events, max_batch=8, window_cycles=4)
        self.assertEqual([batch.sources for batch in batches], [(1, 3), (2, 3)])
        self.assertEqual(batches[0].close_reason, "refire")
        self.assertEqual(batches[1].close_reason, "partial")
        self.assertEqual(batches[1].closed_cycle, 15)
        for batch in batches:
            frame = codec.encode_batch(
                batch.sources, num_sources=16, max_batch=8, force_mode="elias_fano"
            )
            decoded = codec.decode_beats(
                frame.beats, num_sources=16, max_batch=8
            )
            restored = codec.restore_provenance(batch, decoded)
            self.assertEqual(tuple(event.source for event in restored), batch.sources)

    def test_comparison_sweep_covers_n16_n64_k0_to_k(self) -> None:
        report = evaluate.sweep(16)
        self.assertEqual(len(report["points"]), 34)
        self.assertEqual(
            {(point["num_sources"], point["k"]) for point in report["points"]},
            {(n, k) for n in (16, 64) for k in range(17)},
        )
        n16_k8 = next(
            point for point in report["points"]
            if point["num_sources"] == 16 and point["k"] == 8
        )
        self.assertLess(
            n16_k8["elias_fano"]["mean_selected_cycles"],
            n16_k8["raw"]["cycles"],
        )

    def test_end_to_end_refire_and_burst_conservation(self) -> None:
        events = []
        sequence = 0
        for cycle in range(0, 64, 4):
            for source in range(16):
                events.append(codec.Event(cycle, sequence, source))
                sequence += 1
        result = evaluate.simulate(
            events, stim_cycles=64, num_sources=16, max_batch=16,
            window_cycles=0, codec=True,
        )
        self.assertEqual(result.generated, result.accepted + result.overrun)
        self.assertEqual(result.accepted, result.delivered)
        # Refire beyond the two-bank/link capacity is reported as source
        # overrun; it must never be hidden as codec loss.
        self.assertGreater(result.overrun, 0)
        self.assertGreater(result.ef_batches, 0)

    def test_end_to_end_ef_retires_only_after_terminal_beat(self) -> None:
        events = [codec.Event(0, source, source) for source in range(16)]
        raw = evaluate.simulate(
            events, stim_cycles=1, num_sources=16, max_batch=16,
            window_cycles=0, codec=False,
        )
        encoded = evaluate.simulate(
            events, stim_cycles=1, num_sources=16, max_batch=16,
            window_cycles=0, codec=True,
        )
        self.assertEqual(raw.latencies, tuple(range(3, 34, 2)))
        self.assertEqual(encoded.latencies, tuple(range(20, 36)))
        self.assertEqual(encoded.p95_latency, 35)
        self.assertGreater(encoded.p95_latency, raw.p95_latency)

    def test_same_cycle_mode_does_not_merge_backlogged_cycles(self) -> None:
        events = [
            codec.Event(0, 0, 0), codec.Event(0, 1, 1),
            codec.Event(1, 2, 2), codec.Event(1, 3, 3),
        ]
        # The transport can queue batches, but a window-zero batch is never
        # allowed to manufacture a four-source set spanning cycles 0 and 1.
        result = evaluate.simulate(
            events, stim_cycles=2, num_sources=16, max_batch=16,
            window_cycles=0, codec=True,
        )
        self.assertEqual(result.ef_batches, 0)
        self.assertEqual(result.raw_batches, 2)


if __name__ == "__main__":
    unittest.main()
