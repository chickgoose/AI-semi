#!/usr/bin/env python3
"""Fail-closed qualifier for the final post-route activity-power receipt."""
from __future__ import annotations
import argparse,hashlib,json,os,stat,sys
from pathlib import Path
from typing import Any,Sequence

import validate_full_link_qualification as schema_support
from evaluate_activity_power_ppa import canonical_bytes
import produce_final_activity_power as producer

HERE=Path(__file__).resolve().parent
SCHEMA=HERE/"final_activity_power_comparison.schema.json"
CONTRACT_SHA256="79d44a39f19ce29ac7437807f94965d70b239030cde2605e46384e212cbf8c43"
IDS=("fovea_a7","a2_p6","a3_p6")
class QualifierError(ValueError): pass

def read_regular(path:Path)->bytes:
    try:
        a=os.lstat(path)
        if not stat.S_ISREG(a.st_mode) or a.st_nlink!=1: raise QualifierError("receipt must be a single-link regular file")
        data=path.read_bytes(); b=os.lstat(path)
    except OSError as exc: raise QualifierError(f"cannot read receipt: {exc}") from exc
    if (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)!=(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns): raise QualifierError("receipt changed while read")
    return data

def qualify(path:Path,evidence_path:Path|None=None)->dict[str,Any]:
    raw=read_regular(path)
    try: receipt=json.loads(raw)
    except json.JSONDecodeError as exc: raise QualifierError("receipt is not JSON") from exc
    schema=json.loads(SCHEMA.read_text()); errors=[]
    schema_support._validate_against_schema(receipt,schema,schema,"$",errors)
    if errors: raise QualifierError("\n".join(errors))
    if receipt["contract_sha256"]!=CONTRACT_SHA256 or receipt["candidate_go"] is not False: raise QualifierError("receipt contract/decision firewall mismatch")
    if receipt["evidence_class"] is not None:
        if evidence_path is None: raise QualifierError("evidence-backed receipt requires its exact evidence manifest")
        evidence_raw=read_regular(evidence_path)
        if hashlib.sha256(evidence_raw).hexdigest()!=receipt["evidence_manifest_sha256"]: raise QualifierError("evidence manifest digest mismatch")
        try: reproduced=producer.produce(evidence_path)
        except (producer.ProducerError,producer.ComparisonError,ValueError,OSError) as exc: raise QualifierError(f"producer reproduction failed: {exc}") from exc
        if canonical_bytes(reproduced)!=raw: raise QualifierError("receipt is not the canonical reproduction of its evidence")
    elif evidence_path is not None or receipt["evidence_manifest_sha256"] is not None:
        raise QualifierError("no-evidence HOLD must not bind an evidence manifest")
    status=receipt["status"]
    if status=="TEST_ONLY_COMPLETE": decision="TEST_ONLY_NOT_CANDIDATE_EVIDENCE"
    elif status=="READY_FOR_W2_EVALUATION": decision="READY_FOR_EXTERNAL_W2_EVALUATOR"
    else: decision=status
    ready=status=="READY_FOR_W2_EVALUATION"
    if ready and [row["candidate_id"] for row in receipt["rows"]]!=list(IDS): raise QualifierError("ready receipt roster mismatch")
    return {"schema_version":1,"qualifier_id":"aer-final-activity-power-qualifier-v1","producer_receipt_sha256":hashlib.sha256(raw).hexdigest(),"contract_sha256":CONTRACT_SHA256,"decision":decision,"external_evaluation_ready":ready,"candidate_go":False,"reason":"this qualifier authenticates producer completeness only; it never publishes candidate GO"}

def main(argv:Sequence[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("receipt",type=Path); p.add_argument("--evidence",type=Path); a=p.parse_args(argv)
    try: sys.stdout.buffer.write(canonical_bytes(qualify(a.receipt,a.evidence)))
    except QualifierError as exc: print(f"NOT_QUALIFIED: {exc}",file=sys.stderr); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
