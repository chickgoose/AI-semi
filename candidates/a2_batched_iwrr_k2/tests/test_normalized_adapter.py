from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from normalized_adapter import (  # noqa: E402
    NormalizedAdapter,
    Record,
    transport_step,
)


def ready_pair(bits: int) -> tuple[bool, bool]:
    return bool(bits & 1), bool(bits & 2)


class ExhaustiveTransportPropertyTest(unittest.TestCase):
    def test_every_queue_offer_ready_reset_state(self) -> None:
        cases = 0
        for queue_count, offer_count, ready_bits, reset_n in itertools.product(
            range(3), range(3), range(4), (False, True),
        ):
            queue = tuple(Record(2 * index, 0x100 + index)
                          for index in range(queue_count))
            offered = tuple(Record(8 + index, 0x200 + index)
                            for index in range(offer_count))
            ready = ready_pair(ready_bits)
            result = transport_step(queue, offered, ready, reset_n)
            cases += 1

            if not reset_n:
                self.assertEqual((False, False), result.valid)
                self.assertFalse(result.offer_ready)
                self.assertEqual((), result.retired)
                self.assertEqual((), result.accepted)
                self.assertEqual((), result.queue)
                continue

            expected_retire = int(bool(queue) and ready[0])
            if len(queue) == 2 and ready[0] and ready[1]:
                expected_retire = 2
            remaining = queue[expected_retire:]
            fits = offer_count <= 2 - len(remaining)
            self.assertEqual(queue[:expected_retire], result.retired)
            self.assertEqual(offered if offered and fits else (), result.accepted)
            self.assertEqual(remaining + (offered if offered and fits else ()),
                             result.queue)
            self.assertLessEqual(len(result.queue), 2)
            self.assertEqual(fits, result.offer_ready)
            self.assertEqual(bool(queue), result.valid[0])
            self.assertEqual(len(queue) == 2 and ready[0] and ready[1],
                             result.valid[1])
            if not fits:
                # A two-record offer with one free slot is not a one-record
                # acceptance; no source acknowledgement is permitted.
                self.assertEqual((), result.accepted)
        self.assertEqual(72, cases)

    def test_retirement_is_a_prefix_and_refill_preserves_total_order(self) -> None:
        queue = (Record(1, 0x11), Record(2, 0x22))
        offered = (Record(3, 0x33), Record(4, 0x44))
        for ready_bits in range(4):
            result = transport_step(queue, offered, ready_pair(ready_bits))
            self.assertEqual(queue[:len(result.retired)], result.retired)
            history = result.retired + result.queue
            accepted = offered if result.accepted else ()
            self.assertEqual(queue + accepted, history)


class AtomicOwnerPropertyTest(unittest.TestCase):
    def test_policy_advances_exactly_by_the_atomically_enqueued_offer(self) -> None:
        payload = tuple(0xA000 | source for source in range(16))
        cases = 0
        for queue_count, request_count, ready_bits in itertools.product(
            range(3), range(3), range(4),
        ):
            model = NormalizedAdapter()
            model.queue = tuple(Record(12 + index, 0xD000 + index)
                                for index in range(queue_count))
            req = (1 << request_count) - 1
            before_cursor = model.owner.cursor
            before_pointers = model.owner.pointers
            observation = model.step(req, payload, ready_pair(ready_bits))
            offer = observation.owner_offer
            assert offer is not None
            cases += 1

            if observation.owner_fire:
                self.assertEqual(offer.next_cursor, model.owner.cursor)
                self.assertEqual(offer.next_pointers, model.owner.pointers)
                self.assertEqual(offer.bitmap, observation.source_ready)
            else:
                self.assertEqual(before_cursor, model.owner.cursor)
                self.assertEqual(before_pointers, model.owner.pointers)
                self.assertEqual(0, observation.source_ready)
            self.assertLessEqual(len(model.queue), 2)
        self.assertEqual(36, cases)

    def test_full_queue_holds_owner_offer_until_whole_bundle_fits(self) -> None:
        payload = tuple(0xB000 | source for source in range(16))
        model = NormalizedAdapter()
        model.queue = (Record(14, 0xEE), Record(15, 0xFF))
        stalled = model.step(0x0003, payload, (False, False))
        self.assertFalse(stalled.owner_fire)
        self.assertIsNotNone(model.owner.held)
        state = model.owner.cursor, model.owner.pointers, model.owner.held

        changed = model.step(0xF000, payload, (True, False))
        self.assertFalse(changed.owner_fire)  # only one slot was freed
        self.assertEqual(state, (model.owner.cursor, model.owner.pointers,
                                 model.owner.held))
        self.assertEqual((Record(15, 0xFF),), model.queue)

        committed = model.step(0, payload, (True, False))
        self.assertTrue(committed.owner_fire)
        self.assertEqual(stalled.owner_offer, committed.owner_offer)
        self.assertEqual(2, len(model.queue))
        self.assertEqual(
            stalled.owner_offer.address,
            tuple(record.source for record in model.queue),
        )

    def test_reset_aborts_queue_and_held_offer_without_phantom(self) -> None:
        payload = tuple(0xC000 | source for source in range(16))
        model = NormalizedAdapter()
        model.queue = (Record(9, 0x99), Record(10, 0xAA))
        model.step(0x0003, payload, (False, False))
        self.assertIsNotNone(model.owner.held)
        reset = model.step(0xFFFF, payload, (True, True), reset_n=False)
        self.assertEqual((False, False), reset.retire_valid)
        self.assertEqual(0, reset.source_ready)
        self.assertTrue(reset.drain_idle)
        self.assertEqual((), model.queue)
        self.assertIsNone(model.owner.held)
        self.assertEqual(0, model.owner.cursor)
        self.assertEqual((0, 0, 0, 0), model.owner.pointers)


if __name__ == "__main__":
    unittest.main()
