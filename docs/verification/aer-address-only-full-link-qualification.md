# Address-Only AER Full-Link and PPA-Boundary Qualification

Status: **GO for gate implementation**, candidate-neutral schema v5, 2026-08-10.
This releases the validator contract, not any candidate result. No record is
ranked until that individual record passes schema validation, actual artifact
digest verification, hierarchy/accounting closure, and all physical result
gates.

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

Schema v5 is intentionally fail-closed. The validator executes the schema's
`required`, `type`, `const`, `enum`, bounds, uniqueness, and
`additionalProperties: false` rules before checking cross-field invariants.
Unknown result fields, wrong types, and incomplete nested evidence are therefore
rejected rather than ignored. Every evidence value is a `{path, sha256}` record
relative to the qualification JSON. The validator reads the named file, rejects
absolute or non-normalized paths, symlinks in the artifact path, and non-regular
files, verifies that file metadata is unchanged across the read, and compares
the SHA-256 against the bytes actually read. Every component from the artifact
base directory to the filesystem root must also be non-symlink. One path and
inode may serve only one evidence role, with one SHA snapshot; path reuse,
hard-link reuse, or contradictory SHA records fail qualification.

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

The synthesis top and ordered file list must elaborate this entire scope. The
candidate bundle inventory contains an ordered `{path, sha256}` entry for every
source and is read and verified by the validator. Its order and path set must
exactly equal the separately digested file list.
Synthesizing a TX-only native core while performing its decode in the TB is not
eligible for full-link PPA ranking. Conversely, synthesizing the normalized
`N * 16` input array or normalized retire buses merely because they appear in
the testbench overcharges an address-only candidate and is also ineligible.

Candidate-specific wrappers are allowed, but every gate, register, memory,
clock gate, and generated clock inside them belongs to the candidate result.
The same source count, logical map, endpoint load, link environment, libraries,
corner, SDC policy, and physical-flow effort apply to all candidates.

## Physical feature declarations and charged blocks

Every record explicitly declares whether it contains physical codec,
serializer, deserializer, buffer, CDC/clocking, and normalizer/adapter
instances. Absence is represented by an empty declaration array; omission is
invalid. Each present declaration names exactly one `charged_blocks` entry and
matches its hierarchy path and compatible block kind. Conversely, every charged
feature block must have exactly one declaration. A charged block cannot be
reused to satisfy two declarations. Declarations do not carry a candidate-owned
copy of hierarchy evidence; the trusted inventory producer supplies that side
of the comparison.

Serializer and deserializer declarations must either both be present or both be
empty. A runtime-decoded link still requires separately charged encoder and
decoder blocks. TX, link, and RX blocks are mandatory regardless of which
optional feature arrays are empty.

The declaration is accounting metadata, not an authorization to put the
zero-feature TB binding into the physical top. The record must assert
`zero_feature_tb_binding_excluded=true`; scoreboard normalization remains
outside area, timing, activity, and power. Any runtime normalizer required for
delivery is instead a physical `normalizer` or `adapter` charged block.

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

The trusted inventory producer extracts the synthesis-top ANSI ports and
annotated mapped link-cut nets directly from the mapped structural netlist. The
generated name, direction, width, and role arrays must exactly equal
`native_boundary_pins` and `link_cut.pins`, in order and bit-for-bit. The
validator derives both functional totals afterward; a record cannot shrink a
wide native or link signal to one bit while adjusting only its entered total.

The link-cut result is intentionally limited to this annotation contract. It
proves that annotated mapped signals were inventoried and compared by name,
direction, width, and role; it does **not** trace electrical connectivity from
TX logic through each bit to RX logic or prove endpoint reachability. Such
connectivity remains unverified by this gate and must not be claimed from a
`QUALIFIED` record alone.

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

The activity `hierarchy_root` must exactly equal `candidate.synthesis_top`.
Each record freezes a strictly positive annotation-coverage threshold; measured
coverage must meet or exceed it. A frozen ranked row additionally requires
activity-annotated power and positive coverage. The power report and common
functional result are opened and digest-verified like every other artifact;
their entered SHA strings are not accepted as proof by themselves. The trusted
evidence extractor parses hierarchy root, annotation format/coverage, exact
window and cycle count from the raw activity report. It also parses and compares
candidate ID, test ID, seed, error count, delivered events, measurement cycles,
and average power across the power and common-result reports.

Schema v5 freezes one `clock_port` and `clock_period_ns` in the flow and
activity records. The trusted SDC parser requires exactly one canonical
`create_clock`, extracts its port and period, and compares them exactly against
the flow record and trusted inventory. Activity and power raw evidence must each
repeat the same port, period, and MHz value; `clock_mhz` must exactly equal
`1000 / clock_period_ns`. A faster claimed activity clock cannot be paired with
a slower SDC. Before parsing the canonical line, the parser scans all
uncommented SDC text for every `create_clock` and `create_generated_clock`
command token. A second, generated, or noncanonical clock therefore fails
closed instead of being ignored by the canonical-line matcher.

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

