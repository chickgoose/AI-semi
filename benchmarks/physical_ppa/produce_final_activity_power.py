#!/usr/bin/env python3
"""Verify and derive the final three-candidate post-route activity comparison."""
from __future__ import annotations

import argparse, csv, hashlib, io, json, math, os, re, stat, sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from evaluate_activity_power_ppa import (
    ArtifactReader, ComparisonError, _scope_bits, _scope_hash,
    _validate_waveform, canonical_bytes, canonical_sha256,
)

HERE = Path(__file__).resolve().parent
DEFAULT_PLAN = HERE / "final_activity_power_plan.json"
CONTRACT_SHA256 = "79d44a39f19ce29ac7437807f94965d70b239030cde2605e46384e212cbf8c43"
REQUIRED_IDS = ("fovea_a7", "a2_p6", "a3_p6")
FORBIDDEN_ALIASES = {"load_i", "pending_i", "source_ready_o", "protocol_fault_o"}
NUMBER = r"([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
# No immutable server execution authority/tool+PDK registry is committed yet.
# There is deliberately no mutable enable switch: measured input remains HOLD.

class ProducerError(ValueError): pass

def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict): raise ProducerError(f"{label} must be an object")
    missing, extra = sorted(keys-set(value)), sorted(set(value)-keys)
    if missing or extra: raise ProducerError(f"{label} keys mismatch: missing={missing}, extra={extra}")
    return value

def json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try: value=json.loads(data.decode())
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise ProducerError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(value,dict): raise ProducerError(f"{label} must contain an object")
    return value

def stable_json(path: Path, label: str) -> tuple[dict[str,Any],bytes]:
    try:
        before=os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: raise ProducerError(f"{label} must be a single-link regular file")
        data=path.read_bytes(); after=os.lstat(path)
    except OSError as exc: raise ProducerError(f"cannot read {label}: {exc}") from exc
    identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
    if identity(before)!=identity(after): raise ProducerError(f"{label} changed while read")
    return json_bytes(data,label),data

def load_plan(path: Path=DEFAULT_PLAN) -> tuple[dict[str,Any],str]:
    if path.resolve()!=DEFAULT_PLAN.resolve(): raise ProducerError("producer plan is repository-fixed and cannot be overridden")
    plan,raw=stable_json(path,"producer plan")
    exact(plan,{"schema_version","producer_id","comparison_id","contract","required_candidates","candidate_profiles","common_policy","consumers","launch_authorized","current_status"},"producer plan")
    if plan["schema_version"]!=1 or plan["producer_id"]!="aer-final-activity-power-v1" or plan["required_candidates"]!=list(REQUIRED_IDS) or plan["launch_authorized"] is not False: raise ProducerError("producer plan identity/roster/launch policy mismatch")
    root=path.resolve().parents[2]; reader=ArtifactReader(root)
    if sha(reader.read(plan["contract"],"plan.contract"))!=CONTRACT_SHA256: raise ProducerError("shared endpoint contract digest mismatch")
    names=[]
    for item in plan["consumers"]:
        row=exact(item,{"name","path","sha256"},"consumer"); names.append(row["name"])
        if CONTRACT_SHA256 not in reader.read({"path":row["path"],"sha256":row["sha256"]},row["name"]).decode(): raise ProducerError(f"{row['name']} is not contract-bound")
    if names != ["techmap_manifest","genus_registry","genus_sdc","innovus_registry","campaign","qualifier"]: raise ProducerError("consumer set/order mismatch")
    return plan,sha(raw)

def expected_ports(profile: dict[str,Any]) -> dict[str,tuple[str,int]]:
    return {"ref_clk_i":("input",1),"sample_clk_i":("input",1),"rst_n":("input",1),"source_pending_i":("input",16),"source_accept_o":("output",16),"link_clk_o":("output",1),"link_data_o":("output",profile["link_data_width"]),"retire_valid_o":("output",2),"retire_addr0_o":("output",4),"retire_addr1_o":("output",4),"drain_idle_o":("output",1),"protocol_error_o":("output",1)}

