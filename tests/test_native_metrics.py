import json
import shutil
from pathlib import Path

import pytest
from metricflow_semantics.errors.error_classes import (
    InvalidQueryException,
    UnknownMetricError,
)
from sqlmesh_flow.compiler import build
from sqlmesh_flow.runtime import apply

ROOT = Path(__file__).parents[1]


@pytest.fixture
def project(tmp_path):
    out = tmp_path / "native"
    build(ROOT / "examples/project.json", out)
    return out


def test_native_plan_values_tests_and_replay(project):
    result = apply(project)
    assert result["model_tests"] == 2
    assert result["models"]["metrics.by_country"] == [
        {
            "customer__country": "CH",
            "revenue": 150.0,
            "orders": 3,
            "average_order_value": 50.0,
        },
        {
            "customer__country": "DE",
            "revenue": 200.0,
            "orders": 1,
            "average_order_value": 200.0,
        },
    ]
    assert result["models"]["metrics.overview"] == [
        {"revenue": 350.0, "buyers": 3, "revenue_after_fee": 315.0}
    ]
    assert [r["running_revenue"] for r in result["models"]["metrics.running"]] == [
        100,
        350,
        350,
        350,
        350,
    ]
    assert apply(project)["models"] == result["models"]


def test_seed_change_replanned_and_materialized(project):
    apply(project)
    seed = project / "seeds/main.orders.csv"
    seed.write_text(seed.read_text() + "5,20,2026-01-04,50,paid\n")
    result = apply(project, "2026-01-07T00:00:00Z")
    assert result["models"]["metrics.overview"][0]["revenue"] == 400
    assert result["models"]["metrics.overview"][0]["revenue_after_fee"] == 360


def test_bad_metric_rejected_before_execution(tmp_path):
    spec = json.loads((ROOT / "examples/project.json").read_text())
    spec["queries"]["metrics.bad"] = {"metric_names": ["nonexistent_metric"]}
    src = tmp_path / "source"
    shutil.copytree(ROOT / "examples", src)
    (src / "project.json").write_text(json.dumps(spec))
    with pytest.raises((InvalidQueryException, UnknownMetricError)):
        build(src / "project.json", tmp_path / "out")


def test_native_failed_model_test_blocks_plan(project):
    tests = project / "tests/test_metrics.yaml"
    data = json.loads(tests.read_text())
    data["test_overview_values"]["outputs"]["query"]["rows"][0]["revenue"] = 999
    tests.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="model tests failed"):
        apply(project)


def test_sqlmesh_models_include_native_metricflow_sql(project):
    sql = (project / "models/metrics.by_country.sql").read_text()
    assert "kind FULL" in sql
    assert "JOIN" in sql and "SUM" in sql
    manifest = json.loads((project / "manifest.json").read_text())
    assert manifest["compiler"] == "metricflow"


def test_empty_aggregate_preserves_sql_nulls(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "examples", source)
    spec = json.loads((source / "project.json").read_text())
    spec["queries"] = {
        "metrics.empty": {
            "metric_names": [
                "revenue",
                "orders",
                "average_order_value",
                "revenue_after_fee",
            ],
            "where_constraints": ["{{ Dimension('order__status') }} = 'missing'"],
        }
    }
    spec["tests"] = {}
    (source / "project.json").write_text(json.dumps(spec))
    project = tmp_path / "empty"
    build(source / "project.json", project)
    result = apply(project)
    expected = [
        {
            "revenue": None,
            "orders": 0,
            "average_order_value": None,
            "revenue_after_fee": None,
        }
    ]
    assert result["models"]["metrics.empty"] == expected
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert apply(project)["models"]["metrics.empty"] == expected
