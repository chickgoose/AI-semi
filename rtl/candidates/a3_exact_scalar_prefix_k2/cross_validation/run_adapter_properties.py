#!/usr/bin/env python3
"""Fail-closed exhaustive/reference qualification for the charged K2 link."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ordered_link_model import OrderedLinkModel


HERE = Path(__file__).resolve().parent
RTL = HERE / "a3_k2_ordered_link_adapter.sv"
TB = HERE / "adapter_lockstep_tb.sv"
PASS_MARKER = "A3_K2_ORDERED_LINK_LOCKSTEP_PASS"
MISMATCH_MARKER = "ADAPTER_LOCKSTEP_MISMATCH"
MUTATIONS = (
    "A3_K2_LINK_MUT_BYPASS",
    "A3_K2_LINK_MUT_OVERFLOW",
    "A3_K2_LINK_MUT_ORDER",
    "A3_K2_LINK_MUT_REFILL",
    "A3_K2_LINK_MUT_RESET",
)


class PropertyGateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_tool(env_name: str, names: tuple[str, ...],
              fallbacks: tuple[Path, ...]) -> Path:
    override = os.environ.get(env_name)
    if override:
        candidate = Path(override).resolve()
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            raise PropertyGateError(f"{env_name} is not executable: {candidate}")
        return candidate
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for fallback in fallbacks:
        if fallback.is_file() and os.access(fallback, os.X_OK):
            return fallback.resolve()
    raise PropertyGateError(f"required tool missing: {env_name}")


def command(argv: list[str], *, expect_success: bool = True,
            timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(
            argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PropertyGateError(
            f"command could not complete: {' '.join(argv)}: {exc}"
        ) from exc
    if expect_success and process.returncode != 0:
        raise PropertyGateError(
            f"command failed ({process.returncode}): {' '.join(argv)}\n{process.stdout}"
        )
    return process


class VectorCorpus:
    """Cycle vectors derived only from the independent sequence model."""

    def __init__(self) -> None:
        self.model = OrderedLinkModel()
        self.rows: list[str] = []
        self.categories: dict[str, int] = {}

    def drive(self, *, rst: bool = False, offer_count: int = 0,
              offer_addr0: int = 0, offer_addr1: int = 0,
              retire_ready: int = 0) -> None:
        transition = self.model.step(
            rst=rst,
            offer_count=offer_count,
            offer_addr0=offer_addr0,
            offer_addr1=offer_addr1,
            retire_ready=retire_ready,
        )
        output = transition.outputs
        post_count, post_addr0, post_addr1 = self.model.physical_state()
        fields = (
            int(rst), offer_count, offer_addr0, offer_addr1, retire_ready,
            int(output.offer_ready), output.retire_valid,
            output.retire_addr0, output.retire_addr1, int(output.link_empty),
            post_count, post_addr0, post_addr1,
        )
        self.rows.append(" ".join(str(value) for value in fields))

    def seed(self, entries: tuple[int, ...]) -> None:
        self.drive(rst=True)
        if entries:
            padded = entries + (0,) * (2 - len(entries))
            self.drive(
                offer_count=len(entries), offer_addr0=padded[0],
                offer_addr1=padded[1], retire_ready=0,
            )
            if self.model.entries != entries:
                raise PropertyGateError(f"model seed failed: {entries}")

    def count(self, category: str) -> None:
        self.categories[category] = self.categories.get(category, 0) + 1

    def write(self, path: Path) -> None:
        path.write_text(
            f"{len(self.rows)}\n" + "\n".join(self.rows) + "\n",
            encoding="ascii",
        )


def build_vectors(path: Path) -> dict[str, int]:
    corpus = VectorCorpus()
    representatives = {0: (), 1: (3,), 2: (3, 12)}

    # Complete control-state Cartesian product: every occupancy, legal offer
    # count, and independent two-lane ready pattern.
    for occupancy in range(3):
        for offer_count in range(3):
            for ready in range(4):
                corpus.seed(representatives[occupancy])
                corpus.drive(
                    offer_count=offer_count, offer_addr0=5,
                    offer_addr1=10, retire_ready=ready,
                )
                corpus.count("control_cross_product")

    # Every concrete legal stored queue state: empty, all 16 singletons, and
    # all 256 ordered pairs (duplicates included).  Draining makes order and
    # ready-qualified lane-1 presentation externally observable.
    corpus.seed(())
    corpus.count("logical_queue_states")
    for address in range(16):
        corpus.seed((address,))
        corpus.drive(retire_ready=1)
        corpus.count("logical_queue_states")
    for head in range(16):
        for tail in range(16):
            corpus.seed((head, tail))
            corpus.drive(retire_ready=3)
            corpus.count("logical_queue_states")

    # Simultaneous full retirement/refill covers every two-address offer.
    # Head-only retirement/refill and no-retire fill cover every single value.
    for first in range(16):
        for second in range(16):
            for old in ((7,), (3, 12)):
                corpus.seed(old)
                corpus.drive(
                    offer_count=2, offer_addr0=first, offer_addr1=second,
                    retire_ready=1 if len(old) == 1 else 3,
                )
                corpus.count("simultaneous_full_refill")
    for address in range(16):
        corpus.seed((3, 12))
        corpus.drive(offer_count=1, offer_addr0=address, retire_ready=1)
        corpus.count("head_retire_single_refill")
        corpus.seed((3,))
        corpus.drive(offer_count=1, offer_addr0=address, retire_ready=0)
        corpus.count("single_free_slot_fill")

    # Reset dominates every control combination from every occupancy.
    for occupancy in range(3):
        for offer_count in range(3):
            for ready in range(4):
                corpus.seed(representatives[occupancy])
                corpus.drive(
                    rst=True, offer_count=offer_count, offer_addr0=6,
                    offer_addr1=9, retire_ready=ready,
                )
                corpus.count("reset_cross_product")

    # A named stall/head-drain/stall/tail-drain sequence closes reset/drain
    # temporal coverage in addition to the one-edge Cartesian properties.
    corpus.seed((4, 11))
    corpus.drive(retire_ready=2)
    corpus.drive(retire_ready=1)
    corpus.drive(retire_ready=0)
    corpus.drive(retire_ready=3)
    if corpus.model.entries:
        raise PropertyGateError("reference drain sequence did not empty")
    corpus.categories["ordered_reset_drain_sequences"] = 1

    corpus.write(path)
    return {"vectors": len(corpus.rows), **corpus.categories}


def exhaustive_reference_properties() -> dict[str, int]:
    """Check every concrete queue/offer/address/ready transition in Python."""

    queues = [()] + [(value,) for value in range(16)] + [
        (first, second) for first in range(16) for second in range(16)
    ]
    offers = [()] + [(value,) for value in range(16)] + [
        (first, second) for first in range(16) for second in range(16)
    ]
    checked = 0
    rejected_overflow = 0
    simultaneous = 0
    for queue in queues:
        for offered in offers:
            for ready in range(4):
                model = OrderedLinkModel()
                model.entries = queue
                padded = offered + (0,) * (2 - len(offered))
                transition = model.step(
                    rst=False, offer_count=len(offered),
                    offer_addr0=padded[0], offer_addr1=padded[1],
                    retire_ready=ready,
                )
                expected_retire_count = 0
                if queue and ready & 1:
                    expected_retire_count = (
                        2 if len(queue) == 2 and ready & 2 else 1
                    )
                expected_retired = queue[:expected_retire_count]
                remaining = queue[expected_retire_count:]
                free = 2 - len(remaining)
                should_be_ready = len(offered) <= free
                expected_accepted = offered if offered and should_be_ready else ()
                expected_after = remaining + expected_accepted
                expected_valid = int(bool(queue)) | (
                    int(len(queue) == 2 and ready == 3) << 1
                )
                if transition.outputs.retire_valid != expected_valid:
                    raise PropertyGateError("reference violated lane presentation")
                if transition.outputs.offer_ready != should_be_ready:
                    raise PropertyGateError("reference violated capacity ready")
                if transition.retired != expected_retired:
                    raise PropertyGateError("reference violated ordered retirement")
                if transition.accepted != expected_accepted:
                    raise PropertyGateError("reference violated atomic acceptance")
                if transition.after != expected_after:
                    raise PropertyGateError("reference violated ordered prefix/append")
                if len(transition.after) > 2:
                    raise PropertyGateError("reference violated capacity")
                if offered and not should_be_ready:
                    rejected_overflow += 1
                if transition.retired and transition.accepted:
                    simultaneous += 1
                checked += 1
    return {
        "concrete_transition_cases": checked,
        "rejected_overflow_cases": rejected_overflow,
        "simultaneous_retire_refill_cases": simultaneous,
    }


def compile_image(iverilog: Path, output: Path,
                  defines: tuple[str, ...] = ()) -> None:
    argv = [str(iverilog), "-g2012", "-Wall"]
    argv.extend(f"-D{define}" for define in defines)
    argv.extend([
        "-s", "a3_k2_ordered_link_adapter_lockstep_tb",
        "-o", str(output), str(RTL), str(TB),
    ])
    process = command(argv)
    diagnostics = [
        line for line in process.stdout.splitlines()
        if any(token in line.lower() for token in ("warning", "error", "sorry"))
    ]
    if diagnostics:
        raise PropertyGateError(
            "unexpected Icarus compile diagnostic:\n" + "\n".join(diagnostics)
        )


def simulate(vvp: Path, image: Path, vectors: Path,
             *, expect_success: bool) -> subprocess.CompletedProcess[str]:
    return command(
        [str(vvp), str(image), f"+VECTORS={vectors}"],
        expect_success=expect_success,
    )


def execute(output: Path | None = None) -> dict[str, object]:
    iverilog = find_tool(
        "A3_K2_IVERILOG", ("iverilog",),
        (Path("/tmp/a7-toolchain/usr/bin/iverilog"),),
    )
    vvp = find_tool(
        "A3_K2_VVP", ("vvp",), (Path("/tmp/a7-toolchain/usr/bin/vvp"),),
    )
    reference = exhaustive_reference_properties()

    with tempfile.TemporaryDirectory(prefix="a3-k2-adapter-properties-") as temporary:
        work = Path(temporary)
        vectors = work / "adapter.vec"
        coverage = build_vectors(vectors)

        baseline_image = work / "baseline.vvp"
        compile_image(iverilog, baseline_image)
        baseline = simulate(vvp, baseline_image, vectors, expect_success=True)
        if baseline.stdout.count(PASS_MARKER) != 1:
            raise PropertyGateError(f"baseline pass marker mismatch:\n{baseline.stdout}")

        mutation_results: dict[str, dict[str, str]] = {}
        for mutation in MUTATIONS:
            image = work / f"{mutation}.vvp"
            compile_image(iverilog, image, (mutation,))
            result = simulate(vvp, image, vectors, expect_success=False)
            if result.returncode == 0 or MISMATCH_MARKER not in result.stdout:
                raise PropertyGateError(f"RTL mutation escaped: {mutation}\n{result.stdout}")
            if PASS_MARKER in result.stdout:
                raise PropertyGateError(f"mutation emitted false pass: {mutation}")
            mutation_results[mutation] = {
                "status": "EXPECTED_FAIL_CAUGHT",
                "diagnostic": MISMATCH_MARKER,
            }

    receipt: dict[str, object] = {
        "schema": "a3-k2-ordered-link-properties-v1",
        "status": "PASS",
        "rtl": str(RTL.relative_to(HERE.parents[3])),
        "rtl_sha256": sha256(RTL),
        "model_sha256": sha256(HERE / "ordered_link_model.py"),
        "lockstep_tb_sha256": sha256(TB),
        "semantics": {
            "capacity": 2,
            "offers": [0, 1, 2],
            "ready_patterns": [0, 1, 2, 3],
            "retirement": "ordered prefix only; lane 1 requires both ready bits",
            "refill": "atomic offer appends after same-edge ordered retirement",
            "reset": "synchronous reset discards all buffered entries",
        },
        "reference_exhaustion": reference,
        "sv_lockstep": coverage,
        "mutations": mutation_results,
        "tools": {
            "iverilog": command([str(iverilog), "-V"]).stdout.splitlines()[0],
            "vvp": str(vvp),
        },
    }
    if output is not None:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_output, output)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = execute(args.output)
    except PropertyGateError as exc:
        print(f"A3_K2_ORDERED_LINK_PROPERTIES_FAIL: {exc}", file=sys.stderr)
        return 2
    coverage = result["sv_lockstep"]
    assert isinstance(coverage, dict)
    print(
        "A3_K2_ORDERED_LINK_PROPERTIES_PASS "
        f"vectors={coverage['vectors']} mutations={len(result['mutations'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
