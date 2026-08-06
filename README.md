# AI-semi

Digital AER RTL project. The active direction as of 2026-08-04 is clean-slate:
freeze the AER event semantics, architecture-neutral workloads, scoreboard, and
Cadence PPA boundary before writing a new candidate RTL. Ganghee's ROW/COL
design, Junyoung's A23, and Hyeonsu's rotation-priority design are historical
references and benchmark-calibration candidates, not the implementation base for
the new architecture.

The benchmark contract and the exact AER limitations it is intended to expose are
recorded in
[`docs/verification/aer-clean-benchmark-spec.md`](docs/verification/aer-clean-benchmark-spec.md).
Team members should start with the runnable package map and commands in
[`docs/TEAM_COMMON_WORKLOAD_GUIDE.md`](docs/TEAM_COMMON_WORKLOAD_GUIDE.md).
Deterministic trace injection and physical qualification are specified separately
in [`docs/verification/aer-trace-loader.md`](docs/verification/aer-trace-loader.md)
and [`docs/verification/aer-physical-ppa-contract.md`](docs/verification/aer-physical-ppa-contract.md).
The earlier A23 qualification remains reproducible and is preserved in
[`docs/experiments/a23-final-candidate.md`](docs/experiments/a23-final-candidate.md).

팀 작업 경위, 서버 제공물, 자체 workload와 공식/비공식 평가 조건의 구분은
[`docs/TEAM_HANDOFF_WORKLOAD.md`](docs/TEAM_HANDOFF_WORKLOAD.md)를 먼저 읽는다.

Candidate RTL and file list:

```text
rtl/experiments/a23_ee430/
tb/filelists/a23_ee430.f
```

Run the first clean benchmark calibration with:

```bash
scripts/run_clean_benchmark.sh mock
scripts/run_clean_benchmark.sh baseline
scripts/run_clean_benchmark.sh a23-ee430
```

Xcelium is the qualification simulator. The normalized benchmark interface can
retire multiple logical events per cycle, while the legacy adapter maps existing
single-lane ready/valid designs without adding storage.

Run candidate verification with:

```bash
scripts/run_sim.sh a23-ee430
scripts/run_a23_ee430_checks.sh
scripts/run_a23_functional_checks.sh
scripts/run_a23_stress.sh
```

## Parallel agent workflow

Use Git worktrees so each task or agent works in its own branch and directory.

```bash
git worktree add .worktrees/<task-name> -b feature/<task-name> main
```

Hermes Agent can create an isolated worktree automatically:

```bash
hermes -w
```
