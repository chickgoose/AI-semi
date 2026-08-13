# Fovea/Cluster2 workspace-diff functional loss evidence

This is a non-official, workspace-diff functional receipt. It is bound only to
the immutable server-origin archive:

```text
/tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz
SHA256 22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39
```

The evidence may be used to report generated, accepted, delivered, and overrun
counts. It may not support area, power, timing, energy, PPA qualification, or a
ranking in any of the three physical boundary cohorts.

The bound provenance is the archive's `provenance.txt`, which identifies the
server attempt `eval-fovea-cluster2.yZr1kmYL` and records
`binding_reset_quiet_arming_patch=workspace-diff`. The two candidate run logs,
the 338-entry inner result ledger, and the complete archive are also bound by
SHA-256. The outer `eval-driver-final.log` is deliberately excluded because it
is stale and names the older `0FfaT8kp` attempt.

Run `tests/k2_w2_tops/run_all.sh` to verify the archive and recompute every
published loss total directly from its candidate logs.
