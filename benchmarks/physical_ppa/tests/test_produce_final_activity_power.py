import copy, hashlib, importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
import jsonschema

HERE=Path(__file__).resolve().parents[1]
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location("producer",HERE/"produce_final_activity_power.py")
producer=importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
qualifier_spec=importlib.util.spec_from_file_location("final_power_qualifier",HERE/"qualify_final_activity_power.py")
qualifier=importlib.util.module_from_spec(qualifier_spec); qualifier_spec.loader.exec_module(qualifier)

def put(root,path,data):
    data=data if isinstance(data,bytes) else data.encode(); target=root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    return {"path":path,"sha256":hashlib.sha256(data).hexdigest()}
def put_json(root,path,value): return put(root,path,json.dumps(value,sort_keys=True,indent=2)+"\n")

def save_evidence(root,evidence):
    path=root/"evidence-mutated.json"; path.write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n"); return path

def repin(root,evidence,index,key,data):
    candidate=evidence["candidates"][index]; target=root/candidate[key]["path"]
    target.write_bytes(data if isinstance(data,bytes) else data.encode()); candidate[key]["sha256"]=hashlib.sha256(target.read_bytes()).hexdigest()
    run_path=root/candidate["run_manifest"]["path"]; run=json.loads(run_path.read_text())
    if key in run["inputs"]: run["inputs"][key]=candidate[key]["sha256"]
    if key in run["outputs"]: run["outputs"][key]=candidate[key]["sha256"]
    candidate["run_manifest"]=put_json(root,candidate["run_manifest"]["path"],run)
    return save_evidence(root,evidence)

def netlist(top,width,alias=False):
    extra=", load_i" if alias else ""; decl="  input load_i;\n" if alias else ""
    return f"module {top}(ref_clk_i,sample_clk_i,rst_n,source_pending_i,source_accept_o,link_clk_o,link_data_o,retire_valid_o,retire_addr0_o,retire_addr1_o,drain_idle_o,protocol_error_o{extra});\n  input ref_clk_i,sample_clk_i,rst_n;\n  input [15:0] source_pending_i;\n  output [15:0] source_accept_o;\n  output link_clk_o;\n  output [{width-1}:0] link_data_o;\n  output [1:0] retire_valid_o;\n  output [3:0] retire_addr0_o,retire_addr1_o;\n  output drain_idle_o,protocol_error_o;\n{decl}endmodule\n"

