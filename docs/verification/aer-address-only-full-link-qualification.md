# Address-Only AER Full-Link and PPA-Boundary Qualification

Status: candidate-neutral freeze candidate, 2026-08-10

## Scope

This contract qualifies an address-only AER transport as a complete
synthesizable path:

```text
common source latch -> TX/ingress -> physical link -> RX/egress -> logical delivery
```

It applies unchanged to a conventional reference and to every new candidate.
The normalized common-TB interface is a measurement seam, not a physical pin
contract. In particular, a normalized `ADDR_WIDTH=16` container does not create
an arbitrary 16-bit payload, and a scoreboard `retire_source` sideband is not a
physical link field.

The machine-readable record is defined by
[`full_link_qualification.schema.json`](../../benchmarks/physical_ppa/full_link_qualification.schema.json)
and checked by
[`validate_full_link_qualification.py`](../../benchmarks/physical_ppa/validate_full_link_qualification.py).
A missing or invalid field makes a row `NOT_QUALIFIED`; it does not become a
zero-area or zero-power result.

## Logical event and completion

The mandatory logical event is exactly one occurrence of a source address:

```text
logical_event = (logical_source in [0, N), occurrence_cycle)
```

`logical_source` is the address identity. There is no arbitrary payload.
Polarity, type, deadline, occurrence time, and `tb_only_event_id` are not DUT
payload in this qualification. The source map from logical source to native
code must be bijective and frozen by description plus SHA-256.

An event is accepted only on the frozen native ingress condition. It is
delivered only when the included RX/egress boundary emits enough information to
identify that source occurrence without consulting TB-only identity, future
traffic, or hidden scoreboard state. A packed word or bitmap contributes the
number of distinct logical source occurrences reconstructed at the RX boundary,
not one event merely because one physical word transferred.

The normalized TB may widen a recovered source address to 16 bits and may copy
that source into `retire_source` for matching. Those fields are excluded from
physical pin, state, toggle, area, and power accounting. If identity cannot be
recovered without runtime Boolean or sequential logic, that logic is RX RTL and
must be synthesized and charged.

## One physical boundary for every candidate

The ranked PPA scope is `full_link_tx_link_rx`. It contains all synthesizable
logic required between the common source-latch outputs and completed logical
events at the receiver:

- ingress arbitration and acknowledgement generation;
- TX buffering, packing, encoding, serialization, and retry state;
- every modeled link register, pipeline stage, or CDC element;
- RX sampling, deserialization, decoding, unpacking, buffering, ordering, and
  completion generation; and
- candidate-specific runtime protocol conversion at either endpoint.

The synthesis top and ordered file list must elaborate this entire scope.
Synthesizing a TX-only native core while performing its decode in the TB is not
eligible for full-link PPA ranking. Conversely, synthesizing the normalized
`N * 16` input array or normalized retire buses merely because they appear in
the testbench overcharges an address-only candidate and is also ineligible.

Candidate-specific wrappers are allowed, but every gate, register, memory,
clock gate, and generated clock inside them belongs to the candidate result.
The same source count, logical map, endpoint load, link environment, libraries,
corner, SDC policy, and physical-flow effort apply to all candidates.

## TB seam and free wiring whitelist

Only transformations with no runtime decision or state may remain free outside
the PPA top:

- port rename;
- static bit permutation, slice, or concatenation;
- constant tie required by the frozen mode; and
- zero extension of an already recovered source address into the normalized
  scoreboard container.

The qualification record lists every free mapping and its operation. Anything
else is charged RTL. In particular, the following are never free:

- priority selection, arbitration, or grant history;
- AND/OR qualification that creates an acknowledgement or completion;
- row comparison, bitmap expansion, address encode/decode, or popcount;
- serializer/deserializer cycles, packing/unpacking, compression, or tables;
- buffering, retry, duplicate suppression, ordering repair, or backpressure;
- a mapping that uses current pending requests to disambiguate an otherwise
  ambiguous native result; or
- any recovery that uses `tb_only_event_id`, deadline, trace metadata, or a
  candidate model not present in the synthesizable source bundle.

Assertions and metric collectors may parse native signals outside PPA, but
their result proves only correctness. They cannot stand in for a missing RX or
be cited as RX area, power, timing, or physical completeness evidence.

## Native pins and link pins

Freeze two explicit pin sets.

1. `native_boundary_pins` contains every top-level source-side and sink-side bit
   crossing the full-link PPA top.
2. `link_cut_pins` contains every signal bit crossing one designated TX-to-RX
   transport cut. A wire is listed once at the cut, not once as TX output and
   again as RX input.

