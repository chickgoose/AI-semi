# W7 A8 independent adversarial verification

Status: **HOLD** at A1 `2a3a3be94be8f12585f484b5b1da2b372f7282d9`.

This audit owns only this test directory. It does not patch A1, owner RTL,
common TBs, manifests, or campaign scripts. All behavioral mutations occur in
disposable `/tmp` materializations or in copied result artifacts.

`run_w7_audit.py` executes the actual pinned W6/Fovea qualification and its
five owner mutants plus the A8 premature-drain, latency, and stale/no-live
mutants. That execution dynamically covers reset release/disjoint epochs,
same-address retrigger, duplicate/order checks, exact +1/+2 timing, and 120
continuous-valid acceptances. It then executes the latest A1 common-trace RTL
smoke baseline and attacks its copied result/log verifier.

Observed blockers:

- a delivered row's `logical_source` can be changed to another in-range source
  and `validate_result` still accepts it. Cardinality, timing, and summary
  conservation do not bind each result occurrence to the input trace address.
- both raw Fovea and Cluster2 Xcelium runners accept a fake `xrun` that returns
  zero without producing any CSV or PASS sentinel, then print their completion
  marker and return zero.
- Xcelium is absent locally, so actual raw Fovea/Cluster2 elaboration and the
  72-trace native campaign are not requalified by this audit. This is distinct
  from the executable fake-tool false-PASS attack above.

Duplicate result rows, +1 delivery latency, and duplicate PASS log markers are
rejected. Exact A1 HEAD and six campaign/qualification blob identities are
checked before execution. Default execution reports research completion even
when the decision is HOLD; `--require-go` returns nonzero when any blocker is
present.

Run:

```sh
tests/w7_a8_adversarial/run_all.sh
tests/w7_a8_adversarial/run_all.sh --require-go
```

## Follow-up commits

`run_followup_cross_audit.py` binds A7 `0233690` and A4 `63c4f2a` and reruns
the three original attacks. The A7 digital submission baseline, exhaustive
65,536-bitmap run, W6 timing/reset/retrigger checks, five RTL mutants, and stale
negative all pass. The new A4 orchestrator closes both no-output/rc0 attacks by
requiring an actual Xcelium log before accepting either candidate. The overall
decision remains **HOLD** because its result validator still accepts an in-range
`logical_source` rebound that disagrees with the corresponding input occurrence.