def fixture(root,fmt="vcd"):
    plan,_=producer.load_plan(); profiles=plan["candidate_profiles"]
    contract=put(root,"contract.json",(HERE/"final_endpoint_contract.json").read_bytes())
    workload=put(root,"common/workload.json","fixture common workload\n"); trace=put(root,"common/trace.jsonl","fixture common trace\n")
    policy=put_json(root,"common/scope-policy.json",{"schema_version":1,"policy_id":"postroute-all-eligible-state-and-comb-v1","minimum_annotation_percent":95.0,"required_annotation_percent":100.0,"exclude":["power","ground"]})
    common={"workload_manifest":workload,"trace":trace,"test_id":"fixture-common-window","seed":17,"activity_format":fmt,"scope_root":"dut","scope_policy":policy,"window_start_cycle":0,"window_end_cycle_exclusive":100,"measurement_cycles":100,"clock_period_ns":1.0}
    common_hash=producer.canonical_sha256({k:(v["sha256"] if isinstance(v,dict) else v) for k,v in common.items()})
    campaign="fixture-campaign-not-server"
    tools={}
    for name in ("simulator","genus","innovus","power_engine"):
        exe=put(root,f"tools/{name}.bin",f"fixture {name} executable\n"); version=f"{name}-fixture-1"
        tools[name]={"name":name,"version":version,"executable":exe,"version_report":put(root,f"tools/{name}.version",version+"\n")}
    tool_manifest=put_json(root,"tools/manifest.json",{"schema_version":1,"campaign_id":campaign,"tools":tools})
    pdk_files={role:put(root,f"pdk/{role}.dat",f"fixture {role}\n") for role in ("liberty","tech_lef","cell_lef","qrc")}
    pdk_manifest=put_json(root,"pdk/manifest.json",{"schema_version":1,"pdk_id":"fixture-pdk","revision":"fixture-r1","voltage_v":0.9,"temperature_c":125,"rc_corner":"fixture-rc","files":pdk_files})
    candidates=[]
    for profile in profiles:
        cid,top,width=profile["id"],profile["top"],profile["link_data_width"]; base=f"candidates/{cid}"
        source=put(root,f"{base}/rtl/source.sv",f"module {cid}_source; endmodule\n")
        inventory={"schema_version":1,"candidate_id":cid,"commit_sha":profile["commit_sha"],"top":top,"sources":[source]}
        source_inventory=put_json(root,f"{base}/source-inventory.json",inventory)
        closure=producer.canonical_sha256({"sources":[source]})
        mapped=put(root,f"{base}/mapped.v",netlist(top,width)); post=put(root,f"{base}/postroute.v",netlist(top,width))
        sdf=put(root,f"{base}/postroute.sdf",f'(DELAYFILE (DESIGN "{top}"))\n'); spef=put(root,f"{base}/postroute.spef","*SPEF fixture\n")
        scope_core={"schema_version":1,"scope_root":"dut","objects":[{"path":"dut.q","bits":1}]}
        scope_value={**scope_core,"postroute_netlist_sha256":post["sha256"],"scope_policy_sha256":policy["sha256"]}
        scope=put_json(root,f"{base}/scope.json",scope_value); scope_hash=producer._scope_hash(scope_core,"dut")
        if fmt=="vcd": wave_text=f"$timescale 1ns $end\n$comment {cid} $end\n$scope module dut $end\n$var wire 1 ! q $end\n$upscope $end\n$enddefinitions $end\n#0\n0!\n#100\n1!\n"
        else: wave_text=f"(SAIFILE (DESIGN {cid}) (TIMESCALE 1 ns) (DURATION 100) (INSTANCE dut (NET (q (T0 50) (T1 50) (TX 0) (TC 2) (IG 0)))))\n"
        waveform=put(root,f"{base}/activity.{fmt}",wave_text)
        observed=producer._validate_waveform(wave_text.encode(),fmt,"dut",scope_core,100,1.0,1)
        coverage_text="ACTIVITY_ANNOTATION_COVERAGE_V1\n"+"\n".join([f"format={fmt}",f"waveform_sha256={waveform['sha256']}","scope_root=dut",f"scope_manifest_sha256={scope_hash}","window_start_cycle=0","window_end_cycle_exclusive=100","annotated_bits=1","eligible_bits=1",f"annotated_objects_sha256={observed}","coverage_percent=100.0","unresolved_objects=0"])+"\n"
        coverage=put(root,f"{base}/coverage.rpt",coverage_text)
        command="read_vcd" if fmt=="vcd" else "read_saif"
        import_line=f"{command} {waveform['path']} -scope dut -start 0 -end 100"
        power_script=put(root,f"{base}/power.tcl",f"{import_line}\nreport_power\n")
        power_log=put(root,f"{base}/power.log",f"{import_line}\nreport_power\nPOWER_COMPLETE\n")
        p=(profiles.index(profile)+1)*0.1; internal=p; switching=0.05; leakage=0.01; total=p+0.06
        report=put(root,f"{base}/power.rpt",f"# Design Stage: PostRoute\n* Design: {top}\n* Power Units = 1mW\n* User-Defined Activity : Imported\n* Activity File: {waveform['path']}\nTotal Internal Power: {internal:.8f}\nTotal Switching Power: {switching:.8f}\nTotal Leakage Power: {leakage:.8f}\nTotal Power: {total:.8f}\n")
        retired=put(root,f"{base}/retired.csv","event_id,retire_cycle,retire_addr,order_index\n0,10,1,0\n1,20,2,1\n")
        event_result=put_json(root,f"{base}/event-result.json",{"schema_version":1,"candidate_id":cid,"workload_sha256":workload["sha256"],"trace_sha256":trace["sha256"],"test_id":"fixture-common-window","seed":17,"window_start_cycle":0,"window_end_cycle_exclusive":100,"generated":2,"source_overrun":0,"accepted":2,"delivered":2,"duplicate":0,"corrupt":0,"phantom":0,"late_after_drain":0,"errors":0,"retired_events_sha256":retired["sha256"]})
        sim=put(root,f"{base}/sim.log",f"SDF_ANNOTATION_STATUS=PASS\nSDF={sdf['sha256']}\nNETLIST={post['sha256']}\nWAVEFORM={waveform['sha256']}\n")
        bundle=put(root,f"{base}/bundle.tar",f"fixture {cid} bundle\n")
        run_id=f"fixture-{cid}"
        power_binding=put_json(root,f"{base}/power-binding.json",{"schema_version":1,"campaign_id":campaign,"run_id":run_id,"candidate_id":cid,"top":top,"postroute_netlist_sha256":post["sha256"],"sdf_sha256":sdf["sha256"],"spef_sha256":spef["sha256"],"waveform_sha256":waveform["sha256"],"scope_manifest_sha256":scope["sha256"],"coverage_report_sha256":coverage["sha256"],"power_report_sha256":report["sha256"],"tool_manifest_sha256":tool_manifest["sha256"],"pdk_manifest_sha256":pdk_manifest["sha256"],"window_start_cycle":0,"window_end_cycle_exclusive":100})
        candidate={"id":cid,"commit_sha":profile["commit_sha"],"top":top,"source_closure_sha256":closure,"bundle":bundle,"source_inventory":source_inventory,"mapped_netlist":mapped,"postroute_netlist":post,"sdf":sdf,"spef":spef,"simulator_log":sim,"waveform":waveform,"scope_manifest":scope,"coverage_report":coverage,"power_binding":power_binding,"power_script":power_script,"power_log":power_log,"power_report":report,"retired_events":retired,"event_result":event_result}
        inputs={k:candidate[k]["sha256"] for k in ("bundle","source_inventory","mapped_netlist","postroute_netlist","sdf","spef","waveform")}
        outputs={k:candidate[k]["sha256"] for k in ("scope_manifest","coverage_report","power_binding","power_script","power_log","power_report","retired_events","event_result","simulator_log")}
        run={"schema_version":1,"campaign_id":campaign,"run_id":run_id,"candidate_id":cid,"top":top,"commit_sha":profile["commit_sha"],"started_utc":"2026-08-13T00:00:00Z","finished_utc":"2026-08-13T00:01:00Z","exit_code":0,"common_binding_sha256":common_hash,"tool_manifest_sha256":tool_manifest["sha256"],"pdk_manifest_sha256":pdk_manifest["sha256"],"inputs":inputs,"outputs":outputs}
        candidate["run_manifest"]=put_json(root,f"{base}/run.json",run); candidates.append(candidate)
    evidence={"schema_version":1,"evidence_class":"TEST_ONLY_NOT_SERVER_EVIDENCE","campaign_id":campaign,"contract":contract,"tool_manifest":tool_manifest,"pdk_manifest":pdk_manifest,"common":common,"candidates":candidates}
    path=root/"evidence.json"; path.write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n"); return path,evidence

