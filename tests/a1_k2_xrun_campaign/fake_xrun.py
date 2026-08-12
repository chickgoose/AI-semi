#!/usr/bin/env python3
"""Xrun double with selectable fail-closed mutations."""

import os
import sys
from pathlib import Path


def option(arguments: list[str], name: str) -> str | None:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return None


def plusarg(arguments: list[str], name: str) -> str | None:
    prefix = f"+{name}="
    return next((value[len(prefix):] for value in arguments if value.startswith(prefix)), None)


def journal(kind: str) -> None:
    target = os.environ.get("FAKE_XRUN_JOURNAL")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(kind + "\n")


def write_log(arguments: list[str], payload: str) -> None:
    raw = option(arguments, "-l")
    if raw is None:
        return
    target = Path(raw)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")


def write_results(arguments: list[str], mode: str) -> None:
    metrics = Path(plusarg(arguments, "METRICS") or "")
    events = Path(plusarg(arguments, "EVENT_METRICS") or "")
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text("candidate,test\nfake,pass\n", encoding="utf-8")
    if mode == "partial_output":
        return
    if mode == "duplicate_output":
        os.link(metrics, events)
    elif mode == "symlink_output":
        events.symlink_to(metrics.name)
    else:
        events.write_text("candidate,test,event\nfake,pass,0\n", encoding="utf-8")
    if mode == "stale_output":
        os.utime(metrics, ns=(1, 1))
        os.utime(events, ns=(1, 1))


def main() -> int:
    arguments = sys.argv[1:]
    mode = os.environ.get("FAKE_XRUN_MODE", "success")
    if "-version" in arguments:
        journal("version")
        print("xrun fake 23.09-s013")
        return 0
    if "-elaborate" in arguments:
        journal("compile")
        if mode == "compile_fail":
            write_log(arguments, "xrun compile failed\n")
            return 9
        if mode == "compile_error_zero":
            write_log(arguments, "xrun: *E,FAKE: compile diagnostic\n")
        else:
            write_log(arguments, "xrun compile complete\n")
        mutate = os.environ.get("FAKE_XRUN_MUTATE_PATH")
        if mutate:
            Path(mutate).write_text("mutated during compile\n", encoding="utf-8")
        return 0
    if "-R" not in arguments:
        return 10
    journal("run")
    name = plusarg(arguments, "CLEAN_TEST") or "missing"
    if mode == "run_fail":
        write_log(arguments, "xrun simulation failed\n")
        return 11
    if mode not in {"sentinel_only"}:
        write_results(arguments, mode)
    if mode == "mutate_trace" and plusarg(arguments, "TRACE_FILE"):
        Path(plusarg(arguments, "TRACE_FILE") or "").write_text(
            "mutated prepared trace\n", encoding="utf-8")
    if mode == "missing_pass":
        payload = "simulation complete without sentinel\n"
    elif mode == "error_zero":
        payload = f"AER_CLEAN_TEST_PASS {name}\nxmsim: *E,FAKE: runtime diagnostic\n"
    elif mode == "wrong_pass":
        payload = "AER_CLEAN_TEST_PASS wrong_name\n"
    elif name == "basic_reset_drain" and mode == "reset_missing":
        payload = f"AER_CLEAN_TEST_PASS {name}\n"
    elif name == "basic_reset_drain":
        payload = "AER_RESET_DRAIN_PASS generated=1 accepted=1 delivered=1\n"
        payload += f"AER_CLEAN_TEST_PASS {name}\n"
    else:
        payload = f"AER_CLEAN_TEST_PASS {name}\n"
    write_log(arguments, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