def netlist_ports(data: bytes, top: str) -> dict[str,tuple[str,int]]:
    try: text=data.decode()
    except UnicodeDecodeError as exc: raise ProducerError("netlist must be UTF-8 Verilog") from exc
    module=re.search(rf"\bmodule\s+{re.escape(top)}\s*\((.*?)\)\s*;(?P<body>.*?)\bendmodule\b",text,re.S)
    if not module: raise ProducerError(f"netlist lacks exact final top {top}")
    if any(re.search(rf"\b{re.escape(a)}\b",module.group(0)) for a in FORBIDDEN_ALIASES): raise ProducerError("final top contains forbidden port alias")
    header={x.strip() for x in module.group(1).split(",") if x.strip()}; ports={}
    for m in re.finditer(r"(?m)^\s*(input|output)\s+(?:wire\s+|logic\s+|reg\s+)?(\[[^]]+\])?\s*([^;]+);",module.group("body")):
        direction,width_text,names=m.groups(); width=1
        if width_text:
            b=re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]",width_text)
            if not b: raise ProducerError("nonconstant port width")
            width=abs(int(b.group(1))-int(b.group(2)))+1
        for name in names.split(","):
            name=name.strip()
            if name in ports: raise ProducerError("repeated port declaration")
            ports[name]=(direction,width)
    if set(ports)!=header: raise ProducerError("top header/declaration port sets differ")
    return ports

def decimal(value: Any,label:str,positive:bool=False)->Decimal:
    try: result=Decimal(str(value))
    except (InvalidOperation,ValueError) as exc: raise ProducerError(f"{label} must be numeric") from exc
    if not result.is_finite() or result<0 or (positive and result<=0): raise ProducerError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return result

def one(pattern:str,text:str,label:str)->str:
    found=re.findall(pattern,text,re.MULTILINE)
    if len(found)!=1: raise ProducerError(f"{label} must occur exactly once")
    return found[0]

def parse_power(data:bytes,top:str,waveform_path:str)->dict[str,Decimal]:
    text=data.decode()
    if one(r"^#\s*Design Stage:\s*(\S+)\s*$",text,"stage")!="PostRoute" or one(r"^\*\s*Design:\s*(\S+)\s*$",text,"design")!=top or one(r"^\*\s*Power Units\s*=\s*(\S+)\s*$",text,"units")!="1mW": raise ProducerError("power report stage/design/units mismatch")
    af=one(r"^\*\s*Activity File:\s*(.+?)\s*$",text,"activity file"); ua=one(r"^\*\s*User-Defined Activity\s*:\s*(.+?)\s*$",text,"user activity")
    if af!=waveform_path or ua!="Imported": raise ProducerError("power report lacks exact activity provenance")
    values={}
    for key,title in (("internal","Total Internal Power"),("switching","Total Switching Power"),("leakage","Total Leakage Power"),("total","Total Power")): values[key]=decimal(one(rf"^{title}:\s*{NUMBER}\s*$",text,title),title)
    dynamic=values["internal"]+values["switching"]
    if abs(values["total"]-dynamic-values["leakage"])>Decimal("0.00000005"): raise ProducerError("power components do not sum")
    return {"dynamic":dynamic,"leakage":values["leakage"],"total":values["total"]}

def parse_coverage(data:bytes)->dict[str,str]:
    lines=data.decode().splitlines()
    if not lines or lines[0]!="ACTIVITY_ANNOTATION_COVERAGE_V1": raise ProducerError("coverage header mismatch")
    out={}
    for line in lines[1:]:
        key,sep,value=line.partition("=")
        if not sep or key in out or not value: raise ProducerError("malformed/repeated coverage field")
        out[key]=value
    keys={"format","waveform_sha256","scope_root","scope_manifest_sha256","window_start_cycle","window_end_cycle_exclusive","annotated_bits","eligible_bits","annotated_objects_sha256","coverage_percent","unresolved_objects"}
    if set(out)!=keys: raise ProducerError("coverage fields not exact")
    return out

