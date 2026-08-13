# K2 W2 server environment preflight

This preflight is an environment gate, not a PPA result.  It consumes the
immutable raw and buffered Ganghee archives directly, then (when run on the
server) requires byte-pinned, non-symlink Genus/Innovus/Xrun executables and GPDK045
technology inputs.  It writes canonical JSON before returning nonzero on FAIL
or HOLD.

The contract binds direct live-shell observations dated 2026-08-13: exact SHAs
for slow/fast Liberty, technology/macro LEF and the shared QRC file, plus exact
Genus/Innovus/Xrun paths, executable SHAs and parsed versions. These are
external direct observations, not a claim that the bytes were locally re-read
or invoked by this local run. A strict server preflight must re-read every file,
invoke each exact executable and parse exactly one expected version. The Genus
build-expiration banner is retained as a warning after a zero exit because the
preserved golden executed successfully; any nonzero invocation remains FAIL.
The committed local result is therefore intentionally HOLD.

The corner contract is slow Liberty `(1.0, 0.9 V, 125 C)` for setup and fast
Liberty `(1.0, 1.1 V, 0 C)` for hold. Both
RC corners use the one real `qrc/qx/gpdk045.tch`.  A distinct QRC file is not
required or fabricated.  This shared typical-RC condition is explicitly weaker
than best/worst RC signoff and keeps physical qualification HOLD.

Local evidence-only reproduction:

```sh
python3 physical/k2_w2_server_env/preflight.py \
  --allow-hold \
  --output physical/k2_w2_server_env/canonical_campaign_env.json
```

Future strict server invocation supplies all four live inputs and omits
`--allow-hold`:

```sh
python3 physical/k2_w2_server_env/preflight.py \
  --pdk-root /home/aiasic26911/gsclib045_all_v4.7/gsclib045 \
  --genus /tools/cadence/DDI231/bin/genus \
  --innovus /tools/cadence/DDI231/bin/innovus \
  --xrun /tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun \
  --output /attempt/canonical-server-environment.json
```

`campaign_launch_allowed=true` is emitted only after exact source archives,
tool path/SHA/exact parsed version, both Liberty PVTs, exact Liberty timing
semantics, exact LEF pins and `CoreSite` legality, and the single shared QRC
file pass. The required cells are:

- `TLATNTSCAX2(E,SE,CK,ECK)`, `latch_posedge_precontrol`;
- `MX2X1(A,B,S0,Y)`;
- `DFFRHQX1(RN,CK,D,Q)` with rising-edge D and reset arcs;
- `DFFNSRX1(Q,QN,CKN,D,SN,RN)` with falling-edge D arcs, `!RN` clear,
  `!SN` preset, and nonempty RN/SN recovery/removal arcs.

`TLATNCAX2` is not an allowed substitute. The environment GO is emitted as an
exclusive, hash-bound `PROVEN_SERVER_ENV` receipt. Every Genus, Innovus, or
campaign launcher must reject a missing, stale, tampered, HOLD, or wrong-contract
receipt first:

```sh
python3 physical/k2_w2_server_env/require_go_receipt.py \
  --contract physical/k2_w2_server_env/contract.json \
  --receipt /attempt/canonical-server-environment.json
```

Environment availability does not prove the generated mapping. After Genus and
before Innovus, `verify_mapped_inventory.py` separately requires that environment
GO receipt and a hash-pinned mapped netlist. It requires exactly one
`TLATNTSCAX2` with `SE=0`, the resolved rising/falling FF cells, common
`DFFNSRX1.CKN`, `RN=rst_n`, `SN=1'b1`, and rejects residual `TLATXL`,
`TLATNCAX2`, or any `SDFF*`.
