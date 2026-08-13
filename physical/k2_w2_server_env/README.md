# K2 W2 server environment preflight

This preflight is an environment gate, not a PPA result.  It consumes the
immutable raw and buffered Ganghee archives directly, then (when run on the
server) requires byte-pinned, non-symlink Genus/Innovus executables and GPDK045
technology inputs.  It writes canonical JSON before returning nonzero on FAIL
or HOLD.

The committed contract records the evidence currently available locally.
Tool executable, Liberty, LEF, QRC hashes and the fast Liberty PVT are `null`
because those bytes are not in the preserved golden archives.  They must be
filled from an independently preserved server inventory before a campaign can
be launched; first-use hashes are not trusted.  The committed local result is
therefore intentionally HOLD.

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

Future strict server invocation supplies all three live inputs and omits
`--allow-hold`:

```sh
python3 physical/k2_w2_server_env/preflight.py \
  --pdk-root /home/aiasic26911/gsclib045_all_v4.7/gsclib045 \
  --genus /absolute/non-symlink/genus \
  --innovus /absolute/non-symlink/innovus \
  --output /attempt/canonical-server-environment.json
```

`campaign_launch_allowed=true` is emitted only after exact source archives,
tool version and executable SHA, both Liberty PVTs and FF edges, LEF site/macro
legality, the required `TLATNCAX2`/`MX2X1`, and the single shared QRC file pass.
