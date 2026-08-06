# Native Candidate Workload Capability/Profile Contract

Status: team discussion candidate, 2026-08-06

## Purpose

The common benchmark drives each candidate through its native event protocol.
It does not require every architecture to grow the same ready/valid interface,
FIFO, event-type field, or retire width merely to enter the benchmark.

A profile declares what the native candidate and a storage-free observation
harness can exercise. A workload declares what it needs. The capability
validator resolves the pair before simulation so unsupported optional features
are visible as skips rather than late compile failures or misleading test
failures.

This contract classifies support, not quality. For example, `fairness=true`
means source service can be identified and measured. It does not assert that an
arbiter is fair. A fixed-priority candidate can run the core fairness workload
and receive a poor or unbounded-wait result without a capability failure.

## Native harness boundary

Permitted harness work is observational or structural pin mapping:

- tie an existing sink-ready input high for the core always-ready suite;
- map native source/address pins into the common logical address scoreboard;
- timestamp occurrence, native acceptance, and logical delivery;
- observe each native retire lane and normalize its logical events;
- retain TB-only event identity in the reference model.

The harness must not add a FIFO, elastic buffer, retry protocol, serializer,
polarity field, backpressure state, or extra retire lane to turn an unsupported
feature into a supported one. Such logic is a new candidate implementation and
belongs in its RTL/PPA boundary, not in a capability adapter. A candidate with
no output-ready input is still valid for the always-ready core suite and simply
skips the optional backpressure suite.

## Capability sets

### Mandatory core

Every eligible native candidate must support these common measurements:

| Capability | Contract meaning |
| --- | --- |
| `sink_always_ready` | Candidate can run with a continuously accepting sink, either natively or by tying its existing ready high |
| `address_event_correctness` | A delivered native event can be normalized to the generated logical source/address |
| `occurrence_to_delivery_latency` | Occurrence and logical delivery cycles are observable in the common clock domain |
| `loss_duplicate_phantom` | Accepted and delivered logical events can be matched without placing TB identity in DUT payload |
| `fairness` | Source/request service identity is observable for per-source counts and wait analysis |

The core suite measures always-ready address-event correctness, complete-drain
loss/duplicate/phantom behavior, occurrence-to-delivery latency, and fairness.
Its logical event identity is source/address only; generated polarity or type
metadata is not scored unless the optional polarity/event-type suite is RUN.
Missing a required core capability produces `HARD_FAIL_CORE_UNSUPPORTED` before
the workload runs. This is candidate ineligibility for the frozen common core,
not a claim that simulation found data corruption.

### Optional native features

| Capability | Optional workload scope |
| --- | --- |
| `output_backpressure` | Sink stalls and output payload/valid stability |
| `polarity_event_type` | Preservation of native polarity or event-type semantics |
| `multi_lane_retirement` | Observation and accounting of more than one logical retirement in a cycle |

If an optional workload requires an unsupported optional capability, its result
is `SKIP_UNSUPPORTED`. SKIP is neither PASS nor FAIL and must remain in summary
counts. No adapter or FIFO is required to eliminate it.

## Profile and workload schemas

Both files are JSON with `schema_version: 1`. A profile declares every known
capability explicitly:

```json
{
  "schema_version": 1,
  "candidate": "native_candidate_id",
  "capabilities": {
    "sink_always_ready": {"supported": true},
    "address_event_correctness": {"supported": true},
    "occurrence_to_delivery_latency": {"supported": true},
    "loss_duplicate_phantom": {"supported": true},
    "fairness": {"supported": true},
    "output_backpressure": {
      "supported": false,
      "reason": "native output has no ready input"
    },
    "polarity_event_type": {"supported": true},
    "multi_lane_retirement": {
      "supported": false,
      "reason": "one native retire lane"
    }
  }
}
```

Unsupported declarations require a non-empty reason. A workload entry names its
suite and requirements:

```json
{
  "name": "optional_output_backpressure",
  "suite": "optional",
  "required_capabilities": [
    "address_event_correctness",
    "loss_duplicate_phantom",
    "output_backpressure"
  ]
}
```

A `core` workload cannot depend on optional capabilities. An `optional`
workload must name at least one optional capability. Unknown, missing, or
duplicate declarations are contract errors, not SKIPs. Across all entries, the
core suite must cover every mandatory core capability; omission from the
workload file cannot be used to hide a core incompatibility.

## Pre-run decisions and exit status

Decision precedence for each workload is:

1. any unsupported required core capability: `HARD_FAIL_CORE_UNSUPPORTED`;
2. otherwise, any unsupported required optional capability: `SKIP_UNSUPPORTED`;
3. otherwise: `RUN`.

The validator emits candidate, workload, suite, decision, unsupported capability
names, and profile reasons as CSV or JSON. Exit status is 0 for any mixture of
RUN and optional SKIP, 2 when a core hard failure exists, and 3 for a malformed
contract or I/O error.

```sh
python3 benchmarks/clean_slate_aer/capabilities.py \
  --profile benchmarks/clean_slate_aer/fixtures/capability_profile_native_minimal.json \
  --workloads benchmarks/clean_slate_aer/fixtures/workload_capability_requirements.json

python3 benchmarks/clean_slate_aer/capabilities.py \
  --format json --output /tmp/capability-decisions.json \
  --profile candidate-profile.json --workloads workload-requirements.json
```

Capability decisions must be stored beside the normal clean-benchmark result.
Only workloads marked RUN may be launched. Result aggregation must not convert a
SKIP into zero throughput, zero fairness, a correctness failure, or a PASS.

## Review checklist

- [ ] profile describes native RTL, not capability-adding testbench storage;
- [ ] the common core workload requires all five core capabilities;
- [ ] candidate identity matches the result aggregator identity;
- [ ] all optional omissions have concrete reasons and remain visible as SKIP;
- [ ] TB-only event IDs remain outside the DUT payload;
- [ ] always-ready, backpressure, polarity/type, and multi-lane results are not
      pooled into one unlabeled workload result;
- [ ] fairness support is not confused with a fairness guarantee;
- [ ] any synthesizable feature adapter is charged as candidate RTL and PPA.
