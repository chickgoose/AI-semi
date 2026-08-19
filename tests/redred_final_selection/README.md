# REDRED current final-selection gate tests

These tests prove that the current immutable evidence recomputes to HOLD, that
caller-controlled JSON cannot enter the authoritative CLI, and that the future
policy decision table does not confuse an in-memory eligible candidate with a
published final selection. They also reject shared-failure fallback, missing or
unknown gates, P6 evidence, invented scalar scores, and mutable artifact pins.

Run `tests/redred_final_selection/run_all.sh` from any directory.
