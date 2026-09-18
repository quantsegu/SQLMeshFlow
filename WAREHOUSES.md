# Warehouse capability

The new warehouse paths support configuration and SQL generation for Databricks, Snowflake, and ClickHouse. DuckDB remains the default for existing local examples. No live warehouse tests were performed. Offline SQL compilation and driver stubs do not certify production execution.

| Backend | Metric compilation | Vault / execution |
|---|---|---|
| Databricks | Native MetricFlow renderer | Dialect SQL; SQL Connector or native SQLMesh gateway |
| Snowflake | Native MetricFlow renderer | Dialect SQL; Python Connector or native SQLMesh gateway |
| ClickHouse | DuckDB MetricFlow renderer translated through SQLGlot; limited bridge | ClickHouse SQL and MergeTree; clickhouse-connect or native SQLMesh gateway |

ClickHouse is not a native upstream MetricFlow backend. Unsupported translations fail rather than silently falling back. Use a modern ClickHouse release (25.8+ recommended) for correlated subqueries and window queries; this version floor has not been live certified. Do not assume complete MetricFlow feature parity. Remote MetricFlow bound parameters currently fail closed; use requests that compile to resolved SQL.

## Connection configuration

Copy the matching file from `examples/warehouses/`. Connection-only files contain `type` and `connection`. Vault and calculated metric specifications embed that object under `warehouse`.

```json
{"type":"snowflake","connection":{"account":{"env":"SNOWFLAKE_ACCOUNT"},"user":{"env":"SNOWFLAKE_USER"},"password":{"env":"SNOWFLAKE_PASSWORD"},"warehouse":"COMPUTE_WH","database":"DEMO"}}
```

Environment variables are resolved only when executing, never while compiling. Secret fields require environment references; do not put secret values in JSON or commit credentials. The examples are placeholders, not configured accounts. Install the appropriate optional driver extra in the existing project environment (`pip install '.[databricks]'`, `'.[snowflake]'`, or `'.[clickhouse]'`), retaining the repository's documented vendored dependency setup. Building offline does not require these connectors.

Databricks and Snowflake accept `catalog.schema.table` / `database.schema.table`; ClickHouse uses `database.table` and rejects a catalog. Configured identifier spelling and case are preserved: match existing Snowflake quoted identifiers exactly. UTC sessions and ClickHouse nullable joins are configured explicitly.

## Execution boundaries

Compilation writes local artifacts only. Query, run, or apply commands connect and can write to the configured destination; these commands were not executed against remote warehouses during development. Precreate landing/source relations and grant the execution identity access to source and destination schemas. Remote vault paths do not upload local CSV files.

Use a dedicated staging schema and a single writer with immutable source data during each run. Remote runs do not offer whole-batch rollback, the DuckDB run ledger, automatic schema migration, or concurrency protection. A failed run can leave partial writes. SQLMesh audits may run after materialization. Review generated SQL and plan before production use.

Generated SQLMesh projects use local DuckDB state in `sqlmesh_state.duckdb`, suitable for a single host. Configure an appropriate shared SQLMesh state backend for distributed production deployment. Warehouse data resides in the target engine; local state is orchestration metadata.

## References

- [Databricks SQL Connector](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector)
- [Snowflake Python Connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example)
- [ClickHouse Python client](https://clickhouse.com/docs/integrations/language-clients/python/index)
- SQLMesh engine configuration: [Databricks](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/databricks/), [Snowflake](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/snowflake/), [ClickHouse](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/clickhouse/).

## SQLMeshFlow usage and output tables

```sh
sqlmesh-flow build-calculated examples/warehouses/databricks-metric.json --output build/databricks-metric
# Executes and writes remotely; not run in the offline tests:
sqlmesh-flow apply build/databricks-metric
```

The examples compute `SUM(QUANTITY * UNIT_PRICE * (1 - COALESCE(DISCOUNT_RATE, 0)))` from the latest satellite row per business key, with optional grouping by region. They expect the order satellite shape from `examples/calculated_vault_metric/vault.json`, not the CRM/ERP replacement example used by the other libraries. Match your actual vault columns and table names.

Set `source.table` to an existing vault relation. Set `target.catalog` (Databricks/Snowflake), `target.schema`, `target.table` and optional `target.grouped_table` to choose stored outputs. For ClickHouse omit catalog and use schema as the database. Helper tables `mf_current_<metric>` and `mf_spine_<metric>` are created in the same destination schema. Outputs are FULL models; helpers must not conflict with existing owned tables.

`source.columns` declares input types and `metric.calculation` declares the scalar multi-column expression. This builder supports simple aggregate calculations. Its time spine contains observed dates, not a complete calendar: do not infer cumulative or gap-filling metric support from these examples. Source checks run only during apply.

Ordinary `sqlmesh-flow build` also accepts a top-level `warehouse` object in its existing project specification and uses the matching renderer/gateway. Its small CSV SEED examples are demonstrations, not bulk ingestion tooling.

## Validation record

The local regression suite passed **42 tests** on 2026-09-18. The checked-in [JUnit report](reports/warehouse-regression.xml) includes DuckDB regressions, offline warehouse compilation and driver stubs. No Databricks, Snowflake or ClickHouse service was contacted. SQL parsing establishes compilation coverage, not live backend certification.
