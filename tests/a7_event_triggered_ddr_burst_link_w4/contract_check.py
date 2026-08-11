#!/usr/bin/env python3
import argparse, csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structural-csv", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "constraints/a7_event_triggered_ddr_burst_link_w4.manifest.json").read_text())
    assert manifest["status"] == "physical_hold"
    assert manifest["data_wires"] == 2
    assert manifest["minimum_high_ns"] == manifest["minimum_low_ns"] == 7.0
    assert manifest["technology_boundary"]["module"] == "a7_w4_icg_boundary"
    sdc = (ROOT / "constraints/a7_event_triggered_ddr_burst_link_w4.sdc").read_text()
    for token in ("create_generated_clock", "set_min_pulse_width -high",
                  "set_min_pulse_width -low", "set_clock_uncertainty"):
        assert token in sdc, token
    with args.structural_csv.open(newline="") as stream:
        rows = {row["link"]: row for row in csv.DictReader(stream)}
    assert {name: int(row["physical_pins"]) for name, row in rows.items()} == {
        "parallel4": 5, "ddr2": 3, "serial1": 2}
    assert float(rows["ddr2"]["logical_events_per_link_cycle"]) == 1.0
    assert float(rows["serial1"]["logical_events_per_link_cycle"]) == 0.5
    assert int(rows["parallel4"]["functional_cells"]) < int(rows["ddr2"]["functional_cells"])
    assert int(rows["ddr2"]["functional_cells"]) < int(rows["serial1"]["functional_cells"])
    print("A7_W4_CONTRACT_AND_STRUCTURAL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
