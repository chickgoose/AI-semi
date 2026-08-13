#!/usr/bin/env python3
"""Focused regressions for the mapped-Xcelium functional gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest

import gate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture:
    def __init__(self, root: Path, endpoint: str = "a2") -> None:
        self.root = root
        self.endpoint = endpoint
        self.top = gate.ENDPOINTS[endpoint]
        self.tb_top = f"{self.top}_mapped_tb"
        self.netlist = root / "mapped.v"
        self.vendor = root / "vendor.v"
        self.tb = root / "mapped_tb.sv"
        self.sdf = root / "mapped.sdf"
        self.xrun = root / "fake_xrun.py"
        self.manifest = root / "manifest.json"
        self.netlist.write_text(
            f"module {self.top}(input clk, output q);\n"
            "  DFF u0(.D(clk), .Q(q));\n"
            "endmodule\n", encoding="utf-8",
        )
        self.vendor.write_text(
            "module DFF(D, Q); input D; output Q; endmodule\n",
            encoding="utf-8",
        )
        self.tb.write_text(
            f"module {self.tb_top}; logic clk; logic q;\n"
            f"  {self.top} dut(.clk(clk), .q(q));\n"
            "endmodule\n", encoding="utf-8",
        )
        self.sdf.write_text(
            '(DELAYFILE (SDFVERSION "3.0") '
            f'(DESIGN "{self.top}") '
            '(CELL (CELLTYPE "DFF") (INSTANCE u0) '
            '(DELAY (ABSOLUTE (IOPATH D Q (1:1:1))))))\n',
            encoding="utf-8",
        )
        self.xrun.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "top=sys.argv[sys.argv.index('-top')+1]\n"
            "endpoint='a2' if top.startswith('a2_') else ('a3' if top.startswith('a3_') else 'a4')\n"
            "mode=os.environ.get('FAKE_XRUN_MODE','pass')\n"
            "annotated=0 if mode=='zero_sdf' else 7\n"
            "print(f'SDF annotation count={annotated}')\n"
            "accepted=8; retired=7 if mode=='drop' else 8\n"
            "duplicate=1 if mode=='duplicate' else 0\n"
            "print(f'A2_MAPPED_XCELIUM_CONSERVATION_PASS endpoint={endpoint} generated=10 overrun=2 accepted={accepted} retired={retired} phantom=0 duplicate={duplicate} order_errors=0')\n"
            "print(f'A2_MAPPED_XCELIUM_PASS endpoint={endpoint}')\n",
            encoding="utf-8",
        )
        self.xrun.chmod(self.xrun.stat().st_mode | stat.S_IXUSR)
        self.document: dict = {}
        self.refresh()

    def refresh(self) -> None:
        self.document = {
            "schema": gate.SCHEMA,
            "endpoint": self.endpoint,
            "netlist": {"path": self.netlist.name, "sha256": digest(self.netlist),
                        "top": self.top},
            "sdf": {"path": self.sdf.name, "sha256": digest(self.sdf),
                    "design": self.top, "scope": f"{self.tb_top}.dut"},
            "testbench": {"path": self.tb.name, "sha256": digest(self.tb),
                          "top": self.tb_top, "dut_instance": "dut"},
            "vendor_models": [{
                "path": self.vendor.name, "sha256": digest(self.vendor),
                "modules": {"DFF": ["D", "Q"]},
            }],
        }
        self.write_manifest()

    def write_manifest(self) -> None:
        self.manifest.write_text(
            json.dumps(self.document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def execute(self) -> dict:
        return gate.execute(
            self.manifest, self.xrun, self.root / "work", self.root / "result.json"
        )


class GateTests(unittest.TestCase):
    def fixture(self, endpoint: str = "a2") -> tuple[tempfile.TemporaryDirectory[str], Fixture]:
        temporary = tempfile.TemporaryDirectory(prefix="a2-mapped-xcelium-test-")
        return temporary, Fixture(Path(temporary.name), endpoint)

    def test_all_three_canonical_endpoints_pass(self) -> None:
        for endpoint in gate.ENDPOINTS:
            temporary, fixture = self.fixture(endpoint)
            with temporary, self.subTest(endpoint=endpoint):
                result = fixture.execute()
                self.assertEqual("PASS", result["status"])
                self.assertEqual(gate.ENDPOINTS[endpoint], result["canonical_top"])
                self.assertEqual(7, result["transcript"]["sdf_annotated"])
                self.assertEqual(8, result["transcript"]["retired"])
                self.assertEqual("HOLD", result["physical_qualification"])

    def test_vendor_pin_preflight_rejects_mismatch(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            fixture.document["vendor_models"][0]["modules"]["DFF"] = ["D", "QN"]
            fixture.write_manifest()
            with self.assertRaisesRegex(gate.GateError, "pins differ"):
                gate.preflight(fixture.manifest)

    def test_duplicate_vendor_module_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            duplicate = fixture.root / "vendor_duplicate.v"
            duplicate.write_text(fixture.vendor.read_text(encoding="utf-8"), encoding="utf-8")
            fixture.document["vendor_models"].append({
                "path": duplicate.name, "sha256": digest(duplicate),
                "modules": {"DFF": ["D", "Q"]},
            })
            fixture.write_manifest()
            with self.assertRaisesRegex(gate.GateError, "duplicate declared vendor module"):
                gate.preflight(fixture.manifest)

    def test_duplicate_definition_inside_vendor_file_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            fixture.vendor.write_text(
                fixture.vendor.read_text(encoding="utf-8") * 2,
                encoding="utf-8",
            )
            fixture.document["vendor_models"][0]["sha256"] = digest(fixture.vendor)
            fixture.write_manifest()
            with self.assertRaisesRegex(gate.GateError, "duplicate module DFF within"):
                gate.preflight(fixture.manifest)

    def test_unresolved_cell_and_unknown_named_pin_are_rejected(self) -> None:
        for replacement, marker in (
            ("MISSING u0(.D(clk), .Q(q));", "unresolved vendor cell MISSING"),
            ("DFF u0(.D(clk), .BAD(q));", "uses unknown pins"),
        ):
            temporary, fixture = self.fixture()
            with temporary, self.subTest(replacement=replacement):
                fixture.netlist.write_text(
                    f"module {fixture.top}(input clk, output q);\n"
                    f"  {replacement}\nendmodule\n",
                    encoding="utf-8",
                )
                fixture.document["netlist"]["sha256"] = digest(fixture.netlist)
                fixture.write_manifest()
                with self.assertRaisesRegex(gate.GateError, marker):
                    gate.preflight(fixture.manifest)

    def test_ansi_vendor_pin_declarations_are_supported(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            fixture.vendor.write_text(
                "module DFF(input logic D, output logic Q); endmodule\n",
                encoding="utf-8",
            )
            fixture.document["vendor_models"][0]["sha256"] = digest(fixture.vendor)
            fixture.write_manifest()
            identity = gate.preflight(fixture.manifest)
            self.assertEqual(fixture.top, identity["top"])

    def test_noncanonical_top_and_scope_are_rejected(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            for field, value, marker in (
                (("netlist", "top"), "wrong_top", "netlist top must be canonical"),
                (("sdf", "scope"), "wrong.scope", "SDF design/scope must be"),
            ):
                original = fixture.document[field[0]][field[1]]
                fixture.document[field[0]][field[1]] = value
                fixture.write_manifest()
                with self.subTest(field=field), self.assertRaisesRegex(gate.GateError, marker):
                    gate.preflight(fixture.manifest)
                fixture.document[field[0]][field[1]] = original

    def test_sdf_design_and_nonzero_entries_are_checked(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            fixture.sdf.write_text(
                f'(DELAYFILE (SDFVERSION "3.0") (DESIGN "{fixture.top}"))\n',
                encoding="utf-8",
            )
            fixture.document["sdf"]["sha256"] = digest(fixture.sdf)
            fixture.write_manifest()
            with self.assertRaisesRegex(gate.GateError, "zero annotatable"):
                gate.preflight(fixture.manifest)

    def test_transcript_requires_nonzero_annotation(self) -> None:
        text = (
            "SDF annotation count=0\n"
            "A2_MAPPED_XCELIUM_CONSERVATION_PASS endpoint=a2 generated=10 "
            "overrun=2 accepted=8 retired=8 phantom=0 duplicate=0 order_errors=0\n"
            "A2_MAPPED_XCELIUM_PASS endpoint=a2\n"
        )
        with self.assertRaisesRegex(gate.GateError, "nonzero Xcelium SDF"):
            gate.parse_transcript(text, "a2")

    def test_transcript_conservation_and_duplicates_are_checked(self) -> None:
        base = (
            "SDF annotation count=7\n"
            "A2_MAPPED_XCELIUM_CONSERVATION_PASS endpoint=a2 generated=10 "
            "overrun=2 accepted=8 retired={retired} phantom=0 duplicate={duplicate} "
            "order_errors=0\nA2_MAPPED_XCELIUM_PASS endpoint=a2\n"
        )
        with self.assertRaisesRegex(gate.GateError, "accepted=retired"):
            gate.parse_transcript(base.format(retired=7, duplicate=0), "a2")
        with self.assertRaisesRegex(gate.GateError, "phantom, duplicate, or order"):
            gate.parse_transcript(base.format(retired=8, duplicate=1), "a2")

    def test_artifact_hash_mutation_fails_closed(self) -> None:
        temporary, fixture = self.fixture()
        with temporary:
            fixture.netlist.write_text(
                fixture.netlist.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(gate.GateError, "SHA-256 mismatch"):
                gate.preflight(fixture.manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
