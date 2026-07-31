# tmux 작업 환경 및 복구 방법

## 최종 구성

tmux 그룹 세션 두 개를 사용한다. 그룹 세션은 같은 창과 프로세스를 공유하지만, 각 터미널이 선택하는 창은 독립적이다.

| 세션 | 표시 창 | 패인 구성 | 용도 |
| --- | --- | --- | --- |
| `dev` | 창 0 | Codex 패인 0·1·2, 가로 균등 3분할 | 병렬 Codex 작업 |
| `dev-shell` | 창 1 | 패인 0: 관리 쉘, 패인 1: SSH, 가로 균등 2분할 | Git/worktree 관리 및 SSH |

현재 worktree는 다음과 같다.

| 경로 | 브랜치 | 권장 용도 |
| --- | --- | --- |
| `/home/chickgoose/projects/AI-semi` | `main` | 관리·병합 |
| `/home/chickgoose/projects/a1` | `a1` | Codex 1 |
| `/home/chickgoose/projects/a2` | `a2` | Codex 2 |
| `/home/chickgoose/projects/a3` | `a3` | Codex 3 |

## 복구

tmux 서버와 패인이 살아 있다면 다음 순서로 복구한다.

```bash
tmux select-layout -t dev:0 even-horizontal
tmux select-layout -t dev:1 even-horizontal
```

그 다음 터미널 두 개를 각각 열어 아래 명령으로 연결한다.

```bash
# 터미널 A: Codex 3패인 창 0
tmux attach-session -t dev

# 터미널 B: 관리 쉘 + SSH 창 1
tmux attach-session -t dev-shell
```

## 안전 규칙

- 원본 세션에 창 번호를 직접 붙여 연결하지 않는다. 예: `tmux attach-session -t dev:1` 금지.
  이 방식은 다른 `dev` 클라이언트의 활성 창을 바꿔, 창이 사라진 것처럼 보이게 할 수 있다.
- 별도 터미널 표시가 필요하면 먼저 `tmux new-session -d -t dev -s <전용-세션>`으로 그룹 세션을 만들고, 그 전용 세션에서 선택 창을 설정한 뒤 연결한다.
- 패인을 이동·추가한 뒤에는 대상 창에 `tmux select-layout -t <세션>:<창> even-horizontal`을 적용해 너비를 균등화한다.
- tmux를 변경하기 전과 후에 `tmux list-sessions`, `tmux list-windows -a`, `tmux list-panes -a`, `tmux list-clients`로 상태를 확인한다.
- Codex 패널이 실제 worktree를 사용하려면 해당 패널에서 종료 후 `cd /home/chickgoose/projects/a1`처럼 worktree로 이동해 다시 실행한다.