def retired(data:bytes,start:int,end:int,label:str)->list[tuple[int,int,int,int]]:
    reader=csv.DictReader(io.StringIO(data.decode()))
    if reader.fieldnames != ["event_id","retire_cycle","retire_addr","order_index"]: raise ProducerError(f"{label} header mismatch")
    rows=[]
    for row in reader:
        try: item=tuple(int(row[k]) for k in reader.fieldnames)
        except (TypeError,ValueError) as exc: raise ProducerError(f"{label} malformed") from exc
        if item[0]<0 or not start<=item[1]<end or not 0<=item[2]<16 or item[3]<0: raise ProducerError(f"{label} invalid/out-of-window event")
        rows.append(item)
    if not rows or len({x[0] for x in rows})!=len(rows) or [x[3] for x in rows]!=list(range(len(rows))) or any(a[1]>b[1] for a,b in zip(rows,rows[1:])): raise ProducerError(f"{label} must be positive, unique and ordered")
    return rows

def validate_exact_waveform_window(data:bytes,fmt:str,cycles:int,period_ns:float)->None:
    text=data.decode(); expected=Decimal(str(period_ns))*cycles
    if fmt=="vcd":
        stamps=[Decimal(x) for x in re.findall(r"(?m)^#(\d+)\s*$",text)]
        if not stamps or stamps[0]!=0 or stamps[-1]!=expected or any(a>b for a,b in zip(stamps,stamps[1:])): raise ProducerError("VCD must be rebased to and exactly span the measurement window")
        body=text.split("$enddefinitions $end",1)
        if len(body)!=2 or not re.search(r"(?m)^[01xXzZbBrR]",body[1]): raise ProducerError("VCD has no value changes")
    else:
        duration=one(r"\(DURATION\s+(\d+)\)",text,"SAIF duration")
        if Decimal(duration)!=expected: raise ProducerError("SAIF duration must exactly span the measurement window")

def validate_platform(reader:ArtifactReader,evidence:dict[str,Any])->None:
    tool=json_bytes(reader.read(evidence["tool_manifest"],"tool manifest"),"tool manifest")
    exact(tool,{"schema_version","campaign_id","tools"},"tool manifest")
    if tool["schema_version"]!=1 or tool["campaign_id"]!=evidence["campaign_id"] or set(tool["tools"])!={"simulator","genus","innovus","power_engine"}: raise ProducerError("tool manifest mismatch")
    for name,item in tool["tools"].items():
        exact(item,{"name","version","executable","version_report"},f"tool {name}")
        reader.read(item["executable"],f"{name} executable")
        if item["version"] not in reader.read(item["version_report"],f"{name} version").decode(): raise ProducerError(f"{name} version mismatch")
    pdk=json_bytes(reader.read(evidence["pdk_manifest"],"PDK manifest"),"PDK manifest")
    exact(pdk,{"schema_version","pdk_id","revision","voltage_v","temperature_c","rc_corner","files"},"PDK manifest")
    if pdk["schema_version"]!=1 or set(pdk["files"])!={"liberty","tech_lef","cell_lef","qrc"}: raise ProducerError("PDK closure mismatch")
    decimal(pdk["voltage_v"],"PDK voltage",True); decimal(pdk["temperature_c"],"PDK temperature")
    for role,ref in pdk["files"].items(): reader.read(ref,f"PDK {role}")

