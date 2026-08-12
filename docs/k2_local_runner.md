# A1 K2 local runner and receipt

This scaffold executes candidate-neutral K2 promotion evidence. It is an
orchestrator and integrity boundary, not an audit report and not a simulator
claim. A candidate is eligible only when the same A1-owned boundary suites run
against its attached source closure with `RETIRE_LANES=2`.

The runner never edits or regenerates the frozen common testbench or manifests.
The command plan points a separately versioned K2 driver at those inputs. The
driver and candidate adapters are responsible for exposing the identical K2
boundary to A2 and A3.

## Invocation

```sh
python3 scripts/k2_local_runner.py \
  --candidate a2-k2 \
  --top a2_k2_common_boundary \
  --filelist /absolute/path/to/a2-k2.f \
  --define K2_COMMON_BOUNDARY=1 \
  --param RETIRE_LANES=2 \
  --param NUM_SOURCES=16 \
  --tool k2-driver=/absolute/path/to/k2-driver \
  --command-plan configs/k2_local_plan.example.json \
  --output-root /tmp/k2-local-runs
```

The filelist grammar is deliberately narrow: each nonblank, non-comment line
is one literal source path relative to the filelist. Flags, nested filelists,
and include switches are rejected. Defines and parameters are explicit,
deduplicated CLI values. `RETIRE_LANES=2` must be present exactly; omitting it
or selecting any other value fails before an attempt can receive a receipt.

The plan is an argv-array format, never a shell command. Supported tokens are:

- `@K2_TOOL:name@`: an explicitly supplied, snapshotted executable.
- `@K2_OUTPUT:name@`: a declared current or prior stage output.
- `@K2_TOP@`, `@K2_FILELIST@`, `@K2_RUN_DIR@`: scalar values.
- `@K2_SOURCES@`, `@K2_DEFINES@`, `@K2_PARAMS@`: argv-list expansions.

Every compile plan must consume the top, filelist, define, and parameter
tokens, and every suite must consume a declared compile output. A plan that
merely records candidate metadata while running disconnected commands is
rejected. Commands run with a sanitized, recorded environment (`PATH`, locale,
timezone, and attempt-local `TMPDIR`); required license or tool variables must
be passed explicitly with repeatable `--env NAME=VALUE`. `K2_*` and fixed
environment keys cannot be overridden.
Explicit environment values are stored in command evidence, so they must not
contain secrets.
Each command is bounded by `--timeout-seconds` (600 seconds by default); a
timeout kills the command process group and cannot publish a receipt.

The example `k2-driver` interface is illustrative. A production driver must be
a self-contained executable whose compile and run modes use only the attached
candidate filelist and the frozen A1 suite inputs. Any generated build tree must
be declared as a `tree` output; every file under it is hashed and attached.

## Fail-closed order and evidence

The only accepted prefix is:

1. `compile`
2. `directed_trace`
3. `reset_drain`

`full50` and `capacity22` are optional later stages and can be selected with
`--enable-suite`; they can never run ahead of the directed/reset gates. A
nonzero version, compile, or run exit stops the attempt without `receipt.json`.

Before execution, the runner copies every source, the original and rewritten
filelists, command plan, each tool executable, and the runner/verifier sources
themselves into a unique no-reuse staging attempt. It records tool version
output, the sanitized environment, canonical argv descriptors, stdout/stderr
logs, source/tool/orchestrator hashes, prior-output input bindings, and all
declared output hashes. Originals are re-read after every stage, and attached
outputs are re-read before publication. The complete bundle is independently
verified in staging and then atomically renamed to its public `attempt-*` name.

Every suite must create one fresh JSON `suite_result` with exactly these keys:

```json
{
  "schema_version": 1,
  "suite": "directed_trace",
  "status": "PASS",
  "stage_command_sha256": "from K2_STAGE_COMMAND_SHA256",
  "stage_input_sha256": "from K2_STAGE_INPUT_SHA256",
  "execution_challenge": "from K2_STAGE_CHALLENGE",
  "checks": [
    {
      "name": "order_and_conservation",
      "status": "PASS",
      "evidence": {"accepted": 100, "retired": 100, "errors": 0}
    }
  ]
}
```

A PASS word in a log, an empty `checks` array, copied/stale output, a mismatched
challenge or input/command hash, a missing output, an undeclared file, or an
unattached digest is rejected. This makes accidental or manually copied
evidence fail closed. It does not turn an untrusted testbench into a trust
anchor; promotion must pin the A1 suite/driver hash and preserve the detached
receipt hash printed by the runner.

Verify a transported bundle with:

```sh
python3 scripts/k2_local_receipt.py /path/to/attempt \
  --expected-receipt-sha256 SHA256_PRINTED_BY_THE_RUNNER
```

## A2/A3 promotion integration

1. Land the A1-owned normalized K2 directed/reset suite and driver without
   modifying frozen common assets. Its result checks must cover count 0/1/2,
   atomic/partial-ready behavior, lane order, held output, reset/drain, and
   duplicate/phantom/reorder conservation.
2. Give A2 and A3 separate adapters with the same normalized boundary and one
   explicit filelist each. Candidate/team RTL remains read-only input.
3. Run this orchestrator twice with the same plan and tool hashes, the same
   `RETIRE_LANES=2` and common parameters, and only the candidate name/top/
   source closure changed. Store both detached receipt SHA256 values.
4. Verify both bundles independently. Do not promote either candidate if the
   mandatory three-stage prefix or boundary hashes differ.
5. Only after both directed/reset receipts pass, rerun with `--enable-suite
   full50 --enable-suite capacity22`. Those hooks are present now but do not
   imply official-suite qualification until their A1 driver is integrated.

## Self-test

```sh
python3 -m unittest -v tests.k2_local_runner.test_k2_local_runner
```

The test suite includes missing and changed sources, nonzero compile and run,
stale output, sentinel-only fake evidence, wrong execution bindings, missing
bundle members, undeclared files/hashes, and the fixed two-lane gate.
