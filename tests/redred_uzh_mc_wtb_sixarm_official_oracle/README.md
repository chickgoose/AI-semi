# Official UZH five-arm independent oracle regression

This optional test recomputes the pinned 1 ms `shapes_rotation` cohort from a
completed official pose-join directory. It uses only Python's standard library
and deliberately imports no generator or adapter geometry helper.

The regression checks all 1,100 event IDs/order, five per-arm canonical hashes,
the combined five-arm hash, exact OOF ID sets and counts, the 4,998,186 ns delay,
and the independently searched delayed bracket stream hash. `RETIRE_WARP` and
retire receipts are deliberately absent: this test cannot create six-arm
evidence or promote the MC-WTB result.

Without an input environment variable, the suite exits successfully with one
clean skip:

```sh
bash tests/redred_uzh_mc_wtb_sixarm_official_oracle/run_all.sh
```

Run against the currently pinned local package with:

```sh
REDRED_UZH_SIXARM_ORACLE_POSE_JOIN=/tmp/uzh-posejoin-c6a \
  bash tests/redred_uzh_mc_wtb_sixarm_official_oracle/run_all.sh
```

An explicitly configured missing or wrong directory is a failure, not a skip.