def validate_run(reader:ArtifactReader,evidence:dict[str,Any],candidate:dict[str,Any],raw:dict[str,bytes],common_hash:str)->dict[str,Any]:
    run=json_bytes(raw["run_manifest"],"run manifest")
    exact(run,{"schema_version","campaign_id","run_id","candidate_id","top","commit_sha","started_utc","finished_utc","exit_code","common_binding_sha256","tool_manifest_sha256","pdk_manifest_sha256","inputs","outputs"},"run manifest")
    expected_inputs={k:candidate[k]["sha256"] for k in ("bundle","source_inventory","mapped_netlist","postroute_netlist","sdf","spef","waveform")}
    expected_outputs={k:candidate[k]["sha256"] for k in ("scope_manifest","coverage_report","power_binding","power_script","power_log","power_report","retired_events","event_result","simulator_log")}
    if run["schema_version"]!=1 or run["campaign_id"]!=evidence["campaign_id"] or run["candidate_id"]!=candidate["id"] or run["top"]!=candidate["top"] or run["commit_sha"]!=candidate["commit_sha"] or run["exit_code"]!=0 or run["common_binding_sha256"]!=common_hash or run["tool_manifest_sha256"]!=evidence["tool_manifest"]["sha256"] or run["pdk_manifest_sha256"]!=evidence["pdk_manifest"]["sha256"] or not run["started_utc"]<run["finished_utc"] or run["inputs"]!=expected_inputs or run["outputs"]!=expected_outputs: raise ProducerError("run manifest stale/provenance/artifact closure mismatch")
    return run