## Physical evidence closure

A qualified record binds the elaborated and mapped implementation to immutable
evidence. Every item below is a mandatory path-plus-SHA-256 artifact record and
the digest is checked against the actual stable-read bytes:

- post-elaboration report, synthesis hierarchy report, aggregate synthesis
  evidence, and mapped netlist;
- area and pipeline-stage reports;
- setup and hold timing reports;
- detailed-route, unconstrained-path, and DRC reports;
- activity file, activity-annotated power report, and the common functional
  result used for the delivered-event numerator; and
- the candidate bundle inventory and ordered file list, each source named by the
  bundle, and per-charged-block hierarchy/declaration evidence.

The candidate does not author the authoritative mapped/feature inventories.
[`generate_full_link_inventory.py`](../../benchmarks/physical_ppa/generate_full_link_inventory.py)
is the flow-owned producer, and the validator independently reruns its pure
production function. The canonical output records the producer hash, exact
file-based command, and the SHA-bound bundle, file list, mapped netlist,
hierarchy source, and synthesis-command inputs. The synthesis command in turn
must close its exact tool config, SDC, file list, include files, generated IP,
libraries, mapped-netlist output, and hierarchy output.

The producer traverses the complete reachable mapped module graph beginning at
the synthesized wrapper, including the wrapper itself and externally owned
leaf instances. The wrapper must be owned by the candidate source closure.
`top_ownership=candidate` and
`flatten_policy=preserve_candidate_hierarchy` are explicit and mandatory. It
maps candidate modules back to verified bundle/generated-IP sources and
requires the hierarchy export to cover that discovered candidate set exactly. The
regenerated block set, paths, kinds, tops, and sources must equal
`charged_blocks`; the regenerated feature tuples must equal the codec,
serializer, deserializer, buffer, CDC, and normalizer declarations. Therefore a
candidate cannot obtain a pass by deleting a serializer/FIFO/CDC from both its
declaration and submitted inventory while it remains in the netlist.

Physical numbers are not accepted from record fields or arbitrary “verified”
text. [`extract_full_link_evidence.py`](../../benchmarks/physical_ppa/extract_full_link_evidence.py)
parses canonical raw reports and emits producer-hash, command, raw-report SHA,
and frozen-context bindings. The validator independently regenerates those JSON
objects and derives mapped cells, area, pipeline stages, setup/hold WNS, route,
unresolved/unconstrained/DRC counts, activity coverage/window, power, and common
functional counts. Rehashing an edited canonical number without the matching
raw report and trusted reproduction is rejected.

Every raw summary also has a distinct flow manifest. It must bind a nonempty
tool name and version, exact command tokens, `exit_code=0`, `status=success`, a
separately hashed artifact containing the asserted `FLOW_SUCCESS` sentinel, all
frozen input path/SHA records, and raw-summary plus sentinel output path/SHA
records. Its `flow_id`, tool/version, flow-script path/SHA, and `command[0]`
must exactly match the repository-owned
[`approved_execution_registry.json`](../../benchmarks/physical_ppa/approved_execution_registry.json).
The validator stable-reads the registered real script and verifies its digest;
a manifest cannot approve its own nonexistent tool or flow. The validator also
reads and verifies the sentinel, inputs, and outputs. An arbitrary “verified”
summary or a success string without the bound artifacts does not qualify.

The corresponding results must report a positive mapped-cell count and area,
an explicit nonnegative pipeline-stage count, setup and hold WNS greater than or
equal to zero, completed detailed route, zero unresolved references, zero
unconstrained paths, and zero DRC violations. A hash without the associated
path and result fields, or result fields without verified artifact bytes, is
incomplete.

## Freeze fields

Every ranked record freezes these groups before results are compared:

