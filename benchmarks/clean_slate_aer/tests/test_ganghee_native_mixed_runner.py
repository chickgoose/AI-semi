#!/usr/bin/env python3
"""Runner integration checks for mandatory mixed-phase qualification."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "scripts" / "run_ganghee_native_benchmark.sh"


class GangheeNativeMixedRunnerTest(unittest.TestCase):
    def _run(self, *, analyzer_exit: int = 0) -> tuple[subprocess.CompletedProcess[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        trace = root / "mixed.events.jsonl"
        manifest = root / "mixed.manifest.json"
        rtl = root / "dut.sv"
        trace.write_text("\n", encoding="utf-8")
        manifest.write_text('{"run":{"workload":"mixed_phase_always_ready"}}\n', encoding="utf-8")
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
              printf '3 0 4096 16 0 0 0 0 4001\n' > "$output"
              printf 'prepared trace report_group=mixed_phase_always_ready events=0\n'
              exit 0
            fi
            [[ "$tool" == *mixed_phase_always_ready_metrics.py ]]
            printf '%s\n' "$@" > "$FAKE_ANALYZER_LOG"
            output=""
            summary=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --output) output="$2"; shift 2 ;;
                --summary) summary="$2"; shift 2 ;;
                *) shift ;;
              esac
            done
            grep -qx 'NEW_SUMMARY' "$summary"
            printf '{"diagnostic":"written"}\n' > "$output"
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
            printf 'NEW_SUMMARY\n' > "$metrics"
            printf 'NEW_EVENTS\n' > "$events"
            """), encoding="utf-8")
        xrun.chmod(0o755)

        out_root = root / "out"
        run_dir = out_root / "ganghee-native-n16-seed4001"
        run_dir.mkdir(parents=True)
        (run_dir / "trace.csv").write_text("STALE_SUMMARY\n", encoding="utf-8")
        (run_dir / "trace.events.csv").write_text("STALE_EVENTS\n", encoding="utf-8")
        (run_dir / "trace.mixed_metrics.json").write_text("STALE_JSON\n", encoding="utf-8")
        analyzer_log = root / "analyzer.args"
        environment = os.environ.copy()
        environment.update({
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "AER_GANGHEE_TOP": "fake_dut",
            "AER_GANGHEE_RTL": str(rtl),
            "AER_TRACE_JSONL": str(trace),
            "AER_TRACE_MANIFEST": str(manifest),
            "AER_CLEAN_OUT": str(out_root),
            "AER_SEED": "4001",
            "FAKE_ANALYZER_LOG": str(analyzer_log),
            "FAKE_ANALYZER_EXIT": str(analyzer_exit),
        })
        completed = subprocess.run(
            ["bash", str(RUNNER)], cwd=ROOT, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        return completed, root

    def test_mixed_run_removes_stale_outputs_and_requires_qualification(self):
        completed, root = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        arguments = (root / "analyzer.args").read_text(encoding="utf-8").splitlines()
        run_dir = root / "out" / "ganghee-native-n16-seed4001"
        self.assertEqual(arguments, [
            "--run-manifest", str(root / "mixed.manifest.json"),
            "--events", str(run_dir / "trace.events.csv"),
            "--summary", str(run_dir / "trace.csv"),
            "--require-qualified",
            "--output", str(run_dir / "trace.mixed_metrics.json"),
        ])
        self.assertEqual(
            (run_dir / "trace.mixed_metrics.json").read_text(encoding="utf-8"),
            '{"diagnostic":"written"}\n',
        )

    def test_analyzer_qualification_failure_fails_runner_after_diagnostic(self):
        completed, root = self._run(analyzer_exit=1)
        self.assertNotEqual(completed.returncode, 0)
        diagnostic = (
            root / "out" / "ganghee-native-n16-seed4001" /
            "trace.mixed_metrics.json"
        )
        self.assertTrue(diagnostic.is_file())


if __name__ == "__main__":
    unittest.main()