class ProducerTest(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_no_evidence_is_literal_hold_and_closed_schema(self):
        result=producer.produce(None); self.assertEqual(result["status"],"HOLD_NO_REAL_SERVER_ARTIFACTS"); self.assertFalse(result["comparison_ready"]); self.assertFalse(result["candidate_go"])
        schema=json.loads((HERE/"final_activity_power_comparison.schema.json").read_text()); jsonschema.validate(result,schema)
    def test_shared_manifest_and_every_consumer_bind_literal_signature(self):
        contract=json.loads((HERE/"final_endpoint_contract.json").read_text())
        self.assertEqual([x["name"] for x in contract["inputs"]],["ref_clk_i","sample_clk_i","rst_n","source_pending_i"])
        self.assertEqual([x["width"] for x in contract["inputs"]],[1,1,1,16])
        self.assertEqual([x["link_data_width"] for x in contract["candidates"]],[2,5,5])
        plan,_=producer.load_plan(); self.assertFalse(plan["launch_authorized"])
        for item in plan["consumers"]:
            text=(ROOT/item["path"]).read_text(); self.assertIn(producer.CONTRACT_SHA256,text)
            if item["name"] not in {"genus_sdc","qualifier"}:
                row=json.loads(text); self.assertIn("source_pending_i[15:0]",row["input_signature"]); self.assertIn("protocol_error_o",row["output_signature_r1"]); self.assertFalse(row["launch_authorized"])
        sdc=(HERE/"final_activity_power/genus_common.sdc").read_text()
        self.assertNotIn("set_clock_groups -asynchronous",sdc); self.assertIn("-waveform {0.500 1.500}",sdc)
    def test_three_candidate_vcd_derives_only_allowed_power_metrics(self):
        path,evidence=fixture(self.root); result=producer.produce(path); self.assertEqual(result["status"],"TEST_ONLY_COMPLETE"); self.assertEqual([x["candidate_id"] for x in result["rows"]],list(producer.REQUIRED_IDS)); self.assertTrue(all(x["retired_event_count"]==2 for x in result["rows"])); self.assertAlmostEqual(result["rows"][0]["dynamic_power_mw"],.15); self.assertAlmostEqual(result["rows"][0]["energy_pj_per_retired_event"],8.0)
        evidence_schema=json.loads((HERE/"final_activity_power_evidence.schema.json").read_text()); jsonschema.Draft202012Validator.check_schema(evidence_schema); jsonschema.validate(evidence,evidence_schema)
        forbidden={"area_um2","fmax_mhz","vectorless_power_mw"}; self.assertTrue(all(not forbidden & set(x) for x in result["rows"])); self.assertFalse(result["candidate_go"])
        jsonschema.validate(result,json.loads((HERE/"final_activity_power_comparison.schema.json").read_text()))
    def test_saif_is_candidate_specific_and_complete(self):
        path,_=fixture(self.root,"saif"); result=producer.produce(path); self.assertEqual(result["status"],"TEST_ONLY_COMPLETE"); self.assertEqual({x["activity_format"] for x in result["rows"]},{"saif"}); self.assertEqual(len({x["waveform_sha256"] for x in result["rows"]}),3)
    def test_measured_looking_complete_bytes_remain_hard_hold(self):
        path,e=fixture(self.root); e["evidence_class"]="measured_server"; path=save_evidence(self.root,e)
        plan,plan_sha=producer.load_plan()
        for profile,candidate in zip(plan["candidate_profiles"],e["candidates"]): profile["source_closure_sha256"]=candidate["source_closure_sha256"]
        with mock.patch.object(producer,"load_plan",return_value=(plan,plan_sha)):
            result=producer.produce(path)
        self.assertEqual(result["status"],"HOLD_UNAUTHENTICATED_SERVER_EVIDENCE"); self.assertEqual(result["rows"],[]); self.assertFalse(result["comparison_ready"]); self.assertFalse(result["candidate_go"])
        jsonschema.validate(result,json.loads((HERE/"final_activity_power_comparison.schema.json").read_text()))
    def test_exact_roster_and_alias_contract_fail_closed(self):
        path,e=fixture(self.root); bad=copy.deepcopy(e); bad["candidates"].pop(); (self.root/"bad.json").write_text(json.dumps(bad));
        with self.assertRaisesRegex(producer.ProducerError,"roster"): producer.produce(self.root/"bad.json")
        with self.assertRaisesRegex(producer.ProducerError,"forbidden"): producer.netlist_ports(netlist("fovea_a7_final_endpoint",2,True).encode(),"fovea_a7_final_endpoint")
    def test_zero_coverage_vectorless_nan_and_event_mismatch_reject(self):
        path,e=fixture(self.root)
        coverage=(self.root/e["candidates"][0]["coverage_report"]["path"]).read_bytes().replace(b"coverage_percent=100.0",b"coverage_percent=0.0")
        mutated=repin(self.root,copy.deepcopy(e),0,"coverage_report",coverage)
        with self.assertRaisesRegex(producer.ProducerError,"coverage"): producer.produce(mutated)
        report=(self.root/e["candidates"][0]["power_report"]["path"]).read_bytes().replace(b"Imported",b"N.A.")
        with self.assertRaisesRegex(producer.ProducerError,"activity provenance"): producer.parse_power(report,e["candidates"][0]["top"],e["candidates"][0]["waveform"]["path"])
        report2=report.replace(b"N.A.",b"Imported").replace(b"Total Power: 0.16000000",b"Total Power: NaN")
        with self.assertRaises(producer.ProducerError): producer.parse_power(report2,e["candidates"][0]["top"],e["candidates"][0]["waveform"]["path"])
        with self.assertRaisesRegex(producer.ProducerError,"out-of-window"): producer.retired(b"event_id,retire_cycle,retire_addr,order_index\n0,101,1,0\n",0,100,"events")
    def test_cross_candidate_events_stale_run_sdf_and_source_closure_reject(self):
        _,e=fixture(self.root)
        events=b"event_id,retire_cycle,retire_addr,order_index\n0,10,1,0\n1,21,2,1\n"
        mutated=repin(self.root,copy.deepcopy(e),1,"retired_events",events)
        with self.assertRaisesRegex(producer.ProducerError,"event result"): producer.produce(mutated)
        _,e=fixture(self.root)
        run_path=self.root/e["candidates"][0]["run_manifest"]["path"]; run=json.loads(run_path.read_text()); run["exit_code"]=1
        e["candidates"][0]["run_manifest"]=put_json(self.root,e["candidates"][0]["run_manifest"]["path"],run)
        with self.assertRaisesRegex(producer.ProducerError,"run manifest"): producer.produce(save_evidence(self.root,e))
        _,e=fixture(self.root); mutated=repin(self.root,copy.deepcopy(e),2,"sdf",b'(DELAYFILE (DESIGN "wrong_top"))\n')
        with self.assertRaisesRegex(producer.ProducerError,"SDF top"): producer.produce(mutated)
        _,e=fixture(self.root); e["candidates"][0]["source_closure_sha256"]="0"*64
        with self.assertRaisesRegex(producer.ProducerError,"source closure"): producer.produce(save_evidence(self.root,e))
    def test_all_aliases_and_port_width_direction_mutations_reject(self):
        for alias in producer.FORBIDDEN_ALIASES:
            text=netlist("fovea_a7_final_endpoint",2).replace("protocol_error_o);",f"protocol_error_o,{alias});").replace("endmodule",f"  input {alias};\nendmodule")
            with self.subTest(alias=alias), self.assertRaisesRegex(producer.ProducerError,"forbidden"): producer.netlist_ports(text.encode(),"fovea_a7_final_endpoint")
        wrong=netlist("fovea_a7_final_endpoint",5)
        self.assertNotEqual(producer.netlist_ports(wrong.encode(),"fovea_a7_final_endpoint"),producer.expected_ports({"link_data_width":2}))
    def test_missing_artifact_bad_window_and_wrong_format_reject(self):
        _,e=fixture(self.root); e["candidates"][1]["power_report"]["path"]="missing/power.rpt"
        with self.assertRaises(producer.ComparisonError): producer.produce(save_evidence(self.root,e))
        _,e=fixture(self.root); e["common"]["measurement_cycles"]=99
        with self.assertRaisesRegex(producer.ProducerError,"window"): producer.produce(save_evidence(self.root,e))
        _,e=fixture(self.root); e["common"]["activity_format"]="fsdb"
        with self.assertRaisesRegex(producer.ProducerError,"format"): producer.produce(save_evidence(self.root,e))
    def test_cli_output_no_overwrite(self):
        path,_=fixture(self.root); output=self.root/"out.json"; cmd=[sys.executable,str(HERE/"produce_final_activity_power.py"),"--evidence",str(path),"--output",str(output)]
        self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,0); first=output.read_bytes(); again=subprocess.run(cmd,capture_output=True,text=True); self.assertEqual(again.returncode,2); self.assertIn("NOT_PRODUCED",again.stderr); self.assertEqual(output.read_bytes(),first)

    def test_exact_import_path_and_command_not_comments(self):
        _,e=fixture(self.root)
        stale=b"# read_vcd stale/activity.vcd -scope dut -start 0 -end 100\n# report_power\n"
        mutated=repin(self.root,copy.deepcopy(e),0,"power_script",stale)
        with self.assertRaisesRegex(producer.ProducerError,"exact activity path"): producer.produce(mutated)

    def test_waveform_and_retirement_order_are_exact_window(self):
        with self.assertRaisesRegex(producer.ProducerError,"exactly span"):
            producer.validate_exact_waveform_window(b"$enddefinitions $end\n#0\n0!\n#99\n1!\n","vcd",100,1.0)
        with self.assertRaisesRegex(producer.ProducerError,"exactly span"):
            producer.validate_exact_waveform_window(b"$enddefinitions $end\n#0\n0!\n#100\n1!\n#50\n0!\n#100\n1!\n","vcd",100,1.0)
        with self.assertRaisesRegex(producer.ProducerError,"no value changes"):
            producer.validate_exact_waveform_window(b"$enddefinitions $end\n#0\n#100\n","vcd",100,1.0)
        with self.assertRaisesRegex(producer.ProducerError,"positive, unique and ordered"):
            producer.retired(b"event_id,retire_cycle,retire_addr,order_index\n0,20,1,0\n1,10,2,1\n",0,100,"events")

    def test_scope_power_binding_and_event_conservation_mutations_reject(self):
        _,e=fixture(self.root)
        scope_path=self.root/e["candidates"][0]["scope_manifest"]["path"]
        scope=json.loads(scope_path.read_text()); scope["postroute_netlist_sha256"]="0"*64
        mutated=repin(self.root,copy.deepcopy(e),0,"scope_manifest",json.dumps(scope).encode())
        with self.assertRaisesRegex(producer.ProducerError,"selected routed netlist"): producer.produce(mutated)
        _,e=fixture(self.root)
        result_path=self.root/e["candidates"][1]["event_result"]["path"]
        result=json.loads(result_path.read_text()); result["duplicate"]=1
        mutated=repin(self.root,copy.deepcopy(e),1,"event_result",json.dumps(result).encode())
        with self.assertRaisesRegex(producer.ProducerError,"conservation"): producer.produce(mutated)
        _,e=fixture(self.root)
        binding_path=self.root/e["candidates"][2]["power_binding"]["path"]
        binding=json.loads(binding_path.read_text()); binding["spef_sha256"]="0"*64
        mutated=repin(self.root,copy.deepcopy(e),2,"power_binding",json.dumps(binding).encode())
        with self.assertRaisesRegex(producer.ProducerError,"routed power binding"): producer.produce(mutated)

    def test_closed_output_schema_pairs_exact_candidate_identity(self):
        path,_=fixture(self.root); result=producer.produce(path)
        schema=json.loads((HERE/"final_activity_power_comparison.schema.json").read_text())
        duplicate=copy.deepcopy(result); duplicate["rows"][1]=copy.deepcopy(duplicate["rows"][0])
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(duplicate,schema)
        mismatch=copy.deepcopy(result); mismatch["rows"][0]["top"]="a2_p6_final_endpoint"
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(mismatch,schema)
        wrong_status=copy.deepcopy(result); wrong_status["status"]="READY_FOR_W2_EVALUATION"
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(wrong_status,schema)

    def test_qualifier_binds_receipt_and_never_emits_candidate_go(self):
        path,_=fixture(self.root); receipt=producer.produce(path); receipt_path=self.root/"receipt.json"
        receipt_path.write_bytes(producer.canonical_bytes(receipt)); result=qualifier.qualify(receipt_path,path)
        self.assertEqual(result["decision"],"TEST_ONLY_NOT_CANDIDATE_EVIDENCE"); self.assertFalse(result["external_evaluation_ready"]); self.assertFalse(result["candidate_go"])
        with self.assertRaisesRegex(qualifier.QualifierError,"requires its exact evidence"): qualifier.qualify(receipt_path)
        receipt["candidate_go"]=True; receipt_path.write_bytes(producer.canonical_bytes(receipt))
        with self.assertRaises(qualifier.QualifierError): qualifier.qualify(receipt_path)

if __name__=="__main__": unittest.main()
