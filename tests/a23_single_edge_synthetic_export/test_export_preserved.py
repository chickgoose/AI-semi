#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("export_preserved", HERE / "export_preserved.py")
assert spec and spec.loader
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def write(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def manifest_for(payload: dict[str, bytes]) -> dict:
    inventory = []
    for path, data in sorted(payload.items()):
        inventory.append({
            "archive_path": path,
            "source_path": path.removeprefix("payload/"),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return {"schema": exporter.EXPORT_SCHEMA, "status": "PASS", "inventory": inventory}


class ExportTest(unittest.TestCase):
    def test_preserved_root_is_explicit_hold_for_wrong_result(self) -> None:
        root = Path("/tmp/a23-full-single-edge-replay.IdAjj6")
        if not root.is_dir():
            self.skipTest("preserved root is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "should-not-exist.tar.gz"
            status, code = exporter.export(root, archive)
            self.assertEqual(code, 3)
            self.assertEqual(status["status"], "HOLD")
            self.assertFalse(status["archive_emitted"])
            self.assertFalse(archive.exists())
            reasons = {row["code"] for row in status["hold_reasons"]}
            self.assertIn("RESULT_SHA256_MISMATCH", reasons)
            self.assertIn("SOURCE_COMMIT_MISMATCH", reasons)
            self.assertIn("INTEGRATION_COMMIT_MISMATCH", reasons)

    def test_source_scan_rejects_symlink_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root / "real")
            (root / "link").symlink_to("real")
            with self.assertRaisesRegex(exporter.RejectError, "symlink"):
                exporter.scan_regular_tree(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root / "real")
            os.link(root / "real", root / "alias")
            with self.assertRaisesRegex(exporter.RejectError, "hardlinked"):
                exporter.scan_regular_tree(root)

    def test_closed_inventory_distinguishes_missing_and_extra(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root / "wanted")
            write(root / "extra")
            files, directories = exporter.scan_regular_tree(root)
            with self.assertRaisesRegex(exporter.RejectError, "unexpected"):
                exporter.closed_inventory(files, directories, {"wanted", "missing"})
            files.pop("extra")
            missing, scratch = exporter.closed_inventory(files, set(), {"wanted", "missing"})
            self.assertEqual(missing, {"missing"})
            self.assertEqual(scratch, set())

    def test_deterministic_archive_and_round_trip_validation(self) -> None:
        payload = {"payload/a": b"alpha\n", "payload/b": b"beta\n"}
        manifest = manifest_for(payload)
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.tar.gz"
            second = Path(temporary) / "second.tar.gz"
            exporter.deterministic_archive(first, manifest, payload)
            exporter.deterministic_archive(second, manifest, payload)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(exporter.validate_archive(first), manifest)

    def _raw_archive(self, path: Path, members: list[tuple[str, bytes]]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_archive_rejects_duplicate_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary) / "duplicate.tar.gz"
            name = f"{exporter.ARCHIVE_PREFIX}/manifest.json"
            data = exporter.canonical_bytes(manifest_for({}))
            self._raw_archive(duplicate, [(name, data), (name, data)])
            with self.assertRaisesRegex(exporter.RejectError, "duplicate archive"):
                exporter.validate_archive(duplicate)
            escape = Path(temporary) / "escape.tar.gz"
            self._raw_archive(escape, [("../escape", b"bad")])
            with self.assertRaisesRegex(exporter.RejectError, "escapes"):
                exporter.validate_archive(escape)

    def test_archive_rejects_symbolic_and_hard_link_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for kind, member_type in (("symbolic", tarfile.SYMTYPE),
                                      ("hard", tarfile.LNKTYPE)):
                path = Path(temporary) / f"{kind}.tar.gz"
                with tarfile.open(path, "w:gz") as archive:
                    info = tarfile.TarInfo(f"{exporter.ARCHIVE_PREFIX}/link")
                    info.type = member_type
                    info.linkname = "target"
                    archive.addfile(info)
                with self.assertRaisesRegex(exporter.RejectError, "not a regular file"):
                    exporter.validate_archive(path)

    def test_archive_rejects_missing_extra_hash_and_size(self) -> None:
        prefix = exporter.ARCHIVE_PREFIX
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {"payload/a": b"alpha"}
            base = manifest_for(payload)
            manifest_data = exporter.canonical_bytes(base)

            missing = root / "missing.tar.gz"
            self._raw_archive(missing, [(f"{prefix}/manifest.json", manifest_data)])
            with self.assertRaisesRegex(exporter.RejectError, "closure differs"):
                exporter.validate_archive(missing)

            extra = root / "extra.tar.gz"
            self._raw_archive(extra, [(f"{prefix}/manifest.json", manifest_data),
                                      (f"{prefix}/payload/a", b"alpha"),
                                      (f"{prefix}/payload/extra", b"x")])
            with self.assertRaisesRegex(exporter.RejectError, "closure differs"):
                exporter.validate_archive(extra)

            wrong_hash = json.loads(json.dumps(base))
            wrong_hash["inventory"][0]["sha256"] = "0" * 64
            hashed = root / "hash.tar.gz"
            self._raw_archive(hashed, [
                (f"{prefix}/manifest.json", exporter.canonical_bytes(wrong_hash)),
                (f"{prefix}/payload/a", b"alpha"),
            ])
            with self.assertRaisesRegex(exporter.RejectError, "hash/size"):
                exporter.validate_archive(hashed)

            wrong_size = json.loads(json.dumps(base))
            wrong_size["inventory"][0]["size_bytes"] = 6
            sized = root / "size.tar.gz"
            self._raw_archive(sized, [
                (f"{prefix}/manifest.json", exporter.canonical_bytes(wrong_size)),
                (f"{prefix}/payload/a", b"alpha"),
            ])
            with self.assertRaisesRegex(exporter.RejectError, "hash/size"):
                exporter.validate_archive(sized)


if __name__ == "__main__":
    unittest.main()
