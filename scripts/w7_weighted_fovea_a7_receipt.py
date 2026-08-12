#!/usr/bin/env python3
"""Fail-closed standalone receipt for the A1 2a3a3be Weighted-Fovea+A7 profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = pathlib.Path("submission/w7_weighted_fovea_a7/profile.json")
SCHEMA = pathlib.Path("submission/w7_weighted_fovea_a7/receipt.schema.json")
TOOL = pathlib.Path("scripts/w7_weighted_fovea_a7_receipt.py")
FILELIST = pathlib.Path("submission/w7_weighted_fovea_a7/weighted_fovea_a7.f")
OUTPUT = "weighted_fovea_a7_receipt.json"
SHA = re.compile(r"[0-9a-f]{64}")


class GateError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise GateError(f"not a regular file: {path}")
    return sha_bytes(path.read_bytes())


def safe_rel(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
        raise GateError(f"unsafe relative path: {value!r}")
    return path


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateError(f"object required: {path}")
    return value


class Git:
    def __init__(self, repo: pathlib.Path, profile: dict[str, Any]) -> None:
        pin = profile["bootstrap"]["git"]
        self.binary = pathlib.Path(pin["path"])
        if self.binary != pathlib.Path("/usr/bin/git") or self.binary.is_symlink():
            raise GateError("Git must be pinned absolute /usr/bin/git")
        if sha_file(self.binary) != pin["sha256"]:
            raise GateError("Git executable hash mismatch")
        self.repo = repo.resolve()
        if self._run_raw("--version").decode().strip() != pin["version"]:
            raise GateError("Git version mismatch")
        if not (self.repo / ".git").exists():
            raise GateError("repository is not a Git worktree")

    def _run_raw(self, *args: str) -> bytes:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        cmd = [str(self.binary)]
        if hasattr(self, "repo"):
            cmd += ["-C", str(self.repo)]
        result = subprocess.run(cmd + list(args), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, check=False)
        if result.returncode:
            raise GateError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    def text(self, *args: str) -> str:
        return self._run_raw(*args).decode().strip()

    def blob(self, commit: str, path: str) -> bytes:
        safe_rel(path)
        return self._run_raw("show", f"{commit}:{path}")


def rows_as_map(rows: Any, label: str) -> list[tuple[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise GateError(f"{label} must be nonempty")
    answer = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str) or not SHA.fullmatch(str(row[1])):
            raise GateError(f"invalid {label} row")
        safe_rel(row[0])
        answer.append((row[0], row[1]))
    if len({p for p, _ in answer}) != len(answer):
        raise GateError(f"duplicate {label} path")
    return answer


def artifact(git: Git, commit: str, path: str, expected: str | None = None) -> dict[str, Any]:
    data = git.blob(commit, path)
    digest = sha_bytes(data)
    if expected is not None and digest != expected:
        raise GateError(f"pinned blob mismatch: {path}")
    return {"path": path, "sha256": digest, "size": len(data)}


def verify_pinned_closures(git: Git, base: str, head: str,
                           profile: dict[str, Any]) -> None:
    for label, rows in (("source", profile["source_closure"]),
                        ("verification", profile["verification_closure"]),
                        ("common", profile["common_boundary"]["files"])):
        for path, digest in rows_as_map(rows, label):
            if sha_bytes(git.blob(base, path)) != digest or sha_bytes(git.blob(head, path)) != digest:
                raise GateError(f"{label} closure differs at base/current HEAD: {path}")


def verify_repository(repo: pathlib.Path, profile: dict[str, Any]) -> tuple[Git, str, str]:
    git = Git(repo, profile)
    head = git.text("rev-parse", "HEAD")
    base = profile["integration_base"]
    if git.text("merge-base", "--is-ancestor", base, head) != "":
        pass
    if git._run_raw("status", "--porcelain=v1", "--untracked-files=all"):
        raise GateError("generation checkout is dirty or has untracked files")
    verify_pinned_closures(git, base, head, profile)
    expected_sources = [p for p, _ in rows_as_map(profile["source_closure"], "source")]
    filelist = git.blob(head, str(FILELIST)).decode().splitlines()
    actual_sources = [x.strip() for x in filelist if x.strip() and not x.lstrip().startswith("#")]
    if actual_sources != expected_sources:
        raise GateError("ordered synthesis filelist differs from pinned closure")
    for bootstrap in (str(PROFILE), str(SCHEMA), str(TOOL), str(FILELIST)):
        git.blob(head, bootstrap)
    return git, head, git.text("rev-parse", "HEAD^{tree}")


def tar_member_bytes(tf: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str) -> bytes:
    info = members.get(name)
    if info is None or not info.isfile():
        raise GateError(f"missing regular archive member: {name}")
    stream = tf.extractfile(info)
    if stream is None:
        raise GateError(f"unreadable archive member: {name}")
    return stream.read()


def audit_xcelium_archive(path: pathlib.Path, contract: dict[str, Any]) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GateError("Xcelium archive must be an unshared regular file")
    if path.stat().st_size != contract["size"] or sha_file(path) != contract["sha256"]:
        raise GateError("Xcelium archive outer identity mismatch")
    try:
        tf = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise GateError(f"bad Xcelium archive: {exc}") from exc
    with tf:
        infos = tf.getmembers()
        if len(infos) != contract["member_count"]:
            raise GateError("archive member count mismatch")
        members: dict[str, tarfile.TarInfo] = {}
        for info in infos:
            pure = pathlib.PurePosixPath(info.name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in info.name or str(pure) != info.name.rstrip("/"):
                raise GateError(f"unsafe archive member: {info.name}")
            key = info.name.rstrip("/")
            if key in members:
                raise GateError(f"duplicate archive member: {info.name}")
            if info.issym():
                target = pathlib.PurePosixPath(info.linkname)
                resolved = pathlib.PurePosixPath(key).parent.joinpath(target)
                if target.is_absolute() or ".." in target.parts or "\\" in info.linkname:
                    raise GateError(f"unsafe archive symlink: {info.name}")
                if not str(resolved).startswith(f"{contract['root']}/"):
                    raise GateError(f"archive symlink escapes root: {info.name}")
            elif not (info.isfile() or info.isdir()):
                raise GateError(f"special archive member: {info.name}")
            members[key] = info
        for name, info in members.items():
            if info.issym():
                target = str(pathlib.PurePosixPath(name).parent.joinpath(info.linkname))
                if target not in members:
                    raise GateError(f"archive symlink target missing: {name}")
        index_data = tar_member_bytes(tf, members, contract["index"])
        if sha_bytes(index_data) != contract["index_sha256"]:
            raise GateError("artifact index hash mismatch")
        lines = index_data.decode("utf-8").splitlines()
        if len(lines) != contract["indexed_count"]:
            raise GateError("artifact index count mismatch")
        seen: set[str] = set()
        groups = {key: 0 for key in contract["groups"]}
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
            if not match or not match.group(2).startswith(contract["absolute_prefix"]):
                raise GateError("malformed or rebased artifact index row")
            relative = match.group(2)[len(contract["absolute_prefix"]):]
            safe_rel(relative)
            member = f"{contract['root']}/{relative}"
            if member in seen:
                raise GateError("duplicate indexed artifact")
            seen.add(member)
            if sha_bytes(tar_member_bytes(tf, members, member)) != match.group(1):
                raise GateError(f"indexed artifact hash mismatch: {relative}")
            for group in groups:
                if relative.startswith(f"results/{group}/"):
                    groups[group] += 1
                    break
            else:
                raise GateError(f"artifact outside pinned candidate groups: {relative}")
        if groups != contract["groups"]:
            raise GateError("indexed candidate group counts mismatch")
        provenance = contract["provenance"]
        pbytes = tar_member_bytes(tf, members, provenance["path"])
        if sha_bytes(pbytes) != provenance["sha256"]:
            raise GateError("provenance hash mismatch")
        ptext = pbytes.decode("utf-8")
        plines = ptext.splitlines()
        required = [f"snapshot_head={provenance['snapshot_head']}",
                    f"binding_reset_quiet_arming_patch={provenance['binding']}"]
        tool_rows = [" ".join(line.removeprefix("TOOL:").split())
                     for line in plines if line.startswith("TOOL:")]
        if any(item not in plines for item in required) or tool_rows != [provenance["tool_version"]]:
            raise GateError("pinned provenance fields missing")
        supplemental = []
        for name, digest in rows_as_map(contract["supplemental"], "supplemental"):
            data = tar_member_bytes(tf, members, name)
            if sha_bytes(data) != digest:
                raise GateError(f"supplemental member mismatch: {name}")
            supplemental.append({"path": name, "sha256": digest, "size": len(data)})
    return {
        "status": "BOUND_DIAGNOSTIC_HOLD",
        "classification": "FOVEA_VS_CLUSTER2_LOCAL_ARCHIVE_NOT_EXACT_WEIGHTED_FOVEA_A7_NOT_OFFICIAL_COMMON",
        "archive": {"basename": contract["basename"], "sha256": contract["sha256"],
                    "size": contract["size"], "member_count": contract["member_count"]},
        "artifact_index": {"path": contract["index"], "sha256": contract["index_sha256"],
                           "count": contract["indexed_count"], "groups": groups},
        "provenance": provenance,
        "supplemental": supplemental,
        "tool_executable": {"path": None, "sha256": None, "status": "UNBOUND"}
    }


def empty_stage(name: str) -> dict[str, Any]:
    raw = ({"compile_log": None, "tool_log": None, "check_design": None,
            "unresolved": None, "mapped_netlist": None, "emitted_sdc": None,
            "area": None, "setup": None, "hold": None, "unconstrained": None,
            "power": None, "activity": None} if name == "genus" else
           {"import_log": None, "tool_log": None, "floorplan": None,
            "placed_database": None, "routed_database": None, "drc": None,
            "antenna": None, "setup": None, "hold": None,
            "unconstrained": None, "power": None, "activity": None})
    return {
        "status": "ABSENT_HOLD", "tool": {"path": None, "sha256": None, "version": None},
        "technology": {"libraries": [], "lef": [], "qrc": [], "pvt": None},
        "constraints": {"sdc": None, "clocks": [], "io_delays": None, "loads": None},
        "command": {"argv": [], "cwd": None, "environment": {}},
        "raw_reports": raw,
        "trusted_parser": {"path": None, "sha256": None, "receipt": None}
    }


def validate_physical(physical: Any) -> None:
    expected = {"status": "HOLD", "reason": "NO_PHYSICAL_EXECUTION_OR_TRUSTED_PARSER", "genus": empty_stage("genus"), "innovus": empty_stage("innovus")}
    if physical != expected:
        raise GateError("physical evidence is not the exact fail-closed ABSENT template")


def build(repo: pathlib.Path, archive: pathlib.Path) -> dict[str, Any]:
    profile = load_json(repo / PROFILE)
    if profile.get("schema") != "w7-weighted-fovea-a7-profile-v1":
        raise GateError("profile schema mismatch")
    git, head, tree = verify_repository(repo, profile)
    closures = {}
    for label, rows in (("synthesis", profile["source_closure"]),
                        ("verification", profile["verification_closure"]),
                        ("common_boundary", profile["common_boundary"]["files"])):
        closures[label] = [artifact(git, head, path, digest) for path, digest in rows_as_map(rows, label)]
    bootstrap = [artifact(git, head, p) for p in (str(PROFILE), str(SCHEMA), str(TOOL), str(FILELIST))]
    document: dict[str, Any] = {
        "schema": "w7-weighted-fovea-a7-receipt-v1", "status": "HOLD",
        "binding": {"integration_base": profile["integration_base"], "receipt_commit": head,
                    "receipt_tree": tree, "clean": True, "bootstrap": bootstrap,
                    "python": {"path": os.path.realpath(sys.executable), "sha256": sha_file(pathlib.Path(os.path.realpath(sys.executable))), "version": sys.version.splitlines()[0]},
                    "git": profile["bootstrap"]["git"]},
        "profile": {"design": profile["design"], "owner_lineage": profile["owner_lineage"],
                    "closures": closures, "common_claim": profile["common_boundary"]["claim"],
                    "common_counts": profile["common_boundary"]["expected_counts"]},
        "xcelium": audit_xcelium_archive(archive, profile["xcelium_archive"]),
        "physical": {"status": "HOLD", "reason": "NO_PHYSICAL_EXECUTION_OR_TRUSTED_PARSER",
                     "genus": empty_stage("genus"), "innovus": empty_stage("innovus")},
    }
    document["receipt"] = {"payload_sha256": sha_bytes(canonical(document)),
                           "coverage": "DOCUMENT_WITHOUT_RECEIPT_FIELD",
                           "publish": "ATOMIC_NEW_DIRECTORY_PLUS_SAME_FS_LINK_NO_REPLACE"}
    return document


def validate(repo: pathlib.Path, archive: pathlib.Path, receipt_path: pathlib.Path) -> dict[str, Any]:
    actual = load_json(receipt_path)
    expected = build(repo, archive)
    validate_physical(actual.get("physical"))
    if actual != expected:
        raise GateError("receipt differs from regenerated trusted document")
    payload = dict(actual)
    receipt = payload.pop("receipt")
    if receipt["payload_sha256"] != sha_bytes(canonical(payload)):
        raise GateError("receipt payload hash mismatch")
    return actual


def publish(directory: pathlib.Path, document: dict[str, Any]) -> pathlib.Path:
    parent = directory.parent.resolve()
    if directory.exists() or directory.is_symlink():
        raise GateError("output path already exists (no overwrite)")
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{directory.name}.", dir=parent)
    temp = pathlib.Path(temp_name)
    created_directory = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory.mkdir(mode=0o700)
        created_directory = True
        os.link(temp, directory / OUTPUT, follow_symlinks=False)
        temp.unlink()
    except Exception:
        try:
            if temp.exists():
                temp.unlink()
            if created_directory:
                directory.rmdir()
        except OSError:
            pass
        raise
    return directory / OUTPUT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "validate", "audit-archive"))
    parser.add_argument("--repo", type=pathlib.Path, default=ROOT)
    parser.add_argument("--xcelium-archive", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--receipt", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.command == "audit-archive":
            profile = load_json(args.repo / PROFILE)
            print(json.dumps(audit_xcelium_archive(args.xcelium_archive, profile["xcelium_archive"]), indent=2, sort_keys=True))
        elif args.command == "generate":
            if args.output is None:
                raise GateError("generate requires --output")
            print(publish(args.output, build(args.repo, args.xcelium_archive)))
        else:
            if args.receipt is None:
                raise GateError("validate requires --receipt")
            validate(args.repo, args.xcelium_archive, args.receipt)
            print("W7_WEIGHTED_FOVEA_A7_RECEIPT_VALID_HOLD")
    except GateError as exc:
        print(f"W7_WEIGHTED_FOVEA_A7_RECEIPT_REJECT: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
