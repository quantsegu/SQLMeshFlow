# Sample execution results

All six sample tests passed in 54.37 seconds. The SQLMeshFlow integration suite also passed all six tests, including the new SQL NULL regression.

Executed with real local DuckDB databases. No mocks or remote warehouses were used.

## Configurable paid-order metrics

| Scenario | Fee | Paid revenue | Orders | Average order value | Revenue after fee |
|---|---:|---:|---:|---:|---:|
| baseline | 10% | 380 | 3 | 126.6667 | 342 |
| config_changed | 20% | 380 | 3 | 126.6667 | 304 |
| incremental | 20% | 480 | 4 | 120.0000 | 384 |

HamiltonFlow and SQLMeshFlow matched these independently computed values and the country-level results. Two native SQLMesh model tests passed for each scenario; materialization replay also passed.

## Data Vault

| Table | Initial rows | After incremental batch |
|---|---:|---:|
| hub_customer | 3 | 3 |
| hub_order | 4 | 4 |
| link_customer_order | 4 | 4 |
| sat_customer | 3 | 4 |
| sat_order | 4 | 6 |

Every row matched between HamiltonVault and SQLMeshVault. Replay preserved every row. Both implementations rejected a historical source rewrite and retained the previously stored vault data.

## Alternate filter and empty aggregates

A second full execution used `included_status: pending`. It initially returned revenue 50 and one order. After the second batch, that order became paid: both metric engines returned zero orders, null revenue/ratio values, and no country groups.

This exposed a SQLMeshFlow conversion bug: SQL NULL aggregates were returned as pandas NaN. The runtime now converts missing values to Python None / JSON null; a regression checks strict JSON serialization and replay.

## Evidence

- `test-results.xml`: six passing sample tests.
- `results/results.json`: paid-order assertions and values.
- `results-pending/results.json`: alternate filter and empty aggregate assertions.
- The generated SQL, model tests, source exports, and populated databases are retained in the local results directories. The GitHub copy includes source and compact reports; rerun it to generate databases.

## Framework revisions tested

- HamiltonFlow: `d6c97d738e56fac80f8056bbe0ef980d9bedd9f7`.
- SQLMeshVault: `045b6af90ad034342caa3694febc5abac8136012`.
- SQLMeshFlow: `9df5637563c3891a19cd67c8f149d45700ff110f` plus the SQL NULL fix and regression included with this sample.