| Group | Required fields |
| --- | --- |
| identity | schema v5, qualification ID/status, candidate ID, repository, immutable commit and bundle-inventory path/SHA |
| logical contract | address-only mode, source count/map artifact, one-pending-source rule, exact accept and delivery rules |
| TB seam | normalized address/source widths and retire lanes, explicitly marked PPA-excluded |
| synthesis boundary | full-link scope, synthesis top, verified ordered file list/bundle/config artifacts, parameters, defines, include paths, TX/link/RX inclusion |
| native boundary | generated mapped-top and record port arrays with every name, direction, width, role, and derived functional-pin total |
| link cut | generated mapped-cut and record signal arrays with every name, direction, width, role, once-only rule, and derived functional-pin total |
| mapping | complete free-wiring whitelist entries; explicit no-runtime-decode-in-TB and zero-feature-binding-excluded assertions |
| feature declarations | explicit codec/serializer/deserializer/buffer/CDC/normalizer arrays with 1:1 charged-block, hierarchy, and evidence mapping |
| charged logic | mandatory TX/link/RX and every physical feature block with top/hierarchy evidence, exact source ownership, and area/timing/activity/power inclusion |
| physical flow | repo-approved flow ID, exact tool/version/script SHA/command[0]/exit/status/sentinel/input/output manifest; trusted producer/extractor hashes and commands; SHA-bound tool config/SDC/filelist/include/generated-IP/library/netlist/hierarchy closure; wrapper ownership/flatten policy; regenerated inventory/results |
| activity | raw and regenerated trace/input/activity/power/common-result evidence; candidate/test/seed/errors; SDC-exact clock port/period/MHz; exact top/window; positive coverage threshold; delivered count and power |
| derived metrics | events/cycle, both events/pin-cycle values, and energy per delivered event |

## Qualification checklist

- [ ] The logical event is source/address only; no 16-bit arbitrary payload or
      TB-only identity enters the DUT.
- [ ] The source mapping is bijective and its frozen hash matches the run.
- [ ] Every evidence artifact is a normalized relative path plus SHA-256; it is a
      regular non-symlink file whose actual stable-read digest matches; its base
      ancestors are non-symlinks and its path/inode is not reused by another role.
- [ ] The verified bundle inventory and ordered file list contain exactly the
      same source paths in the same order.
- [ ] The synthesis command binds its tool config, SDC, file list, every include,
      generated IP and library, plus mapped-netlist and hierarchy outputs.
- [ ] SDC, flow, inventory, activity, and power evidence agree exactly on clock
      port and period; activity/power MHz equals `1000 / period_ns`.
- [ ] The simulation binding and the physical synthesis top are named
      separately.
- [ ] The PPA top includes synthesizable TX, link state, and RX/egress.
- [ ] Every free mapping is one of the static whitelist operations.
- [ ] No runtime decode, acknowledgement, arbitration, buffering, or repair is
      performed for free in the TB.
- [ ] The zero-feature TB binding is explicitly excluded from PPA; any physical
      normalizer or adapter has its own declaration and charged block.
- [ ] Codec, serializer, deserializer, buffer, CDC/clocking, and
      normalizer/adapter arrays are all present, including explicit empty arrays.
- [ ] Every declared feature and every charged feature block form one unique
      bidirectional mapping with the identical trusted-produced hierarchy path.
- [ ] The flow-owned producer independently regenerates mapped hierarchy and
      source ownership from netlist/hierarchy/sources and exactly closes against
      charged blocks.
- [ ] The complete mapped graph includes the candidate-owned wrapper, uses the
      hierarchy-preserving flatten policy, and exposes hidden wrapper features.
- [ ] The regenerated feature inventory exactly equals all physical feature
      declarations, with no hidden serializer, FIFO/buffer, CDC, codec, or normalizer.
- [ ] Every required codec endpoint is present in the charged block list and
      included in area, timing, activity, and power.
- [ ] Native-boundary and link-cut pins are enumerated bit-for-bit; clock,
      reset, power, and ground are the only excluded roles.
- [ ] Generated mapped top-port and link-cut arrays exactly equal the record
      arrays before functional pin totals are accepted.
- [ ] Bidirectional and TX/RX link wires are counted once at the designated cut.
- [ ] Post-elaboration top, port, register/memory, and unresolved-reference
      reports match the frozen source/config hashes.
- [ ] Synthesis hierarchy/netlist, area/stage, setup/hold, route,
      unconstrained-path, DRC, activity, power, and common-result raw evidence is
      independently extracted and matches the canonical JSON and record fields.
- [ ] Every raw summary matches a repo-owned approved flow ID, actual
      flow-script SHA, tool/version, `command[0]`, zero exit, success status,
      asserted hashed success sentinel, and exact input/output path/SHA closure.
- [ ] Setup/hold WNS are nonnegative, detailed route is complete, and unresolved
      references, unconstrained paths, and DRC violations are all zero.
- [ ] Correctness proves zero loss, duplicate, corruption, and phantom delivery
      after complete drain without an uncharged RX.
- [ ] Power annotation root equals the synthesis top, meets the frozen positive
      coverage threshold, and covers the exact trace window used for the
      delivered-event numerator.
- [ ] Sparse and near-saturation rows remain separately labeled.
- [ ] Events per pin-cycle and energy per delivered event are validator-derived;
      zero-delivery windows are not ranked.
- [ ] Records with different boundary scope, endpoint inclusion, pin set,
      source count/map, trace, activity window, clock, PVT/RC, SDC, or tool-flow
      identity are diagnostics only and are not directly ranked.
