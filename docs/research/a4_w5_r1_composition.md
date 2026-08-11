# A4 W5 strict synchronous-R1 composition verification

## Frozen decision and exact endpoint

A4 independently composes the **corrected production A7 W5 endpoint at commit `42377ca81340951bfcd453b3bd664e673091f9f3`** with its production parallel reference. The A4 shell adds only joint fail-closed admission and observability ports; it adds no register, queue, valid-edge detector, CDC adapter, or protocol state. The earlier `ca1a209` is explicitly superseded because its drain indication omitted a pending registered output and its test consumed post-NBA availability rather than modeling a real synchronous sink.

This is a strict phase-related synchronous R1 endpoint. `ref_clk_i` and `sample_clk_i` have one frozen source and phase. Admission is at the reference rise, A7 DDR RX commits address/toggle at burst fall 12 ns later, and its charged `seen_toggle` observer registers availability at the next reference rise 16 ns after admission. A real `always_ff` sink samples that registered valid/address in the pre-NBA region of the following reference rise, 32 ns or two cycles after admission. The parallel production endpoint has its own complete launch, link, receive-toggle, and identical ref-domain observer/sink boundary.

This is not unrelated-clock CDC and contains no 2FF claim. The primary sink is always ready. Sink backpressure, unrelated clocks, R greater than one, and level-request conversion require future explicitly charged handshake/FIFO variants.

## Ready-valid and reset arming

Every reference edge satisfying `valid && ready` creates one frame. Keeping `valid` high while presenting the next address on every accepted edge is legal; no valid-edge detector is permitted. “One shot” here means one frame per handshake, not one frame per assertion of valid.

Each exact A7 production endpoint charges one `reset_release_armed_q` bit. Ready stays low through the first safe reference edge after reset release and rises after that arming edge. A transaction held stable over reset/arming is accepted exactly once at the following reference edge. This arming bit is reset safety state, not R1 request deduplication. The A4 shell ANDs the two production-ready outputs and feeds both endpoints only the resulting jointly-qualified valid.

## Pinned production objects

The runner reads the following seven git blobs, never A7 working-tree paths:

| Production object | Git blob | SHA-256 |
|---|---|---|
| `a7_r1_icg_boundary.sv` | `e9c29a63f05be1b44e8d651cd7de0fb0ef0d70ae` | `0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9` |
| `a7_r1_launch_qualifier.sv` | `01e3d6b05072df7ac6b06e30f4fcdba03ace43e4` | `8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d` |
| `a7_r1_ddr_tx.sv` | `544f54353a2bad0fc448765766807d39f59d6514` | `88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197` |
| `a7_r1_ddr_rx.sv` | `51306d854c8ce9bebc89e3126b71982dda123f30` | `7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06` |
| `a7_r1_retire_observer.sv` | `77106c061512c03af599939a0fa71a739408f8b1` | `2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f` |
| `a7_r1_candidate_endpoint.sv` | `1de1363dee70a722dcd994b517eb6bb73ba452c6` | `c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03` |
| `a7_r1_parallel_reference_top.sv` | `03d30c5fefb77360d4f0288147d8aac809fa9616` | `151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748` |

The runner verifies commit, tree path, blob ID, and SHA-256 before compile. At the final run A7 HEAD was the pinned commit and clean. Qualification of both older `ab97aba` and flawed `ca1a209` objects is superseded and is not final evidence.

## Independent checks and result

The A4 TB assigns monotonically increasing IDs only in its scoreboard; synthesizable paths remain four-bit address/event identity only. Each acceptance is joined to DDR rise/fall symbols, parallel link occurrence, endpoint availability, and a separately registered always-ready sink. It fails on loss, duplicate, phantom, address mismatch, order mismatch, endpoint-valid mismatch, wrong +32 ns sink timing, or conservation/drain failure. It also requires `drain_idle==0` whenever a launch or registered `retire_valid` is high; this assertion rejects `ca1a209` at 56 ns and passes unchanged on `42377ca`.

Verilator 5.032 passed:

| Case | Accepted | Checked result |
|---|---:|---|
| continuous `valid=1`, changing address each cycle | 32 | 32 consecutive frames and observer events, no bubble |
| initial gapped traffic | 12 | exact address/order |
| valid/address held while reset and arming keep ready low | 1 | zero early handshakes, exactly one afterward |
| legal drain/reset then gapped traffic | 4 | clean epoch and exact replay |
| explicitly invalid mid-frame reset, then recovery | 1 before + 1 after | in-flight ID aborts with no phantom; next legal epoch delivers |

Totals: 51 accepted, 50 delivered to the synchronous sink, one explicitly reset-aborted. `accepted = delivered + reset_aborted`. DDR raw commit is +12 ns, both endpoints register availability at +16 ns, and the real pre-NBA synchronous consume occurs at +32 ns. Continuous traffic still sustains one consumed event per cycle after fill. The mid-frame case demonstrates fail-closed digital observation only; preservation across an invalid reset remains unsupported.

Owner same-flow structural counts, recorded but not independently re-synthesized here, are: DDR endpoint 3 link pins, 20 state bits, 29 charged functional cells; parallel endpoint 5 link pins, 18 state bits, 27 charged functional cells. These are local generic structural evidence, not physical PPA closure.

## Reproduction and status

```sh
rtl/candidates/a4_w5_r1_composition/run_w5_r1.py \
  --a7-repo /home/chickgoose/projects/a7 \
  --output /tmp/a4_w5_r1_result.json
python3 -m unittest -v \
  rtl/candidates/a4_w5_r1_composition/tests/test_w5_r1.py
```

Tool discovery is `--verilator`, `AER_VERILATOR`, `VERILATOR`, `PATH`, then the existing `/tmp/a7-sim-bin/verilator`; absence or an invalid configured path fails closed. Existing output/work paths are not overwritten.

### Canonical tracked receipt

`results/w5_r1_composition.json` is not a hand-written or derived summary. It is the byte-for-byte output of `run_w5_r1.py`, schema `a4_w5_r1_composition_canonical_v2`. Its SHA-256 is `27c0b02c740e57935e392d7ecf7925dae8f8e44fb3eb1540f576b644985e29c6`.

The canonical schema excludes temporary build paths, wall-time-dependent log hashes, local repository paths, and unrelated A7 working-tree status. It retains the exact A7 commit and seven path/blob/content hashes, A4 RTL/TB/runner hashes, Verilator version and executable hash, canonical compile arguments, process return codes, exact PASS marker and marker hash. The unit regression invokes the runner twice with separate clean temporary build roots, requires the two outputs to be byte-identical, and then requires that byte stream to equal the tracked receipt. It separately retains the no-overwrite check.

Status: **LOCAL COMPOSITION GO** for exact A7 `42377ca`, frozen synchronous R1, and always-ready consumption only. Common workload and physical/PPA/phase closure remain **HOLD / not claimed**.
