# Independent UZH six-arm generator acceptance tests

This suite fixes the black-box production API at:

```python
from benchmarks.redred_uzh_mc_wtb_six_arm_generator import GeneratorFailure, generate, inspect

generate(pose_join_dir, join_spec_path, adapter_dir, retire_receipt_path,
         generator_spec_path, result_dir)
inspect(result_dir, pose_join_dir, join_spec_path, adapter_dir,
        retire_receipt_path, generator_spec_path)
```

Production is loaded from `REDRED_SIXARM_PRODUCTION_ROOT`; the tests never copy
generator or production geometry helpers into the oracle. The always-on suite
builds its own source archive, pose join, correct adapter package, and
hash-bound canonical JSONL retire stream and frozen generator spec. The fixture
is deliberately non-official and must receive exactly
`PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE`, never
`PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED`.

The generated package is exactly:

```text
controls_six_arm.jsonl
receipt.json
COMPLETE.json
```

The test-owned oracle independently performs normalized xyzw shortest-arc
SLERP, OpenCV radtan inversion, camera-to-world matrices, current-to-reference
and deliberately transposed rotations. It checks exact event identity,
one-record/six-arm conservation, the `4,998,186 ns` delayed lookup, per-ID
retire lookup provenance, wrong-direction separation, status/payload
coherence, output/source tamper, coherent output rehash, deterministic bytes,
source-required inspection, false benefit/official claims, and explicit
rejection of the A23 1x replay with 81 overruns and only 1,019/1,100 retires.

Run synthetic acceptance from the repository root with:

```bash
REDRED_SIXARM_PRODUCTION_ROOT="$(pwd)" \
  bash tests/redred_uzh_mc_wtb_sixarm_independent/run_all.sh
```

The official case is opt-in. It does not invent or download a retire receipt.
Even when enabled, it reports HOLD/skip until all five paths exist, especially
one actual source-epoch-bound retire JSONL and its pre-pinned generator spec:

```bash
REDRED_RUN_SIXARM_OFFICIAL=1 \
REDRED_UZH_JOINED_ROOT=/path/to/completed-pose-join \
REDRED_UZH_JOIN_SPEC=/path/to/join_spec.json \
REDRED_UZH_ADAPTER_ROOT=/path/to/completed-adapter \
REDRED_UZH_RETIRE_RECEIPT=/path/to/actual-retire-receipt.jsonl \
REDRED_SIXARM_GENERATOR_SPEC=/path/to/frozen-generator-spec.json \
REDRED_SIXARM_APPROVED_GENERATOR_SPEC_SHA256=/externally/reviewed/lowercase-sha256 \
REDRED_SIXARM_PRODUCTION_ROOT="$(pwd)" \
  bash tests/redred_uzh_mc_wtb_sixarm_independent/run_all.sh
```

An official run may pass only after production validates the original
pose-join/spec, adapter, actual retire JSONL, and frozen generator spec. It
remains a scoped geometry-control generator result, never codec, bandwidth,
benefit, RTL, or PPA evidence.
