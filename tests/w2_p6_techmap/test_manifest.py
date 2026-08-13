#!/usr/bin/env python3

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl/technology/p6"
MANIFEST = RTL / "p6_tech_manifest.json"
ARCHIVE_ENV = {
    "raw": "W2_P6_RAW_GOLDEN",
    "buffered": "W2_P6_BUFFERED_GOLDEN",
}


def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def named_cell_instances(text: str, cell: str) -> list[str]:
    return re.findall(
        rf"^\s*{re.escape(cell)}\s+\\?[^\s(]+\s*\((.*?)\);",
        text,
        re.M | re.S,
    )


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            MANIFEST.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
        cls.evidence = cls.document["authoritative_server_evidence"]
        cls.archives: dict[str, pathlib.Path] = {}
        cls.members: dict[str, dict[str, bytes]] = {}
        for role in ("raw", "buffered"):
            receipt = cls.evidence[role]
            path = pathlib.Path(os.environ.get(
                ARCHIVE_ENV[role], receipt["default_archive"]
            ))
            if sha256(path.read_bytes()) != receipt["archive_sha256"]:
                raise AssertionError(f"{role} golden archive identity mismatch: {path}")
            cls.archives[role] = path
            with tarfile.open(path, "r:gz") as bundle:
                regular_names = [item.name for item in bundle if item.isfile()]
                if len(regular_names) != len(set(regular_names)):
                    raise AssertionError(f"duplicate regular member in {role} archive")
                cls.members[role] = {
                    name: bundle.extractfile(name).read() for name in regular_names
                }

    def test_schema_selection_and_holds_are_fail_closed(self) -> None:
        doc = self.document
        self.assertEqual(set(doc), {
            "schema", "status", "selection", "top", "frozen_owner",
            "authoritative_server_evidence", "bindings",
            "production_source_closure", "test_only_sources", "mock_policy",
            "execution", "holds",
        })
        self.assertEqual(doc["schema"], "w2-p6-clock-edge-techmap-v3")
        self.assertEqual(doc["top"], "w2_p6_exact_pair_endpoint_tech")
        self.assertEqual(doc["selection"], {
            "exactly_one_required": True,
            "generic_macro": "W2_P6_TECH_GENERIC",
            "gsclib045_macro": "W2_P6_TECH_GSCLIB045",
            "implicit_fallback_allowed": False,
        })
        bindings = doc["bindings"]
        self.assertEqual(bindings["integrated_clock_gate"]["cell"],
                         "TLATNTSCAX2")
        self.assertEqual(bindings["integrated_clock_gate"]["test_enable_tie"], 0)
        self.assertEqual(bindings["symbol_mux_bit"]["cell"], "MX2X1")
        self.assertEqual(bindings["positive_edge_async_clear_bit"]["cell"],
                         "DFFRHQX1")
        self.assertIn("RAW_NOT_OBSERVED",
                      bindings["positive_edge_async_clear_bit"]["status"])
        self.assertEqual(bindings["negative_edge_async_clear_bit"]["cell"],
                         "DFFNSRX1")
        self.assertEqual(bindings["negative_edge_async_clear_bit"]["set_n_tie"], 1)
        for name in ("oddr", "iddr"):
            self.assertIsNone(bindings[name]["cell"])
            self.assertTrue(bindings[name]["status"].startswith("HOLD_"))
        self.assertEqual(doc["mock_policy"], {
            "required_macro": "W2_P6_TEST_ONLY",
            "production_filelist_allowed": False,
            "synthesis_evidence_allowed": False,
            "library_behavior_or_timing_evidence_allowed": False,
        })
        for field in ("real_library_model_simulation", "real_library_compile",
                      "dedicated_p6_server_run", "p6_mapped_sta",
                      "p6_place_and_route", "p6_physical_ppa"):
            self.assertFalse(doc["execution"][field], field)

    def test_frozen_owner_hashes_and_commit(self) -> None:
        owner = self.document["frozen_owner"]
        subprocess.run(
            ["git", "cat-file", "-e", f'{owner["origin_commit"]}^{{commit}}'],
            cwd=ROOT, check=True,
        )
        for relative, expected in owner["files"].items():
            path = ROOT / relative
            self.assertEqual(sha256(path.read_bytes()), expected, relative)
            original = subprocess.run(
                ["git", "show", f'{owner["origin_commit"]}:{relative}'],
                cwd=ROOT, check=True, stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(path.read_bytes(), original, relative)

    def test_archive_identities_representatives_and_payload_limits(self) -> None:
        manifest_text = MANIFEST.read_text()
        for superseded in ("reports/a7-drec-physical", "/tmp/a6-w7-audit",
                           "8d91d041ce9dd7acbbb1778c08dec4a1627c4e7c"):
            self.assertNotIn(superseded, manifest_text)
        for role in ("raw", "buffered"):
            receipt = self.evidence[role]
            self.assertEqual(sha256(self.archives[role].read_bytes()),
                             receipt["archive_sha256"])
            for member, expected in receipt["representative_members"].items():
                self.assertIn(member, self.members[role])
                self.assertEqual(sha256(self.members[role][member]), expected,
                                 f"{role}:{member}")
            payload_suffixes = (".lib", ".lef", ".tch")
            self.assertFalse(any(name.endswith(payload_suffixes)
                                 for name in self.members[role]))
        flow = self.evidence["server_flow_references"]
        for field in ("library_payload_archived", "lef_payload_archived",
                      "qrc_payload_archived", "liberty_pin_arcs_proven"):
            self.assertFalse(flow[field])

    def test_complete_mapped_candidate_inventory_and_ports(self) -> None:
        expected_ports = {
            "TLATNTSCAX2": ("E", "CK", "SE", "ECK"),
            "MX2X1": ("A", "B", "S0", "Y"),
            "DFFRHQX1": ("RN", "CK", "D", "Q"),
            "DFFNSRX1": ("CKN", "D", "RN", "SN", "Q", "QN"),
        }
        candidates = (*expected_ports, "TLATNCAX2")
        for role in ("raw", "buffered"):
            netlists = {
                name: data.decode("ascii")
                for name, data in self.members[role].items()
                if name.endswith("_netlist.v")
            }
            self.assertEqual(len(netlists),
                             self.evidence[role]["mapped_netlist_count"])
            observed: dict[str, dict[str, int]] = {}
            for cell in candidates:
                per_file = {
                    name: named_cell_instances(text, cell)
                    for name, text in netlists.items()
                }
                instances = [body for bodies in per_file.values() for body in bodies]
                observed[cell] = {
                    "instances": len(instances),
                    "files": sum(bool(items) for items in per_file.values()),
                }
                for body in instances:
                    for port in expected_ports.get(cell, ()):
                        self.assertRegex(body, rf"\.{port}\s*\(",
                                         f"{role}:{cell}.{port}")
            self.assertEqual(observed, self.evidence[role]["candidate_inventory"])
            all_cell_types = Counter(re.findall(
                r"^\s*([A-Z][A-Z0-9_]*)\s+\\?[^\s(]+\s*\(",
                "\n".join(netlists.values()), re.M,
            ))
            self.assertFalse(any(re.search(r"(?:ODDR|IDDR|^DFFN)", cell)
                                 for cell in all_cell_types))
        buffered_source = self.members["buffered"]["rtl/lane_buffer2.sv"].decode()
        self.assertRegex(buffered_source,
                         r"assign\s+pop_data\s*=\s*slot\[rp_q\]\s*;")
        self.assertIn("always_ff @(posedge clk or negedge rst_n)", buffered_source)
        buffered_netlist = self.members["buffered"][
            "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0_netlist.v"
        ].decode()
        self.assertRegex(
            buffered_netlist,
            r"(?s)MX2X1\s+\S+\(\.A\s*\(\\u_buf0_slot\[0\].*?"
            r"\.B\s*\(\\u_buf0_slot\[1\].*?\.S0\s*\(u_buf0_rp_q\)",
        )

    def test_server_tcl_logs_and_sdc_scope_are_exact(self) -> None:
        flow = self.evidence["server_flow_references"]
        total_sdcs = 0
        for role in ("raw", "buffered"):
            members = self.members[role]
            expected_runs = self.evidence[role]["mapped_netlist_count"]
            groups = {
                "genus": [data.decode(errors="replace") for name, data in members.items()
                          if re.search(r"/genus_[0-9.]+\.tcl$", name)],
                "mmmc": [data.decode(errors="replace") for name, data in members.items()
                         if re.search(r"/mmmc_[0-9.]+\.tcl$", name)],
                "run": [data.decode(errors="replace") for name, data in members.items()
                        if re.search(r"/run_[0-9.]+\.tcl$", name)],
            }
            self.assertTrue(all(len(items) == expected_runs
                                for items in groups.values()))
            for text in groups["genus"]:
                self.assertIn(flow["liberty"], text)
                self.assertIn("lp_insert_clock_gating true", text)
            for text in groups["mmmc"]:
                self.assertIn(flow["liberty"], text)
                self.assertIn(flow["qrc_technology"], text)
            for text in groups["run"]:
                self.assertIn(flow["technology_lef"], text)
                self.assertIn(flow["macro_lef"], text)
            sdcs = [data.decode() for name, data in members.items()
                    if name.endswith(".sdc") and not name.endswith("_out.sdc")]
            self.assertEqual(len(sdcs), expected_runs)
            total_sdcs += len(sdcs)
            for text in sdcs:
                self.assertEqual(len(re.findall(r"^create_clock\b", text, re.M)), 1)
                self.assertRegex(text, r"create_clock -name clk -period [0-9.]+ \[get_ports clk\]")
                self.assertIn("set_clock_uncertainty 0.100 [get_clocks clk]", text)
                self.assertIn("-clock clk 0.250", text)
                self.assertNotIn("create_generated_clock", text)
                self.assertNotRegex(text, r"(?:clock_fall|falling|negedge|ODDR|IDDR)")
        self.assertEqual(total_sdcs,
                         self.evidence["sdc_comparison"]["input_sdc_count"])
        raw_log = self.members["raw"][
            "synth/pnr/resynth_fovea_raw/innovus_1.4.log"].decode(errors="replace")
        buffered_log = self.members["buffered"][
            "synth/pnr/resynth_cluster2_buffered/innovus_1.0.log"].decode(errors="replace")
        for text in (raw_log, buffered_log):
            self.assertIn("TLATNTSCAX2", text)
            self.assertIn("489", text)
            self.assertIn(flow["liberty"], text)
            self.assertIn(flow["technology_lef"], text)
        self.assertIn("MX2X1", buffered_log)
        reports = (
            self.members["raw"][
                "synth/pnr/resynth_fovea_raw/aer_tx16_trad_rowcol_fovea_1.4_check_timing.rpt"
            ].decode(errors="replace"),
            self.members["buffered"][
                "synth/pnr/resynth_cluster2_buffered/aer_cluster2_buffered_1.0_check_timing.rpt"
            ].decode(errors="replace"),
        )
        for report in reports:
            self.assertIn("ideal_clock_waveform", report)
            self.assertIn("No drive assertion", report)
            self.assertIn("Generated by:      Cadence Innovus 23.14-s088_1", report)

    def test_filelists_are_exact_and_test_models_excluded(self) -> None:
        expected = self.document["production_source_closure"]
        self.assertEqual(len(expected), len(set(expected)))
        self.assertTrue(all((ROOT / path).is_file() for path in expected))
        for filelist in sorted((RTL / "filelists").glob("*.f")):
            closure = [
                line.strip() for line in filelist.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "+"))
            ]
            self.assertEqual(closure, expected, filelist.name)
            content = filelist.read_text()
            for test_source in self.document["test_only_sources"]:
                self.assertNotIn(test_source, content)
        for path in self.document["test_only_sources"]:
            self.assertTrue((ROOT / path).is_file(), path)
        mock = (ROOT / self.document["test_only_sources"][0]).read_text()
        self.assertIn("`ifdef W2_P6_TEST_ONLY", mock)

    def test_only_manifest_selected_external_cells_are_instantiated(self) -> None:
        sources = "\n".join((ROOT / path).read_text()
                            for path in self.document["production_source_closure"])
        defined = set(re.findall(r"^module\s+([A-Za-z_]\w*)", sources, re.M))
        instantiated = set(re.findall(
            r"^\s{2,}([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(", sources, re.M
        ))
        external = instantiated - defined - {"end"}
        sentinels = {name for name in external if name.endswith("__compile_error")}
        self.assertEqual(sentinels, {
            "w2_p6_invalid_or_missing_technology_selection__compile_error"
        })
        external -= sentinels
        selected = {
            binding["cell"] for binding in self.document["bindings"].values()
            if binding.get("cell") is not None
        }
        self.assertEqual(selected, {
            "TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1"
        })
        self.assertEqual(external, selected)
        self.assertNotIn("TLATNCAX2", sources)


if __name__ == "__main__":
    unittest.main()
