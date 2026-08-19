#!/usr/bin/env python3
"""Export A5 evidence with the owner's registered offer latency preserved.

The scheduler model is the same registered atomic boundary independently
locked to owner RTL by ``run.py``.  The post-scheduler queue mirrors the
separate synthesizable charged adapter in this directory.  Neither layer
substitutes A5's row-wheel policy for the candidate's Ganghee Fovea policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE))
from oracle import AtomicK2Model  # noqa: E402


OWNER_COMMIT = "a57943adba759fc955b4506e99703c1dd9736fba"
LEGACY_EXPORTER_COMMIT = "29a5003bb47c9c502a3bec9a727de2ed14afcfeb"
A5_COMMIT = "41c425bec79aca6c84f5856ca7dee2a4865a6447"
OWNER_RTL = "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv"
OWNER_ORACLE = "rtl/candidates/a3_exact_scalar_prefix_k2/oracle.py"
A5_ORACLE = "tests/a5_k2_common_evaluator/k2_oracle.py"
EXPECTED_OWNER_RTL_SHA256 = "a1898ff0d142584507b7a78571724e8a7fc9a02d64ad1dbf519dde6942cfef22"
EXPECTED_OWNER_ORACLE_SHA256 = "c2c793a284cb6d58507de6e2d62c25ce54d7120bbd6f9ee642bd210528f0ff9c"
EXPECTED_A5_ORACLE_SHA256 = "193a3ac629b4e27418b29af58331b9261922002a74364a892c004340957cc6f8"
ADAPTER = CANDIDATE / "cross_validation/a3_k2_ordered_link_adapter.sv"
REPO = CANDIDATE.parents[2]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=REPO, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def verify_provenance() -> dict[str, str]:
    for commit in (OWNER_COMMIT, LEGACY_EXPORTER_COMMIT, A5_COMMIT):
        resolved = git("rev-parse", f"{commit}^{{commit}}").decode().strip()
        if resolved != commit:
            raise RuntimeError(f"full commit identity mismatch: {commit}")
    owner_rtl = git("show", f"{OWNER_COMMIT}:{OWNER_RTL}")
    owner_oracle = git("show", f"{OWNER_COMMIT}:{OWNER_ORACLE}")
    a5_oracle = git("show", f"{A5_COMMIT}:{A5_ORACLE}")
    hashes = {
        "owner_rtl_sha256": hashlib.sha256(owner_rtl).hexdigest(),
        "owner_oracle_sha256": hashlib.sha256(owner_oracle).hexdigest(),
        "a5_oracle_sha256": hashlib.sha256(a5_oracle).hexdigest(),
    }
    expected = {
        "owner_rtl_sha256": EXPECTED_OWNER_RTL_SHA256,
        "owner_oracle_sha256": EXPECTED_OWNER_ORACLE_SHA256,
        "a5_oracle_sha256": EXPECTED_A5_ORACLE_SHA256,
    }
    if hashes != expected:
        raise RuntimeError(f"pinned oracle/RTL blob mismatch: {hashes}")
    if (CANDIDATE / "rtl/a3_exact_scalar_prefix_k2.sv").read_bytes() != owner_rtl:
        raise RuntimeError("working scheduler RTL differs from owner commit")
    if (CANDIDATE / "oracle.py").read_bytes() != owner_oracle:
        raise RuntimeError("working oracle differs from owner commit")
    if not ADAPTER.is_file():
        raise RuntimeError("charged synthesizable ordered-link adapter is absent")
    return hashes


def output_record(lane: int, item: dict | None) -> dict:
    if item is None:
        return {"lane": lane, "valid": False}
    return {
        "lane": lane,
        "valid": True,
        "source": int(item["source"]),
        "event_id": str(item["event_id"]),
    }


def export_run(vector: dict) -> dict:
    pending: dict[int, dict] = {}
    link: list[dict] = []
    scheduler = AtomicK2Model()
    observations = []

    for stimulus in vector["cycles"]:
        cycle = int(stimulus["cycle"])
        if not stimulus["reset_n"]:
            pending.clear()
            link.clear()
            scheduler.step(rst=True, ready=False, pending=0)
            observations.append({
                "cycle": cycle,
                "accepts": [],
                "outputs": [output_record(0, None), output_record(1, None)],
                "drain_idle": False,
            })
            continue

        # A5 defines occurrence admission before scheduler acceptance at the
        # same indexed edge.  Occupied-source occurrences are left for the A5
        # evaluator to classify as overrun and never replace the old identity.
        for occurrence in stimulus["occurrences"]:
            source = int(occurrence["source"])
            if source not in pending:
                pending[source] = occurrence

        ready0, ready1 = map(bool, stimulus["retire_ready"])
        lane0 = link[0] if link else None
        # Do not expose the younger physical lane until both its own sink and
        # the ordered head are ready.  Thus an independently stalled record
        # remains an internal link entry and can compact to lane 0 after the
        # older record retires, without ever touching scheduler policy.
        lane1 = link[1] if len(link) > 1 and ready0 and ready1 else None
        outputs = [output_record(0, lane0), output_record(1, lane1)]

        # This is the separate ordered buffered-link boundary.  A blocked head
        # hides the younger record.  Partial link drain never touches policy.
        retired = 0
        if link and ready0:
            retired = 1
            if len(link) > 1 and ready1:
                retired = 2
        if retired:
            del link[:retired]

        # Capacity after this edge's ordered retirement drives the owner's one
        # atomic bundle_ready.  AtomicK2Model retains the real registered offer:
        # newly pending work fills an offer at this edge and cannot commit until
        # a later edge.  A blocked offer remains stable in scheduler.grants.
        free_after_retire = 2 - len(link)
        bundle_ready = len(scheduler.grants) <= free_after_retire
        pending_mask = sum(1 << source for source in pending)
        fired = scheduler.step(
            rst=False, ready=bundle_ready, pending=pending_mask
        )
        accepts = []
        if fired:
            for slot, source in enumerate(fired):
                item = pending.pop(source, None)
                if item is None:
                    raise RuntimeError(f"scheduler accepted nonpending source {source}")
                accepts.append({
                    "slot": slot,
                    "source": source,
                    "event_id": item["event_id"],
                })
                link.append(item)

        if len(link) > 2:
            raise RuntimeError("charged ordered link overflow")

        drain_idle = not pending and not link and not any(
            output["valid"] for output in outputs
        ) and not scheduler.grants
        observations.append({
            "cycle": cycle,
            "accepts": accepts,
            "outputs": outputs,
            "drain_idle": drain_idle,
        })

    return {
        "name": vector["name"],
        "run_sha256": vector["run_sha256"],
        "cycles": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-id", default="a3-exact-scalar-prefix-k2")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")

    bundle = json.loads(args.vectors.read_text(encoding="utf-8"))
    if bundle.get("schema") != "a5_k2_vector_bundle_v1":
        raise SystemExit("unexpected A5 vector schema")
    provenance = verify_provenance()
    rtl = CANDIDATE / "rtl/a3_exact_scalar_prefix_k2.sv"
    evidence = {
        "schema": "a5_k2_candidate_evidence_v1",
        "candidate": {
            "id": args.candidate_id,
            "source_sha256": sha256(rtl),
            "binding_sha256": sha256(ADAPTER),
            "runner_sha256": sha256(Path(__file__)),
            "owner_commit": OWNER_COMMIT,
            "owner_oracle_sha256": provenance["owner_oracle_sha256"],
            "a5_commit": A5_COMMIT,
            "a5_oracle_sha256": provenance["a5_oracle_sha256"],
            "adapter_kind": "charged_synthesizable_two_entry_ordered_link",
            "claims": {"full_future_trace_equivalence": False},
        },
        "vector_bundle_sha256": bundle["bundle_sha256"],
        "runs": [export_run(run) for run in bundle["runs"]],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"A3_K2_A5_TRACE_EXPORTED runs={len(evidence['runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
