from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "physical/k2_postroute_activity/run_postroute_activity.py"
SPEC = importlib.util.spec_from_file_location("postroute_activity", SCRIPT)
assert SPEC and SPEC.loader
activity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activity)


class PostrouteActivityTest(unittest.TestCase):
    def test_registry_covers_exact_five_and_shared_scope(self):
        registry, identity = activity.load_registry()
        self.assertEqual(
            ["fovea", "cluster2", "fovea_a7", "a2_p6", "a3_p6"],
            list(registry["candidates"]),
        )
        self.assertEqual("aer_clean_tb.candidate.dut", registry["activity_scope"])
        self.assertEqual([1, 8, 2, 2, 2], [
            registry["candidates"][name]["retire_lanes"]
            for name in registry["candidates"]
        ])
        self.assertEqual(64, len(identity["sha256"]))
        self.assertEqual(6500, registry["periods"]["6.5"]["period_ps"])
        self.assertEqual(3250,
                         registry["periods"]["6.5"]["ref_half_period_ps"])

    def test_period_tb_transform_changes_only_timebase_and_clock(self):
        frozen = (ROOT / "tb/clean/aer_clean_tb.sv").read_bytes()
        transformed = activity.materialize_period_tb(frozen, 6500, 3250)
        text = transformed.decode()
        self.assertIn("`timescale 1ps/1ps", text)
        self.assertIn("always #3250 clk = ~clk;", text)
        restored = text.replace("`timescale 1ps/1ps", "`timescale 1ns/1ps", 1)
        restored = restored.replace("always #3250 clk", "always #5 clk", 1)
        self.assertEqual(frozen.decode(), restored)
        with self.assertRaises(activity.ActivityError):
            activity.materialize_period_tb(frozen, 6500, 3000)

    def test_exact_netlist_sdf_binding_and_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            netlist = root / "top.postroute.v"
            sdf = root / "top.postroute.sdf"
            netlist.write_text("module exact_top(input clk); endmodule\n")
            sdf.write_text(
                '(DELAYFILE\n (SDFVERSION "3.0")\n'
                ' (DESIGN "exact_top")\n (TIMESCALE 1ns)\n)\n'
            )
            netlist_id, sdf_id = activity.validate_netlist_sdf(
                netlist, sdf, "exact_top")
            self.assertEqual(64, len(netlist_id["sha256"]))
            self.assertEqual(64, len(sdf_id["sha256"]))
            with self.assertRaises(activity.ActivityError):
                activity.validate_netlist_sdf(netlist, sdf, "wrong_top")
            sdf.write_text(sdf.read_text().replace(
                '(DESIGN "exact_top")', '(DESIGN "other")'))
            with self.assertRaises(activity.ActivityError):
                activity.validate_netlist_sdf(netlist, sdf, "exact_top")

    @staticmethod
    def _write_accounting(root: Path, *, ledger_source: int = 3) -> tuple[Path, Path, Path]:
        summary = root / "summary.csv"
        events = root / "events.csv"
        ledger = root / "retire-ledger.tsv"
        summary.write_text(
            "generated,source_overrun,accepted,delivered,errors,"
            "measurement_delivered,measurement_cycles\n2,1,1,1,0,1,4096\n"
        )
        events.write_text(
            "event_state,logical_source\nsource_overrun,2\ndelivered,3\n"
        )
        ledger.write_text(
            "ordinal\tsim_tick_1ps\tlane\tlogical_source\tlogical_event\n"
            f"0\t10000\t0\t{ledger_source}\t{ledger_source:x}\n"
        )
        return summary, events, ledger

    def test_common_accounting_and_retire_ledger_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, events, ledger = self._write_accounting(root)
            counts = activity.validate_functional_evidence(summary, events, ledger)
            self.assertEqual(1, counts["measurement_delivered"])
            ledger.write_text(ledger.read_text().replace("0\t10000", "1\t10000"))
            with self.assertRaises(activity.ActivityError):
                activity.validate_functional_evidence(summary, events, ledger)
            summary, events, ledger = self._write_accounting(root, ledger_source=17)
            with self.assertRaises(activity.ActivityError):
                activity.validate_functional_evidence(summary, events, ledger)

    def test_vcd_scope_duration_and_unknown_residence_gate(self):
        known = '''$timescale 1ps $end
$scope module aer_clean_tb $end
$scope module candidate $end
$scope module dut $end
$var wire 1 ! q $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
#10
1!
'''
        saif = '''(SAIFILE
  (DURATION 10)
  (INSTANCE dut
    (NET
      (q (T0 10) (T1 0) (TX 0) (TC 1) (IG 0))
    )
  )
)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcd_path, saif_path = root / "activity.vcd", root / "activity.saif"
            vcd_path.write_text(known)
            saif_path.write_text(saif)
            self.assertEqual((0, 10), activity.validate_vcd_and_saif(
                vcd_path, saif_path, "aer_clean_tb.candidate.dut"))
            with self.assertRaises(activity.ActivityError):
                activity.validate_vcd_and_saif(
                    vcd_path, saif_path, "aer_clean_tb.candidate.wrong")
            saif_path.write_text(saif.replace("(TX 0)", "(TX 1)"))
            with self.assertRaises(activity.ActivityError):
                activity.validate_vcd_and_saif(
                    vcd_path, saif_path, "aer_clean_tb.candidate.dut")

    def test_binding_uses_one_dut_scope_and_sdf(self):
        text = (ROOT / "physical/k2_postroute_activity/postroute_binding.sv").read_text()
        compact = "".join(text.split())
        self.assertEqual(3, len(list(__import__("re").finditer(
            r"`K2_POSTROUTE_DUT\s+dut\s*\(", text))))
        self.assertIn("$sdf_annotate(`K2_POSTROUTE_SDF,dut)", compact)
        self.assertIn("$dumpvars(0,dut)", compact)
        self.assertIn("wait(aer_clean_tb.measurement_active===1'b1)", compact)
        self.assertIn("wait(aer_clean_tb.measurement_active===1'b0)", compact)
        self.assertNotRegex(text, r"\b(?:fifo|queue|retry)_?\w*\s*\[")

    def test_receipt_emits_exact_innovus_activity_descriptor_shape(self):
        text = SCRIPT.read_text()
        self.assertIn('"innovus_power_input": {', text)
        self.assertIn('"file": {"path": artifacts[rebased.name]["path"]', text)
        self.assertIn('"format": "VCD"', text)
        self.assertIn('"window_start_ns": "0"', text)


if __name__ == "__main__":
    unittest.main()
