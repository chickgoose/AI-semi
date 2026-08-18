#!/usr/bin/env bash
set -euo pipefail

session=${1:-ai-semi}
repo=/home/chickgoose/projects/a1

if ! command -v tmux >/dev/null 2>&1; then
  printf 'tmux is not installed\n' >&2
  exit 2
fi

if [[ ! -d "$repo" ]]; then
  printf 'repository not found: %s\n' "$repo" >&2
  exit 2
fi

if tmux has-session -t "=$session" 2>/dev/null; then
  printf 'refusing to overwrite existing tmux session: %s\n' "$session" >&2
  printf 'attach with: tmux attach-session -t %s\n' "$session" >&2
  exit 2
fi

tmux new-session -d -s "$session" -x 200 -y 60 -n supervisor -c "$repo"
tmux new-window -d -t "$session":1 -n agents -c "$repo"

for _ in 1 2 3 4 5 6 7; do
  tmux split-window -d -t "$session":agents -c "$repo"
  tmux select-layout -t "$session":agents tiled >/dev/null
done

mapfile -t panes < <(
  tmux list-panes -t "$session":agents -F '#{pane_index}:#{pane_id}' |
    sort -t: -k1,1n |
    cut -d: -f2-
)
if [[ ${#panes[@]} -ne 8 ]]; then
  printf 'expected 8 worker panes, got %s\n' "${#panes[@]}" >&2
  exit 2
fi

for i in "${!panes[@]}"; do
  worker=$((i + 2))
  tmux select-pane -t "${panes[$i]}" -T "a${worker}"
  tmux send-keys -t "${panes[$i]}" "cd '$repo'" Enter
done

tmux select-layout -t "$session":agents tiled >/dev/null
tmux select-window -t "$session":supervisor

printf 'created tmux team: %s\n' "$session"
printf '  window 0: supervisor\n'
printf '  window 1: agents (a2-a9, 8 tiled shells)\n'
printf 'attach: tmux attach-session -t %s\n' "$session"
printf 'inspect: tmux list-panes -t %s:agents -F '\''#{pane_index}|#{pane_title}|#{pane_current_command}'\''\n' "$session"
