#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl/technology/p6"
MANIFEST = RTL / "p6_tech_manifest.json"


def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            MANIFEST.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )

    def test_selection_and_holds_are_fail_closed(self) -> None:
        doc = self.document
        self.assertEqual(set(doc), {
            "schema", "status", "selection", "top", "frozen_owner",
            "local_cell_evidence", "icg_cell_evidence", "bindings",
            "production_source_closure", "test_only_sources", "mock_policy",
            "execution", "holds",
        })
        self.assertEqual(doc["schema"], "w2-p6-clock-edge-techmap-v1")
        self.assertEqual(doc["status"], "PARTIAL_GO_DDR_HOLD")
        self.assertEqual(doc["top"], "w2_p6_exact_pair_endpoint_tech")
        self.assertEqual(doc["selection"], {
            "exactly_one_required": True,
            "generic_macro": "W2_P6_TECH_GENERIC",
            "gsclib045_macro": "W2_P6_TECH_GSCLIB045",
            "implicit_fallback_allowed": False,
        })
        bindings = doc["bindings"]
        for name in ("negative_edge_async_clear_bit", "oddr", "iddr"):
            self.assertIsNone(bindings[name]["cell"])
            self.assertTrue(bindings[name]["status"].startswith("HOLD_"))
        self.assertEqual(bindings["integrated_clock_gate"]["cell"], "TLATNCAX2")
        self.assertEqual(bindings["integrated_clock_gate"]["ports"], {
            "clock": "CK", "enable": "E", "output": "ECK"
        })
        self.assertIn("clock is low", bindings["integrated_clock_gate"]["reset_limit"])
        self.assertTrue(doc["holds"])
        self.assertEqual(doc["mock_policy"], {
            "required_macro": "W2_P6_TEST_ONLY",
            "production_filelist_allowed": False,
            "synthesis_evidence_allowed": False,
        })
        self.assertFalse(doc["execution"]["real_library_model_simulation"])
        self.assertFalse(doc["execution"]["real_library_compile"])
        self.assertFalse(doc["execution"]["mapped_sta"])
        self.assertFalse(doc["execution"]["place_and_route"])
        self.assertFalse(doc["execution"]["physical_ppa"])

    def test_frozen_owner_hashes_and_commit(self) -> None:
        owner = self.document["frozen_owner"]
        subprocess.run(
            ["git", "cat-file", "-e", f'{owner["origin_commit"]}^{{commit}}'],
            cwd=ROOT, check=True,
        )
        for relative, expected in owner["files"].items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(sha256(path.read_bytes()), expected, relative)
            original = subprocess.run(
                ["git", "show", f'{owner["origin_commit"]}:{relative}'],
                cwd=ROOT, check=True, stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(path.read_bytes(), original, relative)

    def test_local_cell_evidence_is_exact(self) -> None:
        evidence = self.document["local_cell_evidence"]
        archive = ROOT / evidence["archive"]
        self.assertEqual(sha256(archive.read_bytes()), evidence["archive_sha256"])
        with tarfile.open(archive, "r:gz") as bundle:
            member = bundle.extractfile(evidence["member"])
            self.assertIsNotNone(member)
            netlist = member.read()
        self.assertEqual(sha256(netlist), evidence["member_sha256"])
        text = netlist.decode("ascii")
        for name, ports, count in (
            ("CLKAND2X2", ("A", "B", "Y"), 12),
            ("DFFRHQX1", ("RN", "CK", "D", "Q"), 92),
        ):
            instances = re.findall(rf"^\s*{name}\s+.*?;", text, re.M | re.S)
            self.assertEqual(len(instances), count, name)
            self.assertTrue(all(all(re.search(rf"\.{port}\s*\(", item)
                                    for port in ports)
                                for item in instances), name)
        library_manifest = ROOT / evidence["library_manifest"]
        self.assertEqual(sha256(library_manifest.read_bytes()),
                         evidence["library_manifest_sha256"])
        values = dict(line.split("=", 1) for line in library_manifest.read_text().splitlines()
                      if "=" in line)
        self.assertEqual(values["library_sha256"], evidence["library_sha256"])

    def test_icg_evidence_is_git_pinned(self) -> None:
        evidence = self.document["icg_cell_evidence"]
        for path_key, hash_key in (
            ("feasibility_path", "feasibility_sha256"),
            ("adapter_path", "adapter_sha256"),
        ):
            content = subprocess.run(
                ["git", "show", f'{evidence["commit"]}:{evidence[path_key]}'],
                cwd=ROOT, check=True, stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(sha256(content), evidence[hash_key])
        adapter = subprocess.run(
            ["git", "show", f'{evidence["commit"]}:{evidence["adapter_path"]}'],
            cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
        ).stdout
        for token in ("TLATNCAX2", ".CK", ".E", ".ECK"):
            self.assertIn(token, adapter)

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
            self.assertTrue(all(not path.startswith("tests/") for path in closure))
            content = filelist.read_text()
            for test_source in self.document["test_only_sources"]:
                self.assertNotIn(test_source, content)
        for path in self.document["test_only_sources"]:
            self.assertTrue((ROOT / path).is_file(), path)
        mock = (ROOT / self.document["test_only_sources"][0]).read_text()
        self.assertIn("`ifdef W2_P6_TEST_ONLY", mock)

    def test_only_supported_external_cells_are_instantiated(self) -> None:
        sources = "\n".join((ROOT / path).read_text()
                            for path in self.document["production_source_closure"])
        defined = set(re.findall(r"^module\s+([A-Za-z_]\w*)", sources, re.M))
        instantiated = set(re.findall(
            r"^\s+([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(", sources, re.M
        ))
        external = instantiated - defined
        sentinels = {name for name in external if name.endswith("__compile_error")}
        self.assertEqual(sentinels, {
            "w2_p6_invalid_or_missing_technology_selection__compile_error"
        })
        external -= sentinels
        selected_allowlist = {
            binding["cell"] for binding in self.document["bindings"].values()
            if binding.get("cell") is not None
            and binding["status"] != "EVIDENCED_NOT_SELECTED"
        }
        self.assertEqual(selected_allowlist, {"TLATNCAX2", "DFFRHQX1"})
        self.assertEqual(external, selected_allowlist)


if __name__ == "__main__":
    unittest.main()
