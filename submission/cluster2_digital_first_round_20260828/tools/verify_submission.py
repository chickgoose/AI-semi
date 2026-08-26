#!/usr/bin/env python3
"""Fail-closed verifier and checksum writer for the submission directory."""

import argparse
import csv
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PPA = ROOT / "evidence" / "ppa" / "upstream_9b0d951"
RTL_SHA256 = "20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81"
ARCHIVE_SHA256 = "28401809a244571f084d01a2cc950ad381fc393f8b9a747364c45abbb16e8610"
TRACE_SHA256 = "9f682af4eb11239f0743c2f95a82e4302836ac8a02e68278b8b69464beac55c4"
VERIFIER_SHA256 = "da221ad2a3c4aac05c4b3afa97a327b16cfce8ea41b7dd36a89569636d9229e6"
SOURCE_RTL_NATIVE_RUN_COMMIT = "44f8918c6e0085f7b75bb90fbe6c099abe1882cc"
POLARITY_LEDGER_REPRO_COMMIT = "58c132fb475013634ee156eddf5037128c0ce0b3"
POLARITY_LEDGER_RECEIPT_COMMIT = "f2f93a830414aff2e0a3b7db05154294e1d4b78d"
POLARITY_MANIFEST_SHA256 = "df7ecc74be802c55dedb2596ef8dc7063c71f9324d48ab45dfaa360cb87a02fa"
POLARITY_JSONL_SHA256 = "518a2a5ba977516ea687fdc23a9246ff9cfe90fbf3d013efdd358200596e9cd3"
POLARITY_JSONL_GIT_BLOB = "f43e0c41bf2b2ed15e826e191a7aeee3ee33638c"
INTEGRATION_COMMIT = "fbb053f5e6ae3b8178a479a1a50e7bce50eb6b9f"
PPA_COMMIT = "9b0d95121cf88ba55bee13cf0e5d444d688010b6"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"HOLD missing or empty: {path.relative_to(ROOT)}")
    return path


def require_text(path, expected):
    text = require(path).read_text(errors="replace")
    if expected not in text:
        raise SystemExit(f"HOLD expected {expected!r} in {path.relative_to(ROOT)}")


def strict_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"non-finite JSON number {value}")

    try:
        return json.loads(require(path).read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, ValueError) as error:
        raise SystemExit(f"HOLD invalid JSON {path.relative_to(ROOT)}: {error}") from error


