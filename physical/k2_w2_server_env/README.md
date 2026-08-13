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

The corner contract is slow Liberty for setup and fast Liberty for hold.  Both
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
tool version and executable SHA, both Liberty PVTs and FF edges, LEF site/macro
legality, the required `TLATNCAX2`/`MX2X1`, and the single shared QRC file pass.
