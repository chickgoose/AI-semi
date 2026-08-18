# Public projected actual-RTL replay v2

This isolated lane executes the 1x, 64x, and 256x timing projections of one
1,100-occurrence UZH `shapes_rotation` window against the pinned A2 and A3
single-edge RTL. It is always a noncanonical, nonofficial
`PUBLIC_PROJECTED_EXTENSION`; release and selection remain `HOLD`, and P6 is
false at every pins, result, export-manifest, and publication boundary.

The v2 producer rejects any projection source outside the exact seven-name
inventory and rejects symlinked, multiply hard-linked, or identity-changing
source files. Its versioned export inventory contains exactly 80 payload names
plus `MANIFEST.json`. After writing, it reopens the gzip/tar bytes and checks
name order, type, metadata, size, and SHA-256 for every member.

An observer-only copy of the pinned replay TB adds zero-based accept and retire
sequence ordinals. Actual RTL is not changed. Every retained event therefore
records both cycles and global ordinals, which reconstruct lane order when two
events are accepted or retired in the same cycle. Reset evidence is limited to
the existing clean-drain reset scenario; no mid-flight reset claim is made.

`run_all.sh` performs two complete executions. Each execution includes all six
projected scenarios, two clean-drain resets, two count-two activations, and the
eight source mutants. The two result documents must match under the embedded,
machine-readable closed semantic view. Build/log hashes are deliberately not
semantic because absolute temporary build paths can differ.

Publication is two phase to avoid a false self-referential Git claim. First
commit the result, export, and reproduction result. Then `run.py seal` binds
that payload commit and verifies its exact Git blobs before writing the final
publication record. The final record explicitly says it does not claim the Git
commit that contains itself.

Toolchain statements are limited to executable identity and reported-version
matches made by the hardened base replay. They are not claims that the entire
host environment is reproducible.
