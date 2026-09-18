# Test report

| Suite | Passed | Failed |
|---|---:|---:|
| SQLMeshFlow integration | 5 | 0 |
| SQLMesh audit, dialect, schema-diff and date suites | 355 | 0 |

The integration executes native SQLMesh plans and materializes actual MetricFlow
SQL. Assertions cover joined revenue/ratio metrics, derived and cumulative metrics,
replay, changed seed replanning, invalid metric rejection and failing native model
tests blocking an apply. Two generated SQLMesh model tests verify expected values.

Full copied sources: 5,181 MetricFlow files and 1,726 SQLMesh files, hash-verified.
The same MetricFlow revision's broader upstream suites were executed in
[HamiltonFlow](https://github.com/quantsegu/HamiltonFlow/blob/main/TEST_REPORT.md),
including one failing timing benchmark. They were not rerun here and are not
counted as SQLMeshFlow test executions.

## Reproduction and scope

Run `hatch run test` for the added integration and
`hatch run python scripts/run_upstream_tests.py` for the documented upstream selection.
JUnit XML files are in `reports/`; `environment.txt` records installed versions.
Installed wheel CLI entry points were smoke-tested from outside the source tree.
`python scripts/verify_sources.py` validates every copied upstream file, including symlinks.

Full source copies do not mean all upstream tests were executed. External warehouse,
cloud service, dbt end-to-end, slow/performance and complete cross-platform matrices
were not validated. Tests ran on macOS ARM64 with Python 3.11 and local DuckDB.
Deprecation warnings remain in upstream dependencies. No upstream code was changed
to suppress failures. GitHub CI runs the new integration tests and source verification.
