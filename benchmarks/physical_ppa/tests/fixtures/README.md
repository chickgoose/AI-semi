# W2 comparison fixtures

All W2 activity-power comparison fixtures are materialized into temporary
directories by `test_activity_power_ppa.py`.  They authenticate evaluator
behavior only and carry the immutable origin marker
`TEST_ONLY_NOT_RTL_EVIDENCE`.

They are not simulator, implementation, timing, power, workload, or candidate
evidence.  The evaluator must return `candidate_go: false` and `TEST_ONLY` for
every such fixture, even when every structural gate is otherwise satisfied.
