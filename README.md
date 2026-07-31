# AI-semi

Digital AER RTL project. The fixed-priority baseline is the selected design.
The buffered round-robin experiment was rejected after the normalized Genus
comparison increased area and power while reducing maximum frequency. Its
implementation and measurements remain available in Git history and the `a2`
branch; the decision record remains under `docs/`.

팀 작업 경위, 서버 제공물, 자체 workload와 공식/비공식 평가 조건의 구분은
[`docs/TEAM_HANDOFF_WORKLOAD.md`](docs/TEAM_HANDOFF_WORKLOAD.md)를 먼저 읽는다.

Active RTL and file list:

```text
rtl/baseline/
tb/filelists/baseline.f
```

Run the selected design with:

```bash
scripts/run_sim.sh baseline
scripts/run_ppa.sh scripts/config.ppa.sh
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
