#!/usr/bin/env python3
"""Strict, fail-closed REDRED single-edge GPDK045 source-legality audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = Path(__file__).with_name("legality_matrix.json")
HARDENED_SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
AUDITED_INTEGRATED_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
SUPERSEDED_BASELINE_COMMIT = "4ce4836fab1309d3468db8e660d2da9af371f784"
ROOT_FILELISTS = (
    "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge.f",
    "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge.f",
)
EXPECTED_FILELISTS = {
    ROOT_FILELISTS[0]: "55d6c15e33147a3362dedfddccb0ff022e47401eeb0d8388a8dd30e5d9ca1e76",
    "rtl/technology/single_edge/filelists/generic.f":
        "8445fd6785966a09d6c8dc9b1cdef14787de7494bd0c7824fd524a08df176c2e",
    ROOT_FILELISTS[1]: "1fcf350a51ae32008ba207b5e1406d71e4a3083ffc52193e379f65eb1b623fee",
}
EXPECTED_SOURCES = {
    "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv":
        "800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d",
    "rtl/technology/single_edge/w2_single_edge_error_latch.sv":
        "02729b04c8326bd898a465a5343eb34b40a7c60c3667f6d0bb16eb3fcdb83260",
    "rtl/technology/single_edge/w2_single_edge_pair_tx.sv":
        "e00ac30015e826cef7d017b0a72066e405bce3e84a4ee454e99fb34c68e2642c",
    "rtl/technology/single_edge/w2_single_edge_pair_rx.sv":
        "c6ebefc560e158d4ffa4d1ac340c1c1b65d8caafbe2c1a8957fadbea3b7e59a5",
    "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv":
        "8fb80462a84929813965b9740628ae396ce6a8ebbf5f26a96e67d7ee926a8127",
    "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv":
        "52cf307b92cce5c227d072f103825abe8e321363a9d583369123186e2ebbd057",
    "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv":
        "bd00ade6ebd5f6c5e03ff356393a59f1baf6d890cfb3809a10bf0cda3bb1b0d9",
    "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv":
        "61daf3a31f29106d3f6383936d92131a31401fd86d71e0bee5ee53a3ab5b485d",
}
EXPECTED_POSEDGE_BY_SOURCE = {
    "rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv": ["clk"],
    "rtl/technology/single_edge/w2_single_edge_error_latch.sv": ["clk_i"],
    "rtl/technology/single_edge/w2_single_edge_pair_tx.sv": ["clk_i"],
    "rtl/technology/single_edge/w2_single_edge_pair_rx.sv": ["clk_i"],
    "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv": [],
    "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv":
        ["clk_i"],
    "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv":
        ["clk", "clk"],
    "rtl/candidates/a3_exact_scalar_prefix_k2_single_edge/a3_exact_scalar_prefix_k2_single_edge_top.sv":
        [],
}
EXPECTED_REPOSITORY_EVIDENCE = {
    "docs/AI_SEMI_QNA_REDRED_GOAL_20260819.md": (
        "cc583300ed93985c9da3a10de9618991094bc7778c591c0b701cd93e69747753",
        "SECONDARY_INTERPRETATION_OF_USER_PROVIDED_ORAL_TRANSCRIPT",
    ),
    "docs/server-audit-a1.md": (
        "21874c0f2e2e56f0661690c8e89ca2eec53fa43bbd96217c815abbb05c32eaf9",
        "HISTORICAL_READ_ONLY_SERVER_AUDIT",
    ),
    "physical/k2_w2_server_env/contract.json": (
        "f24b2c6b4857f7fd6161af7ef0efec5ef6ca31dbbbfda95838dbee9d26462c37",
        "REPOSITORY_CONTRACT_FOR_EXTERNAL_SERVER_BYTES",
    ),
    "physical/k2_w2_genus/timing_cohorts.json": (
        "4966b7c077f7f8595db22ed373a6843a69e85367d70689219773eec83b90a64e",
        "TEAM_DEFINED_TIMING_PROFILE",
    ),
    "docs/k2_endpoint_physical_results_20260814.txt": (
        "113d2ad1ffe3b52f59067e948868875f6ce509ad14970f73876418db176050b1",
        "INHERITED_P6_R1_STANDARD_CELL_RESULT_NOT_FALLBACK_EVIDENCE",
    ),
    "contracts/redred_system_goal/active_goal.json": (
        "b67307b8f7dd8c643a580ac13f99623188fd9852f4c1d763cd648004a0eef8ff",
        "POLICY_CONTRACT_NOT_EXTERNAL_APPROVAL",
    ),
}
EXPECTED_EXTERNAL = {
    "GSCLIB_ARCHIVE": (
        "/home/aiasic26911/gsclib045_all_v4.7.tgz",
        "fb15a057bc783e6b0b2b223261bb51ca170c27a62d33cb44dd4c91808d498ad1",
        {},
    ),
    "GIOLIB_ARCHIVE": (
        "/home/aiasic26911/giolib045_v3.3.tgz",
        "4bebbc571333b396a340dd6f47a365bc012d293392268f523c21eb5dcbdafcdb",
        {},
    ),
    "SETUP_LIBERTY": (
        "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib",
        "dec616b7b53aa5166eac9660ba83561a4057ee3b7e62f59f3d4bebad495ffe10",
        {"recorded_pvt": [1.0, 0.9, 125.0],
         "recorded_operating_condition": "PVT_0P9V_125C"},
    ),
    "HOLD_LIBERTY": (
        "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/fast_vdd1v0_basicCells.lib",
        "e63762d156fd929cde2f58b0a5883020d6f16f0a41d3736577d0af6b94191560",
        {"recorded_pvt": [1.0, 1.1, 0.0], "recorded_operating_condition": None},
    ),
    "TECH_LEF": (
        "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/lef/gsclib045_tech.lef",
        "0310f32fe4fb5009053dcfe36ece6e8d7a1f8e8d6e58a0b6fdd2109c2c919f70",
        {},
    ),
    "MACRO_LEF": (
        "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/lef/gsclib045_macro.lef",
        "7bb39c7adef5704aa10d886f9cc404b06d4f486219ffb4a6a8bbb31f965d52b2",
        {},
    ),
    "SHARED_TYPICAL_QRC": (
        "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/qrc/qx/gpdk045.tch",
        "a089c567928e3c8653408ebc503cb4e8270732c5f23e6cb23498d51cd6c75bd5",
        {"limitation": "same typical QRC recorded for setup and hold; no distinct best/worst RC evidence"},
    ),
}
EXPECTED_FIXTURES = {
    "tests/k2_w2_genus/fixtures/slow_vdd1v0_basicCells.lib":
        "9b35c30312e5dc7013394979c1f4d9dd04e1e36dc96d0c5b5c882d1093c10882",
    "tests/k2_w2_genus/fixtures/fast_vdd1v0_basicCells.lib":
        "2170d965e0a139340773336f1447a2c72e585ac38fb28e11bf02b9c674ae20ff",
    "tests/k2_w2_genus/fixtures/gsclib045_macro.lef":
        "54817863bc015e4481fcce3b624976c3a9c40ba27661b7f6817b384d33960c56",
    "tests/k2_w2_genus/fixtures/gpdk045.tch":
        "1f756f4a27d1478132d5a4a6fb8e311a01af15aeff4e8789337ea7eaaf0b5dd6",
}
FORBIDDEN_TOKENS = (
    "ODDR", "IDDR", "TLATNTSCAX2", "DFFNSRX1", "DFFRHQX1", "MX2X1",
    "BUFX2", "BUFX4", "ICG", "VENDOR", "primitive", "always_latch",
    "create_generated_clock",
)
ALLOWED_EVENT_CLOCKS = {"clk", "clk_i"}
TOP_KEYS = {
    "schema", "audit_date", "audit_source_commit", "audit_integrated_commit",
    "decision", "decision_rule",
    "repository_evidence", "expected_external_artifacts", "local_test_doubles",
    "recorded_real_cell_contracts", "team_profiles_not_organizer_rules",
    "audited_rtl", "gates",
}


class AuditError(ValueError):
    """An audit input or evidence contradiction."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_strict(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AuditError(f"invalid JSON: {error}") from error
    require(isinstance(value, dict), "matrix root must be an object")
    return value


def exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    actual = set(value)
    require(actual == expected,
            f"{label} keys differ missing={sorted(expected-actual)} unknown={sorted(actual-expected)}")
    return value


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value != "", f"{label} must be a nonempty path")
    require("\\" not in value, f"{label} contains a backslash")
    path = PurePosixPath(value)
    require(not path.is_absolute(), f"{label} must be repository-relative")
    require(all(part not in {"", ".", ".."} for part in path.parts),
            f"{label} contains traversal or noncanonical components")
    require(str(path) == value, f"{label} is not canonical")
    return value


def local_regular_file(root: Path, relative: Any, label: str) -> Path:
    value = relative_path(relative, label)
    current = root
    for part in PurePosixPath(value).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as error:
            raise AuditError(f"{label} is absent: {value}") from error
        require(not stat.S_ISLNK(mode), f"{label} crosses a symlink: {value}")
    require(current.is_file(), f"{label} is not a regular file: {value}")
    return current


def git(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    require(completed.returncode == 0,
            f"git {' '.join(args)} failed: {completed.stderr.decode(errors='replace').strip()}")
    return completed.stdout


def committed_blob(commit: str, path: str) -> bytes:
    relative_path(path, "committed path")
    row = git("ls-tree", commit, "--", path).decode("utf-8").rstrip("\n")
    require(row != "" and "\t" in row, f"committed path is absent: {path}")
    metadata, observed = row.split("\t", 1)
    fields = metadata.split()
    require(observed == path and len(fields) == 3 and fields[1] == "blob" and
            fields[0] in {"100644", "100755"},
            f"committed path is not a regular blob: {path}")
    return git("show", f"{commit}:{path}")


def expand_filelists(
    roots: tuple[str, ...], read_blob: Callable[[str], bytes]
) -> tuple[list[str], list[str]]:
    visited_filelists: list[str] = []
    sources: list[str] = []
    active: set[str] = set()

    def visit(filelist: str) -> None:
        filelist = relative_path(filelist, "filelist path")
        require(filelist.endswith(".f"), f"filelist does not end in .f: {filelist}")
        require(filelist not in active, f"recursive filelist cycle: {filelist}")
        if filelist in visited_filelists:
            return
        active.add(filelist)
        visited_filelists.append(filelist)
        try:
            text = read_blob(filelist).decode("utf-8")
        except UnicodeDecodeError as error:
            raise AuditError(f"filelist is not UTF-8: {filelist}") from error
        for line_number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            fields = line.split()
            if len(fields) == 2 and fields[0] == "-f":
                visit(relative_path(fields[1], f"{filelist}:{line_number}"))
            elif len(fields) == 1 and not fields[0].startswith(('-', '+')):
                source = relative_path(fields[0], f"{filelist}:{line_number}")
                require(source.endswith((".sv", ".v")),
                        f"unsupported source suffix in {filelist}:{line_number}")
                if source not in sources:
                    sources.append(source)
            else:
                raise AuditError(f"unsupported filelist directive at {filelist}:{line_number}")
        active.remove(filelist)

    for root in roots:
        visit(root)
    return visited_filelists, sources


def strip_comments_and_strings(text: str) -> str:
    result: list[str] = []
    index = 0
    state = "code"
    while index < len(text):
        char = text[index]
        pair = text[index:index + 2]
        if state == "code" and pair == "//":
            state = "line"
            result.extend("  ")
            index += 2
        elif state == "code" and pair == "/*":
            state = "block"
            result.extend("  ")
            index += 2
        elif state == "code" and char == '"':
            state = "string"
            result.append(" ")
            index += 1
        elif state == "line":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "code"
            index += 1
        elif state == "block":
            if pair == "*/":
                state = "code"
                result.extend("  ")
                index += 2
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
        elif state == "string":
            if char == "\\" and index + 1 < len(text):
                result.extend("  ")
                index += 2
            else:
                result.append("\n" if char == "\n" else " ")
                if char == '"':
                    state = "code"
                index += 1
        else:
            result.append(char)
            index += 1
    require(state in {"code", "line"}, "unterminated block comment or string")
    return "".join(result)


def scan_source(path: str, payload: bytes) -> list[str]:
    try:
        clean = strip_comments_and_strings(payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise AuditError(f"source is not UTF-8: {path}") from error
    for token in FORBIDDEN_TOKENS:
        pattern = (r"(?i)\b[A-Za-z0-9_]*vendor[A-Za-z0-9_]*\b" if token == "VENDOR"
                   else rf"(?i)\b{re.escape(token)}\b")
        require(re.search(pattern, clean) is None,
                f"forbidden RTL token {token} in {path}")
    require(re.search(r"(?i)\bnegedge\b", clean) is None,
            f"opposite-edge state in {path}")
    require(re.search(r"(?i)\b(output|inout)\b[^;]*(clk|clock)[A-Za-z0-9_]*[^;]*;", clean) is None,
            f"forwarded clock port in {path}")
    require(re.search(r"(?i)\bassign\b[^;]*(clk|clock)[A-Za-z0-9_]*\s*=", clean) is None,
            f"generated/forwarded clock assignment in {path}")
    events = re.findall(r"@\s*\(\s*(posedge|negedge)\s+([^\)]+)\)", clean,
                        flags=re.IGNORECASE)
    for edge, expression in events:
        signal = expression.strip()
        require(edge.lower() == "posedge" and signal in ALLOWED_EVENT_CLOCKS,
                f"undeclared, gated, or generated event clock '{edge} {signal}' in {path}")
    require("&" not in " ".join(expression for _, expression in events),
            f"gated event expression in {path}")
    return [expression.strip() for _, expression in events]


def validate_rtl(matrix: dict[str, Any]) -> None:
    rtl = exact_keys(matrix["audited_rtl"], {
        "source_commit", "integrated_commit", "supersedes_commit", "commit_role",
        "source_structure_status", "mapped_structure_status",
        "organizer_approval_status", "claim_limit", "root_filelists", "filelists",
        "expanded_sources", "expected_posedge_by_source", "allowed_event_clocks",
        "forbidden_tokens", "expected_posedge_event_count",
    }, "audited_rtl")
    require(matrix["audit_source_commit"] == HARDENED_SOURCE_COMMIT and
            rtl["source_commit"] == HARDENED_SOURCE_COMMIT and
            matrix["audit_integrated_commit"] == AUDITED_INTEGRATED_COMMIT and
            rtl["integrated_commit"] == AUDITED_INTEGRATED_COMMIT and
            rtl["supersedes_commit"] == SUPERSEDED_BASELINE_COMMIT,
            "audit target is not the hardened source and integrated RTL authority")
    require(HARDENED_SOURCE_COMMIT != SUPERSEDED_BASELINE_COMMIT and
            AUDITED_INTEGRATED_COMMIT != SUPERSEDED_BASELINE_COMMIT,
            "superseded baseline cannot be a source-structure PASS authority")
    require(rtl["commit_role"] ==
            "HARDENED_SOURCE_PROVENANCE_AND_INTEGRATED_AUDIT_TARGET",
            "RTL commit role changed")
    require(rtl["source_structure_status"] == "PASS" and
            rtl["mapped_structure_status"] == "HOLD" and
            rtl["organizer_approval_status"] == "HOLD" and
            rtl["claim_limit"] == "RTL_SOURCE_ONLY_NOT_MAPPED_NOT_ORGANIZER_APPROVAL",
            "source PASS escaped its claim boundary")
    require(rtl["root_filelists"] == list(ROOT_FILELISTS), "root filelist set/order changed")
    require(rtl["allowed_event_clocks"] == sorted(ALLOWED_EVENT_CLOCKS),
            "allowed event clocks changed")
    require(rtl["forbidden_tokens"] == list(FORBIDDEN_TOKENS), "forbidden token set changed")
    require(rtl["expected_posedge_event_count"] == 7, "posedge inventory changed")

    filelist_rows = rtl["filelists"]
    source_rows = rtl["expanded_sources"]
    require(isinstance(filelist_rows, list) and isinstance(source_rows, list),
            "RTL inventories must be arrays")
    for index, row in enumerate(filelist_rows):
        exact_keys(row, {"path", "sha256"}, f"audited_rtl.filelists[{index}]")
    for index, row in enumerate(source_rows):
        exact_keys(row, {"path", "sha256"}, f"audited_rtl.expanded_sources[{index}]")
    declared_filelists = {row["path"]: row["sha256"] for row in filelist_rows}
    declared_sources = {row["path"]: row["sha256"] for row in source_rows}
    require(len(declared_filelists) == len(filelist_rows), "duplicate declared filelist")
    require(len(declared_sources) == len(source_rows), "duplicate declared source")
    require(declared_filelists == EXPECTED_FILELISTS, "filelist identity inventory changed")
    require(declared_sources == EXPECTED_SOURCES, "expanded source identity inventory changed")
    require([row["path"] for row in filelist_rows] == list(EXPECTED_FILELISTS),
            "filelist inventory order changed")
    require([row["path"] for row in source_rows] == list(EXPECTED_SOURCES),
            "expanded source inventory order changed")

    event_rows = rtl["expected_posedge_by_source"]
    require(isinstance(event_rows, list), "expected_posedge_by_source must be an array")
    declared_events: dict[str, list[str]] = {}
    for index, row in enumerate(event_rows):
        exact_keys(row, {"path", "clocks"},
                   f"audited_rtl.expected_posedge_by_source[{index}]")
        path = relative_path(row["path"],
                             f"audited_rtl.expected_posedge_by_source[{index}].path")
        clocks = row["clocks"]
        require(isinstance(clocks, list) and
                all(isinstance(clock, str) for clock in clocks),
                f"audited_rtl.expected_posedge_by_source[{index}].clocks is malformed")
        require(path not in declared_events, f"duplicate posedge source: {path}")
        declared_events[path] = clocks
    require(declared_events == EXPECTED_POSEDGE_BY_SOURCE,
            "per-source posedge inventory changed")
    require([row["path"] for row in event_rows] == list(EXPECTED_POSEDGE_BY_SOURCE),
            "per-source posedge inventory order changed")
    require(set(declared_events) == set(declared_sources),
            "posedge inventory does not cover the exact source closure")
    require(sum(len(clocks) for clocks in declared_events.values()) ==
            rtl["expected_posedge_event_count"],
            "declared posedge total differs")

    for commit, role in (
        (HARDENED_SOURCE_COMMIT, "hardened source"),
        (AUDITED_INTEGRATED_COMMIT, "integrated source"),
    ):
        read = lambda path, commit=commit: committed_blob(commit, path)
        expanded_filelists, expanded_sources = expand_filelists(ROOT_FILELISTS, read)
        require(expanded_filelists == list(EXPECTED_FILELISTS),
                f"actual {role} filelist expansion differs")
        require(expanded_sources == list(EXPECTED_SOURCES),
                f"actual {role} source expansion differs")
        for path, expected in {**EXPECTED_FILELISTS, **EXPECTED_SOURCES}.items():
            require(sha256_bytes(read(path)) == expected,
                    f"{role} RTL hash differs: {path}")
        observed_events = {
            path: scan_source(path, read(path)) for path in expanded_sources
        }
        require(observed_events == EXPECTED_POSEDGE_BY_SOURCE,
                f"{role} per-source posedge inventory differs")
        require(sum(len(clocks) for clocks in observed_events.values()) ==
                rtl["expected_posedge_event_count"],
                f"{role} posedge event-control total differs")


def validate_repository_evidence(matrix: dict[str, Any], root: Path) -> None:
    rows = matrix["repository_evidence"]
    require(isinstance(rows, list), "repository_evidence must be an array")
    declared: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(rows):
        exact_keys(row, {"path", "sha256", "authority"}, f"repository_evidence[{index}]")
        path = relative_path(row["path"], f"repository_evidence[{index}].path")
        require(path not in declared, f"duplicate repository evidence path: {path}")
        declared[path] = (row["sha256"], row["authority"])
        local = local_regular_file(root, path, f"repository_evidence[{index}]")
        require(sha256(local) == row["sha256"], f"repository evidence hash mismatch: {path}")
    require(declared == EXPECTED_REPOSITORY_EVIDENCE,
            "repository evidence identity or authority changed")
    require("ORGANIZER_PRIMARY" not in declared[
        "physical/k2_w2_genus/timing_cohorts.json"][1],
        "team timing profile was promoted to organizer authority")


def validate_external_and_fixtures(matrix: dict[str, Any], root: Path) -> None:
    external_rows = matrix["expected_external_artifacts"]
    fixture_rows = matrix["local_test_doubles"]
    require(isinstance(external_rows, list) and isinstance(fixture_rows, list),
            "artifact inventories must be arrays")
    external_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(external_rows):
        require(isinstance(row, dict) and isinstance(row.get("id"), str),
                f"expected_external_artifacts[{index}] is malformed")
        artifact_id = row["id"]
        require(artifact_id in EXPECTED_EXTERNAL and artifact_id not in external_by_id,
                f"unexpected or duplicate external artifact: {artifact_id}")
        expected_path, expected_sha, extras = EXPECTED_EXTERNAL[artifact_id]
        exact_keys(row, {"id", "server_path", "sha256", "present_in_checkout"} | set(extras),
                   f"expected_external_artifacts[{index}]")
        require(row["server_path"] == expected_path and row["sha256"] == expected_sha,
                f"external artifact identity changed: {artifact_id}")
        require(all(row[key] == expected for key, expected in extras.items()),
                f"external artifact recorded facts changed: {artifact_id}")
        require(row["present_in_checkout"] is False,
                f"external artifact claims local presence: {artifact_id}")
        require(Path(row["server_path"]).is_absolute() and ".." not in Path(row["server_path"]).parts,
                f"external server path is malformed: {artifact_id}")
        require(not os.path.lexists(row["server_path"]),
                f"external artifact unexpectedly exists on this host: {artifact_id}")
        external_by_id[artifact_id] = row
    require(set(external_by_id) == set(EXPECTED_EXTERNAL), "external artifact set changed")

    fixtures: dict[str, str] = {}
    real_hashes = {value[1] for value in EXPECTED_EXTERNAL.values()}
    for index, row in enumerate(fixture_rows):
        exact_keys(row, {"path", "sha256"}, f"local_test_doubles[{index}]")
        path = relative_path(row["path"], f"local_test_doubles[{index}].path")
        require(path not in fixtures, f"duplicate fixture path: {path}")
        local = local_regular_file(root, path, f"local_test_doubles[{index}]")
        actual = sha256(local)
        require(actual == row["sha256"] and actual not in real_hashes,
                f"fixture aliases or contradicts real PDK evidence: {path}")
        fixtures[path] = actual
    require(fixtures == EXPECTED_FIXTURES, "fixture identity inventory changed")

    expected_basenames = {Path(value[0]).name for value in EXPECTED_EXTERNAL.values()}
    discovered: set[str] = set()
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name != ".git"]
        for name in files:
            if name not in expected_basenames:
                continue
            local = Path(directory) / name
            relative = local.relative_to(root).as_posix()
            require(not local.is_symlink(), f"PDK-like checkout path is a symlink: {relative}")
            require(relative in EXPECTED_FIXTURES,
                    f"undeclared real-or-fixture PDK-like file in checkout: {relative}")
            discovered.add(relative)
    require(discovered == set(EXPECTED_FIXTURES),
            "declared fixture set does not match independently discovered local files")


def validate_policy_shape(matrix: dict[str, Any]) -> None:
    exact_keys(matrix, TOP_KEYS, "matrix")
    require(matrix["schema"] == "redred_single_edge_gpdk045_legality_matrix_v2",
            "schema mismatch")
    require(matrix["audit_date"] == "2026-08-19", "audit date changed")
    rule = exact_keys(matrix["decision_rule"], {
        "operator", "required_gate_ids", "go_condition", "absence_policy",
    }, "decision_rule")
    require(rule["operator"] == "ALL", "release decision must use ALL gates")
    require("never organizer approval" in rule["absence_policy"].lower(),
            "absence/approval rule was weakened")

    gates = matrix["gates"]
    require(isinstance(gates, list), "gates must be an array")
    by_id: dict[str, dict[str, Any]] = {}
    for index, gate in enumerate(gates):
        exact_keys(gate, {"id", "status", "required_evidence", "current_reason"},
                   f"gates[{index}]")
        require(gate["id"] not in by_id, f"duplicate gate id: {gate['id']}")
        require(gate["status"] in {"HOLD", "GO"}, f"bad gate state: {gate['id']}")
        require(isinstance(gate["required_evidence"], str) and gate["required_evidence"],
                f"empty required evidence: {gate['id']}")
        require(isinstance(gate["current_reason"], str) and gate["current_reason"],
                f"empty current reason: {gate['id']}")
        by_id[gate["id"]] = gate
    require(set(by_id) == set(rule["required_gate_ids"]), "required gate set mismatch")
    require(len(rule["required_gate_ids"]) == len(set(rule["required_gate_ids"])),
            "duplicate required gate id")
    expected_decision = "GO" if all(gate["status"] == "GO" for gate in gates) else "HOLD"
    require(matrix["decision"] == expected_decision, "aggregate decision is not fail-closed")
    require(matrix["decision"] == "HOLD", "current repository must remain HOLD")
    require(by_id["G01_ORGANIZER_PRIMARY_RULE"]["status"] == "HOLD",
            "organizer gate promoted")
    require(by_id["G05_FALLBACK_SINGLE_EDGE_STRUCTURE"]["status"] == "HOLD" and
            "source-level RTL structure passes" in
            by_id["G05_FALLBACK_SINGLE_EDGE_STRUCTURE"]["current_reason"],
            "G05 does not preserve source-PASS/mapped-HOLD separation")

    cells = exact_keys(matrix["recorded_real_cell_contracts"], {
        "evidence_scope", "cells", "single_edge_rule", "forbidden_absence_rule",
    }, "recorded_real_cell_contracts")
    exact_keys(cells["cells"], {"TLATNTSCAX2", "MX2X1", "DFFRHQX1", "DFFNSRX1"},
               "recorded_real_cell_contracts.cells")
    require("does not satisfy organizer approval" in cells["forbidden_absence_rule"],
            "forbidden primitive absence was allowed to imply approval")
    profiles = exact_keys(matrix["team_profiles_not_organizer_rules"], {
        "inherited_complete_endpoint_6p5ns", "early_exploration_5ns",
    }, "team_profiles_not_organizer_rules")
    exact_keys(profiles["inherited_complete_endpoint_6p5ns"], {
        "period_ns", "ref_waveform_ns", "sample_waveform_ns", "clock_uncertainty_ns",
        "input_delay_min_ns", "input_delay_max_ns", "output_delay_min_ns",
        "output_delay_max_ns", "reset_delay_min_ns", "reset_delay_max_ns",
        "input_transition_ns", "output_load_pf", "drive_cell",
    }, "inherited_complete_endpoint_6p5ns")
    exact_keys(profiles["early_exploration_5ns"], {
        "period_ns", "clock_uncertainty_ns", "input_delay_ns", "output_delay_ns",
        "output_load_pf",
    }, "early_exploration_5ns")
    require(profiles["inherited_complete_endpoint_6p5ns"]["output_load_pf"] == 0.01,
            "recorded team load changed")


def validate(matrix_path: Path = MATRIX_PATH, root: Path = ROOT) -> dict[str, Any]:
    matrix = load_json_strict(matrix_path)
    validate_policy_shape(matrix)
    validate_repository_evidence(matrix, root)
    validate_external_and_fixtures(matrix, root)
    validate_rtl(matrix)
    return matrix


def main() -> None:
    try:
        validate()
    except AuditError as error:
        raise SystemExit(f"FAIL: {error}") from error
    print(
        "PASS: REDRED single-edge source structure is pinned/clean; "
        "GPDK045 mapped legality and organizer release remain HOLD"
    )


if __name__ == "__main__":
    main()
