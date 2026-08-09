#!/usr/bin/env python3
"""Dynamic fake-inner regression for the common multilane outer runners.

Set AER_COMMON_RUNNER_SOURCE_ROOT to audit runners outside this worktree.  When
transplanted into the common repository, the test defaults to that repository.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[3]
ENV_SOURCE_ROOT = os.environ.get("AER_COMMON_RUNNER_SOURCE_ROOT")
if ENV_SOURCE_ROOT:
    SOURCE_ROOT = Path(ENV_SOURCE_ROOT).resolve()
elif (ROOT / "scripts" / "run_common_multilane_candidate.sh").is_file():
    SOURCE_ROOT = ROOT
else:
    SOURCE_ROOT = Path("/home/chickgoose/projects/a1")

MIXED_STEMS = (
    "mixed_phase_always_ready_identity",
    "mixed_phase_always_ready_bit_reverse",
)
OFFICIAL_STEMS = (
    "core_simultaneous_identity",
    "pairwise_contention_identity",
    "pairwise_contention_affine",
    "uniform_l1p00_s2001",
    "uniform_l1p00_s2002",
    "uniform_l1p00_s2003",
    "uniform_l1p25_s2001",
    "uniform_l1p25_s2002",
    "uniform_l1p25_s2003",
    "uniform_l1p50_s2001",
    "uniform_l1p50_s2002",
    "uniform_l1p50_s2003",
    "uniform_l2p00_s2001",
    "uniform_l2p00_s2002",
    "uniform_l2p00_s2003",
    "shape_b4",
    "shape_b16",
    "global_fanin_identity",
    "phase_transition_s3501",
    "phase_transition_s3502",
    *MIXED_STEMS,
)
RUNNER_CASES = (
    ("candidate", "run_common_multilane_candidate.sh", ("ganghee-cluster2",)),
    ("benchmark", "run_common_multilane_benchmark.sh", ("drec-prefix", "1")),
)


class FakeOuterProject:
    def __init__(self, temporary_root: Path, runner_name: str, runner_args: tuple[str, ...]):
        self.root = temporary_root / "project"
        self.trace_root = temporary_root / "traces"
        self.out_root = temporary_root / "out"
        self.run_root = self.out_root / "run.FAKE0001"
        self.tmp_root = temporary_root / "tmp"
        self.fake_bin = temporary_root / "bin"
        self.count_path = temporary_root / "analyzer.count"
        self.runner_name = runner_name
        self.runner_args = runner_args
        self._make_layout()

    def _write(self, relative_path: str, content: str, *, executable: bool = False) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        if executable:
            path.chmod(0o755)

    def _make_layout(self) -> None:
        source_runner = SOURCE_ROOT / "scripts" / self.runner_name
        if not source_runner.is_file():
            raise AssertionError(f"runner under test is missing: {source_runner}")
        runner = self.root / "scripts" / self.runner_name
        runner.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_runner, runner)
        common_library = SOURCE_ROOT / "scripts/lib/pairwise_cross_map_common.sh"
        if not common_library.is_file():
            raise AssertionError(f"runner dependency is missing: {common_library}")
        copied_library = self.root / "scripts/lib/pairwise_cross_map_common.sh"
        copied_library.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(common_library, copied_library)

        manifest = self.root / "benchmarks/clean_slate_aer/manifest.multilane-n16.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"schema_version":1,"runs":[]}\n', encoding="utf-8")

        self._write(
            "benchmarks/clean_slate_aer/generate_trace.py",
            r'''
            import argparse
            import json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--manifest", required=True)
            parser.add_argument("--output-dir", required=True, type=Path)
            args = parser.parse_args()
            args.output_dir.mkdir(parents=True, exist_ok=True)
            names = %s
            runs = []
            for name in names:
                trace_name = f"{name}.events.jsonl"
                (args.output_dir / trace_name).write_text("{}\n", encoding="utf-8")
                (args.output_dir / f"{name}.manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                runs.append({"trace_file": trace_name})
            (args.output_dir / "generation-index.json").write_text(
                json.dumps({"runs": runs}) + "\n", encoding="utf-8"
            )
            ''' % repr(list(OFFICIAL_STEMS)),
        )
        self._write(
            "benchmarks/clean_slate_aer/mixed_phase_always_ready_metrics.py",
            r'''
            import argparse
            import os
            from pathlib import Path
            import sys

            parser = argparse.ArgumentParser()
            parser.add_argument("--output", required=True, type=Path)
            args, _ = parser.parse_known_args()
            count_path = Path(os.environ["FAKE_ANALYZER_COUNT"])
            with count_path.open("a", encoding="utf-8") as output:
                output.write(str(args.output) + "\n")
            if os.environ.get("FAKE_ANALYZER_MODE") == "exit1":
                print("FAKE_ANALYZER_EXIT1_DIAGNOSTIC", file=sys.stderr)
                raise SystemExit(1)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text("fresh-analysis\n", encoding="utf-8")
            ''',
        )
        self._write(
            "benchmarks/clean_slate_aer/pairwise_contention_metrics.py",
            r'''
            import argparse
            import json
            import os

            parser = argparse.ArgumentParser()
            parser.add_argument("--output", required=True)
            args, _ = parser.parse_known_args()
            with open(args.output, "w", encoding="utf-8") as output:
                json.dump({"candidate": os.environ["FAKE_REPORT_CANDIDATE"]}, output)
                output.write("\n")
            ''',
        )
        self._write(
            "benchmarks/clean_slate_aer/pairwise_cross_map_compare.py",
            r'''
            import argparse
            import json

            parser = argparse.ArgumentParser()
            parser.add_argument("--identity-report", required=True)
            parser.add_argument("--output", required=True)
            args, _ = parser.parse_known_args()
            with open(args.identity_report, encoding="utf-8") as source:
                candidate = json.load(source)["candidate"]
            with open(args.output, "w", encoding="utf-8") as output:
                json.dump(
                    {"candidate": candidate, "rankable": True,
                     "rankability_reasons": []}, output
                )
                output.write("\n")
            ''',
        )

        inner = r'''
            #!/usr/bin/env bash
            set -euo pipefail
            stem="${AER_TRACE_JSONL##*/}"
            stem="${stem%.events.jsonl}"
            case "$(basename "$0")" in
              run_ganghee_cluster2_benchmark.sh)
                result_root="$AER_CLEAN_OUT/ganghee-cluster2-n16-seed${AER_SEED:-1}"
                ;;
              run_a7_parallel_event_compactor.sh)
                result_root="$AER_CLEAN_OUT/prefix/k$1/$stem"
                ;;
              *)
                printf 'unexpected fake inner: %s\n' "$0" >&2
                exit 2
                ;;
            esac
            mkdir -p "$result_root"
            if [[ "$stem" != mixed_phase_always_ready_* ]]; then
              printf 'fresh-events\n' >"$result_root/trace.events.csv"
              exit 0
            fi
            case "${FAKE_INNER_MODE:-success}" in
              success|analyzer_exit1)
                printf 'fresh-summary\n' >"$result_root/trace.csv"
                printf 'fresh-events\n' >"$result_root/trace.events.csv"
                ;;
              missing_summary)
                printf 'fresh-events\n' >"$result_root/trace.events.csv"
                ;;
              missing_event)
                printf 'fresh-summary\n' >"$result_root/trace.csv"
                ;;
              *)
                printf 'unknown FAKE_INNER_MODE=%s\n' "$FAKE_INNER_MODE" >&2
                exit 2
                ;;
            esac
        '''
        self._write("scripts/run_ganghee_cluster2_benchmark.sh", inner, executable=True)
        self._write("scripts/run_a7_parallel_event_compactor.sh", inner, executable=True)
        self.fake_bin.mkdir(parents=True, exist_ok=True)
        self._write(
            "../bin/mktemp",
            r'''
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${1:-}" == "-d" && "${2:-}" == */run.XXXXXXXX ]]; then
              mkdir -p "$FAKE_FIXED_RUN_ROOT"
              printf '%s\n' "$FAKE_FIXED_RUN_ROOT"
              exit 0
            fi
            exec /usr/bin/mktemp "$@"
            ''',
            executable=True,
        )
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)

    def mixed_result_root(self, stem: str) -> Path:
        if self.runner_name == "run_common_multilane_candidate.sh":
            return self.run_root / stem / "ganghee-cluster2-n16-seed1"
        return self.run_root / "prefix" / "k1" / stem

    def seed_stale_three(self) -> None:
        for stem in MIXED_STEMS:
            result_root = self.mixed_result_root(stem)
            result_root.mkdir(parents=True, exist_ok=True)
            (result_root / "trace.csv").write_text("stale-summary\n", encoding="utf-8")
            (result_root / "trace.events.csv").write_text("stale-events\n", encoding="utf-8")
            (result_root / f"{stem}.mixed.json").write_text(
                "stale-analysis\n", encoding="utf-8"
            )

    def run(self, inner_mode: str, analyzer_mode: str = "success") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "AER_COMMON_MULTILANE_TRACE_DIR": str(self.trace_root),
                "AER_CLEAN_OUT": str(self.out_root),
                "AER_SEED": "1",
                "FAKE_INNER_MODE": inner_mode,
                "FAKE_ANALYZER_MODE": analyzer_mode,
                "FAKE_ANALYZER_COUNT": str(self.count_path),
                "FAKE_FIXED_RUN_ROOT": str(self.run_root),
                "FAKE_REPORT_CANDIDATE": (
                    "ganghee-cluster2-row-bitmap"
                    if self.runner_name == "run_common_multilane_candidate.sh"
                    else "a7_prefix_k1"
                ),
                "TMPDIR": str(self.tmp_root),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
            }
        )
        command = [str(self.root / "scripts" / self.runner_name), *self.runner_args]
        return subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def analyzer_count(self) -> int:
        if not self.count_path.exists():
            return 0
        return len(self.count_path.read_text(encoding="utf-8").splitlines())

    def analyzer_outputs(self) -> list[str]:
        if not self.count_path.exists():
            return []
        return self.count_path.read_text(encoding="utf-8").splitlines()


class CommonMultilaneOuterDynamicTest(unittest.TestCase):
    def make_project(
        self, temporary_root: Path, runner_name: str, runner_args: tuple[str, ...]
    ) -> FakeOuterProject:
        return FakeOuterProject(temporary_root, runner_name, runner_args)

    def test_success_replaces_all_three_stale_files_and_analyzes_once(self) -> None:
        for label, runner_name, runner_args in RUNNER_CASES:
            with self.subTest(runner=label), tempfile.TemporaryDirectory() as temporary:
                project = self.make_project(Path(temporary), runner_name, runner_args)
                project.seed_stale_three()
                result = project.run("success")
                self.assertEqual(result.returncode, 0, result.stderr)
                for stem in MIXED_STEMS:
                    result_root = project.mixed_result_root(stem)
                    self.assertEqual(
                        (result_root / "trace.csv").read_text(encoding="utf-8"),
                        "fresh-summary\n",
                    )
                    self.assertEqual(
                        (result_root / "trace.events.csv").read_text(encoding="utf-8"),
                        "fresh-events\n",
                    )
                    self.assertEqual(
                        (result_root / f"{stem}.mixed.json").read_text(encoding="utf-8"),
                        "fresh-analysis\n",
                    )
                self.assertEqual(project.analyzer_count(), 2)
                for stem in MIXED_STEMS:
                    self.assertEqual(
                        sum(stem in output for output in project.analyzer_outputs()), 1,
                        f"analyzer must run exactly once for {stem}",
                    )

    def test_analyzer_exit_one_retains_diagnostic_and_runs_once(self) -> None:
        for label, runner_name, runner_args in RUNNER_CASES:
            with self.subTest(runner=label), tempfile.TemporaryDirectory() as temporary:
                project = self.make_project(Path(temporary), runner_name, runner_args)
                project.seed_stale_three()
                result = project.run("analyzer_exit1", analyzer_mode="exit1")
                self.assertEqual(result.returncode, 1)
                self.assertIn("FAKE_ANALYZER_EXIT1_DIAGNOSTIC", result.stderr)
                self.assertEqual(project.analyzer_count(), 1)
                self.assertFalse(
                    (project.mixed_result_root(MIXED_STEMS[0]) /
                     f"{MIXED_STEMS[0]}.mixed.json").exists()
                )

    def test_cluster2_missing_summary_fails_closed_before_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(
                Path(temporary),
                "run_common_multilane_candidate.sh",
                ("ganghee-cluster2",),
            )
            project.seed_stale_three()
            result = project.run("missing_summary")
            self.assertEqual(result.returncode, 1)
            self.assertIn("expected fresh mixed-phase summary result", result.stderr)
            self.assertEqual(project.analyzer_count(), 0)
            self.assertFalse(
                (project.mixed_result_root(MIXED_STEMS[0]) / "trace.csv").exists()
            )

    def test_cluster2_missing_event_fails_closed_before_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self.make_project(
                Path(temporary),
                "run_common_multilane_candidate.sh",
                ("ganghee-cluster2",),
            )
            project.seed_stale_three()
            result = project.run("missing_event")
            self.assertEqual(result.returncode, 1)
            self.assertIn("candidate did not produce a fresh event result", result.stderr)
            self.assertEqual(project.analyzer_count(), 0)
            self.assertFalse(
                (project.mixed_result_root(MIXED_STEMS[0]) / "trace.events.csv").exists()
            )


if __name__ == "__main__":
    unittest.main()
