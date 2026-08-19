# eb298fe digital replay evidence

`digital_authority_artifacts.tar` is a deterministic two-member archive:

- `pins.json`: the temporary immutable file/tool/RTL authority used for the
  replay;
- `result.json`: the actual 100-run full50, two reset, two mutation-activation,
  and eight literal-mutation result.

Archive SHA-256:
`c795ef5653cc9666c8912e553430e4f1987fdc8078b86d61c7853597cf30b930`
(163840 bytes).

The replay used the campaign's dirty-pin override because only the temporary
pin document changed; the RTL bytes were independently bound to source commit
`eb298fe1416a4312269a6f9232e1445f8958dda2` and integration commit
`bfb4b998049bbf9c66c4af9ffabba2c8ff096363`.  Consequently this is strong
hash-bound team diagnostic evidence, not authenticated producer/freshness
evidence.  The verifier cannot promote its release ceiling above HOLD.

