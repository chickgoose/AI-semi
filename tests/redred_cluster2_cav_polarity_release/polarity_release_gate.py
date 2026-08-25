#!/usr/bin/env python3
"""Offline, fail-closed gate for an integrated Cluster2/CAV polarity-v1 release.

The implementation intentionally lives with its tests so that this directory can
be cherry-picked before the eventual authority artifacts.  Until the fixed
manifest exists and every bound artifact and Git stage validates, the CLI exits
with HOLD.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "redred.cluster2_cav_polarity_release/v1"
RECEIPT_SCHEMA = "redred.cluster2_cav_polarity_receipt/v1"
INTEGRATION_SCHEMA = "redred.cluster2_cav_polarity_integration_authority/v1"
TRACE_SCHEMA = "redred.cluster2_cav_polarity_trace/v1"
LEDGER_SCHEMA = "redred.cluster2_cav_polarity_ledger/v1"
DEFAULT_MANIFEST = (
    "benchmarks/redred_cluster2_cav_bridge/polarity_release_authority.json"
)
EXPECTED_EVENTS = 8503
EXPECTED_TOP = (
    "aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity"
)
EXPECTED_SOURCE_REPOSITORY = "https://github.com/GangHeeJo/AI-SEMI"
REQUIRED_ROLES = {
    "polarity_v1_rtl": "source",
    "polarity_v1_tb": "source",
    "polarity_v1_trace": "source",
    "polarity_v1_ledger": "receipt",
    "polarity_v1_receipt": "receipt",
    "integration_authority": "integration",
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ROLE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_FORBIDDEN_FILELIST = re.compile(r"[?*\[\]{}$`\\]")


class ReleaseHold(ValueError):
    """Required release evidence is absent, ambiguous, or inconsistent."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ReleaseHold("value is not canonical-JSON serializable") from error
    return (encoded + "\n").encode("ascii")