def safe_member_name(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def reject_non_regular_paths():
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if path.is_symlink():
            raise SystemExit(f"HOLD symlink is forbidden: {relative}")
        if not path.is_file() and not path.is_dir():
            raise SystemExit(f"HOLD special path is forbidden: {relative}")


def report_number(path, pattern):
    match = re.search(pattern, require(path).read_text(errors="replace"), re.MULTILINE)
    if match is None:
        raise SystemExit(f"HOLD cannot parse metric from {path.relative_to(ROOT)}")
    return match.group(1)


def validate_design_metadata():
    design = strict_json(ROOT / "DESIGN_MANIFEST.json")
    if design.get("schema") != "cluster2-digital-design-manifest-v1":
        raise SystemExit("HOLD design manifest schema mismatch")
    if design.get("rtl_top") != "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity":
        raise SystemExit("HOLD RTL top mismatch")
    expected_rtl = [
        "source/rtl/arbiter2.v",
        "source/rtl/arbiter4_tree.v",
        "source/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
    ]
    if design.get("synthesis_sources") != expected_rtl:
        raise SystemExit("HOLD synthesis source inventory mismatch")
    expected_functional = [
        "source/tb/redred_cluster2_polarity_v1_native_observational_tb.sv",
        "source/tb/run_polarity_v1_native_observational.py",
        "source/tb/polarity_native_ledger.py",
        "source/testdata/uzh_shapes_rotation_patch.addrpol.txt",
    ]
    if design.get("functional_sources") != expected_functional:
        raise SystemExit("HOLD functional source inventory mismatch")
    for path in expected_rtl + expected_functional:
        require(ROOT / path)

    provenance = strict_json(ROOT / "PROVENANCE.json")
    if provenance.get("integration", {}).get("commit") != INTEGRATION_COMMIT:
        raise SystemExit("HOLD integration provenance mismatch")
    if provenance.get("ppa_source", {}).get("commit") != PPA_COMMIT:
        raise SystemExit("HOLD PPA provenance mismatch")
    if provenance.get("hashes", {}).get("rtl_sha256") != RTL_SHA256:
        raise SystemExit("HOLD provenance RTL hash mismatch")
    functional = provenance.get("functional_source", {})
    expected_functional_provenance = {
        "source_rtl_native_run_commit": SOURCE_RTL_NATIVE_RUN_COMMIT,
        "polarity_ledger_repro_commit": POLARITY_LEDGER_REPRO_COMMIT,
        "polarity_ledger_receipt_commit": POLARITY_LEDGER_RECEIPT_COMMIT,
        "native_receipt_commit": "29d785661fec4062930d7bf54ff3fec0d306be60",
        "release_gate_commit": "c3d0a2479bcfd1bc68e942acfc418f023f6d3506",
    }
    if functional != expected_functional_provenance:
        raise SystemExit("HOLD functional provenance role mismatch")
    hashes = provenance.get("hashes", {})
    if hashes.get("polarity_ledger_manifest_sha256") != POLARITY_MANIFEST_SHA256:
        raise SystemExit("HOLD provenance polarity manifest hash mismatch")
    if hashes.get("polarity_ledger_jsonl_sha256") != POLARITY_JSONL_SHA256:
        raise SystemExit("HOLD provenance polarity JSONL hash mismatch")


def validate_polarity_ledger_provenance():
    relative = "evidence/functional/uzh_shapes_rotation_patch.polarity_manifest.json"
    manifest_path = require(ROOT / relative)
    if sha256(manifest_path) != POLARITY_MANIFEST_SHA256:
        raise SystemExit("HOLD bundled polarity manifest SHA-256 mismatch")
    manifest = strict_json(manifest_path)
    if manifest.get("repro", {}).get("git_commit") != POLARITY_LEDGER_REPRO_COMMIT:
        raise SystemExit("HOLD polarity manifest reproduction commit mismatch")
    if (manifest.get("generated"), manifest.get("delivered"), manifest.get("overrun")) != (
        8503, 8503, 0
    ):
        raise SystemExit("HOLD polarity manifest conservation mismatch")
    if manifest.get("sha1", {}).get("jsonl_out") != POLARITY_JSONL_GIT_BLOB:
        raise SystemExit("HOLD polarity manifest JSONL blob mismatch")

    authority = strict_json(
        ROOT / "evidence/functional/ganghee_cluster2_polarity_v1_authority.json"
    )
    if authority.get("schema") != (
        "redred.cluster2_cav_bridge.ganghee_polarity_v1_authority/v2"
    ):
        raise SystemExit("HOLD Ganghee polarity authority schema mismatch")
    if authority.get("git_commit") != SOURCE_RTL_NATIVE_RUN_COMMIT:
        raise SystemExit("HOLD Ganghee source/native-run commit mismatch")
    upstream = authority.get("provenance", {})
    if upstream.get("source_rtl_native_run_commit") != SOURCE_RTL_NATIVE_RUN_COMMIT:
        raise SystemExit("HOLD authority source/native-run role mismatch")
    if upstream.get("polarity_ledger_repro_commit") != POLARITY_LEDGER_REPRO_COMMIT:
        raise SystemExit("HOLD authority ledger reproduction role mismatch")
    if upstream.get("polarity_ledger_receipt_commit") != POLARITY_LEDGER_RECEIPT_COMMIT:
        raise SystemExit("HOLD authority ledger receipt role mismatch")
    evidence = {row.get("role"): row for row in authority.get("evidence_files", [])}
    if evidence.get("v1_polarity_ledger_manifest", {}).get("sha256") != POLARITY_MANIFEST_SHA256:
        raise SystemExit("HOLD authority polarity manifest hash mismatch")
    if evidence.get("v1_polarity_ledger_jsonl", {}).get("sha256") != POLARITY_JSONL_SHA256:
        raise SystemExit("HOLD authority polarity JSONL hash mismatch")


def validate_sweep():
    summary_path = ROOT / "evidence" / "ppa" / "SWEEP_SUMMARY.tsv"
    with require(summary_path).open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    expected = {
        "4.5": ("222.222", "1.349", "0.166", "1265.058", "602", "0.08497222", "PASS"),
        "4.0": ("250.000", "0.849", "0.166", "1265.058", "602", "0.09559035", "PASS"),
        "3.5": ("285.714", "0.454", "0.167", "1254.114", "596", "0.10738887", "PASS"),
        "3.0": ("333.333", "-0.004", "0.169", "1261.980", "599", "0.12577530", "FAIL_SETUP"),
    }
    if len(rows) != len(expected):
        raise SystemExit("HOLD sweep row count mismatch")
    prefix = "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity"
    for row in rows:
        period = row.get("period_ns")
        values = (
            row.get("frequency_mhz"), row.get("setup_slack_ns"),
            row.get("hold_slack_ns"), row.get("pnr_area_raw"),
            row.get("instances"), row.get("power_mw"), row.get("result"),
        )
        if period not in expected or values != expected[period]:
            raise SystemExit(f"HOLD sweep summary mismatch at period {period}")
        stem = PPA / f"{prefix}_{period}"
        raw_setup = report_number(Path(str(stem) + "_setup_timing.rpt"), r"Slack Time\s+(-?\d+\.\d+)")
        raw_hold = report_number(Path(str(stem) + "_hold_timing.rpt"), r"Slack Time\s+(-?\d+\.\d+)")
        area_text = require(Path(str(stem) + "_pnr_area.rpt")).read_text(errors="replace")
        area_match = re.search(rf"^{prefix}\s+(\d+)\s+(\d+\.\d+)\s*$", area_text, re.MULTILINE)
        if area_match is None:
            raise SystemExit(f"HOLD cannot parse P&R area at period {period}")
        raw_power = report_number(Path(str(stem) + "_pnr_power.rpt"), r"Total Power:\s+(\d+\.\d+)")
        if (raw_setup, raw_hold, area_match.group(2), area_match.group(1), raw_power) != (
            values[1], values[2], values[3], values[4], values[5]
        ):
            raise SystemExit(f"HOLD raw PPA report mismatch at period {period}")


def validate_archives():
    evidence = ROOT / "evidence" / "functional" / "polarity_v1_native_observation_44f8918.tgz"
    with tarfile.open(evidence, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if not safe_member_name(member.name) or member.issym() or member.islnk() or member.isdev():
                raise SystemExit(f"HOLD unsafe evidence archive member: {member.name}")
        names = {member.name for member in members}
        required_names = {
            "bridge_snapshot/redred_cluster2_polarity_v1_native_observational_tb.sv",
            "source_snapshot/rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
            "source_snapshot/common_traces_uzh/uzh_shapes_rotation_patch.addrpol.txt",
            "polarity_v1_raw_native_ledger.psv",
            "xrun.log",
        }
        if not required_names.issubset(names):
            raise SystemExit("HOLD native evidence archive inventory mismatch")

    pptx = require(ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx")
    with zipfile.ZipFile(pptx) as archive:
        if archive.testzip() is not None:
            raise SystemExit("HOLD corrupt PPTX member")
        names = archive.namelist()
        if any(not safe_member_name(name) for name in names):
            raise SystemExit("HOLD unsafe PPTX member path")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise SystemExit("HOLD macro-enabled PPTX content is forbidden")
        slides = [name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        if len(slides) != 10:
            raise SystemExit("HOLD PPTX slide-count mismatch")


def validate_payload():
    reject_non_regular_paths()
    validate_design_metadata()
    validate_polarity_ledger_provenance()
    rtl = require(ROOT / "source" / "rtl" / "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v")
    if sha256(rtl) != RTL_SHA256:
        raise SystemExit("HOLD final RTL SHA-256 mismatch")

    archive = require(ROOT / "evidence" / "functional" / "polarity_v1_native_observation_44f8918.tgz")
    if sha256(archive) != ARCHIVE_SHA256:
        raise SystemExit("HOLD native evidence archive SHA-256 mismatch")

    require(ROOT / "source" / "rtl" / "arbiter2.v")
    require(ROOT / "source" / "rtl" / "arbiter4_tree.v")
    require(ROOT / "source" / "rtl" / "polarity_v1_synth.f")
    require(ROOT / "source" / "tb" / "redred_cluster2_polarity_v1_native_observational_tb.sv")
    require(ROOT / "source" / "tb" / "run_polarity_v1_native_observational.py")
    require(ROOT / "source" / "tb" / "polarity_v1_tb.f")
    trace = require(ROOT / "source" / "testdata" / "uzh_shapes_rotation_patch.addrpol.txt")
    verifier = require(ROOT / "source" / "tb" / "polarity_native_ledger.py")
    if sha256(trace) != TRACE_SHA256:
        raise SystemExit("HOLD polarity trace SHA-256 mismatch")
    if sha256(verifier) != VERIFIER_SHA256:
        raise SystemExit("HOLD independent verifier SHA-256 mismatch")
    require(ROOT / "DESIGN_MANIFEST.json")
    require(ROOT / "NOTICE.md")
    require(ROOT / "presentation" / "cluster2_digital_first_round_20260828.pptx")

    require_text(ROOT / "evidence" / "functional" / "polarity_v1_release_receipt.json", '"generated":8503')
    require_text(ROOT / "evidence" / "functional" / "polarity_v1_release_receipt.json", '"delivered":8503')

    prefix = "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity"
    checks = {
        f"{prefix}_3.5_pnr_area.rpt": "1254.114",
        f"{prefix}_3.5_setup_timing.rpt": "Slack Time                    0.454",
        f"{prefix}_3.5_hold_timing.rpt": "Slack Time                    0.167",
        f"{prefix}_3.5_pnr_power.rpt": "Total Power:                 0.10738887",
        f"{prefix}_3.0_setup_timing.rpt": "Slack Time                   -0.004",
        f"{prefix}_3.5_drc.rpt": "No DRC violations were found",
        f"{prefix}_3.5_antenna.rpt": "No Violations Found",
        f"{prefix}_3.5_check_timing.rpt": "ideal_clock_waveform",
        "innovus_3.5.log": "IMPIMEX-7043",
    }
    for name, expected in checks.items():
        require_text(PPA / name, expected)
    validate_sweep()
    validate_archives()


def payload_files():
    excluded = {"SHA256SUMS", "MANIFEST.json"}
    return sorted(
        (path for path in ROOT.rglob("*") if path.is_file() and path.name not in excluded),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def write_manifest():
    files = payload_files()
    entries = [{
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    } for path in files]
    manifest = {
        "schema": "cluster2-digital-first-round-manifest-v1",
        "file_count": len(entries),
        "files": entries,
    }
    (ROOT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "SHA256SUMS").write_text("".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries))


def verify_manifest():
    manifest_path = require(ROOT / "MANIFEST.json")
    sums_path = require(ROOT / "SHA256SUMS")
    manifest = strict_json(manifest_path)
    if manifest.get("schema") != "cluster2-digital-first-round-manifest-v1":
        raise SystemExit("HOLD package manifest schema mismatch")
    current = payload_files()
    recorded = manifest.get("files", [])
    if manifest.get("file_count") != len(recorded) or len(recorded) != len(current):
        raise SystemExit("HOLD manifest file-count mismatch")
    current_names = [path.relative_to(ROOT).as_posix() for path in current]
    recorded_names = [entry["path"] for entry in recorded]
    if len(recorded_names) != len(set(recorded_names)):
        raise SystemExit("HOLD duplicate manifest path")
    for entry in recorded:
        if set(entry) != {"path", "bytes", "sha256"}:
            raise SystemExit("HOLD malformed manifest entry")
        if not safe_member_name(entry["path"]):
            raise SystemExit(f"HOLD unsafe manifest path: {entry['path']}")
        if not isinstance(entry["bytes"], int) or entry["bytes"] <= 0:
            raise SystemExit(f"HOLD invalid manifest byte count: {entry['path']}")
        if not isinstance(entry["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
            raise SystemExit(f"HOLD invalid manifest digest: {entry['path']}")
    if current_names != recorded_names:
        raise SystemExit("HOLD manifest path-set mismatch")
    for path, entry in zip(current, recorded):
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise SystemExit(f"HOLD manifest mismatch: {entry['path']}")
    expected_sums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in recorded)
    if sums_path.read_text() != expected_sums:
        raise SystemExit("HOLD SHA256SUMS does not match MANIFEST.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    validate_payload()
    if args.write_manifest:
        write_manifest()
    verify_manifest()
    print("PASS cluster2 digital first-round submission")


if __name__ == "__main__":
    main()
