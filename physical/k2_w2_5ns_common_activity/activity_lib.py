#!/usr/bin/env python3
"""Pure, fail-closed helpers for the immutable W2 5 ns activity campaign."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import stat
import types
from typing import Any


REGISTRY_RELATIVE = Path("physical/k2_w2_5ns_common_activity/registry.json")
EXPECTED_SCOPE = "aer_clean_tb.candidate.dut"
ACTIVITY_WORKLOAD = "mixed_phase_always_ready_identity"
REQUIRED_PASS = "AER_CLEAN_TEST_PASS"


class ActivityError(RuntimeError):
    """Raised whenever evidence does not exactly satisfy the campaign contract."""


def stable_bytes(path: Path) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ActivityError(f"cannot stat {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ActivityError(f"not a regular non-symlink file: {path}")
    payload = path.read_bytes()
    after = path.lstat()
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_mode
    )
    if identity(before) != identity(after):
        raise ActivityError(f"file changed while reading: {path}")
    return payload


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    return sha256_bytes(stable_bytes(path))


def require_digest(path: Path, expected: str) -> bytes:
    payload = stable_bytes(path)
    actual = sha256_bytes(payload)
    if actual != expected:
        raise ActivityError(
            f"SHA mismatch for {path}: expected={expected} actual={actual}"
        )
    return payload


def write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise ActivityError(f"refusing to overwrite {path}") from exc


def seal_receipt(path: Path) -> None:
    stable_bytes(path)
    path.chmod(0o444)
    if stat.S_IMODE(path.lstat().st_mode) != 0o444:
        raise ActivityError(f"could not seal immutable receipt: {path}")


def require_sealed_receipt(path: Path) -> None:
    stable_bytes(path)
    if stat.S_IMODE(path.lstat().st_mode) != 0o444:
        raise ActivityError(f"receipt is not sealed read-only: {path}")


def snapshot(source: Path, destination: Path, expected: str) -> None:
    write_exclusive(destination, require_digest(source, expected))


def load_registry(repo: Path) -> dict[str, Any]:
    path = repo / REGISTRY_RELATIVE
    payload = stable_bytes(path)
    try:
        registry = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ActivityError("activity registry is not valid JSON") from exc
    if registry.get("schema") != "k2_w2_5ns_common_activity_registry_v1":
        raise ActivityError("activity registry schema mismatch")
    if registry.get("status") != "IMMUTABLE_EXECUTION_INPUTS":
        raise ActivityError("activity registry is not immutable")
    if set(registry.get("candidates", {})) != {"fovea", "a2", "a3"}:
        raise ActivityError("candidate set must be exactly Fovea+A7, A2+P6, A3+P6")
    clock = registry.get("clock", {})
    if (
        clock.get("ref_period_ps") != 5000
        or clock.get("sample_period_ps") != 5000
        or clock.get("sample_phase_ps") != 1250
        or clock.get("clock_source") != "tb_only_bound_force"
        or clock.get("physical_clock_claim") is not False
    ):
        raise ActivityError("registry does not bind the TB-only 5 ns clocks")
    if "vectorless" not in registry.get("forbidden_modes", []):
        raise ActivityError("registry must explicitly forbid vectorless activity")
    return registry


def verify_repository_inputs(repo: Path, registry: dict[str, Any]) -> None:
    for relative, expected in registry["pinned_repository_inputs"].items():
        require_digest(repo / relative, expected)
    for suite in registry["official_suites"].values():
        require_digest(repo / suite["manifest"], suite["manifest_sha256"])
    contract = json.loads(stable_bytes(repo / "physical/k2_w2_server_env/contract.json"))
    observed_xrun = contract.get("tools", {}).get("xrun", {})
    if observed_xrun != {
        "version": registry["xcelium"]["version"],
        "sha256": registry["xcelium"]["sha256"],
        "observed_path": registry["xcelium"]["path"],
        "golden_executable_identity": None,
    }:
        raise ActivityError("pinned server observation and Xcelium contract differ")


def parse_staged_filelist(path: Path) -> tuple[list[str], list[str], list[str]]:
    includes: list[str] = []
    defines: list[str] = []
    sources: list[str] = []
    for raw in stable_bytes(path).decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+incdir+"):
            includes.append(line[len("+incdir+"):])
        elif line.startswith("+define+"):
            defines.append(line[len("+define+"):])
        elif line.startswith(("+", "-")):
            raise ActivityError(f"unsupported staged filelist option: {line}")
        else:
            sources.append(line)
    if includes != ["rtl/technology/p6"]:
        raise ActivityError("staged filelist include directory changed")
    if defines != ["W2_P6_TECH_GENERIC"]:
        raise ActivityError("staged filelist must select exactly generic technology")
    if not sources or len(sources) != len(set(sources)):
        raise ActivityError("staged source list is empty or duplicated")
    return includes, defines, sources


def verify_staged_inputs(staged: Path, registry: dict[str, Any]) -> dict[str, list[str]]:
    manifest_contract = registry["staged_manifest"]
    manifest_path = staged / manifest_contract["path"]
    manifest = json.loads(
        require_digest(manifest_path, manifest_contract["sha256"]).decode("utf-8")
    )
    if (
        manifest.get("schema") != manifest_contract["required_schema"]
        or manifest.get("status") != manifest_contract["required_status"]
        or manifest.get("repository_commit")
        != manifest_contract["declared_repository_commit"]
    ):
        raise ActivityError("staged manifest identity/status mismatch")
    expected_hashes = registry["staged_source_hashes"]
    for relative, expected in expected_hashes.items():
        require_digest(staged / relative, expected)
    closures: dict[str, list[str]] = {}
    for name, candidate in registry["candidates"].items():
        filelist = staged / candidate["staged_filelist"]
        require_digest(filelist, candidate["staged_filelist_sha256"])
        _, _, sources = parse_staged_filelist(filelist)
        for relative in sources:
            if relative not in expected_hashes:
                raise ActivityError(f"{name}: unpinned staged source {relative}")
        closures[name] = sources
    return closures


def load_official(repo: Path, registry: dict[str, Any]) -> Any:
    path = repo / "scripts/common_suite_official.py"
    expected = registry["pinned_repository_inputs"][str(path.relative_to(repo))]
    payload = require_digest(path, expected)
    module = types.ModuleType("w2_activity_official")
    module.__file__ = str(path)
    exec(compile(payload, str(path), "exec"), module.__dict__)
    if module.GENERATOR_VERSION != "4.0":
        raise ActivityError("official suite generator version is not 4.0")
    for suite, pinned in registry["official_suites"].items():
        observed = module.SUITES[suite]
        if (
            observed["manifest_sha256"] != pinned["manifest_sha256"]
            or len(observed["names"]) != pinned["run_count"]
        ):
            raise ActivityError(f"{suite}: official suite identity mismatch")
    return module


def validate_generation(
    trace_root: Path,
    suite: str,
    manifest: Path,
    official: Any,
) -> dict[str, dict[str, Any]]:
    index_path = trace_root / "generation-index.json"
    try:
        index = json.loads(stable_bytes(index_path))
    except json.JSONDecodeError as exc:
        raise ActivityError(f"{suite}: malformed generation index") from exc
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise ActivityError(f"{suite}: generation-index field set mismatch")
    if index["schema_version"] != 1 or index["generator_version"] != "4.0":
        raise ActivityError(f"{suite}: generator identity mismatch")
    if Path(index["input_manifest"]).name != manifest.name:
        raise ActivityError(f"{suite}: generation-index manifest mismatch")
    expected_names = list(official.SUITES[suite]["names"])
    rows = index["runs"]
    names = [row.get("run", {}).get("name") for row in rows]
    if names != expected_names or len(names) != len(set(names)):
        raise ActivityError(f"{suite}: exact ordered run set mismatch")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["run"]["name"]
        if (
            row.get("trace_sha256") != official.TRACE_SHA256[name]
            or row.get("generator_version") != "4.0"
            or row.get("event_identity_mode") != "address_only"
            or row.get("dut_address_fields") != ["logical_source"]
            or row.get("dut_payload_fields") != []
        ):
            raise ActivityError(f"{suite}/{name}: trace contract mismatch")
        trace_path = trace_root / row["trace_file"]
        metadata_path = trace_root / f"{name}.manifest.json"
        if digest(trace_path) != row["trace_sha256"]:
            raise ActivityError(f"{suite}/{name}: trace bytes mismatch")
        if json.loads(stable_bytes(metadata_path)) != row:
            raise ActivityError(f"{suite}/{name}: per-run manifest mismatch")
        result[name] = {
            "row": row,
            "trace": trace_path,
            "manifest": metadata_path,
        }
    return result


def prove_capacity_subset(
    full: dict[str, dict[str, Any]],
    capacity: dict[str, dict[str, Any]],
    official: Any,
) -> dict[str, Any]:
    names = list(capacity)
    if names != list(official.CAPACITY22):
        raise ActivityError("capacity22 is not the exact ordered official subset")
    if not set(names).issubset(full):
        raise ActivityError("capacity22 contains a non-full50 member")
    trace_hashes: dict[str, str] = {}
    manifest_hashes: dict[str, str] = {}
    for name in names:
        full_trace = stable_bytes(full[name]["trace"])
        capacity_trace = stable_bytes(capacity[name]["trace"])
        full_manifest = stable_bytes(full[name]["manifest"])
        capacity_manifest = stable_bytes(capacity[name]["manifest"])
        if full_trace != capacity_trace or full_manifest != capacity_manifest:
            raise ActivityError(f"capacity22/{name}: not byte-identical to full50")
        trace_hashes[name] = sha256_bytes(full_trace)
        manifest_hashes[name] = sha256_bytes(full_manifest)
    return {
        "semantics": "exact_ordered_byte_identical_full50_subset",
        "capacity22_run_count": 22,
        "full50_run_count": 50,
        "additional_executions": 0,
        "trace_sha256": trace_hashes,
        "run_manifest_sha256": manifest_hashes,
    }


def parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stable_bytes(path).decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise ActivityError(f"malformed or duplicate key in {path}")
        values[key] = value
    return values


def parse_summary(path: Path, candidate_id: str) -> dict[str, str]:
    text = stable_bytes(path).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    if len(rows) != 1:
        raise ActivityError("common summary must have exactly one row")
    row = rows[0]
    required = {
        "candidate", "test", "generated", "source_overrun", "accepted",
        "delivered", "errors",
        "measurement_delivered", "measurement_cycles",
    }
    if not required.issubset(row):
        raise ActivityError("common summary field set is incomplete")
    if (
        row["candidate"] != candidate_id
        or row["test"] != ACTIVITY_WORKLOAD
        or int(row["errors"]) != 0
        or int(row["measurement_cycles"]) != 4096
        or int(row["accepted"]) != int(row["delivered"])
        or int(row["generated"])
        != int(row["source_overrun"]) + int(row["accepted"])
        or int(row["measurement_delivered"]) > int(row["delivered"])
        or int(row["generated"]) <= 0
    ):
        raise ActivityError("common summary violates the activity contract")
    return row


def validate_events(path: Path, candidate_id: str, summary: dict[str, str]) -> None:
    text = stable_bytes(path).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    if len(rows) != int(summary["generated"]):
        raise ActivityError("event evidence cardinality differs from generated count")
    state_counts: dict[str, int] = {
        "delivered": 0, "source_overrun": 0, "accepted": 0, "pending": 0,
    }
    for expected_id, row in enumerate(rows):
        state = row.get("event_state")
        if (
            row.get("candidate") != candidate_id
            or row.get("test") != ACTIVITY_WORKLOAD
            or int(row.get("tb_only_event_id", "-1")) != expected_id
            or state not in state_counts
        ):
            raise ActivityError("event evidence identity/state mismatch")
        state_counts[state] += 1
        if state == "delivered":
            if not row.get("accept_cycle") or not row.get("delivery_cycle"):
                raise ActivityError("delivered event lacks accept/delivery cycles")
        elif row.get("accept_cycle") or row.get("delivery_cycle"):
            raise ActivityError("non-delivered event gained accept/delivery cycles")
    if (
        state_counts["delivered"] != int(summary["delivered"])
        or state_counts["accepted"] != 0
        or state_counts["pending"] != 0
        or state_counts["delivered"] + state_counts["source_overrun"]
        != int(summary["generated"])
    ):
        raise ActivityError("event evidence does not close against common summary")


def validate_window(path: Path, candidate_id: str, measurement_cycles: int) -> dict[str, Any]:
    values = parse_key_values(path)
    required = {
        "schema", "candidate", "scope", "start_tick_1ps", "end_tick_1ps",
        "ref_period_ps", "sample_period_ps", "sample_phase_ps", "ref_rises",
        "sample_rises", "accepted_edges", "retired_edges",
        "drain_idle_at_window_end",
    }
    if set(values) != required:
        raise ActivityError("activity window field set mismatch")
    start = int(values["start_tick_1ps"])
    end = int(values["end_tick_1ps"])
    activity_cycles = measurement_cycles + 1
    if (
        values["schema"] != "w2_5ns_activity_window_v1"
        or values["candidate"] != candidate_id
        or values["scope"] != EXPECTED_SCOPE
        or int(values["ref_period_ps"]) != 5000
        or int(values["sample_period_ps"]) != 5000
        or int(values["sample_phase_ps"]) != 1250
        or start < 0
        or end - start != activity_cycles * 5000
        or int(values["ref_rises"]) != activity_cycles
        or int(values["sample_rises"]) != activity_cycles
        or int(values["accepted_edges"]) < 0
        or int(values["retired_edges"]) < 0
    ):
        raise ActivityError("activity window timing/count contract mismatch")
    return {
        **values,
        "start_tick_1ps": start,
        "end_tick_1ps": end,
        "duration_tick_1ps": end - start,
        "activity_window_ref_cycles": activity_cycles,
        "benchmark_measurement_cycles": measurement_cycles,
    }


@dataclass
class BitActivity:
    state: str = "x"
    initialized: bool = False
    last: int = 0
    t0: int = 0
    t1: int = 0
    tx: int = 0
    tc: int = 0

    def change(self, value: str, now: int) -> None:
        elapsed = now - self.last
        if elapsed < 0:
            raise ActivityError("VCD time is non-monotonic")
        if self.state == "0":
            self.t0 += elapsed
        elif self.state == "1":
            self.t1 += elapsed
        else:
            self.tx += elapsed
        if self.initialized and value != self.state:
            self.tc += 1
        self.state = value
        self.initialized = True
        self.last = now


@dataclass
class VcdVariable:
    scope: tuple[str, ...]
    reference: str
    width: int
    bits: list[BitActivity] = field(default_factory=list)


def rebase_vcd_bytes(raw_path: Path, start: int, end: int) -> bytes:
    if end <= start:
        raise ActivityError("invalid VCD extraction window")
    raw = stable_bytes(raw_path).decode("utf-8")
    if (
        re.search(r"\$timescale\s+1\s*ps\s+\$end", raw, re.IGNORECASE) is None
        or "$enddefinitions" not in raw
    ):
        raise ActivityError("raw VCD lacks mandatory header fields")
    blocks = re.split(r"(?m)(?=^#\d+\s*$)", raw)
    header = blocks[0]
    kept: list[str] = []
    for block in blocks[1:]:
        match = re.match(r"#(\d+)", block)
        if match is None:
            continue
        tick = int(match.group(1))
        if start <= tick <= end:
            kept.append(re.sub(r"^#\d+", f"#{tick - start}", block, count=1))
    if not kept:
        raise ActivityError("raw VCD has no value changes in the declared window")
    output = header + "".join(kept)
    markers = [int(value) for value in re.findall(r"(?m)^#(\d+)\s*$", output)]
    duration = end - start
    if not markers or markers[0] != 0:
        marker = "$enddefinitions $end"
        if marker not in output:
            raise ActivityError("VCD enddefinitions marker is noncanonical")
        output = output.replace(marker, marker + "\n#0", 1)
    if not markers or max(markers) != duration:
        output += "" if output.endswith("\n") else "\n"
        output += f"#{duration}\n"
    return output.encode("utf-8")


def rebase_vcd(raw_path: Path, output_path: Path, start: int, end: int) -> None:
    write_exclusive(output_path, rebase_vcd_bytes(raw_path, start, end))


def _expand_vector(value: str, width: int) -> str:
    value = value.lower().replace("z", "x")
    if len(value) < width:
        fill = "x" if value[:1] == "x" else "0"
        value = fill * (width - len(value)) + value
    return value[-width:]


def parse_vcd(path: Path) -> tuple[int, list[VcdVariable]]:
    lines = stable_bytes(path).decode("utf-8").splitlines()
    scopes: list[str] = []
    variables: list[VcdVariable] = []
    by_code: dict[str, list[VcdVariable]] = {}
    end_index: int | None = None
    for index, line in enumerate(lines):
        words = line.split()
        if words[:1] == ["$scope"]:
            if len(words) < 4:
                raise ActivityError("malformed VCD scope")
            scopes.append(words[2])
        elif words[:1] == ["$upscope"]:
            if not scopes:
                raise ActivityError("unbalanced VCD scope")
            scopes.pop()
        elif words[:1] == ["$var"]:
            if len(words) < 6:
                raise ActivityError("malformed VCD variable")
            width, code = int(words[2]), words[3]
            if width <= 0:
                raise ActivityError("VCD variable width must be positive")
            reference = " ".join(words[4:-1])
            reference = re.sub(r"\s*\[[^]]+\]\s*$", "", reference)
            variable = VcdVariable(
                tuple(scopes), reference, width,
                [BitActivity() for _ in range(width)],
            )
            variables.append(variable)
            by_code.setdefault(code, []).append(variable)
        elif "$enddefinitions" in line:
            end_index = index
            break
    if end_index is None or scopes:
        raise ActivityError("VCD header is incomplete or unbalanced")
    now = 0
    for line in lines[end_index + 1:]:
        line = line.strip()
        if not line or line.startswith("$"):
            continue
        if line.startswith("#"):
            now = int(line[1:])
            continue
        if line[0] in "01xXzZ":
            value, code = line[0].lower(), line[1:]
        elif line[0] in "bB":
            pieces = line[1:].split()
            if len(pieces) != 2:
                raise ActivityError("malformed VCD vector change")
            value, code = pieces
        else:
            raise ActivityError(f"unsupported VCD value record: {line}")
        aliases = by_code.get(code)
        if aliases is None:
            raise ActivityError("VCD change references an unknown identifier")
        for variable in aliases:
            expanded = _expand_vector(value, variable.width)
            for activity, bit in zip(variable.bits, expanded):
                activity.change(bit, now)
    if now <= 0 or not variables:
        raise ActivityError("VCD has no positive duration or variables")
    for variable in variables:
        for activity in variable.bits:
            activity.change(activity.state, now)
            if activity.t0 + activity.t1 + activity.tx != now:
                raise ActivityError("VCD bit accounting does not close")
            if not activity.initialized or activity.tx != 0:
                raise ActivityError("VCD contains unknown/uninitialized DUT activity")
    return now, variables


def _saif_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?", name):
        return name
    return "\\" + name.replace(" ", "_") + " "


def _build_saif_tree(variables: list[VcdVariable]) -> dict[str, Any]:
    tree: dict[str, Any] = {"nets": [], "children": {}}
    for variable in variables:
        if "dut" not in variable.scope:
            raise ActivityError("rebased VCD contains a signal outside DUT scope")
        dut_index = variable.scope.index("dut")
        node = tree
        for scope in variable.scope[dut_index + 1:]:
            node = node["children"].setdefault(scope, {"nets": [], "children": {}})
        for bit_index, activity in enumerate(variable.bits):
            name = variable.reference
            if variable.width > 1:
                name = f"{name}[{variable.width - 1 - bit_index}]"
            node["nets"].append((name, activity))
    if not tree["nets"] and not tree["children"]:
        raise ActivityError("rebased VCD has no DUT activity")
    return tree


def _emit_saif_instance(name: str, tree: dict[str, Any], indent: str = "  ") -> list[str]:
    lines = [f"{indent}(INSTANCE {_saif_name(name)}"]
    if tree["nets"]:
        lines.append(f"{indent}  (NET")
        names: set[str] = set()
        for net, activity in sorted(tree["nets"], key=lambda item: item[0]):
            if net in names:
                raise ActivityError(f"duplicate SAIF net name in one scope: {net}")
            names.add(net)
            lines.append(
                f"{indent}    ({_saif_name(net)} (T0 {activity.t0}) "
                f"(T1 {activity.t1}) (TX {activity.tx}) "
                f"(TC {activity.tc}) (IG 0))"
            )
        lines.append(f"{indent}  )")
    for child, subtree in sorted(tree["children"].items()):
        lines.extend(_emit_saif_instance(child, subtree, indent + "  "))
    lines.append(f"{indent})")
    return lines


def saif_bytes_and_statistics(
    vcd_path: Path, candidate_id: str
) -> tuple[bytes, dict[str, int]]:
    duration, variables = parse_vcd(vcd_path)
    tree = _build_saif_tree(variables)
    lines = [
        "(SAIFILE", '  (SAIFVERSION "2.0")', '  (DIRECTION "backward")',
        f'  (DESIGN "{candidate_id}")', '  (DATE "")',
        '  (VENDOR "W2 immutable common activity producer")',
        '  (PROGRAM_NAME "activity_lib.py")', '  (VERSION "1")',
        "  (DIVIDER /)", "  (TIMESCALE 1 ps)", f"  (DURATION {duration})",
    ]
    lines.extend(_emit_saif_instance("dut", tree))
    lines.append(")")
    bit_count = sum(variable.width for variable in variables)
    transition_count = sum(bit.tc for variable in variables for bit in variable.bits)
    statistics = {
        "duration_tick_1ps": duration,
        "net_bits": bit_count,
        "transitions": transition_count,
    }
    return ("\n".join(lines) + "\n").encode("utf-8"), statistics


def vcd_to_saif(vcd_path: Path, saif_path: Path, candidate_id: str) -> dict[str, int]:
    payload, statistics = saif_bytes_and_statistics(vcd_path, candidate_id)
    write_exclusive(saif_path, payload)
    return statistics


def artifact(path: Path, base: Path) -> dict[str, Any]:
    resolved_base = base.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved_base not in resolved.parents:
        raise ActivityError(f"artifact escapes receipt root: {path}")
    payload = stable_bytes(resolved)
    return {
        "path": resolved.relative_to(resolved_base).as_posix(),
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def verify_artifact(base: Path, record: dict[str, Any]) -> None:
    if set(record) != {"path", "sha256", "size_bytes"}:
        raise ActivityError("artifact record field set mismatch")
    relative = Path(record["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != record["path"]:
        raise ActivityError("artifact path is not normalized relative")
    path = base / relative
    payload = stable_bytes(path)
    if sha256_bytes(payload) != record["sha256"] or len(payload) != record["size_bytes"]:
        raise ActivityError(f"artifact bytes mismatch: {record['path']}")


def verify_candidate_receipt(receipt: dict[str, Any], base: Path) -> None:
    if receipt.get("schema") != "k2_w2_5ns_candidate_activity_receipt_v1":
        raise ActivityError("candidate receipt schema mismatch")
    if receipt.get("status") != "PROVEN_REAL_XCELIUM_ACTIVITY":
        raise ActivityError("candidate receipt is not proven real activity")
    if receipt.get("power_mode") != "activity_annotated" or receipt.get("vectorless") is not False:
        raise ActivityError("candidate receipt permits vectorless activity")
    clock = receipt.get("clock", {})
    if clock != {"ref_period_ps": 5000, "sample_period_ps": 5000, "sample_phase_ps": 1250}:
        raise ActivityError("candidate receipt clock mismatch")
    if receipt.get("scope") != EXPECTED_SCOPE:
        raise ActivityError("candidate receipt scope mismatch")
    expected_artifacts = {
        "prepared_trace", "prepare_log", "elaborate_log",
        "elaborate_driver_log", "run_log", "run_driver_log",
        "common_summary", "common_events", "window", "raw_vcd",
        "activity_vcd", "activity_saif",
    }
    if set(receipt.get("artifacts", {})) != expected_artifacts:
        raise ActivityError("candidate artifact set mismatch")
    for record in receipt["artifacts"].values():
        verify_artifact(base, record)
    tools = receipt.get("tools", {})
    if set(tools) != {"xcelium", "python"}:
        raise ActivityError("candidate tool set mismatch")
    tool = tools["xcelium"]
    if (
        tool.get("path") != "/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun"
        or tool.get("version") != "23.09-s013"
        or tool.get("sha256") != "b797ff6331f16102dfa453abf88761235f4d6bb75885b7b5e15b2e6f5bc7a5d7"
    ):
        raise ActivityError("candidate receipt Xcelium identity mismatch")
    verify_artifact(base, tool["version_log"])
    python = tools["python"]
    if (
        not Path(python.get("path", "")).is_absolute()
        or not re.fullmatch(r"[0-9a-f]{64}", python.get("sha256", ""))
        or not re.fullmatch(r"Python 3\.(?:1[1-9]|[2-9][0-9])(?:\.\d+)?(?: .*)?", python.get("version", ""))
    ):
        raise ActivityError("candidate receipt Python identity mismatch")
    verify_artifact(base, python["version_log"])
    closure = receipt.get("candidate_source_identity", {})
    verify_artifact(base, closure["staged_filelist"])
    verify_artifact(base, closure["include"])
    for record in closure.get("sources", []):
        verify_artifact(base, record)
    if not closure.get("sources"):
        raise ActivityError("candidate source closure is empty")
    tb_closure = receipt.get("tb_source_identity", {})
    verify_artifact(base, tb_closure["filelist"])
    if len(tb_closure.get("sources", [])) != 6:
        raise ActivityError("candidate TB source closure is not exact")
    for record in tb_closure["sources"]:
        verify_artifact(base, record)
    workload = receipt.get("workload", {})
    workload_artifacts = receipt.get("workload_artifacts", {})
    if set(workload_artifacts) != {"trace", "run_manifest"}:
        raise ActivityError("candidate workload artifact set mismatch")
    for record in workload_artifacts.values():
        verify_artifact(base, record)
    if (
        workload_artifacts["trace"]["sha256"] != workload.get("trace_sha256")
        or workload_artifacts["run_manifest"]["sha256"]
        != workload.get("run_manifest_sha256")
    ):
        raise ActivityError("candidate workload artifacts differ from identity")
    vcd = receipt["artifacts"]["activity_vcd"]
    saif = receipt["artifacts"]["activity_saif"]
    if vcd["size_bytes"] <= 0 or saif["size_bytes"] <= 0:
        raise ActivityError("activity artifact is empty")
    window = receipt.get("window", {})
    if (
        window.get("duration_tick_1ps") != 20_485_000
        or window.get("benchmark_measurement_cycles") != 4096
        or window.get("activity_window_ref_cycles") != 4097
        or receipt.get("saif_statistics", {}).get("duration_tick_1ps") != 20_485_000
    ):
        raise ActivityError("receipt window/SAIF duration mismatch")
    summary = receipt.get("summary", {})
    if (
        set(summary) != {
            "generated", "source_overrun", "accepted", "delivered", "errors",
            "measurement_delivered", "measurement_cycles",
        }
        or summary["errors"] != 0
        or summary["measurement_cycles"] != 4096
        or summary["accepted"] != summary["delivered"]
        or summary["generated"] != summary["source_overrun"] + summary["accepted"]
    ):
        raise ActivityError("candidate receipt summary mismatch")
    candidate_id = receipt.get("candidate_id", "")
    observed_summary = parse_summary(
        base / receipt["artifacts"]["common_summary"]["path"], candidate_id
    )
    expected_summary = {
        key: int(observed_summary[key]) for key in summary
    }
    if summary != expected_summary:
        raise ActivityError("candidate receipt does not match common summary bytes")
    validate_events(
        base / receipt["artifacts"]["common_events"]["path"],
        candidate_id,
        observed_summary,
    )
    observed_window = validate_window(
        base / receipt["artifacts"]["window"]["path"],
        candidate_id,
        int(observed_summary["measurement_cycles"]),
    )
    if window != observed_window:
        raise ActivityError("candidate receipt does not match window bytes")
    raw_vcd = base / receipt["artifacts"]["raw_vcd"]["path"]
    activity_vcd = base / receipt["artifacts"]["activity_vcd"]["path"]
    if stable_bytes(activity_vcd) != rebase_vcd_bytes(
        raw_vcd, observed_window["start_tick_1ps"], observed_window["end_tick_1ps"]
    ):
        raise ActivityError("rebased VCD does not match raw VCD/window")
    expected_saif, observed_statistics = saif_bytes_and_statistics(
        activity_vcd, candidate_id
    )
    if (
        stable_bytes(base / receipt["artifacts"]["activity_saif"]["path"])
        != expected_saif
        or receipt.get("saif_statistics") != observed_statistics
    ):
        raise ActivityError("SAIF does not match real VCD transitions")
    commands = receipt.get("commands", {})
    if set(commands) != {"elaborate", "execute"}:
        raise ActivityError("candidate command set mismatch")
    elaborate, execute = commands["elaborate"], commands["execute"]
    execute_lower = [item.lower() for item in execute]
    flattened = "\n".join(elaborate + execute).lower()
    if (
        not elaborate or not execute
        or elaborate[0] != tool["path"] or execute[0] != tool["path"]
        or "-elaborate" not in elaborate or "-r" not in execute
        or "genus" in flattened or "innovus" in flattened
        or "+clean_test=trace" not in execute_lower
        or f"+candidate={candidate_id}" not in execute_lower
        or f"+trace_name={ACTIVITY_WORKLOAD}" not in execute_lower
    ):
        raise ActivityError("candidate execution command contract mismatch")


def verify_campaign_receipt(receipt: dict[str, Any], base: Path) -> None:
    if receipt.get("schema") != "k2_w2_5ns_common_activity_campaign_receipt_v1":
        raise ActivityError("campaign receipt schema mismatch")
    if receipt.get("status") != "PROVEN_REAL_XCELIUM_ACTIVITY_THREE_CANDIDATES":
        raise ActivityError("campaign receipt is not proven")
    if set(receipt.get("candidate_receipts", {})) != {"fovea", "a2", "a3"}:
        raise ActivityError("campaign candidate receipt set mismatch")
    if receipt.get("power_mode") != "activity_annotated" or receipt.get("vectorless") is not False:
        raise ActivityError("campaign permits vectorless activity")
    verify_artifact(base, receipt["registry"])
    tools = receipt.get("tools", {})
    if set(tools) != {"xcelium", "python"}:
        raise ActivityError("campaign tool set mismatch")
    verify_artifact(base, tools["xcelium"]["version_log"])
    verify_artifact(base, tools["python"]["version_log"])
    snapshot_repo = base / "provenance/repository"
    registry = load_registry(snapshot_repo)
    verify_repository_inputs(snapshot_repo, registry)
    closures = verify_staged_inputs(base / "provenance/staged", registry)
    official = load_official(snapshot_repo, registry)
    suites = receipt.get("suite_identity", {}).get("suites", {})
    if set(suites) != {"full50", "capacity22"}:
        raise ActivityError("campaign suite artifact set mismatch")
    for suite, expected_count in (("full50", 50), ("capacity22", 22)):
        row = suites[suite]
        verify_artifact(base, row["manifest"])
        verify_artifact(base, row["generation_index"])
        verify_artifact(base, row["generation_log"])
        if len(row.get("ordered_names", [])) != expected_count:
            raise ActivityError(f"{suite}: ordered name count mismatch")
    subset = receipt.get("suite_identity", {}).get("capacity22_subset_proof", {})
    if (
        subset.get("semantics") != "exact_ordered_byte_identical_full50_subset"
        or subset.get("capacity22_run_count") != 22
        or subset.get("full50_run_count") != 50
        or subset.get("additional_executions") != 0
        or len(subset.get("trace_sha256", {})) != 22
        or len(subset.get("run_manifest_sha256", {})) != 22
    ):
        raise ActivityError("campaign capacity22 subset proof mismatch")
    generated: dict[str, dict[str, dict[str, Any]]] = {}
    for suite, identity in registry["official_suites"].items():
        generated[suite] = validate_generation(
            base / "traces" / suite,
            suite,
            snapshot_repo / identity["manifest"],
            official,
        )
    if prove_capacity_subset(
        generated["full50"], generated["capacity22"], official
    ) != subset:
        raise ActivityError("campaign subset proof differs from generated bytes")
    capacity_names = suites["capacity22"]["ordered_names"]
    full_names = suites["full50"]["ordered_names"]
    if (
        capacity_names != list(subset["trace_sha256"])
        or capacity_names != list(subset["run_manifest_sha256"])
        or [name for name in full_names if name in set(capacity_names)]
        != capacity_names
    ):
        raise ActivityError("campaign capacity22 ordered subset mismatch")
    candidate_ids = {"fovea": "fovea_a7", "a2": "a2_p6", "a3": "a3_p6"}
    for name, record in receipt["candidate_receipts"].items():
        verify_artifact(base, record)
        candidate_path = base / record["path"]
        require_sealed_receipt(candidate_path)
        candidate_receipt = json.loads(stable_bytes(candidate_path))
        verify_candidate_receipt(candidate_receipt, base)
        contract = registry["candidates"][name]
        if (
            candidate_receipt.get("candidate") != name
            or candidate_receipt.get("candidate_id") != candidate_ids[name]
            or candidate_receipt.get("top") != contract["top"]
            or candidate_receipt.get("tools") != tools
            or candidate_receipt.get("candidate_source_identity", {}).get("top")
            != contract["top"]
            or len(candidate_receipt["candidate_source_identity"].get("sources", []))
            != len(closures[name])
        ):
            raise ActivityError(f"{name}: candidate receipt identity mismatch")
        workload = candidate_receipt.get("workload", {})
        if workload != registry["activity_workload"]:
            raise ActivityError(f"{name}: candidate workload identity mismatch")
        expected_tb = [
            line.strip()
            for line in stable_bytes(snapshot_repo / contract["tb_filelist"])
            .decode("utf-8")
            .splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        observed_tb = candidate_receipt.get("tb_source_identity", {})
        if (
            observed_tb.get("filelist", {}).get("path")
            != (Path("provenance/repository") / contract["tb_filelist"]).as_posix()
            or [record.get("path") for record in observed_tb.get("sources", [])]
            != [
                (Path("provenance/repository") / relative).as_posix()
                for relative in expected_tb
            ]
        ):
            raise ActivityError(f"{name}: candidate TB closure mismatch")
