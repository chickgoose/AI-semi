from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.a4_w7_fovea_cluster2_compare.run_w7_compare import (
    W7Error, discover_xrun, expected_load_pct, filelist_sources, scan_xcelium,
)


HERE = Path(__file__).resolve().parent
A1 = Path("/home/chickgoose/projects/a1")


FAKE_XRUN = r'''#!/usr/bin/env python3
import csv, os, sys
from pathlib import Path

args=sys.argv[1:]
log=Path(args[args.index('-l')+1])
log.parent.mkdir(parents=True,exist_ok=True)
ledger=Path(os.environ['FAKE_XRUN_LEDGER'])
with ledger.open('a') as f: f.write(('compile' if '-elaborate' in args else 'run')+'\n')
if '-elaborate' in args:
    log.write_text('xrun fake compile clean\n')
    sys.exit(0)
plus={a[1:].split('=',1)[0]:a.split('=',1)[1] for a in args if a.startswith('+') and '=' in a}
summary=Path(plus['METRICS']); events=Path(plus['EVENT_METRICS'])
summary.parent.mkdir(parents=True,exist_ok=True)
candidate=plus['CANDIDATE']; test=plus['CLEAN_TEST']
summary_cols=['candidate','test','seed','load_pct','stim_cycles','generated','source_overrun','accepted','delivered','errors','total_cycles','avg_e2e_latency','max_e2e_latency','avg_internal_latency','max_internal_latency','throughput','fairness','max_request_wait','avg_timing_error','max_timing_error','measurement_delivered','measurement_cycles']
event_cols=['candidate','test','seed','load_pct','tb_only_event_id','logical_source','source_count','occurrence_cycle','accept_cycle','delivery_cycle','deadline_cycle','observation_end_cycle','event_state']
if test == 'basic_reset_drain':
    with summary.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=summary_cols); w.writeheader(); w.writerow(dict.fromkeys(summary_cols,0)|{'candidate':candidate,'test':test,'seed':1,'load_pct':0,'stim_cycles':100,'generated':16,'accepted':16,'delivered':16,'measurement_delivered':16,'measurement_cycles':100,'throughput':'0.160000'})
    with events.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=event_cols); w.writeheader()
        for i in range(16): w.writerow({'candidate':candidate,'test':test,'seed':1,'load_pct':0,'tb_only_event_id':i,'logical_source':i,'source_count':16,'occurrence_cycle':0,'accept_cycle':1,'delivery_cycle':2,'deadline_cycle':'','observation_end_cycle':100,'event_state':'delivered'})
    log.write_text('AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16\nAER_CLEAN_TEST_PASS basic_reset_drain\n')
    sys.exit(0)
trace=Path(plus['TRACE_FILE']); report=plus['TRACE_NAME']
lines=trace.read_text().splitlines(); header=lines[0].split()
count,stim,load_milli,seed=map(int,(header[1],header[2],header[4],header[8])); load=(load_milli+5)//10
with summary.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=summary_cols); w.writeheader(); w.writerow(dict.fromkeys(summary_cols,0)|{'candidate':candidate,'test':report,'seed':seed,'load_pct':load,'stim_cycles':stim,'generated':count,'source_overrun':count,'measurement_cycles':stim,'throughput':'0.000000'})
with events.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=event_cols); w.writeheader()
    for row in lines[1:]:
        occurrence,event_id,source,address,deadline=map(int,row.split())
        w.writerow({'candidate':candidate,'test':report,'seed':seed,'load_pct':load,'tb_only_event_id':event_id,'logical_source':source,'source_count':16,'occurrence_cycle':occurrence,'accept_cycle':'','delivery_cycle':'','deadline_cycle':deadline,'observation_end_cycle':stim-1,'event_state':'source_overrun'})
payload=f'AER_CLEAN_TEST_PASS {report}\n'
if os.environ.get('FAKE_XRUN_BAD') == report: payload='xrun: *E, injected failure\n'+payload
log.write_text(payload)
'''