def candidate_row(reader:ArtifactReader,evidence:dict[str,Any],common:dict[str,Any],profile:dict[str,Any],candidate:dict[str,Any],common_hash:str)->tuple[dict[str,Any],list[tuple[int,int,int,int]]]:
    artifact_keys={"bundle","source_inventory","mapped_netlist","postroute_netlist","sdf","spef","run_manifest","simulator_log","waveform","scope_manifest","coverage_report","power_binding","power_script","power_log","power_report","retired_events","event_result"}
    exact(candidate,{"id","commit_sha","top","source_closure_sha256"}|artifact_keys,f"candidate {candidate.get('id','?')}")
    if any(candidate[k]!=profile[k] for k in ("id","commit_sha","top")): raise ProducerError("candidate locked identity/top mismatch")
    raw={k:reader.read(candidate[k],f"{candidate['id']}.{k}") for k in artifact_keys}
    inventory=json_bytes(raw["source_inventory"],"source inventory")
    exact(inventory,{"schema_version","candidate_id","commit_sha","top","sources"},"source inventory")
    if (inventory["schema_version"],inventory["candidate_id"],inventory["commit_sha"],inventory["top"])!=(1,candidate["id"],candidate["commit_sha"],candidate["top"]): raise ProducerError("source inventory identity mismatch")
    sources=[]; seen=set()
    for source in inventory["sources"]:
        exact(source,{"path","sha256"},"source")
        if source["path"] in seen: raise ProducerError("duplicate source path")
        seen.add(source["path"]); reader.read(source,f"source {source['path']}"); sources.append(source)
    closure=canonical_sha256({"sources":sorted(sources,key=lambda x:x["path"])})
    if closure!=candidate["source_closure_sha256"] or (evidence["evidence_class"]=="measured_server" and closure!=profile["source_closure_sha256"]): raise ProducerError("source closure mismatch")
    expected=expected_ports(profile)
    if netlist_ports(raw["mapped_netlist"],candidate["top"])!=expected or netlist_ports(raw["postroute_netlist"],candidate["top"])!=expected: raise ProducerError("exact final top signature mismatch")
    if len(re.findall(rf'\(DESIGN\s+"{re.escape(candidate["top"])}"\)',raw["sdf"].decode()))!=1: raise ProducerError("SDF top mismatch")
    scope_bound=json_bytes(raw["scope_manifest"],"scope manifest")
    exact(scope_bound,{"schema_version","scope_root","postroute_netlist_sha256","scope_policy_sha256","objects"},"scope manifest")
    if scope_bound["postroute_netlist_sha256"]!=candidate["postroute_netlist"]["sha256"] or scope_bound["scope_policy_sha256"]!=common["scope_policy"]["sha256"]: raise ProducerError("scope manifest is not bound to selected routed netlist/policy")
    scope={"schema_version":scope_bound["schema_version"],"scope_root":scope_bound["scope_root"],"objects":scope_bound["objects"]}
    scope_hash=_scope_hash(scope,common["scope_root"]); eligible=_scope_bits(scope)
    validate_exact_waveform_window(raw["waveform"],common["activity_format"],common["measurement_cycles"],common["clock_period_ns"])
    observed_hash=_validate_waveform(raw["waveform"],common["activity_format"],common["scope_root"],scope,common["measurement_cycles"],common["clock_period_ns"],eligible)
    cov=parse_coverage(raw["coverage_report"]); percent=decimal(cov["coverage_percent"],"coverage",True)
    if cov["format"]!=common["activity_format"] or cov["waveform_sha256"]!=candidate["waveform"]["sha256"] or cov["scope_root"]!=common["scope_root"] or cov["scope_manifest_sha256"]!=scope_hash or int(cov["window_start_cycle"])!=common["window_start_cycle"] or int(cov["window_end_cycle_exclusive"])!=common["window_end_cycle_exclusive"] or int(cov["annotated_bits"])!=eligible or int(cov["eligible_bits"])!=eligible or cov["annotated_objects_sha256"]!=observed_hash or int(cov["unresolved_objects"])!=0 or percent!=Decimal("100.0"): raise ProducerError("annotation coverage/scope/window mismatch")
    command="read_vcd" if common["activity_format"]=="vcd" else "read_saif"; script=raw["power_script"].decode(); log=raw["power_log"].decode()
    import_line=f"{command} {candidate['waveform']['path']} -scope dut -start 0 -end {common['measurement_cycles']}"
    if script.splitlines().count(import_line)!=1 or script.splitlines().count("report_power")!=1 or log.splitlines().count(import_line)!=1 or log.splitlines().count("report_power")!=1: raise ProducerError("exact activity path/scope/window import and report binding missing")
    if re.search(r"(?mi)(\*\*ERROR|ERROR:|FATAL:)",log) or "Activity File: N.A." in log or "default input activity" in log.lower(): raise ProducerError("power log is failed/vectorless")
    sim=raw["simulator_log"].decode()
    if "SDF_ANNOTATION_STATUS=PASS" not in sim or candidate["sdf"]["sha256"] not in sim or candidate["postroute_netlist"]["sha256"] not in sim or candidate["waveform"]["sha256"] not in sim: raise ProducerError("gate simulation SDF/netlist/waveform binding missing")
    power=parse_power(raw["power_report"],candidate["top"],candidate["waveform"]["path"])
    events=retired(raw["retired_events"],common["window_start_cycle"],common["window_end_cycle_exclusive"],f"{candidate['id']} retired events")
    result=json_bytes(raw["event_result"],"event result")
    exact(result,{"schema_version","candidate_id","workload_sha256","trace_sha256","test_id","seed","window_start_cycle","window_end_cycle_exclusive","generated","source_overrun","accepted","delivered","duplicate","corrupt","phantom","late_after_drain","errors","retired_events_sha256"},"event result")
    if result["schema_version"]!=1 or result["candidate_id"]!=candidate["id"] or result["workload_sha256"]!=common["workload_manifest"]["sha256"] or result["trace_sha256"]!=common["trace"]["sha256"] or result["test_id"]!=common["test_id"] or result["seed"]!=common["seed"] or result["window_start_cycle"]!=common["window_start_cycle"] or result["window_end_cycle_exclusive"]!=common["window_end_cycle_exclusive"] or result["retired_events_sha256"]!=candidate["retired_events"]["sha256"] or result["generated"]!=result["source_overrun"]+result["accepted"] or result["accepted"]!=result["delivered"] or result["delivered"]!=len(events) or any(result[k]!=0 for k in ("duplicate","corrupt","phantom","late_after_drain","errors")): raise ProducerError("exact-window event result/conservation mismatch")
    run=validate_run(reader,evidence,candidate,raw,common_hash)
    binding=json_bytes(raw["power_binding"],"power binding")
    exact(binding,{"schema_version","campaign_id","run_id","candidate_id","top","postroute_netlist_sha256","sdf_sha256","spef_sha256","waveform_sha256","scope_manifest_sha256","coverage_report_sha256","power_report_sha256","tool_manifest_sha256","pdk_manifest_sha256","window_start_cycle","window_end_cycle_exclusive"},"power binding")
    expected_binding={"schema_version":1,"campaign_id":evidence["campaign_id"],"run_id":run["run_id"],"candidate_id":candidate["id"],"top":candidate["top"],"postroute_netlist_sha256":candidate["postroute_netlist"]["sha256"],"sdf_sha256":candidate["sdf"]["sha256"],"spef_sha256":candidate["spef"]["sha256"],"waveform_sha256":candidate["waveform"]["sha256"],"scope_manifest_sha256":candidate["scope_manifest"]["sha256"],"coverage_report_sha256":candidate["coverage_report"]["sha256"],"power_report_sha256":candidate["power_report"]["sha256"],"tool_manifest_sha256":evidence["tool_manifest"]["sha256"],"pdk_manifest_sha256":evidence["pdk_manifest"]["sha256"],"window_start_cycle":common["window_start_cycle"],"window_end_cycle_exclusive":common["window_end_cycle_exclusive"]}
    if binding!=expected_binding: raise ProducerError("selected routed power binding mismatch")
    duration=Decimal(str(common["clock_period_ns"]))*common["measurement_cycles"]
    energy=power["total"]*duration/len(events)
    provenance={k:candidate[k]["sha256"] for k in ("bundle","source_inventory","postroute_netlist","sdf","spef","run_manifest","simulator_log","waveform","scope_manifest","coverage_report","power_binding","power_script","power_log","power_report","retired_events","event_result")}
    return {"candidate_id":candidate["id"],"commit_sha":candidate["commit_sha"],"top":candidate["top"],"activity_format":common["activity_format"],"waveform_sha256":candidate["waveform"]["sha256"],"postroute_netlist_sha256":candidate["postroute_netlist"]["sha256"],"sdf_sha256":candidate["sdf"]["sha256"],"spef_sha256":candidate["spef"]["sha256"],"scope_manifest_sha256":scope_hash,"annotation_coverage_percent":float(percent),"retired_event_count":len(events),"dynamic_power_mw":float(power["dynamic"]),"leakage_power_mw":float(power["leakage"]),"total_power_mw":float(power["total"]),"energy_pj_per_retired_event":float(energy),"provenance_sha256":canonical_sha256(provenance)},events

