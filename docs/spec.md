# Baseline AER Specification

## 1. Scope

This document defines the first synthesizable comparison baseline for the
Digital-track Address-Event Representation (AER) design.  It is deliberately
small: a fixed-priority arbiter, a registered transmitter, and a one-entry
receiver buffer.  Round-robin arbitration and deeper FIFO buffering are
improvements to be measured against this baseline.

The competition has not yet published a complete signal-level interface.  All
assumptions in this document are therefore implementation defaults, not claims
about the final official interface.

## 2. Default parameters

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `NUM_SOURCES` | 4 | Number of event-producing sources in the comparison wrapper |
| `ADDR_WIDTH` | 16 | Width of each source-provided event address |
| `SOURCE_INDEX_WIDTH` | `max(1, ceil(log2(NUM_SOURCES)))` | Internal source tag width |

Address payload and source identity are independent.  The comparison DUT
preserves each source-provided `ADDR_WIDTH`-bit payload and separately reports
the selected source ID.  The legacy request/ack wrapper still maps source `i`
directly to address `i` for backward compatibility.

## 2.1 Confirmed implementation environment

The design server was inspected on 2026-07-31 before fixing this baseline:

- RTL simulation: Cadence Xcelium `23.09-s013`.
- Synthesis: Cadence Genus `23.14-s090_1`.
- Place and route: Cadence Innovus `23.14-s088_1`.
- Static timing and power: Cadence Tempus/Voltus `23.14-s089_1`.
- Process kit: Cadence GPDK045 demonstration kit.
- Standard cells: `gsclib045_all_v4.7`.
- Provisional worst-case library: `slow_vdd1v0_basicCells.lib`, whose
  embedded operating condition is `0.9 V / 125 C`.

The library README says its timing models use reduced 2x2 tables for tool
demonstration.  PPA results must therefore be presented as comparisons under
identical conditions, not predictions for fabricated silicon.  The official
evaluation corner and clock target remain unconfirmed.

## 3. Clock and reset

- All functional handshakes are synchronous to the rising edge of `clk_i`.
- `rst_ni` is an active-low asynchronous reset.
- Outputs return to an inactive state while reset is asserted.

The reset polarity and synchrony are provisional and can be changed when the
official server flow or testbench is known.

## 4. Comparison source contract

- `in_valid[i]` indicates a valid address in `in_addr[i]`.
- The source must hold `in_valid[i]` and `in_addr[i]` stable until accepted.
- An input transfer occurs on a rising edge where both `in_valid[i]` and
  `in_ready[i]` are high.
- Only the fixed-priority selected source receives `in_ready` in a cycle.
- Simultaneous requests are legal.
- `out_src` identifies the source of the corresponding `out_addr` payload.

`rtl/baseline/aer_dut.sv` implements this contract for the shared a3
verification environment.  `rtl/baseline/aer_baseline_top.sv` retains the
older level request/one-cycle acknowledge interface as a compatibility wrapper;
it is not the primary PPA comparison top.

## 5. Arbitration

- The arbiter is combinational and fixed priority.
- Lower source index means higher priority: source 0 is highest priority.
- Exactly one grant is produced when at least one request is active.
- Arbitration is sampled only when the transmitter is ready to capture a new
  event.

Fixed priority is intentionally retained as the comparison baseline.  Its
known weakness is starvation of high-numbered sources under continuous
contention.

## 6. AER link handshake

The transmitter and receiver communicate with a conventional ready/valid
interface:

- `aer_valid`: address is valid and must remain stable until accepted.
- `aer_ready`: receiver can accept the address on the current cycle.
- A transfer occurs on a rising edge where both are high.
- Backpressure is represented by `aer_ready == 0`.

The baseline transmitter stores one selected event.  It does not accept a new
source event in the same cycle that the previous link transfer completes, so
the minimum initiation interval is two cycles.  This simple behavior provides
a clear throughput target for an improved implementation.

## 7. Receiver/output contract

- The receiver contains a one-entry elastic buffer.
- `event_valid_o`, `event_addr_o`, and `event_source_o` use another ready/valid
  handshake with the downstream consumer.
- The receiver supports simultaneous downstream consumption and replacement
  by a new AER link event.
- While stalled, output valid, address, and source ID remain stable.

## 8. Required baseline checks

The verification environment must cover:

1. Single request with unmodified payload and source ID delivery.
2. Lowest-index selection for simultaneous requests.
3. Input valid and payload held until ready.
4. Stable address and source ID during receiver backpressure.
5. No missing or duplicate transfers.
6. Continuous high-priority traffic demonstrating the fixed-priority
   starvation risk.

## 9. Measurement definitions

- **Latency:** cycles from the input ready/valid transfer to downstream
  consumption.  Request wait time is measured separately before acceptance.
- **Throughput:** downstream-consumed events divided by elapsed cycles.
- **Fairness:** per-source completed-event count and maximum request wait.
- **Loss:** generated events minus downstream-consumed events after draining.
- **Frequency/area/power:** taken from the official synthesis flow with the
  same constraints and workload for baseline and improved designs.

## 10. Items requiring official confirmation

- Exact source count and address encoding.
- Whether the official AER handshake is synchronous or asynchronous.
- Whether acknowledge means link capture or final consumption.
- Required HDL language/version and reset convention.
- Official clock target, PDK, standard-cell library, and PVT corner.
- Whether the competition expects RTL-to-gate synthesis only or also
  placement/routing results for the first submission.
- Official workload, testbench, output format, and submission layout.

## 11. Design selection

The fixed-priority baseline was selected on 2026-08-01. Under the same Genus
snapshot, SDC, Liberty, PVT, and 5 ns constraint, the buffered round-robin
experiment increased cell area by 548.8924%, reduced Fmax by 51.6836%, and
increased vectorless total power by 228.2244%. The rejected implementation is
retained in the `a2` branch and Git history; `main` contains only the selected
baseline RTL.
