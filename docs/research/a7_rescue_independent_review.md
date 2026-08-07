# A7 radix-4 rescue independent structural review

Date: 2026-08-07

Reviewer branch: `agents/a8-age-calendar-wheel`

A7 evidence commit: `f3520b400e1576f0c4994255d369bfb29dee4ddd` (`agents/a7-parallel-event-compactor`, clean at capture)

Reference commit: `2219040`

## Verdict

No critical functional or decision error was found. The frozen original RTL is
byte-identical to `2219040`, the three implementations expose the same sampled
ports and equal register bits at every N/K point, and an independent local
Yosys rerun reproduced the committed structural CSV byte-for-byte. The
predeclared N=16/K=2 rescue is correctly **rejected**: segmentation saves gates
but is deeper than both references. The K=4 result is correctly kept only as a
conditional area/depth Pareto choice, not as a dominance claim.

There is one important reproducibility finding, but it is not a functional
error. Before A7 commit `f3520b4`, the structural script read a union of frozen
and experimental sources for every top. Merely adding unused experimental
modules perturbed Yosys optimizer ordering and changed original N=64/K=4 from
33,105 gates / fanout 445 to 33,129 / 447. The current committed flow isolates
the frozen and segmented source closures and restores the `2219040` result.
Consequently the literal source-file lists are **not identical** between
segmented and replicated. Their comparison boundary is identical ports,
state, parameters, passes, and generic mapping convention; describing it as an
identical literal source set would be false.

The N=16 combinational prefix/rank verification is exhaustive and strong. The
N=32/64 full-candidate random backpressure runs are useful regression evidence,
but one deterministic 2,048-cycle stream per configuration is not sufficient
for proof or sign-off. K2/K4 structural decisions remain valid as conditional
proxy decisions; equivalence at the larger sizes should not be overstated.

No A7 file was modified and no server flow was used.

## Frozen-original identity

`git diff --exit-code 2219040 --` over the following three files produced no
output. The Git blob IDs at `2219040`, A7 `HEAD`, and the A7 worktree are equal.

| Frozen file | Git blob ID | SHA-256 |
| --- | --- | --- |
| `a7_parallel_event_compactor.sv` | `88a3b7ffe77a5434d4a64af3ee2ec8afac606235` | `401ee99c183ccbbdb6963c8f2f4cf880bd1b152d2614ffbde65815d06d2c231e` |
| `a7_parallel_prefix_count.sv` | `62074b0a870e2427c99d2a79e0c151cca4e341f6` | `a7d24ee7f95ebd81fb5b8d686412ee448dda28dd3324d7470150ade5c45793f4` |
| `a7_replicated_selector_reference.sv` | `cdfbe48b71538702fcc1a2790f4967b256d193e3` | `923af1dae61bc2c4a037f7617c6a0e7bcab9d759e7dbcd27e8bff96c14c9f7c5` |

This establishes byte identity, rather than relying on structural or behavioral
equivalence alone.

## Comparison-boundary audit

### Ports and sequential state

The structural wrappers have the same parameterization (`N`, `K`, address and
source widths) and the same top-level interface:

- inputs: `clk`, `rst_n`, `source_valid[N]`,
  `source_event_flat[N*ADDR_W]`, `retire_ready[K]`;
- outputs: `source_ready[N]`, `retire_valid[K]`,
  `retire_event_flat[K*ADDR_W]`, `retire_source_flat[K*SOURCE_W]`.

Mapped register counts are equal across original prefix, segmented, and
replicated for every like-for-like point:

| N | K=2 register bits | K=4 register bits |
| ---: | ---: | ---: |
| 16 | 62 | 104 |
| 32 | 81 | 125 |
| 64 | 116 | 162 |

Thus none of the gate/depth comparisons buys an advantage by removing output
register state or changing the externally visible lane boundary.

### Sources and Yosys measurement alphabet

Original and replicated use the same `FROZEN_SOURCES`: frozen prefix,
compactor, replicated reference, and frozen wrappers. Segmented uses a separate
`SEGMENTED_SOURCES`: radix-4 prefix, shared rank selector, segmented compactor,
and its structurally identical wrapper. This is a deliberate isolated design
closure, not an identical literal source set. It prevents unused modules from
changing optimizer ordering and is the fairer reproduction boundary.

Every row uses Yosys 0.52 (`fee39a3284c90249e1d9684cf6944ffbbcbb8f90`)
with the same sequence:

```text
read_verilog -sv; hierarchy -top ... -chparam N ... -chparam K ...;
proc; flatten; opt; stat -json -width; ltp -noff;
techmap; opt; stat -json -width; ltp -noff; write_json
```

There is no ABC, Liberty, placement, buffering, or routing step. “Generic
gates” therefore uses the same post-`techmap` one-bit generic-cell counting
rule and `ltp -noff` depth rule for all implementations. The measurement
alphabet and rule are common even though an implementation need not instantiate
every cell type present in another implementation. Fanout is JSON net-bit cell
input connectivity, not physical buffered fanout.

## Independently reproduced structural result

The current committed script was run locally against the same local Yosys
binary, writing only `/tmp/a8-a7-rescue-independent.csv`. Its SHA-256 is
`1fc53e50d57d3d813f3fdd5e76ae1f167f97bc15a48a8abcd1d6ab0f5d626d13`,
identical to the committed `radix4-rescue-structural.csv`.

