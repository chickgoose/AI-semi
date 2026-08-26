import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = (
    ROOT
    / "benchmarks"
    / "redred_cluster2_cav_bridge"
    / "ganghee_cluster2_polarity_v1_authority.json"
)
UPSTREAM = Path("/tmp/ganghee-ai-semi-manifest-audit-20260826")

PINNED_COMMIT = "44f8918c6e0085f7b75bb90fbe6c099abe1882cc"
LEDGER_REPRO_COMMIT = "58c132fb475013634ee156eddf5037128c0ce0b3"
LEDGER_RECEIPT_COMMIT = "f2f93a830414aff2e0a3b7db05154294e1d4b78d"
MANIFEST_PATH = (
    "common_traces_uzh/event_logger_out/"
    "uzh_shapes_rotation_patch.polarity_manifest.json"
)
MANIFEST_SHA256 = "df7ecc74be802c55dedb2596ef8dc7063c71f9324d48ab45dfaa360cb87a02fa"
JSONL_PATH = (
    "common_traces_uzh/event_logger_out/"
    "uzh_shapes_rotation_patch.aer_transport_polarity.jsonl"
)
JSONL_SHA256 = "518a2a5ba977516ea687fdc23a9246ff9cfe90fbf3d013efdd358200596e9cd3"
PINNED_FILES = {
    "rtl/arbiter2.v":
        "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    "rtl/arbiter4_tree.v":
        "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v":
        "20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81",
    "common_traces_uzh/uzh_shapes_rotation_patch.addrpol.txt":
        "9f682af4eb11239f0743c2f95a82e4302836ac8a02e68278b8b69464beac55c4",
    MANIFEST_PATH: MANIFEST_SHA256,
    JSONL_PATH: JSONL_SHA256,
}


class PolarityV1AuthorityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = AUTHORITY.read_bytes()
        cls.authority = json.loads(cls.raw)

    def test_authority_is_canonical_json_and_pins_public_main(self):
        canonical = json.dumps(
            self.authority, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        self.assertEqual(self.raw, canonical)
        self.assertEqual(
            self.authority["schema"],
            "redred.cluster2_cav_bridge.ganghee_polarity_v1_authority/v2",
        )
        self.assertEqual(
            self.authority["repository_url"],
            "https://github.com/GangHeeJo/AI-SEMI",
        )
        self.assertEqual(self.authority["git_commit"], PINNED_COMMIT)
        provenance = self.authority["provenance"]
        self.assertEqual(provenance["public_ref"], "refs/heads/main")
        self.assertEqual(provenance["source_rtl_native_run_commit"], PINNED_COMMIT)
        self.assertEqual(provenance["polarity_ledger_repro_commit"], LEDGER_REPRO_COMMIT)
        self.assertEqual(
            provenance["polarity_ledger_receipt_commit"], LEDGER_RECEIPT_COMMIT
        )

    def test_ledger_manifest_and_unchanged_jsonl_are_separately_bound(self):
        evidence = {
            row["path"]: row["sha256"] for row in self.authority["evidence_files"]
        }
        self.assertEqual(evidence[MANIFEST_PATH], MANIFEST_SHA256)
        self.assertEqual(evidence[JSONL_PATH], JSONL_SHA256)

        bundled = (
            ROOT
            / "benchmarks/redred_cluster2_cav_bridge/evidence"
            / "uzh_shapes_rotation_patch.polarity_manifest.json"
        )
        self.assertEqual(hashlib.sha256(bundled.read_bytes()).hexdigest(), MANIFEST_SHA256)
        manifest = json.loads(bundled.read_bytes())
        self.assertEqual(manifest["repro"]["git_commit"], LEDGER_REPRO_COMMIT)
        self.assertEqual(manifest["sha1"]["jsonl_out"],
                         "f43e0c41bf2b2ed15e826e191a7aeee3ee33638c")

    def test_exact_v1_dependencies_and_trace_are_bound(self):
        code_files = {
            row["path"]: row["sha256"] for row in self.authority["code_files"]
        }
        self.assertEqual(
            code_files,
            {path: digest for path, digest in PINNED_FILES.items() if path.startswith("rtl/")},
        )
        trace = self.authority["tracked_polarity_trace"]
        trace_path = "common_traces_uzh/uzh_shapes_rotation_patch.addrpol.txt"
        self.assertEqual(trace["path"], trace_path)
        self.assertEqual(trace["sha256"], PINNED_FILES[trace_path])
        self.assertEqual((trace["line_count"], trace["event_count"]), (3259, 8503))
        self.assertEqual(trace["line_endings"], "LF")

    def test_interface_and_variant_selection_are_unambiguous(self):
        interface = self.authority["native_interface"]
        self.assertEqual(
            interface["module"],
            "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity",
        )
        self.assertEqual(
            [(row["name"], row["width"]) for row in interface["inputs"]],
            [("clk", 1), ("rst", 1), ("arrival", 16), ("polarity_in", 16)],
        )
        self.assertEqual(
            [lane["pol_mask"] for lane in interface["retire_lanes"]],
            [{"name": "pol_mask0", "width": 4}, {"name": "pol_mask1", "width": 4}],
        )
        selection = self.authority["variant_selection"]
        self.assertEqual(selection["selected_variant"], "polarity_v1")
        self.assertFalse(selection["full_and_granted_same_cycle_accepts_arrival"])
        self.assertEqual(selection["overrun_rule"], "overrun = arrival & pending_full")
        self.assertEqual(
            [row["variant"] for row in selection["not_selected"]],
            ["polarity_v2", "pressure"],
        )

    @unittest.skipUnless(UPSTREAM.is_dir(), "read-only Ganghee audit clone unavailable")
    def test_pins_match_upstream_bytes(self):
        for relative, expected in PINNED_FILES.items():
            actual = hashlib.sha256((UPSTREAM / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

        rtl = (
            UPSTREAM
            / "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v"
        ).read_text(encoding="utf-8")
        for token in (
            "input  [15:0] polarity_in",
            "output reg [3:0]  pol_mask0",
            "output reg [3:0]  pol_mask1",
            "assign overrun = arrival & pending_full;",
        ):
            self.assertIn(token, rtl)
        self.assertNotIn("accept_arrival", rtl)


if __name__ == "__main__":
    unittest.main()
