# AI-semi Codex Working Rules

Persistent new-session context: `docs/NEW_SESSION_HANDOFF_20260818.md`.
Canonical tmux bootstrap: `scripts/bootstrap_codex_team_tmux.sh`.

## Default eight-agent parallel operation

- For every substantial AI-semi task, use the existing tmux Codex panes
  `a2` through `a9` as the default parallel worker pool unless the user
  explicitly says not to use parallel agents or requests a smaller count.
- A request such as "병렬작업", "a123...", "에이전트 돌려", or an
  independently divisible research, audit, verification, or implementation
  task means: dispatch all eight tmux agents from the beginning. Do not
  silently reduce the request to the in-process collaboration-slot limit.
- The head agent owns integration, task decomposition, conflict prevention,
  evidence review, and final decisions. Give the eight workers distinct,
  non-overlapping scopes and keep completed workers supplied with follow-up
  tasks while useful in-scope work remains.
- Use separate worktrees for edits. Use read-only assignments when multiple
  workers inspect the same candidate or when edits could collide. Never let
  two workers edit the same file set concurrently without an explicit merge
  plan.
- Verify execution with `tmux capture-pane`: a pane merely showing a Codex
  `node` process is not proof that it is working. Confirm all eight panes show
  `Working`, and report honestly when a pane is idle, blocked, or complete.
- The built-in collaboration API may expose fewer simultaneous slots than
  eight. In that case, keep its available reviewers running and use the tmux
  `a2`-`a9` Codex sessions for the full eight-worker pool. The lower API limit
  is not permission to ignore the user's eight-agent default.

## Safety and ownership

- Preserve user-owned untracked results and unrelated dirty-worktree changes.
- Team-member repositories and RTL/testbenches are read-only unless the user
  explicitly requests a modification.
- Keep each architecture's native unit tests separate from candidate-neutral
  common workload evidence.
