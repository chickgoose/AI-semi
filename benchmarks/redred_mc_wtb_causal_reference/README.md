# MC-WTB causal reference and exact-once route contract

This package closes the software integration boundary between an epoch motion
decision and the three routes. Every accepted source event produces exactly
one ordered disposition. Out-of-FOV and invalid geometry are preserved as raw
escape/bypass records; they are never silently removed.

The causal reference is a deterministic pair of same-polarity rolling banks.
It has fixed capacity, expires old state, and scores an equal-timestamp cluster
before inserting any member. Therefore a score can depend only on strictly
earlier events. This is a development metric/reference model, not a claim that
the full time-surface memory, sparse warp, or tile datapath exists in RTL.

The consumed metric-v3 43.321 s interval may not be selected, transformed into
an arm, or scored. Development evaluation uses a separate registry and validates
the official full-source hash; that validation and the monotonic extraction pass
necessarily scan the raw source bytes spanning the blacklisted interval.

The official development diagnostic uses the latest supplied pose at or before
each event (zero-order hold), never the future side of an interpolation bracket.
With the roughly 5 ms pose sampling in `shapes_rotation`, this strict causal
variant produced practically zero MID/HIGH improvement. That negative result is
preserved rather than replacing it with the earlier noncausal interpolation.
