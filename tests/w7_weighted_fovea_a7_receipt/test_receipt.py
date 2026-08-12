#!/usr/bin/env python3
"""Attack-oriented tests for the standalone Weighted-Fovea+A7 receipt."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("gate", ROOT / "scripts/w7_weighted_fovea_a7_receipt.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArchiveAttacks(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make(self, *, bad_hash=False, traversal=False, symlink=False, duplicate=False):
        path = self.root / "evidence.tar.gz"
        blobs = {"results/fovea/a.log": b"A", "results/cluster2/b.log": b"B"}
        prefix = "/tmp/pinned/root/"
        index = b"".join(f"{digest(data if not bad_hash else b'wrong')}  {prefix}{name}\n".encode()
                         for name, data in blobs.items())
        provenance = b"snapshot_head=abc\nbinding_reset_quiet_arming_patch=workspace-diff\nTOOL: xrun test\n"
        with tarfile.open(path, "w:gz") as tf:
            for name, data in {"root/index": index, "root/provenance": provenance,
                               "root/results/fovea/a.log": b"A",
                               "root/results/cluster2/b.log": b"B"}.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            if traversal:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                tf.addfile(info, io.BytesIO(b"X"))
            if symlink:
                info = tarfile.TarInfo("root/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            if duplicate:
                info = tarfile.TarInfo("root/index")
                info.size = len(index)
                tf.addfile(info, io.BytesIO(index))
        contract = {
            "basename": path.name, "sha256": gate.sha_file(path), "size": path.stat().st_size,
            "root": "root", "member_count": len(tarfile.open(path).getmembers()),
            "index": "root/index", "index_sha256": digest(index), "indexed_count": 2,
            "absolute_prefix": prefix, "groups": {"fovea": 1, "cluster2": 1},
            "provenance": {"path": "root/provenance", "sha256": digest(provenance),
                           "snapshot_head": "abc", "binding": "workspace-diff", "tool_version": "xrun test"},
            "supplemental": [["root/results/fovea/a.log", digest(b"A")]]
        }
        return path, contract

    def test_valid_archive_rehashes_indexed_members(self):
        path, contract = self.make()
        self.assertEqual(gate.audit_xcelium_archive(path, contract)["status"], "BOUND_DIAGNOSTIC_HOLD")

    def test_index_hash_lie_rejected(self):
        path, contract = self.make(bad_hash=True)
        with self.assertRaises(gate.GateError):
            gate.audit_xcelium_archive(path, contract)

    def test_traversal_rejected(self):
        path, contract = self.make(traversal=True)
        with self.assertRaises(gate.GateError):
            gate.audit_xcelium_archive(path, contract)

    def test_symlink_rejected(self):
        path, contract = self.make(symlink=True)
        with self.assertRaises(gate.GateError):
            gate.audit_xcelium_archive(path, contract)

    def test_duplicate_member_rejected(self):
        path, contract = self.make(duplicate=True)
        with self.assertRaises(gate.GateError):
            gate.audit_xcelium_archive(path, contract)

    def test_outer_archive_hash_is_pinned(self):
        path, contract = self.make()
        contract["sha256"] = "0" * 64
        with self.assertRaises(gate.GateError):
            gate.audit_xcelium_archive(path, contract)


class ReceiptAttacks(unittest.TestCase):
    def test_self_declared_physical_pass_rejected(self):
        physical = {"status": "HOLD", "reason": "NO_PHYSICAL_EXECUTION_OR_TRUSTED_PARSER",
                    "genus": gate.empty_stage("genus"), "innovus": gate.empty_stage("innovus")}
        physical["genus"]["status"] = "PASS"
        physical["genus"]["raw_reports"]["area"] = {"path": "fake.rpt", "sha256": "0" * 64}
        with self.assertRaises(gate.GateError):
            gate.validate_physical(physical)

    def test_innovus_report_without_trusted_parser_rejected(self):
        physical = {"status": "HOLD", "reason": "NO_PHYSICAL_EXECUTION_OR_TRUSTED_PARSER",
                    "genus": gate.empty_stage("genus"), "innovus": gate.empty_stage("innovus")}
        physical["innovus"]["raw_reports"]["drc"] = {"path": "drc.rpt", "sha256": "1" * 64}
        with self.assertRaises(gate.GateError):
            gate.validate_physical(physical)

    def test_no_overwrite_publish(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "existing"
            out.mkdir()
            marker = out / "owned"
            marker.write_text("preserve")
            with self.assertRaises(gate.GateError):
                gate.publish(out, {"x": 1})
            self.assertEqual(marker.read_text(), "preserve")

    def test_profile_rejects_stale_source_hash(self):
        profile = json.loads((ROOT / "submission/w7_weighted_fovea_a7/profile.json").read_text())
        blobs = {}
        for rows in (profile["source_closure"], profile["verification_closure"],
                     profile["common_boundary"]["files"]):
            for path, expected in rows:
                blobs[(profile["integration_base"], path)] = bytes.fromhex(expected)
                blobs[("new-head", path)] = bytes.fromhex(expected)
        victim = profile["source_closure"][0][0]
        blobs[("new-head", victim)] = b"clean committed attacker replacement"

        class FakeGit:
            def blob(self, commit, path):
                return blobs[(commit, path)]

        # Re-pin the fixture hashes to the controlled bytes, then alter only HEAD.
        for rows in (profile["source_closure"], profile["verification_closure"],
                     profile["common_boundary"]["files"]):
            for row in rows:
                row[1] = gate.sha_bytes(blobs[(profile["integration_base"], row[0])])
        with self.assertRaises(gate.GateError):
            gate.verify_pinned_closures(FakeGit(), profile["integration_base"], "new-head", profile)


if __name__ == "__main__":
    unittest.main()