| N | K | implementation | gates | generic depth | max/p95 fanout | reg bits |
| ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 16 | 2 | original | 4,299 | 133 | 85 / 4 | 62 |
| 16 | 2 | segmented | 3,307 | 149 | 131 / 4 | 62 |
| 16 | 2 | replicated | 3,733 | 133 | 131 / 3 | 62 |
| 16 | 4 | original | 5,592 | 139 | 104 / 5 | 104 |
| 16 | 4 | segmented | 4,784 | 165 | 133 / 4 | 104 |
| 16 | 4 | replicated | 6,729 | 248 | 154 / 5 | 104 |
| 32 | 2 | original | 10,855 | 222 | 194 / 4 | 81 |
| 32 | 2 | segmented | 7,282 | 237 | 259 / 4 | 81 |
| 32 | 2 | replicated | 12,111 | 234 | 259 / 3 | 81 |
| 32 | 4 | original | 13,436 | 228 | 194 / 4 | 125 |
| 32 | 4 | segmented | 10,011 | 253 | 261 / 4 | 125 |
| 32 | 4 | replicated | 20,983 | 447 | 298 / 5 | 125 |
| 64 | 2 | original | 27,914 | 392 | 447 / 4 | 116 |
| 64 | 2 | segmented | 17,452 | 407 | 515 / 4 | 116 |
| 64 | 2 | replicated | 42,895 | 429 | 515 / 3 | 116 |
| 64 | 4 | original | 33,105 | 398 | 445 / 4 | 162 |
| 64 | 4 | segmented | 22,731 | 423 | 517 / 4 | 162 |
| 64 | 4 | replicated | 72,845 | 836 | 586 / 5 | 162 |

## Verification-strength audit

### N=16 exhaustive unit coverage

The unit flow enumerates all 65,536 request bitmaps at all 16 rotation bases.
It compares every inclusive prefix count and total, then checks selected valid
count, cyclic ordering, and every exact chosen source index for K=1/2/4.
Directed full-compactor cases additionally cover persistent contention, refill,
lane-0 stall/output stability, uniqueness, and fairness. This is sufficient to
exhaust the N=16 combinational prefix/rank selection domain. It is not an
exhaustive traversal of the sequential output-register state machine; the
directed compactor cases cover that boundary instead.

### N=16/32/64 random-backpressure coverage

The three implementations run in cycle lockstep for N=16/32/64 and K=2/4,
2,048 cycles per configuration. Each cycle compares source ready, retire valid,
valid-lane payload, and source index. Stimulus contains independent random lane
ready, a 200-cycle permanent lane-0 stall, a 200-cycle alternating-ready phase,
refill, and drain. All six configurations pass.

This is good deterministic counterexample hunting, but it is not sufficient as
large-N sign-off because:

- there is one fixed xorshift64 stream per N/K and only 2,048 cycles;
- request masks change independently of ready rather than exercising a
  protocol source that holds valid and payload until acceptance;
- payload is fixed to source identity, there is no mid-run reset, and no
  exhaustive sequential state/ready enumeration or formal equivalence exists;
- the long targeted stall is lane 0 only.

Recommended interpretation: accept the passing tests as regression evidence,
not as proof. A stronger sign-off would add multiple recorded seeds, held-valid
sources with changing payload identities, all-lane long stalls, reset injection,
and formal or bounded sequential equivalence.

## K2/K4 decision check

- **N=16/K=2 reject is numerically required.** Segmented is 23.1% smaller
  than original and 11.4% smaller than replicated, but depth 149 is worse than
  both depth-133 references. It fails the predeclared requirement to be strictly
  smaller and shallower than both. Its max fanout also does not improve on
  replicated and is worse than original.
- **K=4 conditional keep is accurate.** At N=16, segmented saves 14.4% gates
  versus original but adds 18.7% depth. Original is the depth choice and
  segmented is the area choice; both beat replicated in gates and depth. The
  same area/depth Pareto relation holds at N=32 and N=64.
- **Scaling does not rescue the strict K2 proposition.** At N=32 segmented is
  smaller but deeper than both references. At N=64 it is smaller and shallower
  than replicated but remains 15 generic levels deeper than original. It never
  satisfies the declared three-way K2 condition.
- These are generic structural proxy decisions only. They do not establish a
  physical PPA win without a common library and timing flow.

## Evidence hashes

| Evidence | SHA-256 |
| --- | --- |
| A7 rescue report | `b418ee628de61e0607b05d027ed8b6ac04a1a7ba133395988fd042071da09b09` |
| committed structural CSV | `1fc53e50d57d3d813f3fdd5e76ae1f167f97bc15a48a8abcd1d6ab0f5d626d13` |
| independent structural CSV | `1fc53e50d57d3d813f3fdd5e76ae1f167f97bc15a48a8abcd1d6ab0f5d626d13` |
| A7 rescue research note | `5e507e5cdfccf378ed964e1a1d452c0019e5acaac79a13cf55ecadcad83b0ed1` |
| radix-4 segmented prefix RTL | `d93794ef305f088545122052f21de9a1bd24f4911be6b47d0092a1ff752ca254` |
| shared rank selector RTL | `2bef742d79ebb620e95e89064672e9993c7bf32d4516c786823c5b1ca5c426b3` |
| segmented compactor RTL | `ad987ee2fa0f956a3c6e4005bd0fcdfebb92e3b8a27ad4c73bdc81057323841a` |
| equivalence TB | `a02e16996397b10606531c8945ca243735d3a8ca6f63c4b63ce2fb1d74c80af1` |
| structural wrapper | `9f221bb6d8d3ba2b37f980501f3894fea63bf36a8c4bde5a1af55dce654a359b` |
| equivalence runner | `d0baea8999480596c018ce6cd23cf870dc7313e5f77a8280c2c6f5f4e593351d` |
| structural extractor | `3313f7fc2051bf80dd9163fa109538fef52c9e7d48174a51c9efa8d5f98bc0b7` |

All hashes and measurements above were captured from the clean A7 worktree at
the stated commit. The independent output in `/tmp` is reproducible scratch
evidence and is not part of either candidate tree.
