# REDRED mapped/post-route single-edge CDC/RDC diagnostic

This contract verifies the exact Genus mapped and Innovus post-route netlists
from the A2/A3 same-environment-snapshot diagnostic campaign. It is an offline,
fresh-clone-verifiable supplement to the source-level contract in
`contracts/redred_single_edge_cdc_rdc`.

Run:

```sh
python3 -B contracts/redred_single_edge_mapped_cdc_rdc/verify_contract.py
bash tests/redred_single_edge_mapped_cdc_rdc/run_all.sh
```

The canonical receipt must report
`DIAGNOSTIC_PASS_RELEASE_HOLD`. A mapped structural PASS is not a release PASS.
The physical campaign remains caller-self-sealed, producer authentication and
freshness are absent, and the constraints are still
`TEAM_PLACEHOLDER_SCREENING_ONLY`. Consequently `final_cdc_rdc_gate` remains
`HOLD` and no file in this contract may promote it.

## What is proved

The verifier rehashes the exact archive and every required member, then follows
the chain:

```text
source CDC contract + physical contract
              |
              v
mapped/post-route netlist + mapped SDC
              |
              v
artifact ledger -> qualification -> same-snapshot cohort
```

For both A2 and A3, in both mapped and post-route views, it proves:

- exactly one gate-level top module with the expected complete endpoint name;
- every instance type belongs to the hash-bound used-cell inventory;
- every sequential cell is a GPDK045 positive-edge flip-flop whose `CK` pin is
  connected directly to top-level `clk_i`;
- no latch, asynchronous control pin, unknown sequential cell, secondary,
  generated, gated, forwarded, or inverted clock exists;
- `clk_i` is not used as combinational data;
- the mapped SDC creates exactly one `se_primary_clk` on `clk_i` at 6.5 ns and
  contains no generated-clock, clock-group, false-path, or multicycle escape;
- mapped and post-route sequential population is conserved: A2 55, A3 48;
- archived netlist/SDC hashes match each candidate's physical artifact ledger,
  qualification, and the A2/A3 cohort's shared environment snapshot hash.
- ledger-bound post-route timing, area, vectorless power, DRC, antenna, signal
  connectivity, and PG connectivity reports reproduce the exact PPA rows.

Canonical structural counts are:

| Candidate | View | Instances | Sequential |
|---|---:|---:|---:|
| A2 | mapped | 518 | 55 |
| A2 | post-route | 747 | 55 |
| A3 | mapped | 389 | 48 |
| A3 | post-route | 580 | 48 |

The same offline receipt reproduces:

| Candidate | Setup WNS (ns) | Hold WNS (ns) | Area (um^2) | Vectorless power (mW) |
|---|---:|---:|---:|---:|
| A2 | +0.0329976 | +0.00057663 | 1962.738 | 0.07962095 |
| A3 | +0.0237889 | +0.00103348 | 1628.262 | 0.06556542 |

Both rows have zero setup/hold violations and zero DRC, antenna, signal
connectivity, and PG connectivity problems in the archived reports.

`cell_semantics.json` records only the semantics needed for the cells present
in these netlists. It binds the setup Liberty SHA-256 from the physical
contract; it does not redistribute the PDK library.

## Deliberate limits

This diagnostic does not prove producer identity, campaign freshness, PDK
license authenticity, source-to-netlist formal equivalence, organizer-approved
constraints, asynchronous external input safety, or silicon signoff. External
functional inputs retain the source contract's synchronous-to-`clk_i`
assumption. The complete original environment snapshot is intentionally not in
the public archive because it contains infrastructure endpoints; only its
qualification/cohort hash is published.

Those limits are why a new reviewed contract backed by a controlled producer,
freshness authority, and organizer constraints is still required before
`A2:FINAL_CDC_RDC` or `A3:FINAL_CDC_RDC` can become PASS.
