#!/usr/bin/env python3
"""Create an immutable, non-destructive common-suite attempt namespace."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _snapshot(path: Path, destination: Path, *, executable: bool = False) -> str:
    source_info = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"provenance input is not a regular file: {path}")
    if executable and not source_info.st_mode & 0o111:
        raise ValueError(f"simulator executable has no execute bit: {path}")
    payload = path.read_bytes()
    if not payload:
        raise ValueError(f"provenance input is empty: {path}")
    mode = 0o500 if executable else 0o400
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(payload)


def _bundle_file(path: Path, destination: Path, relative: Path,
                 logical_name: str) -> dict[str, str]:
    return {"logical_name": logical_name, "path": str(relative),
            "sha256": _snapshot(path, destination)}


def create(root: Path, suite: str, candidate: str, candidate_manifest: Path,
           tools: dict[str, Path], *, tool_dependencies: dict[str, list[Path]] | None = None,
           simulator_name: str, simulator_executable: Path,
           simulator_version: Path) -> Path:
    for label, value in (("suite", suite), ("candidate", candidate),
                         ("simulator identity", simulator_name)):
        if not SAFE.fullmatch(value):
            raise ValueError(f"{label} is not a safe identity")
    if not {"runner", "generator"}.issubset(tools):
        raise ValueError("tool identity must include runner and generator")
    if any(not SAFE.fullmatch(name) for name in tools):
        raise ValueError("tool names must be safe path components")
    dependencies = tool_dependencies or {}
    if set(dependencies) - set(tools):
        raise ValueError("tool dependency names must identify a declared tool")
    simulator_info = simulator_executable.lstat()
    if (simulator_executable.is_symlink() or not simulator_executable.is_file() or
            not simulator_info.st_mode & 0o111):
        raise ValueError(
            f"simulator executable is not an executable regular file: {simulator_executable}"
        )
    try:
        candidate_doc = json.loads(candidate_manifest.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate manifest: {exc}") from exc
    if not isinstance(candidate_doc, dict) or candidate_doc.get("candidate") != candidate:
        raise ValueError("candidate manifest candidate does not match namespace")
    filelist = candidate_doc.get("filelist")
    if not isinstance(filelist, list) or not filelist:
        raise ValueError("candidate manifest must declare a non-empty filelist")
    candidate_sources = []
    for position, row in enumerate(filelist):
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError(f"candidate filelist[{position}] schema mismatch")
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) != row["path"]:
            raise ValueError(f"candidate filelist[{position}] path is not normalized relative")
        source = candidate_manifest.parent / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"candidate bundle source is not a regular file: {source}")
        payload = source.read_bytes()
        if not payload or _sha(payload) != row["sha256"]:
            raise ValueError(f"candidate bundle source hash mismatch: {source}")
        candidate_sources.append((row["path"], source, row["sha256"]))

    attempts = root / "attempts" / suite / candidate
    attempts.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for _ in range(32):
        attempt = attempts / f"{timestamp}-p{os.getpid()}-{secrets.token_hex(6)}"
        try:
            attempt.mkdir(mode=0o700)
        except FileExistsError:
            continue
        (attempt / "runs").mkdir(mode=0o700)
        provenance = attempt / "provenance"
        tool_root = provenance / "tools"
        simulator_root = provenance / "simulator"
        tool_root.mkdir(parents=True, mode=0o700)
        simulator_root.mkdir(mode=0o700)
        candidate_bundle_root = provenance / "candidate" / "bundle"
        candidate_bundle_root.mkdir(parents=True, mode=0o700)
        candidate_relative = Path("provenance/candidate/bundle/candidate.manifest.json")
        candidate_sha = _snapshot(candidate_manifest, attempt / candidate_relative)
        candidate_bundle_rows = []
        candidate_source_parents = []
        for logical_path, source, expected_sha in candidate_sources:
            relative = Path("provenance/candidate/bundle") / logical_path
            destination_parent = (attempt / relative).parent
            destination_parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            candidate_source_parents.append(destination_parent)
            actual_sha = _snapshot(source, attempt / relative)
            if actual_sha != expected_sha:  # source changed after its preflight read
                raise ValueError(f"candidate bundle source changed: {source}")
            candidate_bundle_rows.append({"logical_path": logical_path, "path": str(relative),
                                          "sha256": actual_sha})
        tool_rows = {}
        for name, path in sorted(tools.items()):
            bundle_root = tool_root / name
            bundle_root.mkdir(mode=0o700)
            logical_names = [path.name] + [item.name for item in dependencies.get(name, [])]
            if len(logical_names) != len(set(logical_names)):
                raise ValueError(f"tool {name} bundle has duplicate logical filenames")
            entry_relative = Path("provenance/tools") / name / "entrypoint.snapshot"
            entry = _bundle_file(path, attempt / entry_relative, entry_relative, path.name)
            dependency_rows = []
            for position, dependency in enumerate(dependencies.get(name, [])):
                relative = Path("provenance/tools") / name / f"dependency-{position:03d}.snapshot"
                dependency_rows.append(_bundle_file(dependency, attempt / relative, relative, dependency.name))
            identity_payload = {
                "identity": name,
                "entrypoint": {"logical_name": entry["logical_name"], "sha256": entry["sha256"]},
                "dependencies": [{"logical_name": row["logical_name"], "sha256": row["sha256"]}
                                 for row in dependency_rows],
                "dependency_closure": "declared_complete",
            }
            tool_rows[name] = {**identity_payload, "entrypoint": entry,
                               "dependencies": dependency_rows,
                               "bundle_sha256": _canonical_sha(identity_payload)}

        executable_relative = Path("provenance/simulator/bin") / simulator_name
        (attempt / executable_relative).parent.mkdir(mode=0o700)
        version_relative = Path("provenance/simulator/version.snapshot")
        simulator_row = {
            "identity": simulator_name,
            "executable": {"path": str(executable_relative),
                           "sha256": _snapshot(simulator_executable, attempt / executable_relative,
                                               executable=True)},
            "version": {"path": str(version_relative),
                        "sha256": _snapshot(simulator_version, attempt / version_relative)},
        }
        metadata = {
            "schema_version": 3,
            "suite": suite,
            "candidate": candidate,
            "attempt_id": attempt.name,
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "candidate_manifest": {
                "path": str(candidate_relative), "sha256": candidate_sha,
                "bundle_files": candidate_bundle_rows,
            },
            "tools": tool_rows,
            "simulator": simulator_row,
        }
        descriptor = os.open(attempt / "attempt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        directories = [path.parent for path in (attempt / executable_relative,
                                                  attempt / version_relative)]
        directories += [tool_root / name for name in tools]
        directories += candidate_source_parents
        directories += [candidate_bundle_root, candidate_bundle_root.parent]
        directories += [tool_root, provenance, attempt, attempts]
        for directory in dict.fromkeys(directories):
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return attempt
    raise RuntimeError("could not allocate a unique attempt namespace")


def _assignment(value: str, label: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError(f"{label} must be NAME=PATH")
    return name, Path(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--tool", action="append", type=lambda value: _assignment(value, "tool"), required=True)
    parser.add_argument("--tool-dependency", action="append",
                        type=lambda value: _assignment(value, "tool dependency"), default=[])
    parser.add_argument("--simulator-name", required=True)
    parser.add_argument("--simulator-executable", type=Path, required=True)
    parser.add_argument("--simulator-version", type=Path, required=True,
                        help="file containing exact captured simulator version output")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    tools = dict(args.tool)
    if len(tools) != len(args.tool):
        print("error: duplicate tool identity", file=sys.stderr)
        return 2
    dependencies: dict[str, list[Path]] = {}
    for name, path in args.tool_dependency:
        dependencies.setdefault(name, []).append(path)
    try:
        path = create(args.root, args.suite, args.candidate, args.candidate_manifest, tools,
                      tool_dependencies=dependencies, simulator_name=args.simulator_name,
                      simulator_executable=args.simulator_executable,
                      simulator_version=args.simulator_version)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
