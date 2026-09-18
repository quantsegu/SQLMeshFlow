# SQLMeshFlow

Complete pinned MetricFlow and SQLMesh sources, plus native semantic-metric model
generation and execution. MetricFlow compiles the real semantic graph and metric
queries; SQLMesh owns the resulting models, snapshots, tests, plans and materialization.

## Full copies

`vendor/metricflow/` and `vendor/sqlmesh/` contain every tracked file from the
upstream revisions in `UPSTREAM.lock.json`, including tests, fixtures, documentation,
licenses and optional integrations. They are unmodified source snapshots, not Git
history. Verify their content with `python scripts/verify_sources.py`.
`sqlmesh_flow/` contains the new integration. This is an independent distribution.

## Run

Python 3.11 and Hatch:

```sh
hatch run python -m sqlmesh_flow.cli build examples/project.json --output build/demo
hatch run python -m sqlmesh_flow.cli apply build/demo --execution-time 2026-01-06T00:00:00Z
hatch run test
```

This executes native SQLMesh model tests, creates a plan, applies it, runs the DAG
and reads the persisted results. It is not SQL generation alone.

The example materializes:

- `metrics.by_country`: joined revenue, distinct order count and average order value.
- `metrics.running`: cumulative daily revenue, including empty time-spine days.
- `metrics.overview`: total revenue, distinct buyers and a derived fee-adjusted metric.

Expected totals are revenue 350, buyers 3 and revenue after fee 315. The cumulative
series is 100, 350, 350, 350, 350. Two native model tests validate joined and aggregate
values. Integration tests also change a seed and verify SQLMesh replans and stores
updated totals; deliberately wrong model tests block planning.

## Project contract

`examples/project.json` contains:

- `semantic_directory`: native MetricFlow YAML with models, entities, measures,
  metrics and time-spine metadata. This is the standalone upstream format.
- `sources`: native SQLMesh seed relation names, CSV paths and declared types.
- `queries`: output relation → name-based `MetricFlowQueryRequest` arguments.
- `tests`: native SQLMesh model-test definitions with explicit expected results.

All paths resolve relative to the project specification. ISO time-bound strings
are converted before constructing the native query request. Invalid metrics and
unknown configuration fields fail; no fallback metric logic is invented.
Definitions and query expressions are trusted project code.

The generated project has native SEED source models, FULL metric models, model
tests, a DuckDB gateway and a manifest containing the exact MetricFlow SQL.
Edit a seed and apply again to exercise SQLMesh's change detection. The provided
runtime uses the project's local `warehouse.duckdb`, not any production account.
Generated projects can also be used with the full SQLMesh API/CLI.

```python
from sqlmesh_flow.compiler import build
from sqlmesh_flow.runtime import apply
build('examples/project.json', 'build/demo')
report = apply('build/demo', '2026-01-06T00:00:00Z')
```

## Boundary and validation

The complete engines are included, so their upstream capabilities and APIs remain
available. The new project exporter uses CSV SEED inputs, name-based query options,
resolved parameters and FULL metric models. It does not configure every upstream
connector, expose every typed request object in JSON, or validate external warehouse
credentials. The generated dialect/gateway is DuckDB. Materialization follows
SQLMesh's transaction semantics; it is not a globally atomic multi-model operation.

See `TEST_REPORT.md` and `reports/` for actual test executions. The HamiltonFlow
integration and the SQLMeshFlow integration are separate: this package does not
need Hamilton to run. Both use the same complete MetricFlow compiler source.

## Tested Data Vault → configurable metrics sample

See [the runnable cross-framework sample](examples/vault_metrics_sample/README.md)
and its [measured results](examples/vault_metrics_sample/RESULTS.md). It compares
HamiltonVault with SQLMeshVault, then HamiltonFlow with SQLMeshFlow, exercising
configuration changes, incremental histories, replay, rejected historical rewrites
and empty metric results. Keep the three repositories as sibling checkouts and run
`python examples/vault_metrics_sample/run_sample.py --output build/vault-metrics-demo`.

## Calculations over vault columns

Use `build-calculated` to define a metric in JSON with a source vault table, typed
columns, a scalar calculation, aggregation and grouping. The builder selects the
latest satellite row per key and compiles the expression through MetricFlow into
native SQLMesh models. The source database is attached read-only.

See [the calculated vault metric example](examples/calculated_vault_metric/README.md)
and its [configuration](examples/calculated_vault_metric/metric.json):
`SUM(QUANTITY * UNIT_PRICE * (1 - COALESCE(DISCOUNT_RATE, 0)))` returns 510 through
both HamiltonFlow and SQLMeshFlow on the shipped vault history.

The calculated-metric configuration also accepts `target.database`, `target.schema`,
`target.table` and `target.grouped_table`. See
[the complete target example](examples/calculated_vault_metric/metric-target.json).
Results are persisted by SQLMesh and exposed through stable views at the configured
names; the source vault stays read-only.

## Warehouse adapters

See [WAREHOUSES.md](WAREHOUSES.md) for Databricks, Snowflake, and ClickHouse configuration, target placement, offline validation, and execution limitations.