def produce(evidence_path:Path|None,plan_path:Path=DEFAULT_PLAN)->dict[str,Any]:
    plan,plan_sha=load_plan(plan_path); base={"schema_version":1,"producer_id":plan["producer_id"],"comparison_id":plan["comparison_id"],"plan_sha256":plan_sha,"contract_sha256":CONTRACT_SHA256}
    if evidence_path is None: return {**base,"status":"HOLD_NO_REAL_SERVER_ARTIFACTS","evidence_class":None,"evidence_manifest_sha256":None,"common_binding_sha256":None,"rows":[],"comparison_ready":False,"candidate_go":False,"reason":"no real server activity-power evidence manifest was supplied"}
    evidence,evidence_raw=stable_json(evidence_path,"evidence manifest")
    evidence_sha256=sha(evidence_raw)
    exact(evidence,{"schema_version","evidence_class","campaign_id","contract","tool_manifest","pdk_manifest","common","candidates"},"evidence manifest")
    if evidence["schema_version"]!=1 or evidence["evidence_class"] not in {"measured_server","TEST_ONLY_NOT_SERVER_EVIDENCE"}: raise ProducerError("evidence version/class mismatch")
    reader=ArtifactReader(evidence_path.resolve().parent)
    if sha(reader.read(evidence["contract"],"evidence contract"))!=CONTRACT_SHA256: raise ProducerError("evidence contract mismatch")
    validate_platform(reader,evidence)
    common=exact(evidence["common"],{"workload_manifest","trace","test_id","seed","activity_format","scope_root","scope_policy","window_start_cycle","window_end_cycle_exclusive","measurement_cycles","clock_period_ns"},"common")
    if common["activity_format"] not in {"vcd","saif"} or common["scope_root"]!="dut" or not isinstance(common["seed"],int): raise ProducerError("common format/scope/seed mismatch")
    for key in ("window_start_cycle","window_end_cycle_exclusive","measurement_cycles"):
        if not isinstance(common[key],int) or isinstance(common[key],bool): raise ProducerError("window fields must be integers")
    if common["window_start_cycle"]<0 or common["window_end_cycle_exclusive"]<=common["window_start_cycle"] or common["measurement_cycles"]!=common["window_end_cycle_exclusive"]-common["window_start_cycle"]: raise ProducerError("exact window mismatch")
    decimal(common["clock_period_ns"],"clock period",True); reader.read(common["workload_manifest"],"workload"); reader.read(common["trace"],"trace")
    policy=json_bytes(reader.read(common["scope_policy"],"scope policy"),"scope policy")
    if policy!={"schema_version":1,"policy_id":"postroute-all-eligible-state-and-comb-v1","minimum_annotation_percent":95.0,"required_annotation_percent":100.0,"exclude":["power","ground"]}: raise ProducerError("scope policy mismatch")
    common_hash=canonical_sha256({k:(v["sha256"] if isinstance(v,dict) else v) for k,v in common.items()})
    profiles={x["id"]:x for x in plan["candidate_profiles"]}
    if not isinstance(evidence["candidates"],list) or [x.get("id") for x in evidence["candidates"]]!=list(REQUIRED_IDS): raise ProducerError("exact final candidate roster/order required")
    rows=[]; all_events=[]
    for candidate in evidence["candidates"]:
        row,events=candidate_row(reader,evidence,common,profiles[candidate["id"]],candidate,common_hash); rows.append(row); all_events.append(events)
    # Candidate service/retire cycles and capacity loss may differ. The common
    # workload/trace/window binding is identical; each denominator is derived.
    if evidence["evidence_class"]=="measured_server":
        return {**base,"status":"HOLD_UNAUTHENTICATED_SERVER_EVIDENCE","evidence_class":evidence["evidence_class"],"evidence_manifest_sha256":evidence_sha256,"common_binding_sha256":common_hash,"rows":[],"comparison_ready":False,"candidate_go":False,"reason":"complete bytes were parsed but no immutable server/tool/PDK execution authority is committed"}
    return {**base,"status":"TEST_ONLY_COMPLETE","evidence_class":evidence["evidence_class"],"evidence_manifest_sha256":evidence_sha256,"common_binding_sha256":common_hash,"rows":rows,"comparison_ready":True,"candidate_go":False,"reason":"test-only parser qualification; not server or candidate evidence"}

def write_no_replace(path:Path,data:bytes)->None:
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
        with os.fdopen(fd,"wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError as exc: raise ProducerError(f"cannot create output without overwrite: {exc}") from exc

def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--plan",type=Path,default=DEFAULT_PLAN); parser.add_argument("--evidence",type=Path); parser.add_argument("--output",type=Path); args=parser.parse_args(argv)
    try:
        data=canonical_bytes(produce(args.evidence,args.plan))
        if args.output: write_no_replace(args.output,data)
        else: sys.stdout.buffer.write(data)
    except (ProducerError,ComparisonError,OSError,UnicodeDecodeError,ValueError) as exc:
        print(f"NOT_PRODUCED: {exc}",file=sys.stderr); return 2
    return 0

if __name__=="__main__": raise SystemExit(main())
