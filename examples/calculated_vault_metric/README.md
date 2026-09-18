# A metric calculated from multiple vault columns

`metric.json` defines the complete source and calculation contract. The shipped
metric is:

```json
{
  "metric": {
    "name": "net_sales",
    "calculation": "QUANTITY * UNIT_PRICE * (1 - COALESCE(DISCOUNT_RATE, 0))",
    "aggregation": "sum"
  },
  "group_by": ["REGION"]
}
```

The source is `vault.sat_order`. Its declared columns are cast to their configured
types, then the newest row per `ORDER_HK` is selected by `LOAD_DTS`. MetricFlow
applies the configured calculation **to each current order**, then aggregates it.
This computes `SUM(quantity * price * (1 - discount))`; it does not multiply
independently aggregated column totals. Historical satellite rows are retained
in the vault but do not inflate the current-state metric.

The configuration also specifies the database path (relative to the config file),
source relation, business key, load timestamp, business-time column and column
types. It supports one satellite/current-state relation and a single-column grain;
links, multi-active satellites and historical as-of joins need a suitable source
view with the correct grain first.

## Execute against a real vault

Keep `HamiltonFlow`, `SQLMeshVault` and `SQLMeshFlow` as sibling checkouts. Use
Python 3.11 and Hatch. From the SQLMeshVault repository:

```sh
hatch run python ../SQLMeshFlow/examples/calculated_vault_metric/setup_vault.py \
  --output ../SQLMeshFlow/examples/calculated_vault_metric/build/vault
```

This creates an actual metadata-driven vault with three hub rows and four satellite
rows. It is not a CSV metric seed. Then, from the SQLMeshFlow repository:

```sh
hatch run python -m sqlmesh_flow.cli build-calculated \
  examples/calculated_vault_metric/metric.json --output build/calculated-metric
hatch run python -m sqlmesh_flow.cli apply build/calculated-metric \
  --execution-time 2026-01-08T00:00:00Z
```

From the HamiltonFlow repository, execute the same native semantic configuration:

```sh
hatch run python ../SQLMeshFlow/examples/calculated_vault_metric/query_hamilton.py \
  ../SQLMeshFlow/build/calculated-metric
```

Both produce **net sales 510**, grouped as **CH 350** and **DE 160**. Order 1's
old quantity of 2 is replaced by its latest quantity of 3 when querying. Its
current contribution is `3 * 100 * 0.90 = 270`. Orders 2 and 3 contribute 160
and 80. A missing discount explicitly means zero because of `COALESCE`.

Generated projects attach the source DuckDB database **read-only**, preserving its
catalog name so SQLMesh-generated vault views continue to resolve. Metric tables
and SQLMesh state live separately in `metric_store.duckdb`. Retain the vault
file's original basename: stored views may contain catalog-qualified references.
Database basenames must be SQL identifiers and must differ between source and target.

## Choose the output database, schema and relation names

Add a `target` block alongside `source`, `metric` and `group_by`:

```json
"target": {
  "database": "build/published/analytics.duckdb",
  "schema": "finance",
  "table": "sales_total",
  "grouped_table": "sales_by_region"
}
```

`metric-target.json` is a complete runnable example. From the SQLMeshFlow repository:

```sh
hatch run python -m sqlmesh_flow.cli build-calculated \
  examples/calculated_vault_metric/metric-target.json --output build/target-metric
hatch run python -m sqlmesh_flow.cli apply build/target-metric \
  --execution-time 2026-01-08T00:00:00Z
```

The database path resolves relative to the original JSON configuration file. Its
parent directories and the target schema are created when the project is applied.
The apply result reports the resolved target location. Open that DuckDB database
and query:

```sql
SELECT net_sales FROM finance.sales_total;
SELECT vault_order__region, net_sales
FROM finance.sales_by_region ORDER BY vault_order__region;
```

The metric name still controls the result column (`net_sales`); the target names
control where that result is published. `grouped_table` is used only when `group_by`
is non-empty. If omitted, it defaults to `<table>_by_group`. All target settings are
optional: the existing defaults remain the generated project's `metric_store.duckdb`,
schema `metrics`, and table name matching the metric.

These are SQLMesh-managed FULL models: results are persisted in physical snapshot
tables, with stable views at `finance.sales_total` and `finance.sales_by_region`.
The configured names are queryable views, not independently managed physical tables.
A later run refreshes the current result; it does not append duplicate totals. Use
a later daily execution time after source changes. This feature does not export to
an arbitrary remote database or add append/merge delivery modes.

Source and target must have different database paths and catalog names (file stems).
Source files remain read-only. Target model names cannot collide with each other,
the generated helper models or SQLMesh internal schemas. Keep one SQLMesh project
responsible for a given set of published target names.

## Change the formula in configuration

Change only `metric.calculation` to:

```text
QUANTITY * UNIT_PRICE
```

Build into a new output directory and run it: the same source yields **580**.
Changing `metric.name` to `gross_sales` also changes the metric/output model names.
The builder refuses to overwrite an existing generated project. To refresh an
existing project after new source rows arrive, call `apply` with a later daily
execution time; the tests verify a new quantity update changes net sales to 600.

## Supported calculation contract

- Numeric literals, declared source columns, parentheses, `+`, `-`, `*`, `/`, unary
  minus, `COALESCE` and `NULLIF`.
- Aggregations: `sum`, `average`, `min`, `max`.
- For a potentially zero divisor, use `value / NULLIF(divisor, 0)`.
- Missing values follow SQL semantics. Use `COALESCE` explicitly where zero is
  the intended meaning; no implicit null-to-zero conversion is performed.
- Column names and the formula are validated before SQL generation. Unknown
  columns, statements, subqueries and arbitrary function calls are rejected.
- Duplicate key/load-time combinations and null keys/load timestamps are rejected
  before running; the loader does not arbitrarily choose a tied history row.

The sample uses DOUBLE inputs. Define a currency precision and rounding policy
before adapting it for accounting calculations. This implementation is tested on
DuckDB; it does not configure external warehouse connections.

## Evidence

`tests/test_calculated_metric.py` covers row-level arithmetic, native source views,
latest history, source immutability, replay, new events, formula-only changes,
invalid configurations, ambiguous history and explicit safe division. The complete
SQLMeshFlow integration suite passed **24 tests**, including configurable-target creation, direct reads,
refresh and invalid-target rejection.

The sample was also executed against the actual SQLMeshVault loader output through
both HamiltonFlow and SQLMeshFlow. See `results.json` for values and source-integrity
confirmation; `target-results.json` records the configured-target execution. Run the commands above to regenerate databases and native SQL.
