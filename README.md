# AI-semi

Digital AER RTL project. The current internal final candidate is the FIFO-free
A23 EE430 core: rotating round-robin arbitration, bubble-free TX refill, and an
elastic RX. Its qualification and limitations are recorded in
[`docs/experiments/a23-final-candidate.md`](docs/experiments/a23-final-candidate.md).
The complete baseline-to-A23 design evolution and quantitative comparison are in
[`docs/experiments/baseline-to-a23-improvements.md`](docs/experiments/baseline-to-a23-improvements.md).
The fixed-priority baseline remains the comparison reference.

팀 작업 경위, 서버 제공물, 자체 workload와 공식/비공식 평가 조건의 구분은
[`docs/TEAM_HANDOFF_WORKLOAD.md`](docs/TEAM_HANDOFF_WORKLOAD.md)를 먼저 읽는다.

Candidate RTL and file list:

```text
rtl/experiments/a23_ee430/
tb/filelists/a23_ee430.f
```

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
