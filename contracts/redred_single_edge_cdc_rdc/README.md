# REDRED A2/A3 single-edge CDC/RDC contract

This contract qualifies only the separately bound single-edge fallback.  It
does not reuse the P6/multi-edge endpoint and it does not promote A3 when the
shared A2/A3 CDC/RDC gate is absent or failing.

The checked architecture has exactly one primary positive-edge clock domain.
All state, including TX and RX state, samples that clock.  Falling-edge and
asynchronous-reset event controls, latches, derived/gated/forwarded clocks,
and unsynchronised crossings are forbidden.  External reset assertion and
deassertion are both sampled synchronously at the primary positive edge.  A
simple polarity inversion at a child reset port is permitted. Combinational
ready/error quiescing is not treated as state reset and adds no sensitivity
edge.

`contract.json` binds the exact A2 physical-authority and complete A3 endpoint
source set at commit `eb298fe1416a4312269a6f9232e1445f8958dda2` and proves
those blobs are byte-identical in integration commit
`bfb4b998049bbf9c66c4af9ffabba2c8ff096363`. The blobs are read from Git, so
the verification is independent of whether that commit has already been
merged into the current checkout.  The canonical invocation is:

```sh
python3 -B contracts/redred_single_edge_cdc_rdc/verify_contract.py
```

It must print `REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1`.
Supplying a malformed/stale binding, a missing commit/blob or elaborator, an
unknown module, or an unknown clock is a hard failure, not a HOLD.  The
verifier retains explicit HOLD handling for a genuinely unbound alternate
contract, but the canonical contract is no longer unbound.

## Bound source-set format

Pass `--binding path/to/binding.json --repo path/to/repository`.  The binding
has this exact shape (no extra keys):

```json
{
  "schema": "redred-single-edge-source-binding-v1",
  "source_set_id": "immutable descriptive id",
  "repository_commit": "40 lowercase hex digits",
  "integration_commit": "40 lowercase hex digits",
  "files": [
    {"path": "repo/relative/file.sv", "sha256": "64 lowercase hex digits"}
  ],
  "designs": {
    "a2": {
      "top": "a2_batched_iwrr_single_edge_top",
      "primary_clock": "clk_i",
      "reset": "rst_i",
      "reset_active_low": false,
      "synchronous_inputs": ["link_enable_i", "source_pending_i"],
      "transfer_scope": "endpoint",
      "tx_instance": "tx",
      "rx_instance": "rx",
      "drain_output": "drain_idle_o",
      "scope_drain_port": "drain_idle_o",
      "rx_pending_port": "retire_valid_o",
      "clean_drain_error": "protocol_error_o",
      "scope_error_port": "protocol_error_o",
      "top_drain_require_one": ["scheduler_idle", "endpoint_idle"],
      "top_drain_require_zero": [
        "buffer_valid_q", "scheduler_count", "protocol_error_o"
      ],
      "channels": [
        {"tx_port": "link_valid_o", "rx_port": "link_valid_i"},
        {"tx_port": "link_addr0_o", "rx_port": "link_addr0_i"},
        {"tx_port": "link_addr1_o", "rx_port": "link_addr1_i"}
      ]
    },
    "a3": {
      "top": "a3_exact_scalar_prefix_k2_single_edge_top",
      "primary_clock": "clk_i",
      "reset": "rst_i",
      "reset_active_low": false,
      "synchronous_inputs": ["link_enable_i", "source_pending_i"],
      "transfer_scope": "endpoint",
      "tx_instance": "tx",
      "rx_instance": "rx",
      "drain_output": "drain_idle_o",
      "scope_drain_port": "drain_idle_o",
      "rx_pending_port": "retire_valid_o",
      "clean_drain_error": "protocol_error_o",
      "scope_error_port": "protocol_error_o",
      "top_drain_require_one": ["endpoint_idle"],
      "top_drain_require_zero": [
        "source_pending_i", "scheduler_count", "protocol_error_o"
      ],
      "channels": [
        {"tx_port": "link_valid_o", "rx_port": "link_valid_i"},
        {"tx_port": "link_addr0_o", "rx_port": "link_addr0_i"},
        {"tx_port": "link_addr1_o", "rx_port": "link_addr1_i"}
      ]
    }
  }
}
```

Files are ordered repository-relative Git blobs.  The commit must resolve to
the exact 40-hex objects named by the canonical contract; every source and
integration blob must match and is hashed before the elaborator runs. The
binding document itself is SHA-256 pinned. Verilator must resolve each complete top;
unresolved modules and duplicate-module shadowing fail explicitly.

The structural verifier then walks the reachable elaborated hierarchy.  It
derives every sequential clock and reset port from actual process trees,
recursively follows instance connections to the top ports, and rejects any
non-direct clock expression or clock use as data.  Each sequential process
must have one positive edge and an outer synchronous reset guard.  The named
TX output and RX input ports are only locators: the verifier proves inside the
actual transfer-scope instance that the pins share a private net, that TX
drives it from a nonblocking sequential assignment, and that RX samples it in
a sequential assignment.

Reset is active high in the bound RTL.  Every state reset is an outer guard in
a positive-edge-only process; combinational ready/error quiescing does not add
a state sensitivity edge.  The environment is required to change reset only
at the primary positive edge, so assertion and deassertion are synchronous.
Because assertion aborts an
in-flight accepted record, the release protocol requires `drain_idle_o == 1`
before reset assertion.  The verifier proves that the top drain output depends
on the endpoint drain output, and that endpoint drain depends on both the TX
valid state and the RX retirement-pending state; the receipt records the
drain-before-reset precondition explicitly. Hardened clean drain additionally
requires `!protocol_error_o` at both endpoint and complete-top boundaries; the
new `w2_single_edge_error_latch` is part of the reachable pinned closure.

Run the independent mutation suite with:

```sh
bash tests/redred_single_edge_cdc_rdc/run_all.sh
```

This source-level PASS is supplemented by the exact archived mapped and
post-route structural diagnostic in
`contracts/redred_single_edge_mapped_cdc_rdc`. That successor diagnostic
checks both physical netlist views but deliberately keeps the final release
gate at HOLD until producer authentication, freshness, and organizer authority
exist.
