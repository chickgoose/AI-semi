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
  cell (TLATNCAX2) { pin (CK) { direction : input; } }
  cell (MX2X1) { pin (Y) { direction : output; function : \"A?B:C\"; } }
  cell (DFFPX1) {
    ff (IQ, IQN) { clocked_on : \"CK\"; next_state : \"D\"; }
  }
  cell (DFFNX1) {
    ff (IQ, IQN) { clocked_on : \"!CK\"; next_state : \"D\"; }
  }
}
"""

FAST = SLOW.replace("library (slow)", "library (fast)").replace(
    "nom_voltage : 0.9", "nom_voltage : 1.1").replace(
    "nom_temperature : 125.0", "nom_temperature : -40.0")

TECH_LEF = """VERSION 5.8 ;
SITE CoreSite
  CLASS CORE ;
  SIZE 0.2 BY 1.4 ;
END CoreSite
END LIBRARY
"""


def macro(name: str, site: str = "CoreSite") -> str:
    return f"MACRO {name}\n  CLASS CORE ;\n  SITE {site} ;\nEND {name}\n"


MACRO_LEF = "VERSION 5.8 ;\n" + "".join(
    macro(name) for name in ("TLATNCAX2", "MX2X1", "DFFPX1", "DFFNX1")) + "END LIBRARY\n"


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.pdk = root / "pdk"
        self.raw_archive = root / "raw.tar.gz"
        self.buffered_archive = root / "buffered.tar.gz"
        self.genus = root / "bin" / "genus"
        self.innovus = root / "bin" / "innovus"
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
                b"DFFPX1 u_ff();\n",
            "synth/pnr/resynth_cluster2_raw/innovus_0.7.log":
                b"Clock gates   (no test): TLATNCAX20 TLATNCAX2\n",
        }
        self.buffered_members = {
            "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0_netlist.v":
                b"MX2X1 u_mux(); DFFPX1 u_ff();\n",
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
        write(self.genus, "#!/bin/sh\necho 'Genus 23.14-s090_1'\n", executable=True)
        write(self.innovus, "#!/bin/sh\necho 'Innovus 23.14-s088_1'\n", executable=True)
        make_tar(self.raw_archive, self.raw_members)
        make_tar(self.buffered_archive, self.buffered_members)
        self.refresh_contract()

    def refresh_contract(self) -> None:
        file_hash = lambda path: sha(path.read_bytes())
        document = {
            "schema": "k2_w2_server_env_contract_v1",
            "server_pdk_root": str(self.pdk),
            "tools": {
                "genus": {"version": "23.14-s090_1", "sha256": file_hash(self.genus),
                          "golden_executable_identity": None},
                "innovus": {"version": "23.14-s088_1", "sha256": file_hash(self.innovus),
                            "golden_executable_identity": "/tools/cadence/DDI231/INNOVUS231/bin/innovus_"},
            },
            "technology": {
                "setup_liberty": {"relative_path": "timing/slow_vdd1v0_basicCells.lib",
                                    "sha256": file_hash(self.pdk / "timing/slow_vdd1v0_basicCells.lib"),
                                    "pvt": [1.0, 0.9, 125.0]},
                "hold_liberty": {"relative_path": "timing/fast_vdd1v0_basicCells.lib",
                                   "sha256": file_hash(self.pdk / "timing/fast_vdd1v0_basicCells.lib"),
                                   "pvt": [1.0, 1.1, -40.0]},
                "tech_lef": {"relative_path": "lef/gsclib045_tech.lef",
                             "sha256": file_hash(self.pdk / "lef/gsclib045_tech.lef")},
                "macro_lef": {"relative_path": "lef/gsclib045_macro.lef",
                              "sha256": file_hash(self.pdk / "lef/gsclib045_macro.lef")},
                "setup_qrc": {"relative_path": "qrc/qx/gpdk045.tch",
                              "sha256": file_hash(self.pdk / "qrc/qx/gpdk045.tch")},
                "hold_qrc": {"relative_path": "qrc/qx/gpdk045.tch",
                             "sha256": file_hash(self.pdk / "qrc/qx/gpdk045.tch")},
                "required_timing_directory_entries": [
                    "fast_vdd1v0_basicCells.lib", "slow_vdd1v0_basicCells.lib"],
                "required_qrc_tch_entries": ["gpdk045.tch"],
                "required_site": "CoreSite",
                "required_cells": {"icg": "TLATNCAX2", "mux": "MX2X1"},
                "required_ff_edges": ["posedge", "negedge"],
            },
            "corner_policy": {
                "setup_liberty": "slow_vdd1v0_basicCells.lib",
                "hold_liberty": "fast_vdd1v0_basicCells.lib",
                "setup_qrc": "gpdk045.tch", "hold_qrc": "gpdk045.tch",
                "shared_rc_limitation": "single shared typical QRC; physical signoff HOLD",
            },
            "source_archives": {
                "raw_core": {"default_path": str(self.raw_archive),
                             "sha256": file_hash(self.raw_archive)},
                "buffered_extension": {"default_path": str(self.buffered_archive),
                                       "sha256": file_hash(self.buffered_archive)},
            },
            "golden_anchors": {
                "raw_core": {name: sha(data) for name, data in self.raw_members.items()},
                "buffered_extension": {
                    name: sha(data) for name, data in self.buffered_members.items()},
            },
        }
        write(self.contract, json.dumps(document, indent=2, sort_keys=True) + "\n")

    def run(self, server: bool = True, allow_hold: bool = False) -> subprocess.CompletedProcess:
        command = [sys.executable, str(SCRIPT), "--contract", str(self.contract),
                   "--raw-archive", str(self.raw_archive), "--buffered-archive",
                   str(self.buffered_archive), "--output", str(self.output)]
        if server:
            command += ["--pdk-root", str(self.pdk), "--genus", str(self.genus),
                        "--innovus", str(self.innovus)]
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

    def test_tool_version_mutation(self) -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            write(fixture.genus, "#!/bin/sh\necho 'Genus 99.0'\n", executable=True)
            fixture.refresh_contract()
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

    def semantic_mutation(self, old: str, new: str = "REMOVED") -> None:
        temporary, fixture = self.with_fixture()
        with temporary:
            for name in ("slow_vdd1v0_basicCells.lib", "fast_vdd1v0_basicCells.lib"):
                path = fixture.pdk / "timing" / name
                write(path, path.read_text().replace(old, new))
            fixture.refresh_contract()
            self.assert_fail(fixture)

    def test_missing_icg(self) -> None:
        self.semantic_mutation("TLATNCAX2")

    def test_missing_mux(self) -> None:
        self.semantic_mutation("MX2X1")

    def test_missing_posedge_ff(self) -> None:
        self.semantic_mutation('clocked_on : "CK"', 'clocked_on : "!CK"')

    def test_missing_negedge_ff(self) -> None:
        self.semantic_mutation('clocked_on : "!CK"', 'clocked_on : "CK"')

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
