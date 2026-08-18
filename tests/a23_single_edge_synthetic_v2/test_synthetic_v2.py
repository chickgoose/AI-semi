#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("run_v2", HERE / "run_v2.py")
assert spec and spec.loader
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


def semantic_fixture() -> dict:
    mutations = []
    for index in range(8):
        mutations.append({
            "owner": "a2" if index < 4 else "a3",
            "mutation": ("drop", "duplicate", "reorder", "reset_escape")[index % 4],
            "build_log_sha256": f"build-{index}",
            "simulation_log_sha256": f"simulation-{index}",
        })
    owners = {}
    for owner in ("a2", "a3"):
        owners[owner] = {
            "baseline_build_log_sha256": f"baseline-{owner}",
            "mutation_activation": {"simulation_log_sha256": f"activation-{owner}"},
            "reset": {"simulation_log_sha256": f"reset-{owner}"},
            "semantic": owner,
        }
    return {"owners": owners, "mutations": mutations, "package": "fixed"}


class SyntheticV2Test(unittest.TestCase):
    def test_semantic_digest_excludes_exactly_enumerated_log_fields(self) -> None:
        left = semantic_fixture()
        right = copy.deepcopy(left)
        for pointer in v2.EPHEMERAL_LOG_POINTERS:
            parts = v2.decode_pointer(pointer)
            current = right
            for part in parts[:-1]:
                current = current[int(part)] if isinstance(current, list) else current[part]
            final = parts[-1]
            if isinstance(current, list):
                current[int(final)] = "changed"
            else:
                current[final] = "changed"
        self.assertEqual(v2.semantic_digest(left), v2.semantic_digest(right))
        self.assertEqual(v2.difference_pointers(left, right), set(v2.EPHEMERAL_LOG_POINTERS))
        right["package"] = "changed"
        self.assertNotEqual(v2.semantic_digest(left), v2.semantic_digest(right))

    def test_semantic_digest_rejects_missing_exclusion_field(self) -> None:
        fixture = semantic_fixture()
        del fixture["owners"]["a2"]["baseline_build_log_sha256"]
        with self.assertRaisesRegex(v2.V2Error, "missing semantic exclusion"):
            v2.semantic_digest(fixture)

    def test_deterministic_archive_round_trip(self) -> None:
        payload = {"payload/a": b"alpha\n", "payload/b": b"beta\n"}
        roles = {name: "test" for name in payload}
        rows = v2.inventory(payload, roles)
        manifest = {
            "schema": v2.EXPORT_SCHEMA, "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows, "inventory_entry_count": 2,
            "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        }
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.tar.gz"
            second = Path(temporary) / "second.tar.gz"
            v2.write_archive(first, manifest, payload)
            v2.write_archive(second, manifest, payload)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            reopened, contents = v2.read_archive(first)
            self.assertEqual(reopened, manifest)
            self.assertEqual(contents, payload)

    def _raw_archive(self, path: Path, members: list[tarfile.TarInfo], data: bytes = b"x") -> None:
        with tarfile.open(path, "w:gz") as archive:
            for member in members:
                if member.isfile():
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    archive.addfile(member)

    def test_archive_rejects_path_escape_duplicate_links_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            escape = tarfile.TarInfo("../escape")
            self._raw_archive(root / "escape.tgz", [escape])
            with self.assertRaisesRegex(Exception, "escapes"):
                v2.read_archive(root / "escape.tgz")

            name = f"{v2.ARCHIVE_PREFIX}/same"
            duplicate_members = [tarfile.TarInfo(name), tarfile.TarInfo(name)]
            for member in duplicate_members:
                member.mode = 0o444
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = ""
            self._raw_archive(root / "duplicate.tgz", duplicate_members)
            with self.assertRaisesRegex(v2.V2Error, "duplicate"):
                v2.read_archive(root / "duplicate.tgz")

            for label, kind in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)):
                member = tarfile.TarInfo(f"{v2.ARCHIVE_PREFIX}/{label}")
                member.type = kind
                member.linkname = "target"
                self._raw_archive(root / f"{label}.tgz", [member])
                with self.assertRaisesRegex(v2.V2Error, "regular file"):
                    v2.read_archive(root / f"{label}.tgz")

            unsafe = tarfile.TarInfo(f"{v2.ARCHIVE_PREFIX}/unsafe")
            unsafe.mode = 0o644
            self._raw_archive(root / "unsafe.tgz", [unsafe])
            with self.assertRaisesRegex(v2.V2Error, "metadata"):
                v2.read_archive(root / "unsafe.tgz")

    def test_archive_rejects_missing_extra_hash_and_size(self) -> None:
        payload = {"payload/a": b"alpha"}
        rows = v2.inventory(payload, {"payload/a": "test"})
        base = {
            "schema": v2.EXPORT_SCHEMA, "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows, "inventory_entry_count": len(rows),
            "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, manifest, actual_payload, pattern in (
                ("missing", base, {}, "not closed"),
                ("extra", base, {**payload, "payload/extra": b"x"}, "not closed"),
                ("hash", {**base, "inventory": [{**rows[0], "sha256": "0" * 64}]},
                 payload, "hash/size"),
                ("size", {**base, "inventory": [{**rows[0], "size_bytes": 99}],
                          "inventory_size_bytes": 99},
                 payload, "hash/size"),
            ):
                archive = root / f"{label}.tgz"
                v2.write_archive(archive, manifest, actual_payload)
                with self.assertRaisesRegex(v2.V2Error, pattern):
                    v2.read_archive(archive)

    def test_archive_rejects_derived_counter_drift(self) -> None:
        payload = {"payload/a": b"alpha"}
        rows = v2.inventory(payload, {"payload/a": "test"})
        manifest = {
            "schema": v2.EXPORT_SCHEMA,
            "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows,
            "inventory_entry_count": 999,
            "inventory_size_bytes": 999,
        }
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "counter-drift.tgz"
            v2.write_archive(archive, manifest, payload)
            with self.assertRaisesRegex(v2.V2Error, "counter differs"):
                v2.read_archive(archive)
        valid_manifest = {**manifest, "inventory_entry_count": 1,
                          "inventory_size_bytes": 5}
        with self.assertRaisesRegex(v2.V2Error, "publication inventory counter"):
            v2.validate_publication_inventory_counter(
                valid_manifest, {"export_inventory_entry_count": 999}
            )

    def test_ordinal_contract_has_explicit_global_fields(self) -> None:
        tb = (HERE / "a23_synthetic_v2_ordinal_tb.sv").read_text(encoding="utf-8")
        self.assertIn("accept_ordinal", tb)
        self.assertIn("retire_ordinal", tb)
        self.assertIn("accept_ordinal_next", tb)
        self.assertIn("retire_ordinal_next", tb)

    def test_committed_publication_reopens_when_present(self) -> None:
        result = HERE / "synthetic_v2_result.json"
        archive = HERE / "synthetic_v2_export.tar.gz"
        publication = HERE / "synthetic_v2_publication.json"
        if not all(path.is_file() for path in (result, archive, publication)):
            self.skipTest("generated v2 publication is not committed yet")
        report = v2.validate_reopened(archive, result, publication)
        self.assertEqual(report["status"], v2.STATUS)


if __name__ == "__main__":
    unittest.main()
