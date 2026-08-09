import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = (
    PROJECT_ROOT
    / "benchmarks"
    / "clean_slate_aer"
    / "tests"
    / "fixtures"
    / "pairwise_cross_map_current"
)
HELPER = PROJECT_ROOT / "scripts" / "lib" / "pairwise_cross_map_common.sh"
RUNNERS = (
    PROJECT_ROOT / "scripts" / "run_common_multilane_benchmark.sh",
    PROJECT_ROOT / "scripts" / "run_common_multilane_candidate.sh",
)


class PairwiseCrossMapRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.marker = self.root / "lane.started"
        self.marker.write_text("", encoding="utf-8")
        self.identity = self.root / "identity.pairs.json"
        self.affine = self.root / "affine.pairs.json"
        self._write_report("identity_report.json", self.identity)
        self._write_report("affine_report.json", self.affine)
        self.output = self.root / "cross" / "identity-vs-affine.json"

    def tearDown(self):
        self.temporary.cleanup()

    def _write_report(self, fixture_name, destination, mutate=None):
        payload = json.loads((FIXTURE / fixture_name).read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(payload)
        destination.write_text(json.dumps(payload), encoding="utf-8")
        marker_ns = self.marker.stat().st_mtime_ns
        os.utime(destination, ns=(marker_ns + 1_000_000, marker_ns + 1_000_000))

    def _run(self, expected="current-schema-fixture", output=None):
        command = (
            'set -euo pipefail; source "$1"; '
            'pairwise_cross_map_compare "$2" "$3" "$4" "$5" "$6" '
            '"$7" "$8" "$9"'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                command,
                "pairwise-runner-test",
                str(HELPER),
                str(PROJECT_ROOT),
                expected,
                str(self.marker),
                str(FIXTURE / "identity_manifest.json"),
                str(self.identity),
                str(FIXTURE / "affine_manifest.json"),
                str(self.affine),
                str(output or self.output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _make_no_evaluable(payload):
        payload.update(
            measurement_state="NO_EVALUABLE_PAIRS",
            evaluable_pairs=0,
            dropped_pairs=1,
            censored_pairs=1,
            nonevaluable_pairs=1,
            mean_pair_completion_latency_cycles=None,
            p95_pair_completion_latency_cycles=None,
            max_pair_completion_latency_cycles=None,
            mean_pair_service_skew_cycles=None,
            p95_pair_service_skew_cycles=None,
            max_pair_service_skew_cycles=None,
            a_first_pairs=0,
            b_first_pairs=0,
            same_cycle_pairs=0,
            worst_completion_pair=None,
            worst_skew_pair=None,
        )
        aggregate = payload["pair_aggregates"][0]
        aggregate.update(
            evaluable_trials=0,
            dropped_trials=1,
            censored_trials=1,
            mean_completion_latency_cycles=None,
            max_completion_latency_cycles=None,
            mean_service_skew_cycles=None,
            max_service_skew_cycles=None,
        )
        trial = payload["trials"][0]
        trial.update(
            result="dropped_and_censored",
            event_state_a="source_overrun",
            event_state_b="pending",
        )
        for field in (
            "delivery_a",
            "delivery_b",
            "completion_latency_cycles",
            "service_skew_cycles",
        ):
            trial.pop(field, None)

    def test_rankable_result_is_published_without_overwrite(self):
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.output.read_bytes()
        second = self._run()
        self.assertEqual(second.returncode, 2)
        self.assertIn("output collision", second.stderr)
        self.assertEqual(self.output.read_bytes(), before)

    def test_stale_report_fails_closed_without_output(self):
        newest = max(self.identity.stat().st_mtime_ns, self.affine.stat().st_mtime_ns)
        os.utime(self.marker, ns=(newest + 1_000_000, newest + 1_000_000))
        result = self._run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing or stale", result.stderr)
        self.assertFalse(self.output.exists())

    def test_candidate_mismatch_fails_closed_without_output(self):
        result = self._run(expected="another-candidate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate mismatch", result.stderr)
        self.assertFalse(self.output.exists())

    def test_nonrankable_result_is_retained_and_returns_three(self):
        self._write_report(
            "identity_report.json", self.identity, self._make_no_evaluable
        )
        result = self._run()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("PAIRWISE_CROSS_MAP_NON_RANKABLE", result.stderr)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertFalse(payload["rankable"])
        self.assertIsNone(payload["mean_completion_delta_affine_minus_identity"])
        self.assertEqual(payload["drop_delta_affine_minus_identity"], -1)
        self.assertEqual(payload["censor_delta_affine_minus_identity"], -1)

    def test_runner_sources_encode_exact_pair_and_deferred_exit_policy(self):
        benchmark = RUNNERS[0].read_text(encoding="utf-8")
        candidate = RUNNERS[1].read_text(encoding="utf-8")
        for source in (benchmark, candidate):
            self.assertIn(
                '[[ -n "$identity_pair_report" && -n "$affine_pair_report" ]]',
                source,
            )
            self.assertIn(
                '[[ -n "$identity_pair_report" || -n "$affine_pair_report" ]]',
                source,
            )
            self.assertIn('out_root="$(mktemp -d "$out_parent/run.XXXXXXXX")"', source)
            self.assertNotIn("find \"$result_root\"", source)
        self.assertLess(
            benchmark.index('status="$?"'), benchmark.index("done\n\nprintf")
        )
        self.assertIn('exit "$overall_pairwise_status"', benchmark)
        self.assertIn('exit "$pairwise_status"', candidate)

    def test_runner_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(HELPER), *(str(path) for path in RUNNERS)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_benchmark_all_lanes_finish_before_nonrankable_exit_three(self):
        harness = self.root / "harness"
        scripts = harness / "scripts"
        library = scripts / "lib"
        benchmark = harness / "benchmarks" / "clean_slate_aer"
        library.mkdir(parents=True)
        benchmark.mkdir(parents=True)
        shutil.copy2(RUNNERS[0], scripts / RUNNERS[0].name)
        shutil.copy2(HELPER, library / HELPER.name)
        shutil.copy2(
            PROJECT_ROOT
            / "benchmarks"
            / "clean_slate_aer"
            / "pairwise_cross_map_compare.py",
            benchmark / "pairwise_cross_map_compare.py",
        )
        (benchmark / "manifest.multilane-n16.json").write_text(
            "{}\n", encoding="utf-8"
        )
        for fixture_name in (
            "identity_manifest.json",
            "affine_manifest.json",
            "identity_report.json",
            "affine_report.json",
        ):
            shutil.copy2(FIXTURE / fixture_name, benchmark / fixture_name)

        partial = json.loads(
            (benchmark / "identity_report.json").read_text(encoding="utf-8")
        )
        self._make_no_evaluable(partial)
        (benchmark / "identity_report.json").write_text(
            json.dumps(partial), encoding="utf-8"
        )
        (benchmark / "generate_trace.py").write_text(
            """#!/usr/bin/env python3
import argparse, json, pathlib, shutil
p = argparse.ArgumentParser()
p.add_argument('--manifest')
p.add_argument('--output-dir', required=True)
a = p.parse_args()
root = pathlib.Path(a.output_dir)
root.mkdir(parents=True, exist_ok=True)
here = pathlib.Path(__file__).resolve().parent
names = ['pairwise_contention_identity', 'pairwise_contention_affine']
names += [f'ordinary_{index:02d}' for index in range(20)]
runs = []
for name in names:
    event = root / f'{name}.events.jsonl'
    event.write_text('{}\\n', encoding='utf-8')
    manifest = root / f'{name}.manifest.json'
    if name.startswith('pairwise_contention_'):
        kind = name.rsplit('_', 1)[1]
        shutil.copy2(here / f'{kind}_manifest.json', manifest)
    else:
        manifest.write_text('{}\\n', encoding='utf-8')
    runs.append({'trace_file': event.name})
(root / 'generation-index.json').write_text(
    json.dumps({'runs': runs}), encoding='utf-8')
""",
            encoding="utf-8",
        )
        (benchmark / "pairwise_contention_metrics.py").write_text(
            """#!/usr/bin/env python3
import argparse, json, pathlib, re
p = argparse.ArgumentParser()
p.add_argument('--trace')
p.add_argument('--run-manifest', required=True)
p.add_argument('--events')
p.add_argument('--output', required=True)
a = p.parse_args()
here = pathlib.Path(__file__).resolve().parent
kind = 'identity' if 'identity' in pathlib.Path(a.run_manifest).stem else 'affine'
payload = json.loads((here / f'{kind}_report.json').read_text(encoding='utf-8'))
match = re.search(r'/k([124])/', a.output)
if not match:
    raise SystemExit('test harness could not infer lane')
payload['candidate'] = f'a7_prefix_k{match.group(1)}'
path = pathlib.Path(a.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload), encoding='utf-8')
""",
            encoding="utf-8",
        )
        (benchmark / "mixed_phase_always_ready_metrics.py").write_text(
            "raise SystemExit('mixed phase is outside this harness')\n",
            encoding="utf-8",
        )
        (scripts / "run_a7_parallel_event_compactor.sh").write_text(
            """#!/usr/bin/env bash
set -euo pipefail
lane="$1"
if [[ "${2:-}" == optional_multilane_independent_stall ]]; then
  exit 0
fi
stem="$(basename "$AER_TRACE_JSONL" .events.jsonl)"
result="$AER_CLEAN_OUT/prefix/k$lane/$stem"
mkdir -p "$result"
printf 'cycle\\n0\\n' > "$result/trace.events.csv"
printf '%s %s\\n' "$lane" "$stem" >> "$TEST_RUN_LOG"
""",
            encoding="utf-8",
        )
        for executable in (
            scripts / RUNNERS[0].name,
            scripts / "run_a7_parallel_event_compactor.sh",
            benchmark / "generate_trace.py",
            benchmark / "pairwise_contention_metrics.py",
        ):
            executable.chmod(0o755)

        output_parent = self.root / "runs"
        run_log = self.root / "run.log"
        environment = os.environ.copy()
        environment.update(
            AER_COMMON_MULTILANE_TRACE_DIR=str(self.root / "traces"),
            AER_CLEAN_OUT=str(output_parent),
            TEST_RUN_LOG=str(run_log),
        )
        result = subprocess.run(
            [str(scripts / RUNNERS[0].name), "drec-prefix", "all"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        completed = run_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(completed), 66)
        self.assertEqual({line.split()[0] for line in completed}, {"1", "2", "4"})
        run_roots = list(output_parent.glob("run.*"))
        self.assertEqual(len(run_roots), 1)
        for lane in (1, 2, 4):
            artifact = (
                run_roots[0]
                / "prefix"
                / f"k{lane}"
                / "pairwise-cross-map"
                / "identity-vs-affine.json"
            )
            self.assertFalse(json.loads(artifact.read_text(encoding="utf-8"))["rankable"])


if __name__ == "__main__":
    unittest.main()