For each set, count all data/address, request, valid, ready/acknowledge, lane,
type, framing, and other functional control bits. Exclude only clock, reset,
power, and ground; disclose those excluded ports separately. Count a
bidirectional bit once. A normalized 16-bit event or source sideband is excluded
unless it is an actual port at the frozen physical cut.

```text
native_functional_pin_bits = sum(width of functional native-boundary ports)
link_functional_pin_bits   = sum(width of functional signals at the link cut)

events_per_native_pin_cycle = delivered_events /
                              (measurement_cycles * native_functional_pin_bits)
events_per_link_pin_cycle   = delivered_events /
                              (measurement_cycles * link_functional_pin_bits)
```

Both values must be reported. The link-only value measures transport pin
efficiency; the native-boundary value prevents a candidate from hiding a large
source or sink interface. Neither may silently substitute the normalized TB
port count.

## Activity and energy boundary

Power uses the same synthesized full-link top, candidate configuration,
deterministic trace SHA, clock, and cycle-indexed window used to count delivered
events. The record freezes:

- trace and prepared-input SHA-256;
- clock frequency and measurement start/end cycles;
- annotation format, file SHA-256, hierarchy root, and annotation coverage;
- treatment of clocks, reset, idle cycles, memories, and unannotated nets;
- sparse or near-saturation operating-point label;
- delivered logical event count in exactly that window; and
- measured average full-link power with units and vectorless/activity-annotated
  classification.

Reset, warm-up, and drain are included only when their cycles lie inside the
frozen window. They must not be removed differently per candidate. A window with
no delivered events reports energy per event as undefined and is not a ranked
energy row.

For `power_mW`, `clock_MHz`, and a window of `measurement_cycles`:

```text
events_per_cycle = delivered_events / measurement_cycles
energy_nJ_per_delivered_event = power_mW /
                                (clock_MHz * events_per_cycle)
```

The validator recomputes pin efficiency and energy rather than trusting entered
derived values.

## Freeze fields

Every ranked record freezes these groups before results are compared:

| Group | Required fields |
| --- | --- |
| identity | schema version, qualification ID/status, candidate ID, repository, immutable commit and bundle SHA |
| logical contract | address-only mode, source count/map/hash, one-pending-source rule, exact accept and delivery rules |
| TB seam | normalized address/source widths and retire lanes, explicitly marked PPA-excluded |
| synthesis boundary | full-link scope, synthesis top, file-list/config hashes, parameters, defines, include paths, TX/link/RX inclusion |
| native boundary | every port name, direction, width, role, and native functional-pin total |
| link cut | every cut signal name, direction, width, role, once-only rule, and link functional-pin total |
| mapping | complete free-wiring whitelist entries; explicit no-runtime-decode-in-TB assertion |
| charged logic | TX, RX, codec, buffer, adapter, and link blocks with top/file-list hashes and area/power inclusion |
| physical flow | library/PVT/RC, SDC and tool-config hashes, clock/load/floorplan/effort identity |
| activity | trace/input/activity hashes, exact window, frequency, annotation method/coverage, delivered count, average power |
| derived metrics | events/cycle, both events/pin-cycle values, and energy per delivered event |

## Qualification checklist

- [ ] The logical event is source/address only; no 16-bit arbitrary payload or
      TB-only identity enters the DUT.
- [ ] The source mapping is bijective and its frozen hash matches the run.
- [ ] The simulation binding and the physical synthesis top are named
      separately.
- [ ] The PPA top includes synthesizable TX, link state, and RX/egress.
- [ ] Every free mapping is one of the static whitelist operations.
- [ ] No runtime decode, acknowledgement, arbitration, buffering, or repair is
      performed for free in the TB.
- [ ] Every required codec endpoint is present in the charged block list and
      included in area, timing, activity, and power.
- [ ] Native-boundary and link-cut pins are enumerated bit-for-bit; clock,
      reset, power, and ground are the only excluded roles.
- [ ] Bidirectional and TX/RX link wires are counted once at the designated cut.
- [ ] Post-elaboration top, port, register/memory, and unresolved-reference
      reports match the frozen source/config hashes.
- [ ] Correctness proves zero loss, duplicate, corruption, and phantom delivery
      after complete drain without an uncharged RX.
- [ ] Power annotation covers the same full-link hierarchy and exact trace
      window used for the delivered-event numerator.
- [ ] Sparse and near-saturation rows remain separately labeled.
- [ ] Events per pin-cycle and energy per delivered event are validator-derived;
      zero-delivery windows are not ranked.
- [ ] Records with different boundary scope, endpoint inclusion, pin set,
      source count/map, trace, activity window, clock, PVT/RC, SDC, or tool-flow
      identity are diagnostics only and are not directly ranked.
