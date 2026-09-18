"""Warehouse SQL generation and native SQLMesh model parsing only; never connect."""

from pathlib import Path

import pytest
import sqlglot
from sqlmesh.core.dialect import parse
from sqlmesh_flow.calculated import build
from sqlmesh_flow.warehouse import Connection

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_remote_calculated_project_is_offline(engine, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline compile attempted a warehouse connection")

    monkeypatch.setattr(Connection, "connect", forbidden)
    result = build(
        ROOT / "examples/warehouses" / f"{engine}-metric.json", tmp_path / "project"
    )
    assert result["clickhouse_metricflow_bridge"] == (engine == "clickhouse")
    for path in (tmp_path / "project/models").glob("*.sql"):
        statements = parse(path.read_text(), default_dialect=engine)
        assert len(statements) == 2
        sql = statements[1].sql(dialect=engine)
        sqlglot.parse_one(sql, read=engine)
        assert "read_csv" not in sql.lower()
    for record in result["models"].values():
        assert "SUM(" in record["sql"].upper()
        if engine == "snowflake":
            assert '"DEMO"."analytics"."mf_current_net_sales"' in record["sql"]
    assert "env" in (tmp_path / "project/manifest.json").read_text()
    assert "sqlmesh_config" in (tmp_path / "project/config.py").read_text()


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_standard_remote_compile(engine, tmp_path, monkeypatch):
    import json
    import shutil

    from sqlmesh_flow.compiler import build as compile_standard

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline compilation connected")

    monkeypatch.setattr(Connection, "connect", forbidden)
    examples = tmp_path / "examples"
    shutil.copytree(ROOT / "examples", examples)
    spec = json.loads((examples / "project.json").read_text())
    spec["warehouse"] = json.loads((examples / "warehouses" / f"{engine}.json").read_text())
    (examples / "project.json").write_text(json.dumps(spec))
    compile_standard(examples / "project.json", tmp_path / "project")
    for path in (tmp_path / "project/models").glob("*.sql"):
        assert parse(path.read_text(), default_dialect=engine)
