# A23 actual-P6 replay publication receipt

- Immutable package commit: `a05b943c12fde313357f726b638d84dc747e23ca`
- Result SHA-256: `67c6dd0a2decda78edede6d285c81ae580faa7c5a4b949c74c5b19291a8858b2`
- Independent fresh campaign roots: `/tmp/a23-full-p6-campaign1` and
  `/tmp/a23-full-p6-campaign2`
- Byte comparison: exact match
- Each campaign: 3 owners, 150 actual full50 executions, 3 reset executions,
  and 15 separately compiled actual-RTL mutation executions
- Capacity22: 66 exact subset references, zero additional executions

| Owner | Accepted | Source overrun | Retired | Occurrence→accept max | Accept→retire | Fixed-window retired | Events/cycle |
|---|---:|---:|---:|---:|---:|---:|---:|
| A2 | 104046 | 2370 | 104046 | 23 | fixed 3 | 103940 | 0.896281733 |
| A3 | 93645 | 12771 | 93645 | 265 | fixed 2 | 93548 | 0.806670806 |
| A4 | 102171 | 4245 | 102171 | 23 | fixed 2 | 102099 | 0.880406664 |

The committed `result.json` is copied byte-for-byte from campaign 1. Campaign
2 produced the same SHA-256. It records the package commit and all verified
file/tool pins. Digital RTL is `GO`; physical and CDC/RDC remain `HOLD`.
