import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import parse_ganghee_pnr_archive as parser


GOLDEN_ARCHIVE = Path("/tmp/ganghee-pnr-golden-20260813.tar.gz")
GOLDEN_SHA256 = "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f"


def power_report(activity_file="N.A.", user_activity="N.A."):
    return f"""Power Net Detected:
#################################################################################
# Design Stage: PostRoute
# Design Name: aer_fixture
#################################################################################
* Design: aer_fixture
* User-Defined Activity : {user_activity}
* Activity File: {activity_file}
* Sequential Element Activity: 0.200000
* Primary Input Activity: 0.200000
* Power Units = 1mW
Total Internal Power:        0.03693872
Total Switching Power:       0.01385708
Total Leakage Power:         0.00000659
Total Power:                 0.05080240
Power Unit: W
Subtotal 5.60207e-05
"""


def tar_bytes(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            encoded = data if isinstance(data, bytes) else data.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            archive.addfile(info, io.BytesIO(encoded))
    return stream.getvalue()


def minimal_files(report=None):
    directory = "synth/pnr/resynth_fixture"
    stem = f"{directory}/aer_fixture_2.0"
    return {
        stem + "_pnr_power.rpt": report or power_report(),
        stem + "_setup_timing.rpt": "= Slack Time 0.005\n",
        stem + "_hold_timing.rpt": "Slack Time 0.073\n",
        stem + "_drc.rpt": "No DRC violations were found\n",
        stem + "_antenna.rpt": "No Violations Found\n",
        stem + "_pnr_area.rpt": "aer_fixture 115 318.402\n",
        stem + "_netlist.v": (
            "module aer_fixture(clk, rst, req, valid);\n"
            "input clk, rst;\ninput [15:0] req;\noutput valid;\nendmodule\n"
        ),
        stem + ".sdc": "create_clock -period 2.0 clk\n",
        directory + "/run_2.0.tcl": (
            "report_power > $OUT_DIR/aer_fixture_2.0_pnr_power.rpt\n"
        ),
        directory + "/innovus_2.0.log": (
            "<CMD> report_power\n**ERROR: fixture flow error\n"
        ),
    }


class GangheeArchiveParserTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_archive(self, files):
        data = tar_bytes(files)
        path = self.root / "reports.tar.gz"
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def test_actual_report_shape_is_vectorless_and_never_activity_power(self):
        path, digest = self.write_archive(minimal_files())
        result = parser.summarize(path, digest)
        row = result["power_rows"][0]
        self.assertEqual(row["power_class"], "vectorless_report_power")
        self.assertFalse(row["accepted_for_activity_comparison"])
        self.assertAlmostEqual(row["total_power_mw"], 0.05080240)
        self.assertEqual(row["default_input_activity"], 0.2)
        self.assertEqual(result["publication"]["decision"], "HOLD_NO_ACTIVITY_PROVENANCE")
        self.assertFalse(result["publication"]["candidate_go"])
        self.assertTrue(row["report_power_command_bound"])
        self.assertTrue(row["flow_errors_present"])
        self.assertEqual(result["archive_assessment"]["qualification"], "DIAGNOSTIC_HOLD")
        self.assertAlmostEqual(result["fmax_screening"][0]["points"][0]["area_um2"], 318.402)
        self.assertEqual(result["boundary_diagnostics"][0]["functional_pin_bits"], 17)

    def test_summary_schema_is_machine_readable(self):
        schema = json.loads(
            (ROOT / "ganghee_pnr_archive_summary.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        lock = json.loads(
            (ROOT / "ganghee_pnr_golden_20260813.lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(lock["sha256"], GOLDEN_SHA256)

    def test_activity_filename_without_complete_archive_provenance_is_rejected(self):
        waveform = "waves/activity.vcd"
        files = minimal_files(power_report(waveform, "yes"))
        files[waveform] = "$timescale 1ns $end\n"
        path, digest = self.write_archive(files)
        row = parser.summarize(path, digest)["power_rows"][0]
        self.assertEqual(row["power_class"], "rejected_missing_activity_provenance")
        self.assertFalse(row["accepted_for_activity_comparison"])

    def test_archive_sha_and_member_paths_fail_closed(self):
        path, digest = self.write_archive(minimal_files())
        with self.assertRaisesRegex(parser.ArchiveError, "SHA-256 mismatch"):
            parser.summarize(path, "0" * 64)
        data = tar_bytes({"../escape": "bad"})
        bad = self.root / "bad.tar.gz"
        bad.write_bytes(data)
        with self.assertRaisesRegex(parser.ArchiveError, "unsafe member path"):
            parser.summarize(bad, hashlib.sha256(data).hexdigest())

    @unittest.skipUnless(GOLDEN_ARCHIVE.is_file(), "authoritative server archive absent")
    def test_authoritative_server_archive_classification(self):
        result = parser.summarize(GOLDEN_ARCHIVE, GOLDEN_SHA256)
        self.assertEqual(result["archive"]["size_bytes"], 4626544)
        self.assertEqual(result["archive"]["member_count"], 305)
        self.assertEqual(result["activity_inventory"], {
            "vcd_members": 0,
            "saif_members": 0,
            "activity_based_power_rows": 0,
        })
        self.assertEqual(len(result["power_rows"]), 14)
        self.assertEqual(
            {row["power_class"] for row in result["power_rows"]},
            {"vectorless_report_power"},
        )
        brackets = {
            row["design"]: (row["last_pass_mhz"], row["first_higher_fail_mhz"])
            for row in result["fmax_screening"]
        }
        self.assertEqual(brackets["aer_cluster2_buffered"], (1000.0, 1250.0))
        self.assertAlmostEqual(brackets["aer_fovea_buffered"][0], 1000.0 / 1.4)
        self.assertAlmostEqual(brackets["aer_fovea_buffered"][1], 1000.0 / 1.2)
        self.assertEqual(result["publication"]["decision"], "HOLD_NO_ACTIVITY_PROVENANCE")
        boundaries = {
            row["design"]: row["functional_pin_bits"]
            for row in result["boundary_diagnostics"]
        }
        self.assertEqual(boundaries, {
            "aer_cluster2_buffered": 34,
            "aer_fovea_buffered": 23,
        })


if __name__ == "__main__":
    unittest.main()
