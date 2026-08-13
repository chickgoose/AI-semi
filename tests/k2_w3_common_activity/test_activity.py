from __future__ import annotations

import hashlib
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "physical/k2_w3_common_activity"
STAGED = pathlib.Path("/tmp/k2-phys-w2-techmap")


class ActivityTest(unittest.TestCase):
    def test_frozen_tb_and_three_minimal_bindings(self):
        frozen = ROOT / "tb/clean/aer_clean_tb.sv"
        self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(),
                         "27d9437a5179b0cb909d02edee1ac2f82ea6d20aeab9cfb64997b458192102a2")
        runner = (TOOLS / "run_xcelium_activity.sh").read_text()
        self.assertIn("-access +r", runner)
        self.assertIn(hashlib.sha256(frozen.read_bytes()).hexdigest(), runner)
        campaign = (TOOLS / "run_three_xcelium_activity.sh").read_text()
        self.assertIn("9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9", campaign)
        self.assertIn("9fde0ee816a80975d219b57e9799e73c198efc85d6e9aec4cb2a2e4816974705", campaign)
        self.assertIn("5ecd7d07e906e20a92e18103b72bd4fd0d74099547e57a357e7376657fee8372", campaign)
        for candidate, top, width in (
            ("fovea", "w2_fovea_r1_physical_staging_top", 2),
            ("a2", "w2_a2_p6_physical_staging_top", 5),
            ("a3", "w2_a3_p6_physical_staging_top", 5),
        ):
            text=(TOOLS/f"tb/{candidate}_staged_binding.sv").read_text()
            self.assertIn(top, text)
            compact="".join(text.split())
            self.assertIn("wait(aer_clean_tb.measurement_active===1'b1)",compact)
            self.assertIn("$dumpvars(0,dut)", text)
            self.assertNotRegex(text, r"\b(fifo|queue|retry)_?\w*\s*\[")
            filelist=(TOOLS/f"filelists/{candidate}.f").read_text()
            self.assertNotIn("aer_legacy_candidate_adapter.sv",filelist)
            self.assertIn("tb/clean/aer_clean_tb.sv",filelist)
            self.assertIn(f"logic [{width-1}:0] link_data",text)

    def test_resolver_and_symlink_fail_closed(self):
        if not STAGED.is_dir(): self.skipTest("staged worktree unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output=pathlib.Path(directory)/"resolved.f"
            subprocess.run([
                "python3",str(TOOLS/"resolve_staged_filelist.py"),
                "--root",str(STAGED),"--input",str(STAGED/"rtl/technology/physical_staging/filelists/a3_generic.f"),
                "--output",str(output)],check=True)
            self.assertIn(str(STAGED.resolve()),output.read_text())
            symlink=pathlib.Path(directory)/"link.f"
            symlink.symlink_to(STAGED/"rtl/technology/physical_staging/filelists/a3_generic.f")
            failed=subprocess.run(["python3",str(TOOLS/"resolve_staged_filelist.py"),
                                   "--root",str(STAGED),"--input",str(symlink),
                                   "--output",str(pathlib.Path(directory)/"bad.f")])
            self.assertNotEqual(failed.returncode,0)

    def test_rebase_and_real_saif_activity(self):
        vcd='''$timescale 1ps $end
$scope module aer_clean_tb $end
$scope module candidate $end
$scope module dut $end
$var wire 1 ! state $end
$var wire 2 " bus [1:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#100
0!
b00 "
#110
1!
b01 "
#120
'''
        window='''candidate=a3_p6_staged
start_tick_1ps=100
end_tick_1ps=120
ref_period_ps=10
sample_period_ps=10
sample_first_rise_ps=7
scope=aer_clean_tb.candidate.dut
'''
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory); raw=root/"raw.vcd"; win=root/"window"
            summary=root/"summary.csv"; out=root/"activity.vcd"
            digest=root/"sha"; saif=root/"activity.saif"
            raw.write_text(vcd); win.write_text(window)
            summary.write_text("measurement_cycles,errors\n1,0\n")
            subprocess.run(["python3",str(TOOLS/"rebase_vcd.py"),"--input",str(raw),
                            "--window",str(win),"--summary",str(summary),
                            "--output",str(out),
                            "--sha-output",str(digest)],check=True)
            self.assertRegex(out.read_text(),r"(?m)^#0$")
            self.assertRegex(out.read_text(),r"(?m)^#20$")
            subprocess.run(["python3",str(TOOLS/"vcd_to_saif.py"),"--vcd",str(out),
                            "--output",str(saif)],check=True)
            text=saif.read_text()
            self.assertIn("(DURATION 20)",text)
            self.assertIn("(state (T0 10) (T1 10)",text)
            self.assertIn("(bus[0]",text)
            self.assertIn("(TC 1)",text)
            self.assertIn("benchmark_measurement_cycles=1",digest.read_text())
            self.assertIn("activity_window_ref_cycles=2",digest.read_text())
            summary.write_text("measurement_cycles,errors\n2,0\n")
            failed = subprocess.run([
                "python3", str(TOOLS/"rebase_vcd.py"), "--input", str(raw),
                "--window", str(win), "--summary", str(summary),
                "--output", str(root/"bad.vcd"),
                "--sha-output", str(root/"bad.sha")])
            self.assertNotEqual(failed.returncode, 0)


if __name__ == "__main__": unittest.main()
