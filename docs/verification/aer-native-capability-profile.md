# Native Candidate Workload Capability/Profile Contract

Status: team-approved native-binding contract, 2026-08-10

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

The common suite retains a candidate-specific, TB-only native binding. Earlier
`no-binding`, direct-inline, or binding-removal instructions are superseded;
they do not invalidate any already-approved workload, trace, scoreboard, or
metric improvement. The retained binding is a **zero-feature binding**: it may
express an existing native protocol to the common TB, but may not supply a
hardware feature absent from the DUT.

Permitted binding work is limited to:

- tie an existing sink-ready input high for the core always-ready suite;
- wire, slice, concatenate, or statically relabel existing native pins;
- expand a delivered row/address plus bitmap combinationally into same-cycle
  logical-source observation pulses, with no history or event-bearing state;
- drive the acknowledge timing required by an existing native handshake under
  the workload's declared sink policy; ACK phase tracking may not buffer an
  event, choose among events, retry one, or decouple DUT service from TB service;
- timestamp occurrence, native acceptance, and logical delivery;
- observe each native retire lane and normalize its logical events;
- retain TB-only event identity in the reference model.

The binding must not add event storage (including a pending latch or FIFO),
arbitration, scheduling, retry, serialization, a new ready/backpressure
capability, coding, payload fields, retire lanes, or functional reconstruction.
In particular, decoding that depends on history—repeat/delta/compressed-symbol
state, prior addresses, a dictionary, or any other stateful decoder—is DUT RTL,
must be synthesized, and is charged inside that candidate's PPA boundary. A
stateless address/row-bitmap expansion used only by the scoreboard is not such
a decoder.

The zero-feature binding is non-synthesizable TB infrastructure and is excluded
from synthesis and PPA. This exclusion is valid only while every operation
stays within the permitted list above. A candidate with no output-ready input
is still valid for the always-ready core suite and simply skips the optional
backpressure suite.

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
capability explicitly and records its native protocol, source-count shape,
source observability, and physical retire lanes:

```json
{
  "schema_version": 1,
  "candidate": "native_candidate_id",
  "native_interface": {
    "protocol": "candidate_native_protocol",
    "source_count": {"kind": "fixed", "value": 16},
    "source_observable": true,
    "retire_lanes": 1
  },
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
suite, logical source count, and requirements:

```json
{
  "name": "optional_output_backpressure",
  "suite": "optional",
  "source_count": 16,
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

`source_count.kind` is either `fixed` with one positive `value`, or
`parameterized` with a positive `minimum` and optional `maximum`. A source-count
mismatch is `SKIP_UNSUPPORTED`, not a fabricated core correctness failure. The
profile's `multi_lane_retirement` declaration must agree with whether native
`retire_lanes` is greater than one, and fairness support requires
`source_observable=true`.

## Repository profiles and current-reference status

The checked profiles below describe interfaces used by historical reproduction
and calibration runs; they are not current-reference or score claims. Raw
cluster2 is the current address-only reference, but it must have its own frozen
profile/binding identity before a 50/22 result can be ranked.

| Candidate profile | Native sources/protocol | Core N=16 | Backpressure | Polarity/type | Multi-lane |
| --- | --- | --- | --- | --- | --- |
| `ganghee_trad_rowcol_fovea` | fixed 16, level request input and `valid + addr[3:0]` direct-coordinate output, source-observable, one lane | RUN | SKIP | SKIP | SKIP |
| `baseline` | parameterized per-source ready/valid, source sideband, one retire lane | RUN | RUN | SKIP | SKIP |
| `a23-ee430` | parameterized per-source ready/valid, source sideband, one retire lane | RUN | RUN | SKIP | SKIP |

For baseline and A23, backpressure support comes from their native `out_ready`
contract. Both native payloads carry address/source but no polarity or event-type
field. Baseline's fairness capability means its fixed-priority service can be
measured; it does not convert the policy into bounded fairness. Ganghee's profile
uses the supplied fixed N=16 native contract and does not add ready, FIFO, event
type, or parallel retire hardware.

The `rowcol_fovea` name describes internal row/column arbitration. It does not
make the native output a physical ROW/COL serial protocol: the connected
`aer_tx16_trad_rowcol_fovea` boundary emits a direct four-bit coordinate with
`valid` and has no `addr_type` output.

## Pre-run decisions and exit status

Decision precedence for each workload is:

1. any unsupported required core capability: `HARD_FAIL_CORE_UNSUPPORTED`;
2. otherwise, any unsupported required optional capability: `SKIP_UNSUPPORTED`;
3. otherwise: `RUN`.

The validator emits candidate, workload, suite, decision, unsupported capability
names, source-count mismatch, native protocol/retire width, and profile reasons
as CSV or JSON. Exit status is 0 for any mixture of RUN and SKIP, 2 when a core
hard failure exists, and 3 for a malformed contract or I/O error. Repeat
`--profile` to compare candidates against the exact same workload file.

```sh
python3 benchmarks/clean_slate_aer/capabilities.py \
  --profile benchmarks/clean_slate_aer/fixtures/capability_profile_native_minimal.json \
  --workloads benchmarks/clean_slate_aer/fixtures/workload_capability_requirements.json

python3 benchmarks/clean_slate_aer/capabilities.py \
  --format json --output /tmp/capability-decisions.json \
  --profile candidate-profile.json --workloads workload-requirements.json

python3 benchmarks/clean_slate_aer/capabilities.py --format json \
  --workloads benchmarks/clean_slate_aer/fixtures/workload_capability_requirements.json \
  --profile benchmarks/clean_slate_aer/fixtures/capability_profile_ganghee_trad_rowcol_fovea.json \
  --profile benchmarks/clean_slate_aer/fixtures/capability_profile_baseline.json \
  --profile benchmarks/clean_slate_aer/fixtures/capability_profile_a23_ee430.json
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
