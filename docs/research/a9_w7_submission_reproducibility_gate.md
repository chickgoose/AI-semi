# A9 W7 submission and reproducibility gate

Status: **local submission packaging GO; physical evidence HOLD**.

W7 does not reopen either A9 physical profile.  It creates a deterministic,
no-overwrite handoff receipt for one exact clean Git commit and makes absence of
physical evidence machine visible.  The current trusted policy has no approved
physical tool, characterized library, PVT corner, executed SDC, or trusted
result parser.  Therefore a caller-provided PASS marker, report, log, tool name,
or hash cannot promote the receipt beyond `HOLD`.

## Bound content

For each profile the receipt records the exact commit and tree, top, parameters,
defines, filelist bytes, ordered primary and transitive source closure, and each
Git blob's SHA-256 and size.  The physical contract always has explicit tool,
library, PVT, and SDC fields.  Missing bindings are `UNBOUND`, never inferred
from a shell environment or report text.

The Windows inventory records every selected repo-relative path, byte count,
and SHA-256 for reconstruction below
`C:\Users\박준영\AI-semi`.  It deliberately excludes `build/`, `results/`,
`reports/`, `vivado/`, `.agents/`, `.codex/`, Git metadata, caches, logs, and
waveforms.  It is an inventory, not permission to copy user-owned results.

The receipt hash covers the canonical JSON document before the `receipt` field.
It does not hash itself and therefore has no circular self-hash.  Generation
requires a clean tracked/untracked worktree, rejects an existing output path,
creates a private new directory, and creates both outputs with `O_EXCL`.

## Trust and attack boundary

The gate invokes only the absolute `/usr/bin/git` whose SHA-256 and exact
version are pinned in the trusted policy, with a sanitized PATH and locale.
Caller PATH cannot substitute a fake Git.  Source identities are read from the
bound commit, not mutable worktree bytes.  The manifest records the validator,
policy, schema, Python executable, and Git identities as bootstrap metadata.

The validator and policy remain external bootstrap trust inputs.  This design
does not claim that a hostile party can replace both and then use the modified
program to attest itself.  A physical GO requires a reviewed policy commit that
enables release and pins nonempty approved tool and trusted result-parser
closures.  Hashing a self-authored PPA text file is insufficient: current policy
rejects every nonempty physical claim/evidence tuple.  If artifact validation is
used in a future enabled policy, result and log files must be regular,
single-link files below an explicit evidence root with exact size and SHA-256.

The pinned `/usr/bin/git` policy is the Linux/WSL validation environment.  The
Windows manifest is portable for inspection and copying, but native Windows
validation requires a separately reviewed Windows Git executable identity in a
future policy; it must not silently fall back to PATH.

## Commands

Run after the W7 files are committed and the worktree is clean:

```sh
out=$(mktemp -d /tmp/a9-w7-parent.XXXXXX)/static-n64
python3 scripts/a9_w7_submission_gate.py generate \
  --profile static_n64_timing --output "$out"
python3 scripts/a9_w7_submission_gate.py validate \
  "$out/a9_w7_submission_manifest.json"

python3 -m unittest discover \
  -s tests/a9_w7_submission_gate -p 'test_*.py'
```

Expected status is `A9_W7_SUBMISSION_VALID status=HOLD physical=ABSENT`.
`GO`, nonempty physical result/log claims, output reuse, source/filelist
omission, fake PATH Git, artifact hash mismatch, hard links, or untracked files
fail closed.
