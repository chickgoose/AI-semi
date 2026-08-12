#!/usr/bin/env python3
"""Generate and validate fail-closed A9 W7 submission handoff receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = pathlib.Path("submission/a9_w7/a9_w7_submission_policy.json")
SCHEMA_PATH = pathlib.Path("submission/a9_w7/a9_w7_submission.schema.json")
VALIDATOR_PATH = pathlib.Path("scripts/a9_w7_submission_gate.py")
MANIFEST_NAME = "a9_w7_submission_manifest.json"
WINDOWS_NAME = "WINDOWS_HANDOFF.sha256"
SHA_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class GateError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise GateError(f"not a regular file: {path}")
    return sha_bytes(path.read_bytes())


def strict_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise GateError(f"{label} keys differ: expected={sorted(expected)}")
    return value


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateError(f"JSON root is not an object: {path}")
    return value


class BoundGit:
    def __init__(self, repo: pathlib.Path, policy: dict[str, Any]) -> None:
        item = policy["bootstrap"]["git"]
        self.path = pathlib.Path(item["path"])
        if self.path != pathlib.Path("/usr/bin/git") or self.path.is_symlink():
            raise GateError("trusted Git must be the absolute non-symlink /usr/bin/git")
        if sha_file(self.path) != item["sha256"]:
            raise GateError("trusted Git executable SHA-256 mismatch")
        version = self.run("--version").decode().strip()
        if version != item["version"]:
            raise GateError("trusted Git version mismatch")
        self.repo = repo.resolve()
        if not (self.repo / ".git").exists():
            raise GateError(f"not a Git worktree: {self.repo}")

    def run(self, *args: str) -> bytes:
        command = [str(self.path)]
        if hasattr(self, "repo"):
            command += ["-C", str(self.repo)]
        command += list(args)
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, close_fds=True, check=False)
        if result.returncode:
            raise GateError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    def text(self, *args: str) -> str:
        return self.run(*args).decode().strip()

    def blob(self, commit: str, path: str) -> bytes:
        safe_relative(path)
        return self.run("show", f"{commit}:{path}")


def safe_relative(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or
            str(path) != value or "\\" in value):
        raise GateError(f"unsafe repository-relative path: {value!r}")
    return path


def tracked_policy(git: BoundGit, commit: str) -> tuple[dict[str, Any], dict[str, Any]]:
    policy_bytes = git.blob(commit, str(POLICY_PATH))
    schema_bytes = git.blob(commit, str(SCHEMA_PATH))
    policy = json.loads(policy_bytes)
    schema = json.loads(schema_bytes)
    if policy.get("schema") != "a9-w7-trusted-policy-v1":
        raise GateError("trusted policy schema mismatch")
    if schema.get("$id") != "a9-w7-submission-v1":
        raise GateError("submission schema identity mismatch")
    return policy, schema


def filelist_sources(payload: bytes) -> list[str]:
    rows: list[str] = []
    for raw in payload.decode().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("+"):
            continue
        safe_relative(line)
        rows.append(line)
    return rows


def artifact(commit: str, path: str, payload: bytes) -> dict[str, Any]:
    safe_relative(path)
    return {"path": path, "sha256": sha_bytes(payload), "size": len(payload),
            "git_blob": sha_bytes(b"blob " + str(len(payload)).encode() + b"\0" + payload)}


def profile_document(git: BoundGit, commit: str, key: str,
                     policy: dict[str, Any]) -> dict[str, Any]:
    profiles = policy["profiles"]
    if key not in profiles:
        raise GateError(f"unknown profile: {key}")
    source = profiles[key]
    filelist = source["filelist"]
    filelist_payload = git.blob(commit, filelist)
    expected = source["ordered_sources"]
    if filelist_sources(filelist_payload) != expected:
        raise GateError(f"{key}: filelist differs from ordered source closure")
    closure = [artifact(commit, path, git.blob(commit, path)) for path in expected]
    transitive = [artifact(commit, path, git.blob(commit, path))
                  for path in source.get("transitive_sources", [])]
    sdc = dict(source["sdc"])
    if sdc["path"] is not None:
        sdc["sha256"] = sha_bytes(git.blob(commit, sdc["path"]))
    return {
        "key": key, "decision": source["decision"], "top": source["top"],
        "parameters": source["parameters"], "defines": source["defines"],
        "filelist": artifact(commit, filelist, filelist_payload),
        "ordered_source_closure": closure, "transitive_source_closure": transitive,
        "tool": {"status": "UNBOUND", "identity": None, "path": None,
                 "sha256": None, "version_sha256": None},
        "library": source["library"], "pvt": source["pvt"], "sdc": sdc,
    }


def selected_handoff_paths(git: BoundGit, commit: str,
                           policy: dict[str, Any]) -> list[str]:
    config = policy["windows_handoff"]
    all_paths = git.run("ls-tree", "-r", "--name-only", commit).decode().splitlines()
    selected = []
    for path in all_paths:
        if path in config["include_exact"] or any(
                path.startswith(prefix) for prefix in config["include_prefixes"]):
            if any(path.startswith(prefix) for prefix in config["excluded_prefixes"]):
                raise GateError(f"excluded path entered Windows inventory: {path}")
            if any(part in {"__pycache__", ".git"} for part in pathlib.PurePosixPath(path).parts):
                raise GateError(f"generated/user path entered Windows inventory: {path}")
            selected.append(path)
    if not selected or len(selected) != len(set(selected)):
        raise GateError("Windows handoff inventory is empty or duplicated")
    required = {str(POLICY_PATH), str(SCHEMA_PATH), str(VALIDATOR_PATH)}
    if not required.issubset(selected):
        raise GateError("Windows handoff omits W7 bootstrap closure")
    return sorted(selected)


def bootstrap_document(git: BoundGit, commit: str, policy: dict[str, Any]) -> dict[str, Any]:
    python = pathlib.Path(os.path.realpath(sys.executable))
    rows = {}
    for name, path in (("validator", str(VALIDATOR_PATH)), ("policy", str(POLICY_PATH)),
                       ("schema", str(SCHEMA_PATH))):
        payload = git.blob(commit, path)
        rows[name] = artifact(commit, path, payload)
    rows["git"] = {**policy["bootstrap"]["git"]}
    rows["python"] = {"path": str(python), "sha256": sha_file(python),
                      "version": sys.version.splitlines()[0]}
    rows["trust_boundary"] = (
        "validator and trusted policy are bootstrap inputs; this receipt does not "
        "claim self-attestation against hostile replacement of both"
    )
    return rows


def build_manifest(git: BoundGit, commit: str, key: str,
                   policy: dict[str, Any]) -> dict[str, Any]:
    tree = git.text("rev-parse", f"{commit}^{{tree}}")
    paths = selected_handoff_paths(git, commit, policy)
    files = [artifact(commit, path, git.blob(commit, path)) for path in paths]
    inventory_payload = windows_inventory_payload(files)
    document: dict[str, Any] = {
        "schema": "a9-w7-submission-v1", "status": "HOLD",
        "binding": {"commit": commit, "tree": tree, "clean_generation": True},
        "bootstrap": bootstrap_document(git, commit, policy),
        "profile": profile_document(git, commit, key, policy),
        "physical_evidence": {
            "status": "ABSENT", "trusted_parser": None,
            "result_artifacts": [], "log_artifacts": [],
            "reason": policy["physical_release"]["reason"],
        },
        "windows_handoff": {
            "destination_root": policy["windows_handoff"]["destination_root"],
            "path_format": "repo-relative-posix",
            "files": files, "file_count": len(files),
            "total_bytes": sum(row["size"] for row in files),
            "inventory_file": WINDOWS_NAME,
            "inventory_sha256": sha_bytes(inventory_payload),
            "excluded_prefixes": policy["windows_handoff"]["excluded_prefixes"],
            "excluded_names": policy["windows_handoff"]["excluded_names"],
        },
    }
    document["receipt"] = {
        "payload_sha256": sha_bytes(canonical(document)),
        "self_hash_design": "HASH_COVERS_DOCUMENT_WITHOUT_RECEIPT_FIELD",
        "overwrite_policy": "NEW_DIRECTORY_AND_O_EXCL_FILES_ONLY",
    }
    return document


def windows_inventory_payload(files: list[dict[str, Any]]) -> bytes:
    lines = [f"{row['sha256']}  {row['path']}" for row in files]
    return ("\n".join(lines) + "\n").encode()


def validate_artifact_rows(rows: Any, evidence_root: pathlib.Path | None,
                           label: str) -> None:
    if not isinstance(rows, list):
        raise GateError(f"{label} artifacts are not a list")
    for row in rows:
        strict_keys(row, {"path", "sha256", "size"}, f"{label} artifact")
        path = safe_relative(row["path"])
        if not SHA_RE.fullmatch(row["sha256"]) or not isinstance(row["size"], int):
            raise GateError(f"invalid {label} artifact identity")
        if evidence_root is None:
            raise GateError(f"{label} artifacts require --evidence-root")
        root = evidence_root.resolve()
        actual = (root / pathlib.Path(*path.parts)).resolve()
        if root not in actual.parents or actual.is_symlink() or not actual.is_file():
            raise GateError(f"unsafe or missing {label} artifact: {path}")
        if actual.stat().st_nlink != 1 or actual.stat().st_size != row["size"]:
            raise GateError(f"shared or size-mismatched {label} artifact: {path}")
        if sha_file(actual) != row["sha256"]:
            raise GateError(f"hash-mismatched {label} artifact: {path}")


def validate_manifest(document: dict[str, Any], git: BoundGit,
                      policy: dict[str, Any], evidence_root: pathlib.Path | None = None) -> None:
    strict_keys(document, {"schema", "status", "binding", "bootstrap", "profile",
                           "physical_evidence", "windows_handoff", "receipt"}, "manifest")
    if document["schema"] != "a9-w7-submission-v1":
        raise GateError("manifest schema mismatch")
    receipt = document["receipt"]
    strict_keys(receipt, {"payload_sha256", "self_hash_design", "overwrite_policy"},
                "receipt")
    payload = {key: value for key, value in document.items() if key != "receipt"}
    if sha_bytes(canonical(payload)) != receipt["payload_sha256"]:
        raise GateError("receipt payload SHA-256 mismatch")
    binding = strict_keys(document["binding"], {"commit", "tree", "clean_generation"},
                          "binding")
    if not COMMIT_RE.fullmatch(binding["commit"]):
        raise GateError("invalid binding commit")
    if git.text("rev-parse", f"{binding['commit']}^{{tree}}") != binding["tree"]:
        raise GateError("binding tree mismatch")
    expected = build_manifest(git, binding["commit"], document["profile"]["key"], policy)
    for key in ("binding", "bootstrap", "profile", "windows_handoff"):
        if document[key] != expected[key]:
            raise GateError(f"manifest {key} differs from bound commit/policy")
    evidence = strict_keys(document["physical_evidence"],
                           {"status", "trusted_parser", "result_artifacts",
                            "log_artifacts", "reason"}, "physical evidence")
    validate_artifact_rows(evidence["result_artifacts"], evidence_root, "result")
    validate_artifact_rows(evidence["log_artifacts"], evidence_root, "log")
    release = policy["physical_release"]
    if not release["enabled"]:
        if (document["status"] != "HOLD" or evidence["status"] != "ABSENT" or
                evidence["trusted_parser"] is not None or evidence["result_artifacts"] or
                evidence["log_artifacts"]):
            raise GateError("physical release is disabled; claims/evidence must remain absent HOLD")
    elif not release["approved_tools"] or not release["trusted_result_parsers"]:
        raise GateError("physical release policy lacks trusted tool/parser closure")


def write_new(path: pathlib.Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def generate(repo: pathlib.Path, profile: str, output: pathlib.Path) -> pathlib.Path:
    worktree_policy = load_json(repo / POLICY_PATH)
    git = BoundGit(repo, worktree_policy)
    if git.run("status", "--porcelain=v1", "--untracked-files=all"):
        raise GateError("generation requires a clean tracked/untracked worktree")
    commit = git.text("rev-parse", "HEAD")
    policy, _ = tracked_policy(git, commit)
    if canonical(policy) != canonical(worktree_policy):
        raise GateError("working policy differs from bound HEAD")
    document = build_manifest(git, commit, profile, policy)
    validate_manifest(document, git, policy)
    if output.exists() or output.is_symlink():
        raise GateError(f"refusing to reuse output path: {output}")
    output.mkdir(mode=0o700, parents=False)
    manifest_path = output / MANIFEST_NAME
    inventory_payload = windows_inventory_payload(document["windows_handoff"]["files"])
    if sha_bytes(inventory_payload) != document["windows_handoff"]["inventory_sha256"]:
        raise GateError("internal Windows inventory SHA-256 mismatch")
    # Publish the receipt manifest last: its presence is the completion marker.
    write_new(output / WINDOWS_NAME, inventory_payload)
    write_new(manifest_path, json.dumps(document, indent=2, sort_keys=True,
                                        ensure_ascii=False).encode() + b"\n")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("generate")
    create.add_argument("--profile", required=True)
    create.add_argument("--output", required=True, type=pathlib.Path)
    check = sub.add_parser("validate")
    check.add_argument("manifest", type=pathlib.Path)
    check.add_argument("--evidence-root", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        policy = load_json(args.repo / POLICY_PATH)
        git = BoundGit(args.repo, policy)
        if args.command == "generate":
            path = generate(args.repo, args.profile, args.output)
            print(f"A9_W7_SUBMISSION_HOLD manifest={path} physical=ABSENT")
        else:
            document = load_json(args.manifest)
            bound_policy, _ = tracked_policy(git, document["binding"]["commit"])
            validate_manifest(document, git, bound_policy, args.evidence_root)
            print("A9_W7_SUBMISSION_VALID status=HOLD physical=ABSENT")
        return 0
    except (GateError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"A9_W7_SUBMISSION_INVALID: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
