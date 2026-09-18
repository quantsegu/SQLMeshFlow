# Configurable sales metrics over a Data Vault

This runnable sample tests HamiltonVault, SQLMeshVault, HamiltonFlow and SQLMeshFlow
against the same customer and order histories. It uses real DuckDB databases and
native framework execution, not mocks. `test-results.xml` and `results/results.json`
contain execution evidence; `RESULTS.md` summarizes the measured results.

## Run

Place this directory alongside checkouts named `HamiltonFlow`, `SQLMeshVault` and
`SQLMeshFlow`. Use Python 3.11 and install Hatch (`pip install hatch`). HamiltonVault
is already bundled in SQLMeshVault. No warehouse credentials are needed.

```sh
python run_sample.py --config config.json --output results-new
```

The runner imports each repository's current working-tree code using its own Hatch environment to keep dependencies
separate. Pass `--framework-root /path/to/checkouts` if the repositories are elsewhere.
For existing virtual environments, use `--vault-python`, `--hamilton-python` and
`--sqlmesh-python` to specify their Python executables. The script refuses to reuse
an output directory so previous databases and evidence remain intact.

To run the automated suite with pytest installed in your controlling environment:

```sh
python -m pytest tests -q --junitxml=test-results.xml
```

Tests use temporary output by default. `SAMPLE_OUTPUT` selects a retained directory;
`SAMPLE_VAULT_PYTHON`, `SAMPLE_HAMILTON_PYTHON` and `SAMPLE_SQLMESH_PYTHON` select existing
interpreters. The suite includes four invalid-configuration cases and two full
multi-framework executions with all the assertions described below. The second execution uses the pending
status and checks empty aggregates after the pending order becomes paid.

## Configure

Edit `config.json`:

- `fee_rate`: initial fraction deducted from revenue, between 0 and 1.
- `changed_fee_rate`: different fee used in the configuration-only and incremental scenarios.
- `included_status`: `paid`, `pending`, or `cancelled` order filter.
- `execution_times`: initial and incremental load timestamps. Keep the second on a
  later day and after the provided delta events.

The sample generates native MetricFlow YAML, native query-request JSON and native
SQLMesh model tests. Changing a fee does not require editing Python or writing SQL.
The canned pytest assertions target the shipped paid-order configuration; the
runner's independent CSV calculations adapt to the chosen configuration.

`source/vault.json` is the editable Data Vault metadata: two source stages, two
hubs, one customer/order link, and customer/order satellites. Source column mappings,
hash keys, hashdiff payloads, record sources and parent references are explicit.
The source CSVs contain synthetic data. Delta files add a customer city change,
a pending order becoming paid, and an order amount revision.

## What is executed and checked

1. HamiltonVault and SQLMeshVault load identical metadata and initial events.
2. Every persisted hub, link and satellite row is compared across both frameworks.
3. Both loaders replay the same input; no stored rows may change.
4. A second batch adds satellite history while retaining original hub/link keys.
5. Historical source rewrites are rejected; complete vault snapshots remain unchanged.
6. A latest-state sales mart is exported from SQLMeshVault to typed CSV inputs.
7. HamiltonFlow queries and SQLMeshFlow materializations compute the same metrics.
8. Both results are compared with an independent Python calculation from the CSVs.
9. Native SQLMesh model tests run before materialization, and metric replay is checked.
10. The metric checks repeat for a configuration-only fee change and incremental data.

## Data and metric semantics

Paid revenue sums the latest known amount of orders currently marked paid. Order
count is distinct order IDs; average order value is revenue divided by that count;
revenue after fee is revenue multiplied by `(1 - fee_rate)`. Metrics also group by
customer country. Monetary values use DOUBLE in this demonstration; production
currency precision and rounding require a defined DECIMAL policy.

The export uses the latest satellite record by load timestamp. It preserves order
business time from `EFFECTIVE_AT`, but uses each customer's latest country. This is
an explicit **current-state** mart, not a historical as-of customer join. The two
fee scenarios use separate generated projects/databases to isolate configuration
behavior; the vault incremental scenario updates existing databases.

This sample tests DuckDB only. SQLMesh's native per-model transaction semantics
apply. It does not establish cloud warehouse or production-scale performance.

## Artifacts

- `results/hamilton-vault.duckdb`: HamiltonVault hub/link/satellite data.
- `results/sqlmesh-vault/warehouse.duckdb`: SQLMeshVault data and state.
- `results/sqlmesh-vault/models/`: generated native vault SQL.
- `results/exports/`: initial and incremental current-state marts.
- `results/{baseline,config_changed,incremental}/semantic/`: generated metric definitions.
- Each metric scenario includes source CSVs, query JSON, expected values, generated
  SQL, a Hamilton query database, and a SQLMesh warehouse with persisted metrics.
- `results-pending/`: a second full execution with a pending-status filter; the final
  empty aggregate returns JSON null for sums/ratios and zero for order count.
- `results/results.json`: parity, replay, validation and result summary.
