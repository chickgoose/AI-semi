# MC-WTB occurrence-preserving phase-4 baseline

This block is a deliberately uncompressed correctness baseline.  It accepts up
to six complete occurrence records per clock, stores them in sixteen bounded
source FIFOs, schedules at most two different sources with the frozen A2
weighted round-robin policy, and retires the complete records over a wide raw
link.

The bounds are pinned to the qualified UZH 1 ms cohort: six occurrences in one
cycle, three occurrences for one logical source, and sixteen 4x4 logical
sources.  An offered batch that does not fit is rejected atomically and sets a
sticky overflow flag.  This RTL therefore exposes loss rather than silently
coalescing multiple occurrences into one pending bit.

The replay maps source timestamps to the first 6.5 ns edge at or after the
occurrence (`ceil`, never `floor`).  The earlier A23 projection-floor cycle is
retained only as an audit field; it is not used to admit an event early.

This is not a codec, wire-width, PPA, or novelty result.  The wide payload is
intentional so that phase 4 can establish exact occurrence and retire evidence
before phase-5 architecture work.

`tests/mc_wtb_occurrence_baseline/genus_elaborate.tcl` stops after HDL
elaboration and unresolved-design checking.  It deliberately does not run
generic synthesis, technology mapping, timing, area, power, or place-and-route.
