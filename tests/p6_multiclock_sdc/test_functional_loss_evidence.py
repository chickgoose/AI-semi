#!/usr/bin/env python3
"""Fail-closed receipt gate for the yZr1 functional-loss evidence only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tarfile


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "verification" / "p6-functional-loss-evidence.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_metrics(log: str) -> list[dict[str, str]]:
    rows = []
    for line in log.splitlines():
        if not line.startswith("AER_CLEAN_METRICS "):
            continue
        row = dict(token.split("=", 1) for token in line.split()[1:])
        if row["test"] != "basic_reset_drain":
            rows.append(row)
    return rows


def verify_ledger(root: Path, registry: dict) -> None:
    ledger_path = root / registry["ledger"]["path"]
    entries = []
    seen = set()
    prefix = registry["ledger"]["canonical_path_prefix"]
    for line in ledger_path.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
        require(match is not None, f"malformed ledger line: {line!r}")
        expected, canonical = match.groups()
        require(canonical.startswith(prefix),
                f"ledger member escaped canonical attempt: {canonical}")
        relative = canonical[len(prefix):]
        require(relative and relative not in seen, f"duplicate ledger member: {relative}")
        seen.add(relative)
        path = root / relative
        require(path.is_file() and not path.is_symlink(),
                f"ledger member missing or symlinked: {relative}")
        require(path.resolve().is_relative_to(root.resolve()),
                f"ledger member escaped relocated root: {relative}")
        require(sha256(path) == expected, f"ledger SHA mismatch: {relative}")
        entries.append(relative)
    require(len(entries) == registry["ledger"]["entries"] == 338,
            f"ledger closure mismatch: {len(entries)}")


def verify_candidate(root: Path, registry: dict, candidate: str) -> None:
    expected = registry["full50"][candidate]
    log = (root / f"{candidate}-run.log").read_text()
    require(len(re.findall(rf"^RUN_PASS candidate={candidate} ", log, re.MULTILINE)) ==
            expected["run_pass"] == 50, f"{candidate} run-pass count changed")
    require("AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16" in log,
            f"{candidate} reset pass missing")
    require(f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0" in log,
            f"{candidate} completion/pairwise marker missing")
    rows = parse_metrics(log)
    require(len(rows) == 50, f"{candidate} metric-row count changed: {len(rows)}")
    for row in rows:
        require(int(row["errors"]) == 0, f"{candidate} correctness error in {row['test']}")
    observed = {
        "generated": sum(int(row["generated"]) for row in rows),
        "accepted": sum(int(row["accepted"]) for row in rows),
        "delivered": sum(int(row["delivered"]) for row in rows),
        "overrun": sum(int(row["overrun"]) for row in rows),
    }
    for field, value in observed.items():
        require(value == expected[field],
                f"{candidate} {field} changed: {value} != {expected[field]}")
    require(observed["generated"] == observed["accepted"] + observed["overrun"],
            f"{candidate} loss conservation failed")
    require(observed["accepted"] == observed["delivered"],
            f"{candidate} accepted/delivered conservation failed")
    status = root / "results" / candidate / "pairwise-cross-map.status"
    require(status.read_text().strip() == "0", f"{candidate} pairwise status changed")


def main() -> None:
    registry = json.loads(REGISTRY.read_text())
    require(registry["schema"] == "p6-functional-loss-evidence-v1",
            "functional receipt schema changed")
    require(registry["receipt_class"] == "workspace-diff-non-official",
            "workspace-diff receipt was promoted")
    require(registry["allowed_use"] == "functional-loss-only",
            "functional receipt use boundary changed")
    require(set(registry["forbidden_uses"]) == {
        "PPA", "timing qualification", "physical qualification",
        "official common receipt",
    }, "functional receipt qualification boundary changed")
    require(registry["excluded_outer_log"]["name"] == "eval-driver-final.log" and
            "0FfaT8kp" in registry["excluded_outer_log"]["reason"],
            "stale outer-log exclusion changed")
    require("eval-driver-final.log" not in registry["pinned_files"],
            "stale outer log must never be pinned")

    evidence_root = Path(os.environ.get(
        "P6_FUNCTIONAL_LOSS_ROOT", registry["local_relocated_root"]))
    archive = Path(os.environ.get(
        "P6_FUNCTIONAL_LOSS_ARCHIVE", registry["archive"]["local_relocated_path"]))
    require(evidence_root.is_dir(), f"functional evidence root missing: {evidence_root}")
    require(archive.is_file(), f"functional evidence archive missing: {archive}")
    require(sha256(archive) == registry["archive"]["sha256"],
            "functional evidence archive SHA mismatch")
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            require(not member.name.startswith("/") and ".." not in Path(member.name).parts,
                    f"unsafe archive member: {member.name}")
            require(member.isfile() or member.isdir(),
                    f"non-regular archive member: {member.name}")

    for relative, expected in registry["pinned_files"].items():
        path = evidence_root / relative
        require(path.is_file() and sha256(path) == expected,
                f"functional receipt pin mismatch: {relative}")
    provenance = (evidence_root / "provenance.txt").read_text()
    for token in (
        f"snapshot_head={registry['provenance']['snapshot_head']}",
        "binding_reset_quiet_arming_patch=workspace-diff",
        f"attempt={registry['attempt']}",
        f"TOOL:\txrun(64)\t{registry['provenance']['xcelium']}",
    ):
        require(token in provenance, f"provenance token missing: {token}")

    verify_ledger(evidence_root, registry)
    verify_candidate(evidence_root, registry, "fovea")
    verify_candidate(evidence_root, registry, "cluster2")
    print("P6_FUNCTIONAL_LOSS_EVIDENCE_PASS archive_sha=PASS ledger=338/338 "
          "fovea=50/50 cluster2=50/50 scope=LOSS_ONLY receipt=NON_OFFICIAL")


if __name__ == "__main__":
    main()
