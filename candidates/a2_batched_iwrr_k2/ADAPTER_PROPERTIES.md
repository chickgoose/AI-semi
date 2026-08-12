# Normalized charged-adapter properties

The `a2_batched_iwrr_k2_normalized` boundary and its
`a2_k2_ordered_link_adapter` are a synthesizable two-record normalizer around
the atomic owner.  Payload, source identity, occupancy, and ordering storage
are candidate-charged RTL.  The link accepts an owner bundle only when all of
it fits after same-edge ordered retirements.

Run the independent qualification from the repository root:

```sh
candidates/a2_batched_iwrr_k2/run_adapter_properties.sh
```

The suite has three complementary layers:

- an executable Python reference exhausts all 72 combinations of queue count
  `0..2`, offer count `0..2`, retire-ready `00..11`, and reset state;
- generated vectors lock the independent owner-plus-transport model against
  synthesizable SystemVerilog for all 48 legal normalized combinations (reset
  forces owner offer count zero) and 12,000 legal randomized cycles, checking
  payload/source order and internal owner cursor, row pointers, held offer, and
  FIFO state before every edge;
- four separately compiled RTL mutations must be killed: partial-bundle
  acceptance, capacity overflow, owner-state advancement without atomic
  enqueue, and younger-record reorder around a stalled head.

Reset suppresses normalized outputs immediately, reports the normalized seam
idle, and clears the charged queue and any held owner offer on the active reset
edge.  Independent retire ready signals never feed owner policy; they affect
only how much complete-bundle capacity is available on that edge.
