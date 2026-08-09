#!/usr/bin/env python3
"""Integration checks for the shared mixed-phase runner postprocessor."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[3]
RUNNERS = {
    "clean": ROOT / "scripts" / "run_clean_benchmark.sh",
    "native": ROOT / "scripts" / "run_ganghee_native_benchmark.sh",
}


class CommonMixedRunnerTest(unittest.TestCase):
    def _fixture(self) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        trace = root / "mixed_phase_always_ready_identity.events.jsonl"
        manifest = root / "mixed_phase_always_ready_identity.manifest.json"
        rtl = root / "dut.sv"
        trace.write_text("\n", encoding="utf-8")
        manifest.write_text(
            '{"generator_version":"4.0","run":'
            '{"workload":"mixed_phase_always_ready","seed":4001}}\n',
            encoding="utf-8",
        )
        rtl.write_text("module fake_dut; endmodule\n", encoding="utf-8")

        python = fake_bin / "python3"
        python.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            tool="$1"
            shift
            if [[ "$tool" == *prepare_sv_trace.py ]]; then
              output=""
              while [[ $# -gt 0 ]]; do
                if [[ "$1" == --output ]]; then output="$2"; shift 2; else shift; fi
              done
              printf '4 0 4096 16 0 0 0 0 4001\n' > "$output"
              printf 'TRACE_PREPARED report_group=mixed_phase_always_ready events=0\n'
              exit 0
            fi
            [[ "$tool" == *mixed_phase_always_ready_metrics.py ]]
            printf '%s\n' "$@" > "$FAKE_ANALYZER_LOG"
            output=""
            summary=""
            events=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --output) output="$2"; shift 2 ;;
                --summary) summary="$2"; shift 2 ;;
                --events) events="$2"; shift 2 ;;
                *) shift ;;
              esac
            done
            printf '{"classification":{"correctness_status":"qualified_pass"}}\n' > "$output"
            [[ -s "$summary" && -s "$events" ]]
            exit "$FAKE_ANALYZER_EXIT"
            """), encoding="utf-8")
        python.chmod(0o755)

        xrun = fake_bin / "xrun"
        xrun.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            for argument in "$@"; do
              [[ "$argument" == -elaborate ]] && exit 0
            done
            metrics=""
            events=""
            for argument in "$@"; do
              case "$argument" in
                +METRICS=*) metrics="${argument#+METRICS=}" ;;
                +EVENT_METRICS=*) events="${argument#+EVENT_METRICS=}" ;;
              esac
            done
            [[ -n "$metrics" && -n "$events" ]]
            [[ ! -e "$metrics" && ! -e "$events" ]]
            [[ "${FAKE_OMIT_SUMMARY:-0}" == 1 ]] || printf 'NEW_SUMMARY\n' > "$metrics"
            printf 'NEW_EVENTS\n' > "$events"
            """), encoding="utf-8")
        xrun.chmod(0o755)

        environment = os.environ.copy()
        environment.update({
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "AER_TRACE_JSONL": str(trace),
            "AER_TRACE_MANIFEST": str(manifest),
            "AER_SEED": "4001",
            "AER_NUM_SOURCES": "16",
            "AER_SIMULATOR": "xrun",
            "AER_GANGHEE_TOP": "fake_dut",
            "AER_GANGHEE_RTL": str(rtl),
            "FAKE_ANALYZER_EXIT": "0",
        })
        return root, environment

    def _run(self, kind: str, *, analyzer_exit: int = 0,
             omit_summary: bool = False) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        root, environment = self._fixture()
        out_root = root / "out"
        result_name = (
            "baseline-n16-seed4001" if kind == "clean"
            else "ganghee-native-n16-seed4001"
        )
        result_root = out_root / result_name
        result_root.mkdir(parents=True)
        for name, stale in (
            ("trace.csv", "STALE_SUMMARY\n"),
            ("trace.events.csv", "STALE_EVENTS\n"),
            ("trace.mixed_metrics.json", "STALE_JSON\n"),
        ):
            (result_root / name).write_text(stale, encoding="utf-8")
        analyzer_log = root / "analyzer.args"
        environment.update({
            "AER_CLEAN_OUT": str(out_root),
            "FAKE_ANALYZER_LOG": str(analyzer_log),
            "FAKE_ANALYZER_EXIT": str(analyzer_exit),
            "FAKE_OMIT_SUMMARY": "1" if omit_summary else "0",
        })
        command = ["bash", str(RUNNERS[kind])]
        if kind == "clean":
            command.append("baseline")
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        return completed, root, result_root

    def test_both_common_runners_use_one_qualified_fresh_v4_contract(self):
        for kind in RUNNERS:
            with self.subTest(kind=kind):
                completed, root, result_root = self._run(kind)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                arguments = (root / "analyzer.args").read_text(encoding="utf-8").splitlines()
                self.assertIn("--require-qualified", arguments)
                self.assertEqual(
                    arguments[arguments.index("--summary") + 1],
                    str(result_root / "trace.csv"),
                )
                for name in ("trace.csv", "trace.events.csv", "trace.mixed_metrics.json"):
                    self.assertGreater((result_root / name).stat().st_size, 0)

    def test_failure_diagnostic_and_exit_propagate_from_both_runners(self):
        for kind in RUNNERS:
            with self.subTest(kind=kind):
                completed, _, result_root = self._run(kind, analyzer_exit=1)
                self.assertNotEqual(completed.returncode, 0)
                self.assertGreater((result_root / "trace.mixed_metrics.json").stat().st_size, 0)

    def test_missing_fresh_summary_fails_closed_with_diagnostic(self):
        completed, _, result_root = self._run("native", omit_summary=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((result_root / "trace.csv").exists())
        self.assertGreater((result_root / "trace.mixed_metrics.json").stat().st_size, 0)

    def test_shared_owner_accepts_cluster2_result_paths(self):
        root, environment = self._fixture()
        result_root = root / "ganghee-cluster2-n16-seed4001"
        result_root.mkdir()
        summary = result_root / "trace.csv"
        events = result_root / "trace.events.csv"
        output = result_root / "trace.mixed_metrics.json"
        summary.write_text("CLUSTER2_SUMMARY\n", encoding="utf-8")
        events.write_text("CLUSTER2_EVENTS\n", encoding="utf-8")
        environment.update({
            "FAKE_ANALYZER_LOG": str(root / "cluster2.args"),
            "FAKE_ANALYZER_EXIT": "0",
        })
        command = textwrap.dedent(f"""\
            source {ROOT / 'scripts/lib/mixed_phase_analysis.sh'}
            mixed_phase_require_qualified mixed_phase_always_ready {ROOT} \
              {root / 'mixed_phase_always_ready_identity.manifest.json'} \
              {summary} {events} {output}
            """)
        completed = subprocess.run(
            ["bash", "-c", command], cwd=ROOT, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
