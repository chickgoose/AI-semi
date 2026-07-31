# AI-semi

Private workspace for AI-assisted development.

## Parallel agent workflow

Use Git worktrees so each task or agent works in its own branch and directory.

```bash
git worktree add .worktrees/<task-name> -b feature/<task-name> main
```

Hermes Agent can create an isolated worktree automatically:

```bash
hermes -w
```
