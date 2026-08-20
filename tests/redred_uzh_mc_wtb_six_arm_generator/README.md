# Six-arm generator native tests

The fixture source, adapter and retire stream are non-official and exist only
inside each temporary test directory.  The retire timestamps are supplied by
the test fixture before generator execution; production code never creates or
fills them.  The native suite covers exact six-arm V2 rows, evaluator loading,
deterministic publish/recompute, source-free/tamper/overwrite rejection,
production rejection of synthetic retire provenance, and incomplete-retire
negative evidence analogous to the A23 1x `1019 retired / 1100 generated`
blocker.

Run with `tests/redred_uzh_mc_wtb_six_arm_generator/run_native.sh`.
