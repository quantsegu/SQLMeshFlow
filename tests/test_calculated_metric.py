import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from sqlmesh_flow.calculated import build, scalar_expression
from sqlmesh_flow.runtime import apply

EXAMPLE = Path(__file__).parents[1] / "examples/calculated_vault_metric"


@pytest.fixture
def config_path(tmp_path):
    config = json.loads((EXAMPLE / "metric.json").read_text())
    database = tmp_path / "source.duckdb"
    config["source"]["database"] = str(database)
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE SCHEMA vault")
        db.execute("""CREATE TABLE vault.history (
            ORDER_HK BLOB, LOAD_DTS TIMESTAMPTZ, EFFECTIVE_AT TIMESTAMPTZ,
            QUANTITY VARCHAR, UNIT_PRICE VARCHAR, DISCOUNT_RATE VARCHAR, REGION VARCHAR)""")
        db.execute("""INSERT INTO vault.history VALUES
            ('a', '2026-01-01', '2026-01-01', '2', '100', '.1', 'CH'),
            ('b', '2026-01-02', '2026-01-02', '4', '50', '.2', 'DE'),
            ('c', '2026-01-03', '2026-01-03', '1', '80', NULL, 'CH'),
            ('a', '2026-01-06', '2026-01-01', '3', '100', '.1', 'CH')""")
        # SQLMesh vault tables are exposed as views with catalog-qualified targets.
        db.execute(
            'CREATE VIEW vault.sat_order AS SELECT * FROM "source".vault.history'
        )
    path = tmp_path / "metric.json"
    path.write_text(json.dumps(config))
    return path


def test_direct_vault_calculation_and_history(config_path, tmp_path):
    config = json.loads(config_path.read_text())
    source = Path(config["source"]["database"])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    project = tmp_path / "net"
    build(config_path, project)
    first = apply(project, "2026-01-08T00:00:00Z")["models"]
    assert first["metrics.net_sales"] == [{"net_sales": 510.0}]
    assert sorted(
        first["metrics.net_sales_by_group"], key=lambda r: r["vault_order__region"]
    ) == [
        {"vault_order__region": "CH", "net_sales": 350.0},
        {"vault_order__region": "DE", "net_sales": 160.0},
    ]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert (
        apply(project, "2026-01-08T00:00:00Z")["models"]["metrics.net_sales"]
        == first["metrics.net_sales"]
    )
    with duckdb.connect(str(source)) as db:
        db.execute(
            "INSERT INTO vault.history VALUES ('a', '2026-01-09', '2026-01-01', '4', '100', '.1', 'CH')"
        )
    second = apply(project, "2026-01-10T00:00:00Z")
    assert second["models"]["metrics.net_sales"] == [{"net_sales": 600.0}]


def test_formula_change_requires_config_only(config_path, tmp_path):
    config = json.loads(config_path.read_text())
    config["metric"]["calculation"] = "QUANTITY * UNIT_PRICE"
    config_path.write_text(json.dumps(config))
    project = tmp_path / "gross"
    build(config_path, project)
    assert apply(project, "2026-01-08T00:00:00Z")["models"]["metrics.net_sales"] == [
        {"net_sales": 580.0}
    ]


@pytest.mark.parametrize(
    "expression",
    [
        "QUANTITY + MISSING",
        "SELECT QUANTITY FROM vault.sat_order",
        "QUANTITY; DROP TABLE x",
        "read_csv('/tmp/example')",
        "other.QUANTITY",
    ],
)
def test_invalid_calculation_rejected(expression, config_path, tmp_path):
    config = json.loads(config_path.read_text())
    config["metric"]["calculation"] = expression
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        build(config_path, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_ambiguous_history_rejected(config_path, tmp_path):
    config = json.loads(config_path.read_text())
    with duckdb.connect(config["source"]["database"]) as db:
        db.execute("INSERT INTO vault.history SELECT * FROM vault.history LIMIT 1")
    with pytest.raises(ValueError, match="Ambiguous satellite history"):
        build(config_path, tmp_path / "invalid")


def test_explicit_safe_division():
    assert "NULLIF" in scalar_expression(
        "QUANTITY / NULLIF(UNIT_PRICE, 0)",
        {"QUANTITY": "DOUBLE", "UNIT_PRICE": "DOUBLE"},
    )
