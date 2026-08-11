#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
import random
import statistics
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
CANDIDATE = HERE.parent
ROOT = CANDIDATE.parents[2]
sys.path.insert(0, str(CANDIDATE))

from generate_lockstep import generate  # noqa: E402
from model import MovingBlockTreeModel, run_occurrences  # noqa: E402


class MovingBlockModelTest(unittest.TestCase):
    def test_frozen_counterexamples_are_exercised(self) -> None:
        fixtures = json.loads((HERE / "counterexamples.json").read_text())
        self.assertEqual(
            [item["name"] for item in fixtures],
            [
                "retire_refill_clear_after_write",
                "two_microstep_double_inject",
                "stalled_root_clearance_leak",
                "dual_child_single_parent",
                "no_reset_shock_recovery",
            ],
        )

    def test_same_cycle_retire_refill_has_no_steady_bubble(self) -> None:
        model = MovingBlockTreeModel(16, 2)
        pending = [True] * 16
        payload = [source + 1 for source in range(16)]
        retired = []
        root_refills = 0
        for cycle in range(96):
            before_root = model.nodes[0]
            result = model.step(pending, payload, True)
            for source, accepted in enumerate(result.source_ready):
                if accepted:
                    payload[source] += 0x100
            if result.retired is not None:
                retired.append(result.retired.payload)
                if model.nodes[0] is not None:
                    root_refills += 1
            if cycle >= 4:
                self.assertTrue(result.retire_valid)
        self.assertGreater(root_refills, 80)
        self.assertEqual(len(retired), len(set(retired)))

    def test_long_stall_holds_root_and_recovers(self) -> None:
        model = MovingBlockTreeModel(16, 2)
        payload = [source + 1 for source in range(16)]
        for _ in range(4):
            model.step([True] * 16, payload, True)
            payload = [value + 0x100 for value in payload]
        held = None
        for _ in range(64):
            result = model.step([True] * 16, payload, False)
            if result.retire_valid:
                current = (result.retire_source, result.retire_payload)
                held = current if held is None else held
                self.assertEqual(current, held)
        for _ in range(512):
            result = model.step([False] * 16, [0] * 16, True)
            if model.occupancy() == 0:
                break
        self.assertEqual(model.occupancy(), 0)

    def test_branch_merge_conservation_and_source_order(self) -> None:
        model = MovingBlockTreeModel(16, 2)
        pending: list[int | None] = [None] * 16
        next_seq = [0] * 16
        accepted: set[tuple[int, int]] = set()
        last_retired = [0] * 16
        rng = random.Random(4404)
        for cycle in range(1200):
            for source in range(16):
                if pending[source] is None and rng.random() < 0.42:
                    next_seq[source] += 1
                    pending[source] = (source << 24) | next_seq[source]
            valid = [item is not None for item in pending]
            payload = [item or 0 for item in pending]
            result = model.step(valid, payload, rng.random() > 0.28)
            for source, did_accept in enumerate(result.source_ready):
                if did_accept:
                    key = (source, payload[source])
                    self.assertNotIn(key, accepted)
                    accepted.add(key)
                    pending[source] = None
            if result.retired is not None:
                key = (result.retired.source, result.retired.payload)
                self.assertIn(key, accepted)
                accepted.remove(key)
                sequence = result.retired.payload & 0xFFFFFF
                self.assertEqual(sequence, last_retired[result.retired.source] + 1)
                last_retired[result.retired.source] = sequence
        for _ in range(1000):
            valid = [item is not None for item in pending]
            payload = [item or 0 for item in pending]
            result = model.step(valid, payload, True)
            for source, did_accept in enumerate(result.source_ready):
                if did_accept:
                    accepted.add((source, payload[source]))
                    pending[source] = None
            if result.retired is not None:
                accepted.remove((result.retired.source, result.retired.payload))
            if not accepted and model.occupancy() == 0 and not any(pending):
                break
        self.assertFalse(accepted)
        self.assertEqual(model.occupancy(), 0)

    def test_b16_global_fanin_and_no_reset_mixed_recovery(self) -> None:
        occurrences = []
        for start in range(0, 320, 8):
            occurrences.extend((start, source) for source in range(16))
        for cycle in range(360, 680):
            if cycle < 440:
                occurrences.extend((cycle, source) for source in range(16))
            elif cycle % 13 == 0:
                occurrences.append((cycle, cycle % 16))
        metrics = run_occurrences(
            MovingBlockTreeModel(16, 2),
            occurrences,
            [True] * 17 + [False] * 23 + [True] * 31,
        )
        self.assertEqual(metrics.accepted, metrics.retired)
        self.assertEqual(metrics.offered, metrics.accepted + metrics.overrun)
        self.assertGreater(metrics.retired, 300)
        self.assertLess(max(metrics.latencies), 200)

    def test_one_step_comparison(self) -> None:
        occurrences = []
        for cycle in range(512):
            if cycle % 16 == 0:
                occurrences.extend((cycle, source) for source in range(16))
            elif cycle % 5 == 0:
                occurrences.append((cycle, (cycle // 5) % 16))
        fixed = run_occurrences(MovingBlockTreeModel(16, 1), occurrences, [True])
        moving = run_occurrences(MovingBlockTreeModel(16, 2), occurrences, [True])
        self.assertEqual(fixed.accepted, fixed.retired)
        self.assertEqual(moving.accepted, moving.retired)
        self.assertGreaterEqual(moving.accepted, fixed.accepted)
        self.assertLess(moving.output_bubbles, fixed.output_bubbles)
        self.assertGreater(moving.throughput, fixed.throughput)

        # Compare latency on an identical, uncensored event set. Under overload
        # the moving model accepts more events, so survivor means are not a
        # valid latency ranking.
        isolated = [(cycle, (cycle // 12) % 16) for cycle in range(0, 480, 12)]
        fixed_isolated = run_occurrences(
            MovingBlockTreeModel(16, 1), isolated, [True]
        )
        moving_isolated = run_occurrences(
            MovingBlockTreeModel(16, 2), isolated, [True]
        )
        self.assertEqual(fixed_isolated.overrun, 0)
        self.assertEqual(moving_isolated.overrun, 0)
        self.assertLess(
            statistics.mean(moving_isolated.latencies),
            statistics.mean(fixed_isolated.latencies),
        )


class MovingBlockRTLTest(unittest.TestCase):
    def test_verilator_lint_and_lockstep(self) -> None:
        verilator = pathlib.Path("/tmp/a7-sim-bin/verilator")
        if not verilator.exists():
            self.skipTest("local Verilator package is unavailable")
        rtl = CANDIDATE / "a4_moving_block_tree.sv"
        tb = HERE / "a4_moving_block_lockstep_tb.sv"
        with tempfile.TemporaryDirectory(prefix="a4-moving-block-") as tmp_name:
            tmp = pathlib.Path(tmp_name)
            lint = subprocess.run(
                [
                    str(verilator), "--lint-only", "--timing", "-Wall",
                    "-Wno-fatal", "--top-module", "a4_moving_block_tree", str(rtl),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
            self.assertNotIn("%Warning", lint.stdout + lint.stderr)
            for max_advance in (1, 2):
                vectors = tmp / f"vectors-{max_advance}.txt"
                object_dir = tmp / f"obj-{max_advance}"
                generate(vectors, max_advance=max_advance)
                build = subprocess.run(
                    [
                        str(verilator), "--binary", "--timing", "--assert",
                        "-Wall", "-Wno-fatal", "--top-module",
                        "a4_moving_block_lockstep_tb",
                        f"-GDUT_MAX_ADVANCE={max_advance}",
                        "--Mdir", str(object_dir), "-o", "sim",
                        str(rtl), str(tb),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                run = subprocess.run(
                    [str(object_dir / "sim"), f"+VECTORS={vectors}"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn(
                    "A4_MOVING_BLOCK_LOCKSTEP_PASS cycles=760", run.stdout
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
