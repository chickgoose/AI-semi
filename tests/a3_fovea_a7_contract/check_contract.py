#!/usr/bin/env python3
"""Fail-closed static contract check for canonical scalar FOVEA + A7 R1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

FOVEA_SHA256 = "353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e"
A7_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_SOURCES = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv":
        "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv":
        "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv":
        "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv":
        "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv":
        "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv":
        "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
}


class ContractError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def require(pattern: str, text: str, label: str) -> None:
    if re.search(pattern, text, flags=re.S) is None:
        raise ContractError(f"missing contract: {label}")


def check_fovea(data: bytes) -> None:
    if sha(data) != FOVEA_SHA256:
        raise ContractError(f"FOVEA SHA mismatch: {sha(data)}")
    code = compact(data.decode())
    require(r"moduleaer_tx16_trad_rowcol_fovea#\(parameterWEIGHT=5", code,
            "canonical module and WEIGHT=5 default")
    for port in (r"inputclk", r"inputrst", r"input\[15:0\]req",
                 r"outputregvalid", r"outputreg\[3:0\]addr"):
        require(port, code, f"FOVEA port {port}")
    require(r"wireprefer_center=\(round!=WEIGHT\[RW-1:0\]\)", code,
            "weighted round preference")
    require(r"valid<=\|row_gnt;addr<=\{idx4\(row_gnt\),idx4\(col_gnt\)\}", code,
            "registered scalar valid+coordinate address")


def git_blob(repo: Path, path: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{A7_COMMIT}:{path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        raise ContractError(f"cannot read pinned A7 blob {path}: {proc.stderr.decode().strip()}")
    return proc.stdout


def check_a7(repo: Path) -> dict[str, str]:
    resolved = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", A7_COMMIT], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if resolved.returncode or resolved.stdout.strip() != A7_COMMIT:
        raise ContractError("A7 pinned commit is unavailable or resolves differently")
    texts = {}
    hashes = {}
    for path, expected in A7_SOURCES.items():
        blob = git_blob(repo, path)
        actual = sha(blob)
        if actual != expected:
            raise ContractError(f"A7 source SHA mismatch {path}: {actual}")
        texts[path] = compact(blob.decode())
        hashes[path] = actual
    launch = texts[next(p for p in texts if p.endswith("launch_qualifier.sv"))]
    require(r"always_ff@\(posedgeref_clk_iornegedgerst_n\)", launch,
            "active-low asynchronous reset arming register")
    require(r"if\(!rst_n\)reset_release_armed_q<=1'b0;elsereset_release_armed_q<=1'b1;", launch,
            "charged reset-release arming")
    require(r"event_ready_o=rst_n&reset_release_armed_q", launch, "ready after arming")
    require(r"launch_fire_o=event_valid_i&event_ready_o", launch, "ready-valid handshake")
    top = texts[next(p for p in texts if p.endswith("a7_r1_candidate_endpoint.sv"))]
    require(r"event_valid_i", top, "scalar valid input")
    require(r"event_addr_i", top, "scalar address input")
    require(r"retire_addr_o", top, "scalar retire address")
    return hashes


def check_seam(data: bytes) -> None:
    code = compact(data.decode())
    forbidden = (
        r"\bfifo\b", r"\bqueue\b", r"\bmemory\b", r"\bmem\[",
        r"always_ff", r"always_latch", r"event_type", r"polarity",
        r"source_event", r"payload", r"arbiter",
    )
    raw_lower = data.decode().lower()
    for pattern in forbidden:
        if re.search(pattern, raw_lower):
            raise ContractError(f"forbidden seam feature: {pattern}")
    require(r"current_result_mask=fovea_valid\?\(16'b1<<fovea_addr\):16'b0", code,
            "scalar current-result one-hot mask")
    require(r"fovea_req=source_valid_i&~current_result_mask", code,
            "held-request completion masking")
    require(r"source_ready_o=source_valid_i&current_result_mask", code,
            "live-source ACK identity")
    require(r"#\(\.WEIGHT\(5\)\)", code, "explicit FOVEA WEIGHT=5")
    require(r"\.rst\(~rst_n\)", code, "FOVEA active-high reset inversion")
    require(r"\.rst_n\(rst_n\)", code, "A7 active-low reset")
    require(r"\.event_valid_i\(fovea_valid\)", code, "direct scalar valid")
    require(r"\.event_addr_i\(fovea_addr\)", code, "direct address identity into A7")
    require(r"\.retire_addr_o\(retire_addr_o\)", code, "direct retire identity")
    require(r"\.retire_valid_o\(retire_valid_o\)", code, "direct retire valid")


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--fovea", type=Path, required=True)
    parser.add_argument("--a7-repo", type=Path, default=Path("/home/chickgoose/projects/a7"))
    parser.add_argument("--seam", type=Path, default=here / "fovea_a7_zero_state_seam.sv")
    args = parser.parse_args(argv)
    receipt = {"ok": False, "fovea_sha256": None, "a7_commit": A7_COMMIT,
               "a7_source_sha256": {}, "seam_sha256": None, "diagnostic": None}
    try:
        fovea = args.fovea.read_bytes()
        seam = args.seam.read_bytes()
        check_fovea(fovea)
        receipt["fovea_sha256"] = sha(fovea)
        receipt["a7_source_sha256"] = check_a7(args.a7_repo)
        check_seam(seam)
        receipt["seam_sha256"] = sha(seam)
        receipt["ok"] = True
    except (OSError, ContractError) as exc:
        receipt["diagnostic"] = str(exc)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
