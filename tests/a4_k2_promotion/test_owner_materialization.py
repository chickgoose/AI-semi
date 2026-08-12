from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location(
    "a4_k2_replay_runner", HERE / "run_promotion_replay.py")
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise AssertionError(f"git {' '.join(arguments)} failed:\n{result.stdout}")
    return result.stdout.strip()


class OwnerObjectMaterializationTest(unittest.TestCase):
    def make_moved_repository(self, root: Path) -> tuple[Path, str, str, bytes]:
        repo = root / "owner"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "A4 Test")
        git(repo, "config", "user.email", "a4-test@example.invalid")
        source = repo / "rtl" / "owner.sv"
        source.parent.mkdir()
        pinned = b"module owner; localparam int PINNED = 1; endmodule\n"
        source.write_bytes(pinned)
        git(repo, "add", "rtl/owner.sv")
        git(repo, "commit", "-q", "-m", "pinned owner")
        pinned_commit = git(repo, "rev-parse", "HEAD")

        source.write_bytes(b"module owner; localparam int MOVED = 2; endmodule\n")
        git(repo, "add", "rtl/owner.sv")
        git(repo, "commit", "-q", "-m", "moved head")
        moved_head = git(repo, "rev-parse", "HEAD")
        source.write_bytes(b"malicious dirty worktree substitution\n")
        return repo, pinned_commit, moved_head, pinned

    def test_moved_head_and_dirty_worktree_cannot_substitute_pinned_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, pinned_commit, moved_head, pinned = self.make_moved_repository(root)
            self.assertNotEqual(pinned_commit, moved_head)
            destination = root / "materialized" / "owner.sv"
            record = runner.materialize_git_source(
                repo.resolve(), pinned_commit, "rtl/owner.sv",
                hashlib.sha256(pinned).hexdigest(), destination, "synthetic-owner")
            self.assertEqual(pinned, destination.read_bytes())
            self.assertNotEqual((repo / "rtl" / "owner.sv").read_bytes(), destination.read_bytes())
            self.assertEqual("exact_git_commit_object", record["source_origin"])
            self.assertEqual(git(repo, "rev-parse", f"{pinned_commit}:rtl/owner.sv"),
                             record["source_blob_oid"])

    def test_wrong_pinned_blob_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, pinned_commit, _, _ = self.make_moved_repository(root)
            destination = root / "materialized" / "owner.sv"
            with self.assertRaisesRegex(runner.ReplayError, "pinned owner blob SHA-256 mismatch"):
                runner.materialize_git_source(
                    repo.resolve(), pinned_commit, "rtl/owner.sv", "0" * 64,
                    destination, "synthetic-owner")
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
