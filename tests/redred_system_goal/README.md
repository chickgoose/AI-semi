# REDRED system-goal contract tests

The tests load the committed active contract and exercise the verifier through
its Python API and command-line entry point. Mutation cases prove that it fails
closed when required scope, endpoint semantics, candidate semantics, link
approval fallback, correctness equations, failure classification, canonical
trace provenance, coordinate separation, physical gates, HOLD records, or
portable evidence policy are weakened or contradicted.

Run from the repository root:

```bash
bash tests/redred_system_goal/run_all.sh
```

No EDA tool, network service, or third-party Python package is required.
