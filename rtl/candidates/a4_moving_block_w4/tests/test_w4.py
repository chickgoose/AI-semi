#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest


HERE = pathlib.Path(__file__).resolve().parent
W4 = HERE.parent
ROOT = W4.parents[2]
W3 = ROOT / "rtl/candidates/a4_moving_block_tree"
sys.path.insert(0, str(W3))
sys.path.insert(0, str(W4))

from analyze_p99 import detailed_replay  # noqa: E402


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class W4ResultContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = json.loads(
            (W4 / "results/w4_local_summary.json").read_text()
        )

    def test_locked_sources_and_qualification_boundaries(self) -> None:
        provenance = self.result["provenance"]
        self.assertEqual(
            sha256(W4 / "a4_moving_block_w4.sv"), provenance["w4_rtl_sha256"]
        )
        self.assertEqual(
            sha256(W4 / "run_w4_qualification.py"), provenance["runner_sha256"]
        )
        self.assertEqual(
            sha256(W4 / "analyze_p99.py"), provenance["tail_analyzer_sha256"]
        )
        self.assertEqual(
            sha256(HERE / "a4_w4_exact_lockstep_tb.sv"),
            provenance["lockstep_tb_sha256"],
        )
        self.assertEqual(self.result["status"]["common_qualification"], "HOLD")
        self.assertEqual(
            self.result["status"]["physical_ppa_qualification"], "HOLD"
        )
        self.assertEqual(
            self.result["qualification"]["full50_exact_rtl_lockstep_traces"],
            50,
        )
        self.assertEqual(
            self.result["qualification"]["capacity22_exact_rtl_lockstep_traces"],
            22,
        )

    def test_only_local_enable_is_structural_pareto(self) -> None:
        rows = {
            (row["sources"], row["design"]): row
            for row in self.result["mapping"]
        }
        for sources in (16, 64):
            baseline = rows[(sources, "frozen_850fbcf_normalized")]
            selected = rows[(sources, "shared_clearance_local_enable")]
            self.assertLess(selected["cells"], baseline["cells"])
            self.assertLess(selected["comb_cells"], baseline["comb_cells"])
            self.assertEqual(selected["state_bits"], baseline["state_bits"])
            self.assertLessEqual(selected["logic_depth"], baseline["logic_depth"])
            self.assertLessEqual(selected["max_fanout"], baseline["max_fanout"])
        self.assertGreater(
            rows[(16, "shared_clearance")]["max_fanout"],
            rows[(16, "frozen_850fbcf_normalized")]["max_fanout"],
        )
        self.assertGreater(
            rows[(64, "shared_clearance")]["logic_depth"],
            rows[(64, "frozen_850fbcf_normalized")]["logic_depth"],
        )

    def test_tail_split_is_conservative(self) -> None:
        full = self.result["p99_cause"]["full50"]
        cap = self.result["p99_cause"]["capacity22"]
        self.assertEqual(full["fixed_common_p99"], full["moving_common_p99"])
        self.assertEqual(cap["fixed_common_p99"], 46)
        self.assertEqual(cap["moving_common_p99"], 47)
        self.assertGreater(
            full["moving_latency_ge47_moving_only"],
            full["moving_latency_ge47_common"],
        )

    def test_detailed_replay_preserves_event_identity(self) -> None:
        events = [
            {"id": cycle * 16 + source, "cycle": cycle, "source": source}
            for cycle in range(12)
            for source in range(16)
        ]
        for advance in (1, 2):
            replay = detailed_replay(events, advance)
            self.assertEqual(replay.offered, replay.accepted | replay.dropped)
            self.assertEqual(replay.accepted, set(replay.latency))
            self.assertFalse(replay.accepted & replay.dropped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
