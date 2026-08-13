#!/usr/bin/env python3
"""Parse the authoritative Ganghee PNR tarball without promoting vectorless power."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


class ArchiveError(ValueError):
    """Raised when the archive or a physical report is not fail-closed parseable."""


POWER_RE = re.compile(
    r"synth/pnr/(?P<run>resynth_[^/]+)/(?P<design>[A-Za-z0-9_]+)_"
    r"(?P<period>[0-9]+(?:\.[0-9]+)?)_pnr_power\.rpt$"
)
NUMBER = r"([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
MAX_MEMBERS = 10000
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
LOCK_PATH = Path(__file__).with_name("ganghee_pnr_golden_20260813.lock.json")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _one(pattern: str, text: str, label: str) -> str:
    values = re.findall(pattern, text, flags=re.MULTILINE)
    if len(values) != 1:
        raise ArchiveError(f"{label} expected exactly once, found {len(values)}")
    return values[0]


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"{label} is not UTF-8") from exc


def _read_archive(path: Path, expected_sha256: str) -> tuple[bytes, dict[str, bytes], int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArchiveError(f"cannot read archive: {exc}") from exc
    actual = sha256(raw)
    if actual != expected_sha256:
        raise ArchiveError(f"archive SHA-256 mismatch ({expected_sha256} != {actual})")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            infos = archive.getmembers()
            if len(infos) > MAX_MEMBERS:
                raise ArchiveError("archive member count exceeds safety limit")
            total_size = sum(info.size for info in infos if info.isfile())
            if total_size > MAX_TOTAL_BYTES:
                raise ArchiveError("archive expanded size exceeds safety limit")
            for info in infos:
                name = info.name
                normalized = PurePosixPath(name)
                if (
                    normalized.is_absolute()
                    or normalized.as_posix() != name.rstrip("/")
                    or any(part in {"", ".", ".."} for part in normalized.parts)
                ):
                    raise ArchiveError(f"archive has unsafe member path {name!r}")
                if info.issym() or info.islnk():
                    raise ArchiveError(f"archive links are forbidden: {name!r}")
                if info.isfile():
                    if info.size > MAX_MEMBER_BYTES:
                        raise ArchiveError(f"archive member exceeds size limit: {name!r}")
                    if name in members:
                        raise ArchiveError(f"archive repeats member {name!r}")
                    stream = archive.extractfile(info)
                    if stream is None:
                        raise ArchiveError(f"cannot read archive member {name!r}")
                    members[name] = stream.read()
                elif not info.isdir():
                    raise ArchiveError(f"archive special member is forbidden: {name!r}")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError(f"cannot parse archive: {exc}") from exc
    return raw, members, len(infos)


def _member(members: dict[str, bytes], path: str) -> dict[str, str]:
    if path not in members:
        raise ArchiveError(f"required archive member is missing: {path}")
    return {"path": path, "sha256": sha256(members[path])}


def _power_row(path: str, data: bytes, members: dict[str, bytes]) -> dict[str, Any]:
    match = POWER_RE.fullmatch(path)
    if match is None:
        raise ArchiveError(f"unexpected power report path {path!r}")
    text = _utf8(data, f"power report {path}")
    design = _one(r"^\*\s*Design:\s*(\S+)\s*$", text, f"{path}: design")
    if design != match.group("design"):
        raise ArchiveError(f"{path}: design does not match filename")
    period_label = match.group("period")
    directory = path.rsplit("/", 1)[0]
    command_path = f"{directory}/run_{period_label}.tcl"
    log_path = f"{directory}/innovus_{period_label}.log"
    command_ref = _member(members, command_path)
    log_ref = _member(members, log_path)
    command_text = _utf8(members[command_path], command_path)
    log_text = _utf8(members[log_path], log_path)
    expected_report_command = (
        f"report_power > $OUT_DIR/{design}_{period_label}_pnr_power.rpt"
    )
    command_bound = expected_report_command in command_text and "<CMD> report_power" in log_text
    if not command_bound:
        raise ArchiveError(f"{path}: report_power command/log binding is missing")
    flow_errors = bool(re.search(r"(?m)^\*\*ERROR", log_text))
    activity_file_raw = _one(
        r"^\*\s*Activity File:\s*(.+?)\s*$", text, f"{path}: activity file"
    )
    user_activity = _one(
        r"^\*\s*User-Defined Activity\s*:\s*(.+?)\s*$",
        text, f"{path}: user activity",
    )
    default_activity = float(_one(
        r"^\*\s*Primary Input Activity:\s*" + NUMBER + r"\s*$",
        text, f"{path}: primary input activity",
    ))
    components = {
        "internal_power_mw": float(_one(
            r"^Total Internal Power:\s*" + NUMBER, text, f"{path}: internal power"
        )),
        "switching_power_mw": float(_one(
            r"^Total Switching Power:\s*" + NUMBER, text, f"{path}: switching power"
        )),
        "leakage_power_mw": float(_one(
            r"^Total Leakage Power:\s*" + NUMBER, text, f"{path}: leakage power"
        )),
        "total_power_mw": float(_one(
            r"^Total Power:\s*" + NUMBER, text, f"{path}: total power"
        )),
    }
    if not math.isclose(
        components["total_power_mw"],
        components["internal_power_mw"] + components["switching_power_mw"]
        + components["leakage_power_mw"],
        rel_tol=2e-5, abs_tol=5e-8,
    ):
        raise ArchiveError(f"{path}: power components do not sum to total")
    if activity_file_raw == "N.A." and user_activity == "N.A.":
        power_class = "vectorless_report_power"
        activity_file = None
        accepted = False
    elif activity_file_raw != "N.A.":
        activity_file = activity_file_raw.lstrip("./")
        provenance_path = (
            f"{directory}/{design}_{float(match.group('period')):.1f}_"
            "activity_provenance.json"
        )
        provenance_ok = False
        if (
            activity_file in members
            and activity_file.lower().endswith((".vcd", ".saif"))
            and provenance_path in members
        ):
            try:
                provenance = json.loads(members[provenance_path].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                provenance = None
            required = {
                "schema_version", "waveform_path", "waveform_sha256",
                "import_command_report", "scope_manifest", "window",
                "coverage", "workload_result",
            }
            provenance_ok = (
                isinstance(provenance, dict)
                and set(provenance) == required
                and provenance["schema_version"] == 1
                and provenance["waveform_path"] == activity_file
                and provenance["waveform_sha256"] == sha256(members[activity_file])
                and all(
                    isinstance(provenance[field], dict)
                    and set(provenance[field]) == {"path", "sha256"}
                    and provenance[field]["path"] in members
                    and provenance[field]["sha256"]
                    == sha256(members[provenance[field]["path"]])
                    for field in (
                        "import_command_report", "scope_manifest",
                        "coverage", "workload_result",
                    )
                )
                and isinstance(provenance["window"], dict)
                and set(provenance["window"])
                == {"start_cycle", "end_cycle_exclusive", "sha256"}
            )
        if not provenance_ok:
            power_class = "rejected_missing_activity_provenance"
            accepted = False
        else:
            power_class = "activity_annotated"
            accepted = True
    else:
        power_class = "rejected_missing_activity_provenance"
        activity_file = None
        accepted = False
    return {
        "design": design,
        "period_ns": float(match.group("period")),
        "power_class": power_class,
        "accepted_for_activity_comparison": accepted,
        "report": {"path": path, "sha256": sha256(data)},
        "command_script": command_ref,
        "tool_log": log_ref,
        "report_power_command_bound": command_bound,
        "flow_errors_present": flow_errors,
        **components,
        "activity_file": activity_file,
        "default_input_activity": default_activity if power_class == "vectorless_report_power" else None,
    }


def _slack(members: dict[str, bytes], path: str) -> float:
    text = _utf8(members[path], path)
    return float(_one(r"^=?\s*Slack Time\s+(-?[0-9]+(?:\.[0-9]+)?)\s*$", text, path))


def _area(members: dict[str, bytes], path: str, design: str) -> float:
    text = _utf8(members[path], path)
    return float(_one(
        rf"^{re.escape(design)}\s+\d+\s+([0-9]+(?:\.[0-9]+)?)\s*$",
        text, path,
    ))


def _pin_split(netlist: bytes, design: str) -> dict[str, int]:
    text = _utf8(netlist, f"mapped netlist for {design}")
    module = re.search(
        rf"(?ms)^module\s+{re.escape(design)}\s*\(.*?^endmodule\s*$", text
    )
    if module is None:
        raise ArchiveError(f"mapped netlist does not contain top module {design}")
    split = {"input": 0, "output": 0, "inout": 0}
    names: set[str] = set()
    for direction, high, low, raw_names in re.findall(
        r"(?m)^\s*(input|output|inout)\s+(?:\[(\d+):(\d+)\]\s+)?([^;]+);",
        module.group(0),
    ):
        width = abs(int(high) - int(low)) + 1 if high and low else 1
        for raw_name in raw_names.split(","):
            name = raw_name.strip()
            if not name or name in names:
                raise ArchiveError(f"mapped top has invalid or duplicate port {name!r}")
            names.add(name)
            if name not in {"clk", "rst", "rst_n", "reset", "reset_n"}:
                split[direction] += width
    if sum(split.values()) <= 0:
        raise ArchiveError(f"mapped top {design} has no functional pins")
    return split


def _boundary_diagnostics(
    power_rows: list[dict[str, Any]], members: dict[str, bytes]
) -> list[dict[str, Any]]:
    output = []
    for design in sorted({row["design"] for row in power_rows}):
        splits = []
        for row in power_rows:
            if row["design"] != design:
                continue
            directory = row["report"]["path"].rsplit("/", 1)[0]
            netlist_path = f"{directory}/{design}_{row['period_ns']:.1f}_netlist.v"
            _member(members, netlist_path)
            splits.append(_pin_split(members[netlist_path], design))
        if not splits or any(split != splits[0] for split in splits[1:]):
            raise ArchiveError(f"functional pin boundary changes across {design} sweep")
        split = splits[0]
        output.append({
            "design": design,
            "boundary_scope": "core_only",
            "functional_input_bits": split["input"],
            "functional_output_bits": split["output"],
            "functional_bidirectional_bits": split["inout"],
            "functional_pin_bits": sum(split.values()),
        })
    return output


def _fmax_screening(power_rows: list[dict[str, Any]], members: dict[str, bytes]) -> list[dict[str, Any]]:
    output = []
    for design in sorted({row["design"] for row in power_rows}):
        points = []
        for row in sorted(
            (item for item in power_rows if item["design"] == design),
            key=lambda item: item["period_ns"], reverse=True,
        ):
            prefix = f"synth/pnr/resynth_{design.removeprefix('aer_')}/"
            stem = f"{design}_{row['period_ns']:.1f}"
            setup_path = prefix + stem + "_setup_timing.rpt"
            hold_path = prefix + stem + "_hold_timing.rpt"
            drc_path = prefix + stem + "_drc.rpt"
            antenna_path = prefix + stem + "_antenna.rpt"
            area_path = prefix + stem + "_pnr_area.rpt"
            netlist_path = prefix + stem + "_netlist.v"
            sdc_path = prefix + stem + ".sdc"
            artifact_paths = (
                sdc_path, netlist_path, area_path, setup_path, hold_path,
                drc_path, antenna_path,
            )
            for path in artifact_paths:
                _member(members, path)
            setup = _slack(members, setup_path)
            hold = _slack(members, hold_path)
            clean = (
                "No DRC violations were found" in members[drc_path].decode("utf-8")
                and "No Violations Found" in members[antenna_path].decode("utf-8")
            )
            points.append({
                "period_ns": row["period_ns"],
                "frequency_mhz": 1000.0 / row["period_ns"],
                "setup_wns_ns": setup,
                "hold_wns_ns": hold,
                "area_um2": _area(members, area_path, design),
                "drc_antenna_clean": clean,
                "screening_pass": setup >= 0 and hold >= 0 and clean,
                "artifacts": {
                    role: _member(members, artifact_path)
                    for role, artifact_path in zip((
                        "sdc", "netlist", "area_report", "setup_report",
                        "hold_report", "drc_report", "antenna_report",
                    ), artifact_paths)
                },
            })
        passes = [point for point in points if point["screening_pass"]]
        last_pass = max(passes, key=lambda point: point["frequency_mhz"]) if passes else None
        fails = [point for point in points if last_pass and not point["screening_pass"] and point["frequency_mhz"] > last_pass["frequency_mhz"]]
        first_fail = min(fails, key=lambda point: point["frequency_mhz"]) if fails else None
        output.append({
            "design": design,
            "classification": "POST_ROUTE_SCREENING_NOT_ACTIVITY_POWER_GO",
            "last_pass_mhz": last_pass["frequency_mhz"] if last_pass else None,
            "first_higher_fail_mhz": first_fail["frequency_mhz"] if first_fail else None,
            "points": points,
        })
    return output


def summarize(path: Path, expected_sha256: str) -> dict[str, Any]:
    raw, members, member_count = _read_archive(path, expected_sha256)
    power_rows = [
        _power_row(name, data, members)
        for name, data in sorted(members.items()) if POWER_RE.fullmatch(name)
    ]
    if not power_rows:
        raise ArchiveError("archive contains no recognized Innovus report_power rows")
    vcd = sum(name.lower().endswith(".vcd") for name in members)
    saif = sum(name.lower().endswith(".saif") for name in members)
    annotated = sum(row["power_class"] == "activity_annotated" for row in power_rows)
    return {
        "schema_version": 1,
        "archive": {
            "path": str(path), "sha256": sha256(raw),
            "size_bytes": len(raw), "member_count": member_count,
        },
        "activity_inventory": {
            "vcd_members": vcd, "saif_members": saif,
            "activity_based_power_rows": annotated,
        },
        "power_rows": power_rows,
        "fmax_screening": _fmax_screening(power_rows, members),
        "boundary_diagnostics": _boundary_diagnostics(power_rows, members),
        "archive_assessment": {
            "boundary_scope": "core_only",
            "source_commit_bound": False,
            "external_library_bytes_bound": False,
            "flow_errors_present": any(row["flow_errors_present"] for row in power_rows),
            "unconstrained_paths_proven_zero": False,
            "qualification": "DIAGNOSTIC_HOLD",
        },
        "publication": {
            "candidate_go": False,
            "decision": "HOLD_NO_ACTIVITY_PROVENANCE",
            "reason": (
                "report_power rows without archive-bound VCD/SAIF provenance are "
                "vectorless diagnostics and cannot enter activity-power comparison"
            ),
        },
    }


def _load_lock() -> dict[str, Any]:
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"cannot read authoritative archive lock: {exc}") from exc
    required = {
        "schema_version", "archive_name", "sha256", "size_bytes",
        "member_count", "expected_power_rows", "expected_activity_based_power_rows",
    }
    if not isinstance(lock, dict) or set(lock) != required or lock["schema_version"] != 1:
        raise ArchiveError("authoritative archive lock has an invalid schema")
    return lock


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        lock = _load_lock()
        if args.archive.name != lock["archive_name"]:
            raise ArchiveError("archive basename does not match authoritative lock")
        result = summarize(args.archive, lock["sha256"])
        if (
            result["archive"]["size_bytes"] != lock["size_bytes"]
            or result["archive"]["member_count"] != lock["member_count"]
            or len(result["power_rows"]) != lock["expected_power_rows"]
            or result["activity_inventory"]["activity_based_power_rows"]
            != lock["expected_activity_based_power_rows"]
        ):
            raise ArchiveError("archive contents do not match authoritative lock expectations")
    except ArchiveError as exc:
        print(f"NOT_IMPORTED: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
