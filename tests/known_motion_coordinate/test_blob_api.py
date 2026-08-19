from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from demos.known_motion_coordinate import (
    KNOWN_MOTION_BLOB_API_ID,
    InputBlob,
    load_intrinsics,
    load_pose_stream,
    open_input_blob,
    parse_intrinsics_blob,
    parse_pose_stream_blob,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "demos" / "known_motion_coordinate" / "fixtures"


class PublicImmutableBlobApiTests(unittest.TestCase):
    def test_public_blob_parsers_equal_compatible_path_apis(self) -> None:
        intrinsics_path = FIXTURES / "intrinsics.json"
        poses_path = FIXTURES / "poses.jsonl"
        path_intrinsics = load_intrinsics(intrinsics_path)
        path_header, path_poses = load_pose_stream(poses_path, path_intrinsics)
        with open_input_blob(intrinsics_path, "intrinsics input") as intrinsics_blob:
            self.assertEqual(
                intrinsics_blob.sha256,
                hashlib.sha256(intrinsics_blob.data).hexdigest(),
            )
            blob_intrinsics = parse_intrinsics_blob(intrinsics_blob)
            with open_input_blob(poses_path, "pose input") as pose_blob:
                blob_header, blob_poses = parse_pose_stream_blob(
                    pose_blob, blob_intrinsics
                )
        self.assertEqual(blob_intrinsics, path_intrinsics)
        self.assertEqual(blob_header, path_header)
        self.assertEqual(blob_poses, path_poses)

    def test_blob_digest_is_derived_and_blob_is_immutable(self) -> None:
        data = b"immutable parser bytes"
        blob = InputBlob(Path("synthetic.bin"), data, 7, 11, len(data))
        self.assertEqual(blob.sha256, hashlib.sha256(data).hexdigest())
        with self.assertRaises(TypeError):
            InputBlob(
                Path("synthetic.bin"),
                data,
                7,
                11,
                len(data),
                sha256="0" * 64,
            )
        with self.assertRaises(FrozenInstanceError):
            blob.data = b"changed"
        self.assertEqual(KNOWN_MOTION_BLOB_API_ID, "redred.known_motion.input-blob/v1")

    def test_parser_uses_pinned_bytes_after_path_inode_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "intrinsics.json"
            held = base / "intrinsics-a-held.json"
            variant = base / "intrinsics-b.json"
            source.write_bytes((FIXTURES / "intrinsics.json").read_bytes())
            altered = json.loads(source.read_text(encoding="utf-8"))
            altered["fx"] = altered["fx"] + 5
            variant.write_text(json.dumps(altered) + "\n", encoding="utf-8")
            original_bytes = source.read_bytes()
            with open_input_blob(source, "intrinsics input") as blob:
                os.replace(source, held)
                os.replace(variant, source)
                parsed = parse_intrinsics_blob(blob)
                self.assertEqual(blob.data, original_bytes)
                self.assertEqual(blob.sha256, hashlib.sha256(original_bytes).hexdigest())
                self.assertNotEqual(parsed.fx, altered["fx"])
            os.replace(source, variant)
            os.replace(held, source)
            self.assertEqual(parsed, load_intrinsics(source))


if __name__ == "__main__":
    unittest.main(verbosity=2)
