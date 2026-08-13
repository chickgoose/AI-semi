from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "physical/k2_w2_release_gate/release_gate.py"
SPEC = importlib.util.spec_from_file_location("k2_w2_release_gate", GATE_PATH)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReleaseBundle:
    KEY_ID = "w2-test-release-key"
    SECRET = bytes.fromhex("42" * 32)

    def __init__(self, root: Path):
        self.root = root
        self.release_id = "k2-final-ranking-20260813"
        self.campaign = {
            "campaign_id": "k2-w2-production-20260813",
            "generation": 7,
            "nonce": "ab" * 32,
            "cohort_id": "complete_endpoint_wrappers",
            "candidate_ids": ["a2_p6", "a3_p6", "fovea_a7"],
            "candidate_commits": {
                "a2_p6": "22" * 20,
                "a3_p6": "33" * 20,
                "fovea_a7": "77" * 20,
            },
            "provenance": {
                "liberty": {"library_set_id": "slow_vdd1v0", "sha256": "11" * 32},
                "pvt": {
                    "process": "slow", "voltage_v": "1.0", "temperature_c": "125",
                    "operating_condition": "slow_vdd1v0_125c",
                },
                "sdc": {"constraint_set_id": "k2_w2_multiclock_v1", "sha256": "12" * 32},
                "load": {
                    "model_id": "logical_link_load_v1", "sha256": "13" * 32,
                    "output_load_pf": "0.010",
                },
                "workload": {
                    "suite_id": "aer-clean-v4-full50-cap22",
                    "generator_version": 4, "full_run_count": 50,
                    "capacity_run_count": 22,
                    "full_manifest_sha256": "14" * 32,
                    "capacity_manifest_sha256": "15" * 32,
                    "trace_bundle_sha256": "16" * 32,
                },
            },
        }
        self.receipts: dict[str, dict] = {}
        self.receipt_paths: dict[str, Path] = {}
        self.manifest_path = root / "release-manifest.json"
        self.keyring_path = root / "release-keyring.json"
        self._build()

    def candidate_results(self) -> dict[str, dict[str, str]]:
        return {candidate: {"status": "PASS"}
                for candidate in self.campaign["candidate_ids"]}

    def base(self, role: str) -> dict:
        return {
            "schema": GATE.ROLE_SCHEMAS[role],
            "role": role,
            "receipt_id": f"{role}-receipt-generation7",
            "status": "PASS",
            "release_binding": copy.deepcopy(self.campaign),
            "candidate_results": self.candidate_results(),
        }

    def sweep(self, offset: int) -> dict:
        return {
            "status": "MONOTONIC_QUALIFIED",
            "points": [
                {"period_ns": "0.8", "wns_ns": str(-0.10 - offset / 1000),
                 "qualified": False},
                {"period_ns": "1.0", "wns_ns": str(0.02 + offset / 1000),
                 "qualified": True},
                {"period_ns": "1.2", "wns_ns": str(0.08 + offset / 1000),
                 "qualified": True},
            ],
            "qualified_bracket": {
                "last_fail_period_ns": "0.8", "first_pass_period_ns": "1.0"},
            "selected_period_ns": "1.0",
            "cherry_pick_forbidden": True,
        }

    def _build(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.receipts["genus"] = self.base("genus")
        self.receipts["genus"]["screening_scope"] = "MAPPED_FUNCTIONAL_NO_RANKING_METRICS"

        self.receipts["innovus"] = self.base("innovus")
        self.receipts["innovus"]["frequency_sweeps"] = {
            candidate: self.sweep(index)
            for index, candidate in enumerate(self.campaign["candidate_ids"])
        }

        self.receipts["activity_power"] = self.base("activity_power")
        self.receipts["activity_power"]["activity"] = {
            "mode": "SAIF", "saif_sha256": "21" * 32,
            "scope_sha256": "23" * 32, "window_sha256": "24" * 32,
            "coverage_percent": 99.5,
            "authentication": {
                "method": "BOUNDARY_HMAC_SHA256", "boundary_role": "boundary",
                "scope": "ENTIRE_ACTIVITY_POWER_RECEIPT_SHA256",
            },
        }

        self.receipts["functional_loss"] = self.base("functional_loss")
        self.receipts["functional_loss"]["claim_boundary"] = {
            "loss_accounting": "GO", "accepted_event_conservation": "GO",
            "official_common_receipt": "GO", "workspace_diff": False,
            "ppa_usage": "FORBIDDEN",
        }

        for role in GATE.METRIC_ROLES:
            path = self.root / "receipts" / f"{role}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical(self.receipts[role]))
            self.receipt_paths[role] = path
        self._write_boundary()
        self._write_keyring()
        self._write_manifest()

    def _metric_hashes(self) -> dict[str, str]:
        return {role: digest(self.receipt_paths[role].read_bytes())
                for role in GATE.METRIC_ROLES}

    def _write_boundary(self, *, resign: bool = True) -> None:
        boundary = self.base("boundary")
        payload = {
            "schema": GATE.BOUNDARY_ATTESTATION_SCHEMA,
            "release_id": self.release_id,
            "campaign": copy.deepcopy(self.campaign),
            "receipt_sha256": self._metric_hashes(),
            "boundary_body_sha256": digest(canonical(boundary)),
        }
        mac = hmac.new(self.SECRET, canonical(payload), hashlib.sha256).hexdigest()
        boundary["attestation"] = {
            "algorithm": "hmac-sha256", "key_id": self.KEY_ID,
            "payload": payload, "mac_sha256": mac if resign else "00" * 32,
        }
        self.receipts["boundary"] = boundary
        path = self.root / "receipts" / "boundary.json"
        path.write_bytes(canonical(boundary))
        self.receipt_paths["boundary"] = path

    def _write_keyring(self) -> None:
        self.keyring_path.write_bytes(canonical({
            "schema": GATE.KEYRING_SCHEMA,
            "keys": {self.KEY_ID: {
                "algorithm": "hmac-sha256", "secret_hex": self.SECRET.hex()}},
        }))

    def _write_manifest(self) -> None:
        references = []
        for role in GATE.ROLES:
            path = self.receipt_paths[role]
            references.append({
                "role": role,
                "path": path.relative_to(self.root).as_posix(),
                "sha256": digest(path.read_bytes()),
            })
        self.manifest_path.write_bytes(canonical({
            "schema": GATE.MANIFEST_SCHEMA,
            "release_id": self.release_id,
            "campaign": copy.deepcopy(self.campaign),
            "receipts": references,
        }))

    def rewrite(self, role: str, *, resign_boundary: bool = True,
                rewrite_manifest: bool = True) -> None:
        self.receipt_paths[role].write_bytes(canonical(self.receipts[role]))
        if role != "boundary" and resign_boundary:
            self._write_boundary()
        if rewrite_manifest:
            self._write_manifest()

    def mutate_campaign_everywhere(self, transform) -> None:
        transform(self.campaign)
        for role in GATE.METRIC_ROLES:
            self.receipts[role]["release_binding"] = copy.deepcopy(self.campaign)
            self.receipt_paths[role].write_bytes(canonical(self.receipts[role]))
        self._write_boundary()
        self._write_manifest()


class ReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="k2-w2-release-")
        self.root = Path(self.temp.name)
        self.bundle = ReleaseBundle(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def gate(self) -> dict:
        return GATE.load_and_gate(
            self.root, self.bundle.manifest_path, self.bundle.keyring_path,
            digest(self.bundle.keyring_path.read_bytes()))

    def reject(self, fragment: str) -> None:
        with self.assertRaisesRegex(GATE.ReleaseGateError, fragment):
            self.gate()

    def cli_command(self, output: Path) -> list[str]:
        return [
            sys.executable, str(GATE_PATH), "--bundle-root", str(self.root),
            "--manifest", str(self.bundle.manifest_path),
            "--keyring", str(self.bundle.keyring_path),
            "--keyring-sha256", digest(self.bundle.keyring_path.read_bytes()),
            "--output", str(output),
        ]

    def test_valid_bundle_only_permits_ranking_without_metrics(self) -> None:
        result = self.gate()
        self.assertEqual(result["status"], "RANKING_PERMITTED")
        self.assertEqual(result["decision"], {
            "final_ranking": "PERMITTED_NOT_COMPUTED",
            "metric_copy_or_fabrication": "NONE",
            "raw_report_reparsing": "NONE",
        })
        serialized = canonical(result)
        for forbidden in (b"wns_ns", b"power_mw", b"loss_rate", b"ranking_order"):
            self.assertNotIn(forbidden, serialized)

    def test_output_is_byte_reproducible(self) -> None:
        self.assertEqual(canonical(self.gate()), canonical(self.gate()))

    def test_missing_and_duplicate_role_fail_closed(self) -> None:
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["receipts"] = manifest["receipts"][:-1]
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("receipt role inventory mismatch")

        self.bundle._write_manifest()
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["receipts"][-1] = copy.deepcopy(manifest["receipts"][0])
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("duplicate receipt role")

    def test_duplicate_id_path_and_hash_fail_closed(self) -> None:
        self.bundle.receipts["innovus"]["receipt_id"] = self.bundle.receipts["genus"]["receipt_id"]
        self.bundle.rewrite("innovus")
        self.reject("duplicate receipt_id")

        self.setUp_rebuild()
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["receipts"][1]["path"] = manifest["receipts"][0]["path"]
        manifest["receipts"][1]["sha256"] = manifest["receipts"][0]["sha256"]
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("duplicate receipt path")

        self.setUp_rebuild()
        genus_data = self.bundle.receipt_paths["genus"].read_bytes()
        self.bundle.receipt_paths["innovus"].write_bytes(genus_data)
        manifest = json.loads(self.bundle.manifest_path.read_text())
        for row in manifest["receipts"]:
            if row["role"] == "innovus":
                row["sha256"] = digest(genus_data)
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("duplicate receipt SHA256")

    def test_cross_cohort_and_candidate_identity_fail_even_when_resigned(self) -> None:
        self.bundle.receipts["genus"]["release_binding"]["cohort_id"] = "raw_core_only"
        self.bundle.rewrite("genus")
        self.reject("cross-cohort")

        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["candidate_results"].pop("a3_p6")
        self.bundle.rewrite("activity_power")
        self.reject("candidate_results must exactly match")

    def test_stale_generation_nonce_and_workload_fail_even_when_all_resigned(self) -> None:
        self.bundle.mutate_campaign_everywhere(
            lambda campaign: campaign.__setitem__("generation", 6))
        # The manifest is the frozen expected generation, so make only receipts stale.
        manifest = json.loads(self.bundle.manifest_path.read_text())
        manifest["campaign"]["generation"] = 7
        self.bundle.manifest_path.write_bytes(canonical(manifest))
        self.reject("stale or belongs")

        self.setUp_rebuild()
        self.bundle.mutate_campaign_everywhere(
            lambda campaign: campaign["provenance"]["workload"].__setitem__(
                "full_run_count", 48))
        self.reject("must equal 50")

    def test_liberty_pvt_sdc_load_and_workload_cross_provenance_rejected(self) -> None:
        mutations = [
            lambda p: p["liberty"].__setitem__("sha256", "31" * 32),
            lambda p: p["pvt"].__setitem__("voltage_v", "0.9"),
            lambda p: p["sdc"].__setitem__("sha256", "32" * 32),
            lambda p: p["load"].__setitem__("sha256", "33" * 32),
            lambda p: p["workload"].__setitem__("trace_bundle_sha256", "34" * 32),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.setUp_rebuild()
                mutation(self.bundle.receipts["innovus"]["release_binding"]["provenance"])
                self.bundle.rewrite("innovus")
                self.reject("stale or belongs")

    def test_non_monotonic_fmax_reversion_and_cherry_pick_rejected(self) -> None:
        sweep = self.bundle.receipts["innovus"]["frequency_sweeps"]["a3_p6"]
        sweep["points"][1]["wns_ns"] = "0.020"
        sweep["points"][2]["wns_ns"] = "0.010"
        self.bundle.rewrite("innovus")
        self.reject("non-monotonic Fmax slack")

        self.setUp_rebuild()
        sweep = self.bundle.receipts["innovus"]["frequency_sweeps"]["a3_p6"]
        sweep["points"][2] = {"period_ns": "1.2", "wns_ns": "-0.01", "qualified": False}
        self.bundle.rewrite("innovus")
        self.reject("non-monotonic Fmax slack|pass-to-fail")

        self.setUp_rebuild()
        sweep = self.bundle.receipts["innovus"]["frequency_sweeps"]["a3_p6"]
        sweep["selected_period_ns"] = "1.2"
        self.bundle.rewrite("innovus")
        self.reject("cherry-picked")

    def test_raw_non_monotonic_hold_status_rejected(self) -> None:
        sweep = self.bundle.receipts["innovus"]["frequency_sweeps"]["fovea_a7"]
        sweep["status"] = "NON_MONOTONIC_HOLD"
        sweep["qualified_bracket"] = None
        sweep["selected_period_ns"] = None
        self.bundle.rewrite("innovus")
        self.reject("not a monotonic qualified sweep")

    def test_unauthenticated_or_vectorless_power_rejected(self) -> None:
        activity = self.bundle.receipts["activity_power"]["activity"]
        activity["authentication"]["method"] = "SELF_ASSERTED"
        self.bundle.rewrite("activity_power")
        self.reject("unauthenticated")

        self.setUp_rebuild()
        self.bundle.receipts["activity_power"]["activity"]["mode"] = "VECTORLESS"
        self.bundle.rewrite("activity_power")
        self.reject("must be SAIF")

    def test_power_byte_change_cannot_be_authorized_by_manifest_rehash_only(self) -> None:
        self.bundle.receipts["activity_power"]["activity"]["coverage_percent"] = 98.0
        # Rewrite power and manifest but intentionally retain the old signed boundary payload.
        self.bundle.rewrite("activity_power", resign_boundary=False)
        self.reject("does not bind every metric receipt")

    def test_forged_boundary_or_untrusted_key_rejected(self) -> None:
        boundary = self.bundle.receipts["boundary"]
        boundary["attestation"]["mac_sha256"] = "00" * 32
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("MAC mismatch")

        self.setUp_rebuild()
        boundary = self.bundle.receipts["boundary"]
        boundary["attestation"]["key_id"] = "unknown-key"
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("not trusted")

        self.setUp_rebuild()
        self.bundle.receipts["boundary"]["status"] = "HOLD_WAS_TAMPERED_TO_PASS"
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("status is not PASS")

        self.setUp_rebuild()
        self.bundle.receipts["boundary"]["candidate_results"]["a2_p6"]["status"] = "FAIL"
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("is not PASS")

        self.setUp_rebuild()
        self.bundle.receipts["boundary"]["receipt_id"] = "boundary-valid-looking-substitute"
        self.bundle.rewrite("boundary", resign_boundary=False)
        self.reject("does not bind the boundary receipt body")

    def test_keyring_requires_out_of_band_exact_hash(self) -> None:
        with self.assertRaisesRegex(GATE.ReleaseGateError, "out-of-band trusted SHA256"):
            GATE.load_and_gate(
                self.root, self.bundle.manifest_path, self.bundle.keyring_path, "00" * 32)

    def test_workspace_diff_loss_only_receipt_is_not_promoted(self) -> None:
        claim = self.bundle.receipts["functional_loss"]["claim_boundary"]
        claim["official_common_receipt"] = "HOLD_WORKSPACE_DIFF_NON_OFFICIAL"
        claim["workspace_diff"] = True
        self.bundle.rewrite("functional_loss")
        self.reject("non-official")

    def test_changed_missing_symlink_and_hash_mismatch_fail_closed(self) -> None:
        path = self.bundle.receipt_paths["genus"]
        path.write_bytes(path.read_bytes() + b" ")
        self.reject("SHA256 mismatch")

        self.setUp_rebuild()
        self.bundle.receipt_paths["genus"].unlink()
        self.reject("cannot read genus receipt")

        self.setUp_rebuild()
        path = self.bundle.receipt_paths["genus"]
        target = self.root / "other.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        self.reject("contains a symlink|non-symlink")

    def test_manifest_outside_bundle_is_rejected(self) -> None:
        outside = Path(self.temp.name).parent / f"{self.root.name}-outside-manifest.json"
        try:
            outside.write_bytes(self.bundle.manifest_path.read_bytes())
            with self.assertRaisesRegex(GATE.ReleaseGateError, "contained by the bundle root"):
                GATE.load_and_gate(
                    self.root, outside, self.bundle.keyring_path,
                    digest(self.bundle.keyring_path.read_bytes()))
        finally:
            outside.unlink(missing_ok=True)

    def test_cli_exit_contract_diagnostic_and_exclusive_output(self) -> None:
        output = self.root / "permit.json"
        command = self.cli_command(output)
        passed = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(output.read_text())["status"], "RANKING_PERMITTED")
        repeated = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(repeated.returncode, 1)
        self.assertEqual(json.loads(output.read_text())["status"], "RANKING_PERMITTED")

        self.setUp_rebuild()
        self.bundle.receipts["innovus"]["status"] = "HOLD"
        self.bundle.rewrite("innovus")
        output = self.root / "hold.json"
        command = self.cli_command(output)
        held = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(held.returncode, 2)
        result = json.loads(output.read_text())
        self.assertEqual(result["status"], "RANKING_HOLD")
        self.assertEqual(result["decision"]["final_ranking"], "FORBIDDEN")
        self.assertIn("innovus receipt status is not PASS", result["diagnostic"])

    def setUp_rebuild(self) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="k2-w2-release-")
        self.root = Path(self.temp.name)
        self.bundle = ReleaseBundle(self.root)


if __name__ == "__main__":
    unittest.main()
