#!/usr/bin/env python3
"""Positive, HOLD, and mutation tests for the K2 W2 server preflight."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "physical" / "k2_w2_server_env" / "preflight.py"
RECEIPT_SCRIPT = ROOT / "physical" / "k2_w2_server_env" / "require_go_receipt.py"
MAPPED_SCRIPT = ROOT / "physical" / "k2_w2_server_env" / "verify_mapped_inventory.py"
REPO_CONTRACT = ROOT / "physical" / "k2_w2_server_env" / "contract.json"
REPO_RESULT = ROOT / "physical" / "k2_w2_server_env" / "canonical_campaign_env.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, data: str | bytes, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode() if isinstance(data, str) else data
    path.write_bytes(payload)
    if executable:
        path.chmod(0o755)


def make_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as stream:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mtime = 0
            info.mode = 0o444
            stream.addfile(info, io.BytesIO(members[name]))


SLOW = """library (slow) {
  nom_process : 1.0;
  nom_voltage : 0.9;
  nom_temperature : 125.0;
  cell (TLATNTSCAX2) {
    clock_gating_integrated_cell : latch_posedge_precontrol;
    pin (E) { direction : input; }
    pin (SE) { direction : input; }
    pin (CK) { direction : input; }
    pin (ECK) { direction : output; }
  }
  cell (MX2X1) {
    pin (A) { direction : input; }
    pin (B) { direction : input; }
    pin (S0) { direction : input; }
    pin (Y) { direction : output; function : \"A?B:C\"; }
  }
  cell (DFFRHQX1) {
    ff (IQ, IQN) { clocked_on : \"CK\"; next_state : \"D\"; clear : \"!RN\"; }
    pin (RN) { direction : input;
      timing () { related_pin : \"CK\"; timing_type : recovery_rising; }
      timing () { related_pin : \"CK\"; timing_type : removal_rising; }
    }
    pin (CK) { direction : input; }
    pin (D) { direction : input;
      timing () { related_pin : \"CK\"; timing_type : setup_rising; }
      timing () { related_pin : \"CK\"; timing_type : hold_rising; }
    }
    pin (Q) { direction : output; }
  }
  cell (DFFNSRX1) {
    ff (IQ, IQN) { clocked_on : \"(!CKN)\"; next_state : \"D\";
      clear : \"(!RN)\"; preset : \"(!SN)\"; }
    pin (Q) { direction : output; }
    pin (QN) { direction : output; }
    pin (CKN) { direction : input; }
    pin (D) { direction : input;
      timing () { related_pin : \"CKN\"; timing_type : setup_falling; }
      timing () { related_pin : \"CKN\"; timing_type : hold_falling; }
    }
    pin (SN) { direction : input;
      timing () { related_pin : \"CKN\"; timing_type : recovery_falling; }
      timing () { related_pin : \"CKN\"; timing_type : removal_falling; }
    }
    pin (RN) { direction : input;
      timing () { related_pin : \"CKN\"; timing_type : recovery_falling; }
      timing () { related_pin : \"CKN\"; timing_type : removal_falling; }
    }
  }
}
"""

FAST = SLOW.replace("library (slow)", "library (fast)").replace(
    "nom_voltage : 0.9", "nom_voltage : 1.1").replace(
    "nom_temperature : 125.0", "nom_temperature : 0.0")

TECH_LEF = """VERSION 5.8 ;
SITE CoreSite
  CLASS CORE ;
  SIZE 0.2 BY 1.4 ;
END CoreSite
END LIBRARY
"""


def macro(name: str, pins: dict[str, str], site: str = "CoreSite") -> str:
    pin_text = "".join(
        f"  PIN {pin}\n    DIRECTION {direction} ;\n  END {pin}\n"
        for pin, direction in pins.items())
    return (f"MACRO {name}\n  CLASS CORE ;\n  SITE {site} ;\n"
            f"{pin_text}END {name}\n")


MACRO_LEF = "VERSION 5.8 ;\n" + "".join((
    macro("TLATNTSCAX2", {"E": "INPUT", "SE": "INPUT", "CK": "INPUT",
                           "ECK": "OUTPUT", "VDD": "INOUT", "VSS": "INOUT"}),
    macro("MX2X1", {"A": "INPUT", "B": "INPUT", "S0": "INPUT",
                     "Y": "OUTPUT", "VDD": "INOUT", "VSS": "INOUT"}),
    macro("DFFRHQX1", {"RN": "INPUT", "CK": "INPUT", "D": "INPUT",
                        "Q": "OUTPUT", "VDD": "INOUT", "VSS": "INOUT"}),
    macro("DFFNSRX1", {"Q": "OUTPUT", "QN": "OUTPUT", "CKN": "INPUT",
                        "D": "INPUT", "SN": "INPUT", "RN": "INPUT",
                        "VDD": "INOUT", "VSS": "INOUT"}),
)) + "END LIBRARY\n"

MAPPED_NETLIST = """
module mapped(input link_clock, rst_n, e, a, b, s, d, output eck, y, q0, q1);
  TLATNTSCAX2 u_icg(.E(e), .SE(1'b0), .CK(link_clock), .ECK(eck));
  MX2X1 u_mux(.A(a), .B(b), .S0(s), .Y(y));
  DFFRHQX1 u_pos(.RN(rst_n), .CK(link_clock), .D(d), .Q(q0));
  DFFNSRX1 u_neg(.RN(rst_n), .SN(1'b1), .CKN(link_clock), .D(d), .Q(q1), .QN());
endmodule
"""


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.pdk = root / "pdk"
        self.raw_archive = root / "raw.tar.gz"
        self.buffered_archive = root / "buffered.tar.gz"
        self.genus = root / "bin" / "genus"
        self.innovus = root / "bin" / "innovus"
        self.xrun = root / "bin" / "xrun"
        self.contract = root / "contract.json"
        self.output = root / "result.json"
        self.raw_members = {
            "synth/pnr/resynth_fovea_raw/genus_1.2.log": (
                b"Version: 23.14-s090_1\nPVT values (1.000000, 0.900000, 125.000000)\n"),
            "synth/pnr/resynth_fovea_raw/innovus_1.2.log": (
                b"Version:\tv23.14-s088_1\n/tools/cadence/DDI231/INNOVUS231/bin/innovus_\n"
                b"gsclib045_tech.lef gsclib045_macro.lef\n"),
            "synth/pnr/resynth_fovea_raw/mmmc_1.2.tcl":
                b"create_rc_corner -qrc_tech /pdk/qrc/qx/gpdk045.tch\n",
            "synth/pnr/resynth_fovea_raw/aer_tx16_trad_rowcol_fovea_1.2_netlist.v":
                b"TLATNTSCAX2 u_icg(.E(e),.SE(1'b0),.CK(clk),.ECK(gclk));\n",
            "synth/pnr/resynth_cluster2_raw/innovus_0.7.log":
                b"Clock gates (with test): TLATNTSCAX20 TLATNTSCAX2\n",
        }
        self.buffered_members = {
            "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0_netlist.v":
                b"TLATNTSCAX2 u_icg(); MX2X1 u_mux(); DFFRHQX1 u_ff();\n",
            "synth/pnr/resynth_cluster2_buffered/innovus_1.0.log":
                b"site name: CoreSite, cell type: MX2X1\n",
        }
        self._write_all()

    def _write_all(self) -> None:
        write(self.pdk / "timing/slow_vdd1v0_basicCells.lib", SLOW)
        write(self.pdk / "timing/fast_vdd1v0_basicCells.lib", FAST)
        write(self.pdk / "lef/gsclib045_tech.lef", TECH_LEF)
        write(self.pdk / "lef/gsclib045_macro.lef", MACRO_LEF)
        write(self.pdk / "qrc/qx/gpdk045.tch", "QRC TYPICAL\n")
        write(self.genus, "#!/bin/sh\necho 'Genus 23.14-s090_1'\necho 'Build expiration notice'\n", executable=True)
        write(self.innovus, "#!/bin/sh\necho 'Innovus 23.14-s088_1'\n", executable=True)
        write(self.xrun, "#!/bin/sh\necho 'Xcelium 23.09-s013'\n", executable=True)
        make_tar(self.raw_archive, self.raw_members)
        make_tar(self.buffered_archive, self.buffered_members)
        self.refresh_contract()

    def refresh_contract(self) -> None:
        file_hash = lambda path: sha(path.read_bytes())
        document = json.loads(REPO_CONTRACT.read_text())
        document["server_pdk_root"] = str(self.pdk)
        tool_paths = {"genus": self.genus, "innovus": self.innovus,
                      "xrun": self.xrun}
        for name, path in tool_paths.items():
            document["tools"][name]["observed_path"] = str(path)
            document["tools"][name]["sha256"] = file_hash(path)
            document["direct_server_observation"]["tool_paths"][name] = str(path)
            document["direct_server_observation"]["tool_sha256"][name] = file_hash(path)
        technology_paths = {
            "setup_liberty": self.pdk / "timing/slow_vdd1v0_basicCells.lib",
            "hold_liberty": self.pdk / "timing/fast_vdd1v0_basicCells.lib",
            "tech_lef": self.pdk / "lef/gsclib045_tech.lef",
            "macro_lef": self.pdk / "lef/gsclib045_macro.lef",
            "setup_qrc": self.pdk / "qrc/qx/gpdk045.tch",
            "hold_qrc": self.pdk / "qrc/qx/gpdk045.tch",
        }
        for role, path in technology_paths.items():
            document["technology"][role]["sha256"] = file_hash(path)
            document["direct_server_observation"]["technology_sha256"][role] = file_hash(path)
        document["source_archives"] = {
            "raw_core": {"default_path": str(self.raw_archive),
                         "sha256": file_hash(self.raw_archive)},
            "buffered_extension": {"default_path": str(self.buffered_archive),
                                   "sha256": file_hash(self.buffered_archive)},
        }
        document["golden_anchors"] = {
            "raw_core": {name: sha(data) for name, data in self.raw_members.items()},
            "buffered_extension": {
                name: sha(data) for name, data in self.buffered_members.items()},
        }
        write(self.contract, json.dumps(document, indent=2, sort_keys=True) + "\n")

    def run(self, server: bool = True, allow_hold: bool = False) -> subprocess.CompletedProcess:
        command = [sys.executable, str(SCRIPT), "--contract", str(self.contract),
                   "--raw-archive", str(self.raw_archive), "--buffered-archive",
                   str(self.buffered_archive), "--output", str(self.output)]
        if server:
            command += ["--pdk-root", str(self.pdk), "--genus", str(self.genus),
                        "--innovus", str(self.innovus), "--xrun", str(self.xrun)]
        if allow_hold:
            command.append("--allow-hold")
        return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)

    def result(self) -> dict:
        return json.loads(self.output.read_text())


class PreflightTests(unittest.TestCase):
    def with_fixture(self) -> tuple[tempfile.TemporaryDirectory, Fixture]:
        temporary = tempfile.TemporaryDirectory()
        return temporary, Fixture(Path(temporary.name))

    def assert_fail(self, fixture: Fixture) -> None:
        run = fixture.run()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(fixture.result()["qualification_status"], "FAIL")
        self.assertFalse(fixture.result()["campaign_launch_allowed"])

    def run_mapped(self, fixture: Fixture, netlist_text: str,
                   environment_receipt: Path | None = None) -> subprocess.CompletedProcess:
        netlist = fixture.root / "mapped.v"
        write(netlist, netlist_text)
        output = fixture.root / f"mapped-receipt-{sha(netlist.read_bytes())}.json"
        command = [
            sys.executable, str(MAPPED_SCRIPT), "--contract", str(fixture.contract),
            "--environment-receipt", str(environment_receipt or fixture.output),
            "--mapped-netlist", str(netlist), "--expected-netlist-sha256",
            sha(netlist.read_bytes()), "--output", str(output),
        ]
        return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)

    def test_repository_local_hold_receipt_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "canonical.json"
            run = subprocess.run([
                sys.executable, str(SCRIPT), "--contract", str(REPO_CONTRACT),
                "--allow-hold", "--output", str(output),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(output.read_bytes(), REPO_RESULT.read_bytes())
            result = json.loads(output.read_text())
            self.assertEqual(result["qualification_status"], "HOLD")
            self.assertFalse(result["campaign_launch_allowed"])

    def test_repository_direct_server_observation_exact(self) -> None:
        contract = json.loads(REPO_CONTRACT.read_text())
        observation = contract["direct_server_observation"]
        technology = contract["technology"]
        self.assertEqual(technology["setup_liberty"]["pvt"], [1.0, 0.9, 125.0])
        self.assertEqual(technology["hold_liberty"]["pvt"], [1.0, 1.1, 0.0])
        self.assertEqual(technology["required_cells"], {
            "icg": "TLATNTSCAX2", "mux": "MX2X1",
            "posedge_ff": "DFFRHQX1", "negedge_ff": "DFFNSRX1",
        })
        self.assertEqual(technology["cell_contracts"]["TLATNTSCAX2"]
                         ["liberty_pins"], {
            "E": "input", "SE": "input", "CK": "input", "ECK": "output",
        })
        self.assertEqual(technology["cell_contracts"]["DFFNSRX1"]["ff"], {
            "clocked_on": "!CKN", "clear": "!RN", "preset": "!SN",
        })
        self.assertEqual(observation["technology_sha256"], {
            "setup_liberty": "dec616b7b53aa5166eac9660ba83561a4057ee3b7e62f59f3d4bebad495ffe10",
            "hold_liberty": "e63762d156fd929cde2f58b0a5883020d6f16f0a41d3736577d0af6b94191560",
            "tech_lef": "0310f32fe4fb5009053dcfe36ece6e8d7a1f8e8d6e58a0b6fdd2109c2c919f70",
            "macro_lef": "7bb39c7adef5704aa10d886f9cc404b06d4f486219ffb4a6a8bbb31f965d52b2",
            "setup_qrc": "a089c567928e3c8653408ebc503cb4e8270732c5f23e6cb23498d51cd6c75bd5",
            "hold_qrc": "a089c567928e3c8653408ebc503cb4e8270732c5f23e6cb23498d51cd6c75bd5",
        })
        self.assertEqual(observation["tool_paths"], {
            "genus": "/tools/cadence/DDI231/GENUS231/bin/.cdnWrapperIndep",
            "innovus": "/tools/cadence/DDI231/INNOVUS231/bin/.cdnWrapperIndep",
            "xrun": "/tools/cadence/XCELIUMMAIN2309/tools.lnx86/inca/bin/64bit/xrun",
        })
        for role, expected in observation["technology_sha256"].items():
            self.assertEqual(contract["technology"][role]["sha256"], expected)
        for name, expected in observation["tool_paths"].items():
            self.assertEqual(contract["tools"][name]["observed_path"], expected)
        self.assertEqual(observation["tool_sha256"], {
            "genus": "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa",
            "innovus": "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa",
            "xrun": "b797ff6331f16102dfa453abf88761235f4d6bb75885b7b5e15b2e6f5bc7a5d7",
        })
        self.assertEqual(observation["tool_versions"], {
            "genus": "23.14-s090_1", "innovus": "23.14-s088_1",
            "xrun": "23.09-s013",
        })
        for name, expected in observation["tool_sha256"].items():
            self.assertEqual(contract["tools"][name]["sha256"], expected)

    def test_pass_and_canonical_reproducibility(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            run = fixture.run()
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            first = fixture.output.read_bytes()
            result = fixture.result()
            self.assertEqual(result["qualification_status"], "PROVEN_ENVIRONMENT")
            self.assertTrue(result["campaign_launch_allowed"])
            self.assertEqual(result["gates"]["rc_policy"]["status"],
                             "PROVEN_WITH_LIMITATION")
            genus = result["gates"]["tool_executables"]["evidence"]["genus"]
            self.assertEqual(genus["parsed_version"], "23.14-s090_1")
            self.assertEqual(genus["warnings"][0]["code"],
                             "TOOL_BANNER_EXPIRATION")
            self.assertEqual(result["receipt"]["evidence_status"],
                             "PROVEN_SERVER_ENV")
            self.assertRegex(result["receipt_sha256"], r"^[0-9a-f]{64}$")
            verify = subprocess.run([
                sys.executable, str(RECEIPT_SCRIPT), "--contract",
                str(fixture.contract), "--receipt", str(fixture.output),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            second_output = fixture.root / "second.json"
            fixture.output = second_output
            self.assertEqual(fixture.run().returncode, 0)
            self.assertEqual(first, second_output.read_bytes())

    def test_local_golden_only_is_hold_and_strict_nonzero(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertNotEqual(fixture.run(server=False).returncode, 0)
            self.assertEqual(fixture.result()["qualification_status"], "HOLD")
            self.assertEqual(fixture.run(server=False, allow_hold=True).returncode, 0)

    def test_missing_file(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            (fixture.pdk / "timing/fast_vdd1v0_basicCells.lib").unlink()
            self.assert_fail(fixture)

    def test_symlink_file(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            target = fixture.pdk / "lef/gsclib045_macro.lef"
            payload = target.read_bytes()
            target.unlink()
            alternate = fixture.root / "macro.lef"
            alternate.write_bytes(payload)
            target.symlink_to(alternate)
            self.assert_fail(fixture)

    def test_hash_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            with (fixture.pdk / "qrc/qx/gpdk045.tch").open("ab") as stream:
                stream.write(b"mutation")
            self.assert_fail(fixture)

    def test_pdk_root_path_substitution(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["server_pdk_root"] = str(fixture.root / "different-pdk")
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_tool_version_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.genus, "#!/bin/sh\necho 'Genus 99.0'\n", executable=True)
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_xrun_version_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.xrun, "#!/bin/sh\necho 'Xcelium 99.0'\n", executable=True)
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_tool_version_invocation_nonzero(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.genus,
                  "#!/bin/sh\necho 'Genus 23.14-s090_1'\n"
                  "echo 'Build expiration notice'\nexit 7\n", executable=True)
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_ambiguous_parsed_version_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.xrun,
                  "#!/bin/sh\necho 'Xcelium 23.09-s013 and 99.99-s999'\n",
                  executable=True)
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_tool_observed_path_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["tools"]["innovus"]["observed_path"] = str(fixture.root / "wrong")
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_direct_technology_observation_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["direct_server_observation"]["technology_sha256"][
                "macro_lef"] = "0" * 64
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_unpinned_tool_hash(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["tools"]["genus"]["sha256"] = None
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_tool_symlink(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            payload = fixture.innovus.read_bytes()
            fixture.innovus.unlink()
            alternate = fixture.root / "innovus.real"
            alternate.write_bytes(payload)
            alternate.chmod(0o755)
            fixture.innovus.symlink_to(alternate)
            self.assert_fail(fixture)

    def test_pvt_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            path = fixture.pdk / "timing/slow_vdd1v0_basicCells.lib"
            write(path, SLOW.replace("nom_voltage : 0.9", "nom_voltage : 0.8"))
            contract = json.loads(fixture.contract.read_text())
            contract["technology"]["setup_liberty"]["sha256"] = sha(path.read_bytes())
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_unpinned_fast_pvt(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["technology"]["hold_liberty"]["pvt"] = None
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_wrong_fast_pvt_contract(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["technology"]["hold_liberty"]["pvt"] = [1.0, 1.1, -40.0]
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def semantic_mutation(self, old: str, new: str = "REMOVED") -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            for name in ("slow_vdd1v0_basicCells.lib", "fast_vdd1v0_basicCells.lib"):
                path = fixture.pdk / "timing" / name
                write(path, path.read_text().replace(old, new))
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_missing_icg(self) -> None:
        self.semantic_mutation("TLATNTSCAX2")

    def test_missing_mux(self) -> None:
        self.semantic_mutation("MX2X1")

    def test_missing_posedge_ff(self) -> None:
        self.semantic_mutation('clocked_on : "CK"', 'clocked_on : "!CK"')

    def test_missing_negedge_ff(self) -> None:
        self.semantic_mutation('clocked_on : "(!CKN)"', 'clocked_on : "CKN"')

    def test_missing_icg_se_pin(self) -> None:
        self.semantic_mutation("pin (SE)", "pin (BAD_SE)")

    def test_missing_negedge_recovery_arc(self) -> None:
        self.semantic_mutation("recovery_falling", "recovery_rising")

    def test_wrong_icg_class(self) -> None:
        self.semantic_mutation("latch_posedge_precontrol", "latch_posedge")

    def test_missing_lef_negedge_reset_pin(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            path = fixture.pdk / "lef/gsclib045_macro.lef"
            write(path, path.read_text().replace(
                "  PIN SN\n    DIRECTION INPUT ;\n  END SN\n", ""))
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_missing_go_receipt_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            run = subprocess.run([
                sys.executable, str(RECEIPT_SCRIPT), "--contract",
                str(fixture.contract), "--receipt", str(fixture.root / "missing.json"),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False)
            self.assertNotEqual(run.returncode, 0)

    def test_hold_receipt_rejected_for_campaign_launch(self) -> None:
        run = subprocess.run([
            sys.executable, str(RECEIPT_SCRIPT), "--contract", str(REPO_CONTRACT),
            "--receipt", str(REPO_RESULT),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("not a PROVEN_SERVER_ENV GO", run.stderr)

    def test_tampered_go_receipt_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            receipt = json.loads(fixture.output.read_text())
            receipt["gates"]["technology_files"]["status"] = "FAKE"
            write(fixture.output, json.dumps(receipt))
            run = subprocess.run([
                sys.executable, str(RECEIPT_SCRIPT), "--contract",
                str(fixture.contract), "--receipt", str(fixture.output),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False)
            self.assertNotEqual(run.returncode, 0)

    def test_go_receipt_output_is_immutable(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            original = fixture.output.read_bytes()
            second = fixture.run()
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(fixture.output.read_bytes(), original)

    def test_mapped_inventory_exact_fixture_passes(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            run = self.run_mapped(fixture, MAPPED_NETLIST)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(next(fixture.root.glob("mapped-receipt-*.json")).read_text())
            self.assertEqual(result["inventory"]["cell_counts"], {
                "TLATNTSCAX2": 1, "MX2X1": 1,
                "DFFRHQX1": 1, "DFFNSRX1": 1,
            })

    def test_mapped_inventory_requires_environment_receipt(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            run = self.run_mapped(
                fixture, MAPPED_NETLIST, fixture.root / "missing-environment.json")
            self.assertNotEqual(run.returncode, 0)

    def test_mapped_inventory_forbidden_cells_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            for forbidden in ("TLATXL", "TLATNCAX2", "SDFFX1"):
                with self.subTest(cell=forbidden):
                    run = self.run_mapped(
                        fixture, MAPPED_NETLIST + f"\n{forbidden} u_bad();\n")
                    self.assertNotEqual(run.returncode, 0)

    def test_mapped_inventory_wrong_negedge_binding_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            run = self.run_mapped(
                fixture, MAPPED_NETLIST.replace(".SN(1'b1)", ".SN(1'b0)"))
            self.assertNotEqual(run.returncode, 0)

    def test_mapped_inventory_wrong_hash_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            netlist = fixture.root / "mapped.v"
            output = fixture.root / "mapped-wrong-hash.json"
            write(netlist, MAPPED_NETLIST)
            run = subprocess.run([
                sys.executable, str(MAPPED_SCRIPT), "--contract", str(fixture.contract),
                "--environment-receipt", str(fixture.output), "--mapped-netlist",
                str(netlist), "--expected-netlist-sha256", "0" * 64,
                "--output", str(output),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False)
            self.assertNotEqual(run.returncode, 0)

    def test_site_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            tech = fixture.pdk / "lef/gsclib045_tech.lef"
            write(tech, TECH_LEF.replace("CoreSite", "WrongSite"))
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_duplicate_qrc_file(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.pdk / "qrc/qx/fabricated.tch", "forbidden\n")
            self.assert_fail(fixture)

    def test_distinct_qrc_contract_rejected(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            contract = json.loads(fixture.contract.read_text())
            contract["technology"]["hold_qrc"]["relative_path"] = "qrc/qx/hold.tch"
            write(fixture.contract, json.dumps(contract))
            self.assert_fail(fixture)

    def test_source_archive_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            with fixture.raw_archive.open("ab") as stream:
                stream.write(b"mutation")
            self.assert_fail(fixture)


if __name__ == "__main__":
    unittest.main(verbosity=2)
