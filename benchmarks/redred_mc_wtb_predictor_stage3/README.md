# MC-WTB predictor Stage-3 common framework

This package is the candidate-neutral software boundary frozen before any RG3,
DSPB, or SO3-PLL implementation. It contains no selector, score, loss, filter,
dataset reader, RTL, or PPA model.

The wrapper guarantees:

- every input event produces exactly one append-only decision and ordered `Q`
  is unchanged;
- a pose is visible only when `commit_cycle < decision_cycle` and its
  measurement timestamp is not later than the event;
- all events on one decision cycle, including every equal-timestamp cluster,
  consume one immutable content-addressed state version;
- pose/event observations from a cycle can publish state only at the following
  cycle;
- candidate code sees only calibrated event values, relative pose timing,
  visible pose values, validity flags, and opaque immutable state bytes;
- candidate failure takes the exact frozen current-CAV result; when current CAV
  is unavailable, only a pose no older than 1 ms may take fresh ZOH, followed by
  sensor-fixed bypass;
- baseline validity, identity, query membership, provenance, fallback routing,
  and decision/state digests remain outside candidate control.

Candidate packages implement `CandidateModel`. They must keep no behavioral
state outside the supplied immutable state payload and must return structured
failure rather than deleting, delaying, or rerouting an event.
