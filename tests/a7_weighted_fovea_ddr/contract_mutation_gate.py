#!/usr/bin/env python3
"""Require each unsupported/safety contract mutation to fail uniquely."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tests/a7_weighted_fovea_ddr/submission_contract_check.py"
MANIFEST = ROOT / "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_w7.manifest.json"
SDC = ROOT / "constraints/a7_weighted_fovea_ddr_w7.sdc"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutants = []

    def add(name, kind, change):
        value = json.loads(json.dumps(baseline))
        change(value)
        mutants.append((name, kind, value))

    add("claim_backpressure", "BACKPRESSURE", lambda x: x["capabilities"].update(output_backpressure=True))
    add("claim_unrelated_cdc", "CDC", lambda x: x["capabilities"].update(unrelated_clock_cdc=True))
    add("free_output_queue", "QUEUE", lambda x: x["storage"].update(output_queue_depth=1))
    add("midtraffic_reset", "RESET", lambda x: x["reset_contract"].update(mid_traffic_flush_supported=True))
    add("phase_drift", "PHASE", lambda x: x["clock_contract"].update(sample_phase_from_reference_rise_ns=5.0))

    for name, kind, value in mutants:
        path = args.output / f"{name}.json"
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(CHECK), "--manifest", str(path), "--sdc", str(SDC)],
                                cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
        expected = f"A7_W7_CONTRACT_{kind}_CAUGHT:"
        if result.returncode == 0 or result.stdout.count(expected) != 1:
            print(f"A7_W7_CONTRACT_MUTANT_GATE_FAIL name={name} rc={result.returncode} output={result.stdout!r}", file=sys.stderr)
            return 1
        print(f"A7_W7_CONTRACT_MUTANT_EXPECTED_FAIL_PASS name={name} diagnostic={kind}")
    print("A7_W7_FIVE_CONTRACT_MUTANT_GATE_PASS count=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
