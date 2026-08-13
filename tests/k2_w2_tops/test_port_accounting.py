#!/usr/bin/env python3
"""Elaborate W2 tops and fail closed on their physical port/file boundaries."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "physical/k2_w2_tops/designs.json"
SERVER_MANIFEST_PATH = ROOT / "physical/k2_w2_server_golden/server_golden.json"
RAW_MANIFEST_PATH = ROOT / "physical/k2_w2_raw_golden/raw_golden.json"
BOUNDARY_REGISTRY_PATH = ROOT / "physical/k2_w2_boundaries.json"

EXPECTED_PORTS = {
    "k2_w2_fovea_a7_top": {
        "ref_clk_i": ("input", 1),
        "sample_clk_i": ("input", 1),
        "rst_n": ("input", 1),
        "load_i": ("input", 16),
        "source_ready_o": ("output", 16),
        "burst_clk_o": ("output", 1),
        "burst_data_o": ("output", 2),
        "retire_addr_o": ("output", 4),
        "retire_valid_o": ("output", 1),
        "drain_idle_o": ("output", 1),
        "protocol_fault_o": ("output", 1),
    },
    "k2_w2_a2_p6_top": {
        "ref_clk_i": ("input", 1),
        "sample_clk_i": ("input", 1),
        "rst_n": ("input", 1),
        "load_i": ("input", 16),
        "grant_commit_o": ("output", 1),
        "grant_count_o": ("output", 2),
        "grant_addr0_o": ("output", 4),
        "grant_addr1_o": ("output", 4),
        "grant_bitmap_o": ("output", 16),
        "p6_clk_o": ("output", 1),
        "p6_data_o": ("output", 5),
        "retire_valid_o": ("output", 2),
        "retire_addr0_o": ("output", 4),
        "retire_addr1_o": ("output", 4),
        "protocol_error_o": ("output", 1),
        "drain_idle_o": ("output", 1),
    },
    "k2_w2_a3_p6_top": {
        "ref_clk_i": ("input", 1),
        "sample_clk_i": ("input", 1),
        "rst_n": ("input", 1),
        "load_i": ("input", 16),
        "bundle_valid_o": ("output", 1),
        "bundle_ready_o": ("output", 1),
        "bundle_commit_o": ("output", 1),
        "grant_count_o": ("output", 2),
        "grant_addr0_o": ("output", 4),
        "grant_addr1_o": ("output", 4),
        "policy_microsteps_o": ("output", 2),
        "bundle_protocol_error_o": ("output", 1),
        "p6_clk_o": ("output", 1),
        "p6_data_o": ("output", 5),
        "retire_valid_o": ("output", 2),
        "retire_addr0_o": ("output", 4),
        "retire_addr1_o": ("output", 4),
        "retire_protocol_error_o": ("output", 1),
        "drain_idle_o": ("output", 1),
    },
}

EXPECTED_CHILD = {
    "k2_w2_fovea_a7_top": "a7_weighted_fovea_ddr",
    "k2_w2_a2_p6_top": "a2_batched_iwrr_p6_top",
    "k2_w2_a3_p6_top": "a3_exact_scalar_prefix_k2_p6_top",
}

EXPECTED_SERVER_PORTS = {
    "aer_fovea_buffered": {
        "clk": ("input", 1),
        "rst": ("input", 1),
        "req": ("input", 16),
        "valid": ("output", 1),
        "ready": ("input", 1),
        "addr": ("output", 4),
        "push_overrun": ("output", 1),
    },
    "aer_cluster2_buffered": {
        "clk": ("input", 1),
        "rst": ("input", 1),
        "req": ("input", 16),
        "valid0": ("output", 1),
        "ready0": ("input", 1),
        "row0": ("output", 2),
        "col_mask0": ("output", 4),
        "push_overrun0": ("output", 1),
        "valid1": ("output", 1),
        "ready1": ("input", 1),
        "row1": ("output", 2),
        "col_mask1": ("output", 4),
        "push_overrun1": ("output", 1),
        "occ0": ("output", 2),
        "occ1": ("output", 2),
    },
}

EXPECTED_RAW_PORTS = {
    "aer_tx16_trad_rowcol_fovea": {
        "clk": ("input", 1),
        "rst": ("input", 1),
        "req": ("input", 16),
        "valid": ("output", 1),
        "addr": ("output", 4),
    },
    "aer_tx16_trad_rowcol_fovea_cluster2": {
        "clk": ("input", 1),
        "rst": ("input", 1),
        "req": ("input", 16),
        "valid0": ("output", 1),
        "row0": ("output", 2),
        "col_mask0": ("output", 4),
        "valid1": ("output", 1),
        "row1": ("output", 2),
        "col_mask1": ("output", 4),
    },
}


def find_yosys() -> tuple[str, dict[str, str]]:
    requested = os.environ.get("YOSYS")
    candidates = [requested] if requested else []
    discovered = shutil.which("yosys")
    if discovered:
        candidates.append(discovered)
    candidates.extend([
        "/tmp/a7-yosys/usr/bin/yosys",
        "/tmp/a7-toolchain/usr/bin/yosys",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            env = os.environ.copy()
            fallback_lib = "/tmp/a7-yosys/usr/lib/x86_64-linux-gnu"
            if candidate.startswith("/tmp/a7-") and Path(fallback_lib).is_dir():
                prior = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = fallback_lib + (":" + prior if prior else "")
            return candidate, env
    raise RuntimeError("yosys not found")


def read_filelist(relative: str) -> list[str]:
    filelist = ROOT / relative
    rows = []
    for raw in filelist.read_text(encoding="utf-8").splitlines():
        row = raw.strip()
        if not row or row.startswith("#"):
            continue
        path = PurePosixPath(row)
        if path.is_absolute() or str(path) != row or ".." in path.parts:
            raise AssertionError(f"non-normalized file-list entry: {row}")
        rows.append(row)
    if not rows or len(rows) != len(set(rows)):
        raise AssertionError(f"empty or duplicate file-list entries: {filelist}")
    for row in rows:
        if not (ROOT / row).is_file():
            raise AssertionError(f"missing file-list source: {row}")
    return rows


def read_server_filelist(relative: str, authoritative_root: Path) -> list[str]:
    filelist = ROOT / relative
    rows = [
        row.strip() for row in filelist.read_text(encoding="utf-8").splitlines()
        if row.strip() and not row.strip().startswith("#")
    ]
    if not rows or len(rows) != len(set(rows)):
        raise AssertionError(f"empty or duplicate server file-list entries: {filelist}")
    for row in rows:
        path = Path(row)
        if not path.is_absolute() or authoritative_root not in path.parents:
            raise AssertionError(f"server file-list escaped authoritative root: {row}")
        if not path.is_file():
            raise AssertionError(f"missing authoritative server source: {row}")
    return rows


def elaborate(top: str, sources: list[str]) -> dict:
    yosys, env = find_yosys()
    with tempfile.TemporaryDirectory(prefix="k2-w2-ports-") as directory:
        output = Path(directory) / "elaborated.json"
        quoted = " ".join('"' + source.replace('"', '\\"') + '"' for source in sources)
        script = (
            f"read_verilog -sv -DSYNTHESIS {quoted}; "
            f"hierarchy -check -top {top}; proc; check -assert; "
            f"write_json \"{output}\""
        )
        result = subprocess.run(
            [yosys, "-Q", "-p", script], cwd=ROOT, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode:
            raise AssertionError(f"Yosys elaboration failed for {top}:\n{result.stdout}")
        return json.loads(output.read_text(encoding="utf-8"))


class FairTopPortAccounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_and_filelist_scope(self) -> None:
        self.assertEqual(self.manifest["schema"], "k2-w2-fair-physical-tops-v1")
        self.assertEqual(self.manifest["common_input_bits"], 19)
        self.assertEqual(
            self.manifest["boundary_scope"], "complete_endpoint_compositions"
        )
        self.assertEqual(
            self.manifest["boundary_registry"],
            "physical/k2_w2_boundaries.json",
        )
        self.assertEqual(set(self.manifest["designs"]), {"fovea_a7", "a2_p6", "a3_p6"})
        for design in self.manifest["designs"].values():
            rows = read_filelist(design["filelist"])
            self.assertNotIn("cluster2", "\n".join(rows).lower())
            self.assertTrue(rows[-1].endswith(f"{design['top']}.sv"))

    def test_elaborated_port_accounting_and_stateless_shells(self) -> None:
        common = {
            row["name"]: ("input", row["width"])
            for row in self.manifest["common_inputs"]
        }
        for design in self.manifest["designs"].values():
            top = design["top"]
            document = elaborate(top, read_filelist(design["filelist"]))
            module = document["modules"][top]
            observed = {
                name.lstrip("\\"): (port["direction"], len(port["bits"]))
                for name, port in module["ports"].items()
            }
            self.assertEqual(observed, EXPECTED_PORTS[top], top)
            self.assertEqual(
                {name: value for name, value in observed.items() if value[0] == "input"},
                common,
                top,
            )
            output_bits = sum(width for direction, width in observed.values()
                              if direction == "output")
            self.assertEqual(output_bits, design["output_bits"], top)
            for link_port in design["link_cut"]:
                self.assertEqual(
                    observed[link_port["name"]], ("output", link_port["width"]), top
                )

            cells = module.get("cells", {})
            self.assertEqual(len(cells), 1, top)
            only_cell = next(iter(cells.values()))
            self.assertEqual(only_cell["type"].lstrip("\\"), EXPECTED_CHILD[top], top)


class ServerGoldenBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(SERVER_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.golden_root = Path(cls.manifest["authoritative_root"])

    def test_exact_server_sources_and_separate_filelists(self) -> None:
        self.assertEqual(self.manifest["schema"], "k2-w2-ganghee-server-golden-v1")
        self.assertEqual(
            self.manifest["boundary_scope"],
            "standalone_buffered_fovea_and_cluster2",
        )
        self.assertEqual(
            self.manifest["boundary_registry"],
            "physical/k2_w2_boundaries.json",
        )
        endpoint_lists = "\n".join(
            "\n".join(read_filelist(row["filelist"]))
            for row in json.loads(MANIFEST_PATH.read_text())["designs"].values()
        )
        self.assertNotIn(str(self.golden_root), endpoint_lists)

        for design in self.manifest["designs"].values():
            rows = read_server_filelist(design["filelist"], self.golden_root)
            expected_rows = [str(self.golden_root / row["path"])
                             for row in design["sources"]]
            self.assertEqual(rows, expected_rows, design["top"])
            for source in design["sources"]:
                payload = (self.golden_root / source["path"]).read_bytes()
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(), source["sha256"], source["path"]
                )
            wrapper = (self.golden_root / design["sources"][-1]["path"]).read_text()
            self.assertIn(f"module {design['top']}", wrapper)
            self.assertEqual(
                wrapper.count("lane_buffer2 #"),
                design["lane_buffer2_instances"],
                design["top"],
            )

    def test_exact_server_report_inventories(self) -> None:
        for design in self.manifest["designs"].values():
            spec = design["reports"]
            report_root = self.golden_root / spec["directory"]
            reports = sorted(report_root.glob("*.rpt"))
            self.assertEqual(len(reports), spec["report_count"], design["top"])
            rows = "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in reports
            )
            self.assertEqual(
                hashlib.sha256(rows.encode()).hexdigest(),
                spec["inventory_sha256"],
                design["top"],
            )
            prefix = design["top"] + "_"
            periods = sorted({
                float(path.name[len(prefix):].split("_", 1)[0])
                for path in reports
            })
            self.assertEqual(periods, sorted(spec["periods_ns"]), design["top"])
            for period in spec["periods_ns"]:
                stem = f"{design['top']}_{period:.1f}_"
                self.assertEqual(
                    sum(path.name.startswith(stem) for path in reports), 10, stem
                )

    def test_server_wrapper_port_accounting(self) -> None:
        for design in self.manifest["designs"].values():
            sources = read_server_filelist(design["filelist"], self.golden_root)
            document = elaborate(design["top"], sources)
            module = document["modules"][design["top"]]
            observed = {
                name.lstrip("\\"): (port["direction"], len(port["bits"]))
                for name, port in module["ports"].items()
            }
            self.assertEqual(observed, EXPECTED_SERVER_PORTS[design["top"]])
            lane_buffers = sum(
                cell["type"].lstrip("\\").startswith("$paramod\\lane_buffer2")
                or cell["type"].lstrip("\\") == "lane_buffer2"
                for cell in module.get("cells", {}).values()
            )
            self.assertEqual(lane_buffers, design["lane_buffer2_instances"])


class RawGoldenBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(RAW_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.golden_root = Path(cls.manifest["authoritative_root"])

    def test_archive_sources_and_raw_only_scope(self) -> None:
        self.assertEqual(self.manifest["schema"], "k2-w2-ganghee-raw-golden-v1")
        self.assertEqual(
            self.manifest["boundary_scope"], "raw_fovea_and_cluster2_core_only"
        )
        self.assertEqual(
            self.manifest["boundary_registry"], "physical/k2_w2_boundaries.json"
        )
        archive = self.manifest["authoritative_archive"]
        archive_path = Path(archive["path"])
        self.assertTrue(archive_path.is_file())
        self.assertEqual(
            hashlib.sha256(archive_path.read_bytes()).hexdigest(), archive["sha256"]
        )
        self.assertEqual(
            archive["sha256"],
            "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3",
        )

        for design in self.manifest["designs"].values():
            rows = read_server_filelist(design["filelist"], self.golden_root)
            expected_rows = [str(self.golden_root / row["path"])
                             for row in design["sources"]]
            self.assertEqual(rows, expected_rows, design["top"])
            self.assertNotIn("lane_buffer2", "\n".join(rows))
            self.assertNotIn("a7_", "\n".join(rows).lower())
            self.assertNotIn("p6", "\n".join(rows).lower())
            for source in design["sources"]:
                payload = (self.golden_root / source["path"]).read_bytes()
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(), source["sha256"], source["path"]
                )

    def test_exact_raw_report_inventories(self) -> None:
        for design in self.manifest["designs"].values():
            spec = design["reports"]
            reports = sorted((self.golden_root / spec["directory"]).glob("*.rpt"))
            self.assertEqual(len(reports), spec["report_count"], design["top"])
            rows = "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in reports
            )
            self.assertEqual(
                hashlib.sha256(rows.encode()).hexdigest(),
                spec["inventory_sha256"],
                design["top"],
            )
            prefix = design["top"] + "_"
            periods = sorted({
                float(path.name[len(prefix):].split("_", 1)[0])
                for path in reports
            })
            self.assertEqual(periods, sorted(spec["periods_ns"]), design["top"])
            for period in spec["periods_ns"]:
                stem = f"{design['top']}_{period:.1f}_"
                self.assertEqual(
                    sum(path.name.startswith(stem) for path in reports), 10, stem
                )

    def test_raw_core_port_accounting(self) -> None:
        for design in self.manifest["designs"].values():
            sources = read_server_filelist(design["filelist"], self.golden_root)
            document = elaborate(design["top"], sources)
            module = document["modules"][design["top"]]
            observed = {
                name.lstrip("\\"): (port["direction"], len(port["bits"]))
                for name, port in module["ports"].items()
            }
            self.assertEqual(observed, EXPECTED_RAW_PORTS[design["top"]])
            cell_types = [cell["type"].lower() for cell in module.get("cells", {}).values()]
            self.assertFalse(any("lane_buffer2" in cell for cell in cell_types))
            self.assertFalse(any("a7_" in cell or "p6" in cell for cell in cell_types))


class BoundaryCohortRegistry(unittest.TestCase):
    def test_three_disjoint_ranking_cohorts(self) -> None:
        registry = json.loads(BOUNDARY_REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(registry["schema"], "k2-w2-physical-boundary-cohorts-v1")
        self.assertIs(
            registry["ranking_policy"]["cross_cohort_area_power_ranking"], False
        )
        cohorts = registry["cohorts"]
        self.assertEqual(
            [row["id"] for row in cohorts],
            ["raw_core_only", "buffered_server_golden", "complete_endpoint_wrappers"],
        )
        manifests = [row["manifest"] for row in cohorts]
        self.assertEqual(len(manifests), len(set(manifests)))
        tops = [top for row in cohorts for top in row["tops"]]
        self.assertEqual(len(tops), len(set(tops)))
        for row in cohorts:
            manifest = json.loads((ROOT / row["manifest"]).read_text(encoding="utf-8"))
            declared_tops = [design["top"] for design in manifest["designs"].values()]
            self.assertEqual(row["tops"], declared_tops, row["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
