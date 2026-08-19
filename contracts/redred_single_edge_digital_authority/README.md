# Latest physical-authority RTL replay

This contract verifies the A2/A3 `generator-v4-full50` replay against the
exact RTL used by the matched physical campaign (`eb298fe...`, integrated by
`bfb4b99...`).  It recomputes event conservation, accepted/retired exact-once
counts, reset clean-drain behavior, execution accounting, and all eight
literal mutation kills.  The 50-run rows for both candidates also match the
older canonical campaign byte-for-byte.

Run from the repository root:

```sh
python3 contracts/redred_single_edge_digital_authority/verify_contract.py
tests/redred_single_edge_digital_authority/run_all.sh
```

The output is deliberately `DIAGNOSTIC_PASS_RELEASE_HOLD`.  The replay used a
temporary hash-pinned `pins.json` and the campaign's explicit dirty-pin
override.  The archive is hash-bound and independently checked, but it was not
produced by an authenticated controlled runner and carries no freshness
authority.  Its metrics may be used for the team's diagnostic A2/A3 decision;
they are not official release or competition sign-off evidence.

Current full50 aggregate:

| Candidate | generated | overrun | accepted=retired | fixed-window events/cycle | occurrence→accept max | accept→retire max |
|---|---:|---:|---:|---:|---:|---:|
| A2 | 106416 | 2370 | 104046 | 0.896281733 | 23 | 3 |
| A3 | 106416 | 12771 | 93645 | 0.806670806 | 265 | 2 |

