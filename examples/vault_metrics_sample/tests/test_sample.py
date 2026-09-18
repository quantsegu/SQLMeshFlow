import json
import os
from pathlib import Path

import pytest
from run_sample import ROOT, run_sample, validate_config


@pytest.mark.parametrize(
    "key,value",
    [
        ("fee_rate", -0.1),
        ("fee_rate", 1.1),
        ("fee_rate", float("nan")),
        ("included_status", "paid' OR 1=1"),
    ],
)
def test_invalid_configuration(key, value):
    config = json.loads((ROOT / "config.json").read_text())
    config[key] = value
    with pytest.raises(ValueError):
        validate_config(config)


def test_vault_to_configurable_metrics(tmp_path):
    interpreters = {
        k: os.environ["SAMPLE_" + k.upper() + "_PYTHON"]
        for k in ["vault", "hamilton", "sqlmesh"]
        if "SAMPLE_" + k.upper() + "_PYTHON" in os.environ
    }
    output = Path(os.environ.get("SAMPLE_OUTPUT", tmp_path / "sample"))
    report = run_sample(output, python_paths=interpreters)
    assert report["status"] == "passed"
    expected = {
        "baseline": (380, 342),
        "config_changed": (380, 304),
        "incremental": (480, 384),
    }
    for phase, (revenue, after_fee) in expected.items():
        row = report["metrics"][phase]["expected"]["metrics.overview"][0]
        assert row["revenue"] == revenue
        assert row["revenue_after_fee"] == after_fee
    assert report["vault"]["incremental"]["counts"]["sat_order"] == 6


def test_configurable_status_filter_and_empty_result(tmp_path):
    config = json.loads((ROOT / "config.json").read_text())
    config["included_status"] = "pending"
    config_path = tmp_path / "pending.json"
    config_path.write_text(json.dumps(config))
    interpreters = {
        k: os.environ["SAMPLE_" + k.upper() + "_PYTHON"]
        for k in ["vault", "hamilton", "sqlmesh"]
        if "SAMPLE_" + k.upper() + "_PYTHON" in os.environ
    }
    output = Path(os.environ.get("SAMPLE_FILTER_OUTPUT", tmp_path / "pending"))
    report = run_sample(output, config_path=config_path, python_paths=interpreters)
    initial = report["metrics"]["baseline"]["expected"]["metrics.overview"][0]
    assert initial["revenue"] == 50 and initial["orders"] == 1
    final = report["metrics"]["incremental"]["expected"]
    assert final["metrics.overview"] == [
        {
            "revenue": None,
            "orders": 0,
            "average_order_value": None,
            "revenue_after_fee": None,
        }
    ]
    assert final["metrics.by_country"] == []
