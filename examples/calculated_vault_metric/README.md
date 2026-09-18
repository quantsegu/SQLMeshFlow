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
The source basename must be an SQL identifier and cannot be `metric_store`.

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
SQLMeshFlow integration suite passed **15 tests** after this addition.

The sample was also executed against the actual SQLMeshVault loader output through
both HamiltonFlow and SQLMeshFlow. See `results.json` for values and source-integrity
confirmation. Run the commands above to regenerate databases and native SQL.