def _unique_object(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseHold("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReleaseHold("non-finite JSON number: %s" % value)


def parse_canonical_json(data: bytes, where: str) -> Mapping[str, object]:
    try:
        text = data.decode("ascii", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ReleaseHold:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ReleaseHold("%s is not canonical ASCII JSON" % where) from error
    if not isinstance(value, Mapping) or canonical_json(value) != data:
        raise ReleaseHold("%s is not byte-canonical JSON" % where)
    return value


def _exact(value: object, keys: Iterable[str], where: str) -> Mapping[str, object]:
    expected = frozenset(keys)
    if not isinstance(value, Mapping) or frozenset(value) != expected:
        raise ReleaseHold("%s keys differ" % where)
    return value


def _digest(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ReleaseHold("%s must be a full lowercase SHA-256" % where)
    return value


def _commit(value: object, where: str) -> str:
    if type(value) is not str or _COMMIT.fullmatch(value) is None:
        raise ReleaseHold("%s must be a full lowercase commit SHA" % where)
    return value


def _path(value: object, where: str) -> str:
    if (
        type(value) is not str
        or not value
        or value[0] in ("-", "+", "#")
        or any(character.isspace() or character == "\x00" for character in value)
        or _FORBIDDEN_FILELIST.search(value)
    ):
        raise ReleaseHold("%s must be one explicit normalized path" % where)
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or value != parsed.as_posix()
        or any(part in ("", ".", "..") for part in parsed.parts)
    ):
        raise ReleaseHold("%s must be one explicit normalized path" % where)
    return value


def _regular_bytes(root: Path, relative: str, where: str) -> bytes:
    normalized = _path(relative, where)
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ReleaseHold("%s is unavailable" % where) from error
        if stat.S_ISLNK(mode):
            raise ReleaseHold("%s traverses a symlink" % where)
    try:
        data = candidate.read_bytes()
    except OSError as error:
        raise ReleaseHold("%s is unreadable" % where) from error
    if not candidate.is_file():
        raise ReleaseHold("%s is not a regular file" % where)
    return data


def _text_semantics(value: object, raw: bytes, where: str) -> bytes:
    semantics = _exact(value, ("line_endings", "semantic_lf_sha256"), where)
    expected = _digest(semantics["semantic_lf_sha256"], where + ".semantic_lf_sha256")
    ending = semantics["line_endings"]
    if ending == "LF":
        if b"\r" in raw or not raw.endswith(b"\n"):
            raise ReleaseHold("%s does not have canonical LF endings" % where)
        normalized = raw
    elif ending == "CRLF":
        if not raw.endswith(b"\r\n") or raw.replace(b"\r\n", b"").find(b"\r") >= 0:
            raise ReleaseHold("%s does not match declared CRLF semantics" % where)
        if b"\n" in raw.replace(b"\r\n", b""):
            raise ReleaseHold("%s has mixed line endings" % where)
        normalized = raw.replace(b"\r\n", b"\n")
    else:
        raise ReleaseHold("%s line_endings must be LF or CRLF" % where)
    if sha256(normalized) != expected:
        raise ReleaseHold("%s semantic LF hash differs" % where)
    return normalized


def _binding(value: object, where: str, with_role: bool = True) -> Mapping[str, object]:
    keys = ("path", "sha256", "text", "role", "scope") if with_role else (
        "path", "sha256", "text"
    )
    binding = _exact(value, keys, where)
    _path(binding["path"], where + ".path")
    _digest(binding["sha256"], where + ".sha256")
    if with_role:
        if type(binding["role"]) is not str or _ROLE.fullmatch(binding["role"]) is None:
            raise ReleaseHold("%s.role is invalid" % where)
        if binding["scope"] not in ("source", "receipt", "integration"):
            raise ReleaseHold("%s.scope is invalid" % where)
    return binding


def _capture_binding(root: Path, binding: Mapping[str, object], where: str) -> Tuple[bytes, bytes]:
    raw = _regular_bytes(root, str(binding["path"]), where)
    if sha256(raw) != binding["sha256"]:
        raise ReleaseHold("%s raw SHA-256 differs" % where)
    return raw, _text_semantics(binding["text"], raw, where + ".text")


def _safe_git(root: Path, *args: str) -> bytes:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise ReleaseHold("git authority check failed: %s" % completed.stderr.decode(
            "utf-8", errors="replace").strip())
    return completed.stdout


def _git_blob(root: Path, commit: str, path: str) -> bytes:
    return _safe_git(root, "show", "%s:%s" % (commit, path))


def _parse_jsonl(normalized: bytes, schema: str, keys: Iterable[str], where: str) -> List[Mapping[str, object]]:
    rows: List[Mapping[str, object]] = []
    if not normalized or not normalized.endswith(b"\n"):
        raise ReleaseHold("%s must be nonempty and LF terminated" % where)
    for number, line in enumerate(normalized.splitlines(keepends=True), 1):
        row = parse_canonical_json(line, "%s line %d" % (where, number))
        _exact(row, keys, "%s line %d" % (where, number))
        if row["schema"] != schema:
            raise ReleaseHold("%s line %d schema differs" % (where, number))
        rows.append(row)
    return rows


def _verify_trace_and_ledger(trace: bytes, ledger: bytes) -> Mapping[str, int]:
    trace_rows = _parse_jsonl(
        trace,
        TRACE_SCHEMA,
        ("schema", "event_id", "source_index", "occurrence_cycle", "polarity"),
        "polarity trace",
    )
    ledger_rows = _parse_jsonl(
        ledger,
        LEDGER_SCHEMA,
        (
            "schema", "event_id", "source_index", "outcome",
            "expected_polarity", "observed_polarity",
        ),
        "polarity ledger",
    )
    if len(trace_rows) != EXPECTED_EVENTS or len(ledger_rows) != EXPECTED_EVENTS:
        raise ReleaseHold("trace and ledger must each contain exactly 8503 events")
    delivered = 0
    overrun = 0
    mismatch = 0
    for index, (trace_row, ledger_row) in enumerate(zip(trace_rows, ledger_rows)):
        for row, label in ((trace_row, "trace"), (ledger_row, "ledger")):
            if type(row["event_id"]) is not int or row["event_id"] != index:
                raise ReleaseHold("%s event IDs must be ordered 0..8502" % label)
            if type(row["source_index"]) is not int or not 0 <= row["source_index"] < 16:
                raise ReleaseHold("%s source_index is invalid" % label)
        if type(trace_row["occurrence_cycle"]) is not int or trace_row["occurrence_cycle"] < 0:
            raise ReleaseHold("trace occurrence_cycle is invalid")
        if trace_row["polarity"] not in (0, 1):
            raise ReleaseHold("trace polarity must be one bit")
        if (
            ledger_row["event_id"] != trace_row["event_id"]
            or ledger_row["source_index"] != trace_row["source_index"]
            or ledger_row["expected_polarity"] != trace_row["polarity"]
        ):
            raise ReleaseHold("trace-to-ledger identity or polarity binding differs")
        if ledger_row["outcome"] == "DELIVERED":
            delivered += 1
            if ledger_row["observed_polarity"] not in (0, 1):
                raise ReleaseHold("delivered observed_polarity must be one bit")
            if ledger_row["observed_polarity"] != ledger_row["expected_polarity"]:
                mismatch += 1
        elif ledger_row["outcome"] == "OVERRUN":
            overrun += 1
            if ledger_row["observed_polarity"] is not None:
                raise ReleaseHold("overrun observed_polarity must be null")
        else:
            raise ReleaseHold("ledger outcome is invalid")
    return {
        "generated": len(trace_rows),
        "delivered": delivered,
        "overrun": overrun,
        "polarity_mismatch": mismatch,
    }


def _verify_filelist(filelist: bytes, declared_files: Sequence[Mapping[str, object]]) -> None:
    try:
        lines = filelist.decode("ascii", errors="strict").splitlines()
    except UnicodeError as error:
        raise ReleaseHold("source filelist must be ASCII") from error
    if not lines or any(not line or line.strip() != line for line in lines):
        raise ReleaseHold("source filelist has blank or padded entries")
    paths = [_path(line, "source filelist entry") for line in lines]
    if len(paths) != len(set(paths)):
        raise ReleaseHold("source filelist contains duplicates")
    declared = [str(binding["path"]) for binding in declared_files]
    if paths != declared:
        raise ReleaseHold("source filelist order differs from exact source closure")


def validate_release(root: Path, document: Mapping[str, object]) -> Mapping[str, object]:
    root = Path(root).resolve()
    release = _exact(
        document,
        ("schema", "authority", "source", "artifacts", "counts", "integration"),
        "release manifest",
    )
    if release["schema"] != SCHEMA:
        raise ReleaseHold("release manifest schema differs")

    authority = _exact(
        release["authority"],
        ("source_repository", "source_commit", "receipt_commit"),
        "authority",
    )
    if authority["source_repository"] != EXPECTED_SOURCE_REPOSITORY:
        raise ReleaseHold("source_repository differs from the exact Ganghee authority")
    source_commit = _commit(authority["source_commit"], "source_commit")
    receipt_commit = _commit(authority["receipt_commit"], "receipt_commit")
    if source_commit == receipt_commit:
        raise ReleaseHold("source commit must be distinct from receipt commit")

    source = _exact(release["source"], ("top", "filelist", "files"), "source")
    if source["top"] != EXPECTED_TOP:
        raise ReleaseHold("polarity-v1 top differs")
    filelist_binding = _binding(source["filelist"], "source.filelist", with_role=False)
    if not isinstance(source["files"], list) or not source["files"]:
        raise ReleaseHold("source.files must be a nonempty ordered list")
    source_files = [
        _binding(value, "source.files[%d]" % index, with_role=False)
        for index, value in enumerate(source["files"])
    ]
    if len({value["path"] for value in source_files}) != len(source_files):
        raise ReleaseHold("source.files contains duplicates")

    if not isinstance(release["artifacts"], list):
        raise ReleaseHold("artifacts must be a list")
    artifacts: Dict[str, Mapping[str, object]] = {}
    for index, value in enumerate(release["artifacts"]):
        binding = _binding(value, "artifacts[%d]" % index)
        role = str(binding["role"])
        if role in artifacts:
            raise ReleaseHold("artifact role is duplicated: %s" % role)
        artifacts[role] = binding
    if set(artifacts) != set(REQUIRED_ROLES):
        raise ReleaseHold("polarity-v1 artifact role inventory differs")
    for role, scope in REQUIRED_ROLES.items():
        if artifacts[role]["scope"] != scope:
            raise ReleaseHold("%s scope differs" % role)
    if artifacts["polarity_v1_rtl"]["path"] not in {
        binding["path"] for binding in source_files
    }:
        raise ReleaseHold("polarity-v1 RTL is absent from the source filelist closure")

    raw_by_role: Dict[str, bytes] = {}
    normalized_by_role: Dict[str, bytes] = {}
    for role, binding in artifacts.items():
        raw, normalized = _capture_binding(root, binding, role)
        raw_by_role[role] = raw
        normalized_by_role[role] = normalized
    filelist_raw, filelist_normalized = _capture_binding(
        root, filelist_binding, "source filelist"
    )
    if filelist_raw != filelist_normalized:
        raise ReleaseHold("source filelist must use canonical LF")
    source_raw: Dict[str, bytes] = {}
    for index, binding in enumerate(source_files):
        raw, _ = _capture_binding(root, binding, "source file %d" % index)
        source_raw[str(binding["path"])] = raw
    _verify_filelist(filelist_normalized, source_files)

    rtl_text = normalized_by_role["polarity_v1_rtl"].decode("ascii", errors="strict")
    for token in ("module " + EXPECTED_TOP, "polarity_in", "pol_mask0", "pol_mask1"):
        if token not in rtl_text:
            raise ReleaseHold("polarity-v1 RTL token is absent: %s" % token)
    tb_text = normalized_by_role["polarity_v1_tb"].decode("ascii", errors="strict")
    for token in (EXPECTED_TOP, "POLARITY_V1_PASS", "POLARITY_MISMATCH"):
        if token not in tb_text:
            raise ReleaseHold("polarity-v1 TB token is absent: %s" % token)

    recomputed = _verify_trace_and_ledger(
        normalized_by_role["polarity_v1_trace"],
        normalized_by_role["polarity_v1_ledger"],
    )
    counts = _exact(
        release["counts"],
        ("generated", "delivered", "overrun", "polarity_mismatch"),
        "counts",
    )
    expected_counts = {
        "generated": EXPECTED_EVENTS,
        "delivered": EXPECTED_EVENTS,
        "overrun": 0,
        "polarity_mismatch": 0,
    }
    if counts != expected_counts or recomputed != expected_counts:
        raise ReleaseHold("8503-event conservation or zero-polarity-mismatch differs")

    receipt = parse_canonical_json(
        normalized_by_role["polarity_v1_receipt"], "polarity receipt"
    )
    _exact(receipt, ("schema", "status", "source_commit", "bindings", "counts"), "polarity receipt")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["status"] != "PASS":
        raise ReleaseHold("polarity receipt is not an exact PASS receipt")
    if receipt["source_commit"] != source_commit or receipt["counts"] != expected_counts:
        raise ReleaseHold("polarity receipt authority or counts differ")
    receipt_bindings = _exact(
        receipt["bindings"],
        ("polarity_v1_rtl", "polarity_v1_tb", "polarity_v1_trace", "polarity_v1_ledger"),
        "polarity receipt bindings",
    )
    for role, digest_value in receipt_bindings.items():
        if digest_value != artifacts[role]["sha256"]:
            raise ReleaseHold("polarity receipt binding differs: %s" % role)

    integration = _exact(
        release["integration"],
        (
            "authority_mode", "release_authority", "polarity_transport",
            "commit", "authority_artifact_role",
        ),
        "integration",
    )
    integration_commit = _commit(integration["commit"], "integration.commit")
    if receipt_commit == integration_commit:
        raise ReleaseHold("receipt commit must be distinct from integration commit")
    if integration != {
        "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
        "release_authority": True,
        "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
        "commit": integration_commit,
        "authority_artifact_role": "integration_authority",
    }:
        raise ReleaseHold("explicit integration authority differs")

    integration_authority = parse_canonical_json(
        normalized_by_role["integration_authority"], "integration authority"
    )
    _exact(
        integration_authority,
        (
            "schema", "status", "authority_mode", "source_commit",
            "receipt_commit", "top", "filelist_sha256", "bindings", "counts",
            "polarity_transport",
        ),
        "integration authority",
    )
    expected_binding_hashes = {
        role: binding["sha256"]
        for role, binding in artifacts.items()
        if role != "integration_authority"
    }
    if integration_authority != {
        "schema": INTEGRATION_SCHEMA,
        "status": "GO",
        "authority_mode": "EXPLICIT_INTEGRATION_RELEASE_AUTHORITY",
        "source_commit": source_commit,
        "receipt_commit": receipt_commit,
        "top": EXPECTED_TOP,
        "filelist_sha256": filelist_binding["sha256"],
        "bindings": expected_binding_hashes,
        "counts": expected_counts,
        "polarity_transport": "NATIVE_POLARITY_V1_BOUND",
    }:
        raise ReleaseHold("integration authority content differs")

    _safe_git(root, "cat-file", "-e", receipt_commit + "^{commit}")
    _safe_git(root, "cat-file", "-e", integration_commit + "^{commit}")
    _safe_git(root, "merge-base", "--is-ancestor", receipt_commit, integration_commit)
    _safe_git(root, "merge-base", "--is-ancestor", integration_commit, "HEAD")
    every_binding = [filelist_binding] + source_files + list(artifacts.values())
    for binding in every_binding:
        path = str(binding["path"])
        if sha256(_git_blob(root, integration_commit, path)) != binding["sha256"]:
            raise ReleaseHold("integration commit blob differs: %s" % path)
        if binding.get("scope") == "receipt":
            if sha256(_git_blob(root, receipt_commit, path)) != binding["sha256"]:
                raise ReleaseHold("receipt commit blob differs: %s" % path)

    return {
        "status": "GO",
        "schema": SCHEMA,
        "source_commit": source_commit,
        "receipt_commit": receipt_commit,
        "integration_commit": integration_commit,
        "generated": EXPECTED_EVENTS,
        "delivered": EXPECTED_EVENTS,
        "overrun": 0,
        "polarity_mismatch": 0,
    }


def evaluate_repository(root: Path, manifest_relative: str = DEFAULT_MANIFEST) -> Mapping[str, object]:
    root = Path(root).resolve()
    try:
        raw = _regular_bytes(root, manifest_relative, "release manifest")
        document = parse_canonical_json(raw, "release manifest")
        return validate_release(root, document)
    except ReleaseHold as error:
        return {"status": "HOLD", "reason": str(error)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate_repository(args.root, args.manifest)
    if args.json:
        sys.stdout.buffer.write(canonical_json(result))
    elif result["status"] == "GO":
        print(
            "REDRED_CLUSTER2_CAV_POLARITY_RELEASE_GO "
            "events=8503 delivered=8503 overrun=0 polarity_mismatch=0"
        )
    else:
        print("REDRED_CLUSTER2_CAV_POLARITY_RELEASE_HOLD reason=%s" % result["reason"])
    return 0 if result["status"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