class W7CompareTest(unittest.TestCase):
    def make_fake(self, root: Path) -> tuple[Path, Path]:
        xrun = root / "xrun"
        xrun.write_text(FAKE_XRUN)
        xrun.chmod(xrun.stat().st_mode | stat.S_IXUSR)
        ledger = root / "ledger.txt"
        return xrun, ledger

    def command(self, root: Path, xrun: Path, output: Path) -> list[str]:
        fovea, cluster = root / "fovea.v", root / "cluster2.v"
        fovea.write_text("module fake_fovea; endmodule\n")
        cluster.write_text("module fake_cluster2; endmodule\n")
        return [sys.executable, str(HERE / "run_w7_compare.py"),
                "--a1-root", str(A1), "--xrun", str(xrun), "--output", str(output),
                "--fovea-top", "fake_fovea", "--fovea-rtl", str(fovea),
                "--cluster2-top", "fake_cluster2", "--cluster2-rtl", str(cluster)]

    def test_exact_compile_once_run_many_and_deferred_nonrankable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); xrun,ledger=self.make_fake(root); output=root/'out'
            env=os.environ.copy(); env['FAKE_XRUN_LEDGER']=str(ledger)
            completed=subprocess.run(self.command(root,xrun,output),env=env,text=True,capture_output=True,check=False)
            self.assertEqual(3,completed.returncode,completed.stdout + completed.stderr)
            receipt=json.loads((output/'receipt.json').read_text())
            self.assertEqual('HOLD_NONRANKABLE_CROSS_MAP',receipt['status'])
            self.assertEqual(['compile','compile'],[x for x in ledger.read_text().splitlines() if x=='compile'])
            self.assertEqual(102,sum(x=='run' for x in ledger.read_text().splitlines()))
            for key in ('fovea','cluster2'):
                item=receipt['candidates'][key]
                self.assertEqual((1,51),(item['compile_count'],item['run_count']))
                self.assertEqual(50,item['views']['full50']['run_count'])
                self.assertEqual(22,item['views']['capacity22']['run_count'])
                self.assertEqual(9,len(item['analyzers']))
                self.assertEqual({'full50_special':8,'capacity22_subset_special':6,'cross_map':1},item['analyzer_cardinality'])
            self.assertEqual({'full50','capacity22'},set(receipt['comparison']))
            self.assertEqual(0,receipt['comparison']['full50']['cluster2_minus_fovea']['accepted'])
            self.assertIn('trace_runs=100',completed.stdout)

    def test_xcelium_scan_rejects_error_even_with_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'xrun.log'
            log.write_text('xrun: *E, broken\nAER_CLEAN_TEST_PASS trace\n')
            with self.assertRaisesRegex(W7Error,'diagnostic'):
                scan_xcelium(log,'AER_CLEAN_TEST_PASS trace')

    def test_refuses_existing_output_before_xrun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); xrun,ledger=self.make_fake(root); output=root/'out'; output.mkdir()
            env=os.environ.copy(); env['FAKE_XRUN_LEDGER']=str(ledger)
            completed=subprocess.run(self.command(root,xrun,output),env=env,text=True,capture_output=True,check=False)
            self.assertEqual(2,completed.returncode)
            self.assertIn('refusing to overwrite',completed.stderr)
            self.assertFalse(ledger.exists())

    def test_load_rounding_matches_frozen_tb(self) -> None:
        self.assertEqual(13,expected_load_pct({'run':{'load':'0.125'}}))
        self.assertEqual(77,expected_load_pct({'run':{'load':'0.769'}}))

    def test_missing_xrun_and_relative_filelist_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaisesRegex(W7Error,'unavailable'):
                discover_xrun(root/'missing-xrun')
            filelist=root/'candidate.f'; filelist.write_text('relative.v\n')
            with self.assertRaisesRegex(W7Error,'must be absolute'):
                filelist_sources(filelist)


if __name__ == '__main__':
    unittest.main()
