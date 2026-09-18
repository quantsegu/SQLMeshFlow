"""Run a configurable vault-to-metrics sample through four real frameworks."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_FRAMEWORK_ROOT = next(
    (parent for parent in ROOT.parents if (parent / "SQLMeshFlow").is_dir()),
    ROOT.parent,
)


def validate_config(config):
    for key in ["fee_rate", "changed_fee_rate"]:
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"{key} must be a finite number between 0 and 1")
    if config["included_status"] not in ["paid", "pending", "cancelled"]:
        raise ValueError("included_status must be paid, pending or cancelled")
    if config["fee_rate"] == config["changed_fee_rate"]:
        raise ValueError("Use different fee rates to demonstrate configuration changes")


def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def expected_values(export, config, rate):
    customers = {
        int(r["customer_id"]): r["country"] for r in rows(export / "customers.csv")
    }
    orders = [
        r
        for r in rows(export / "orders.csv")
        if r["status"] == config["included_status"]
    ]

    def aggregate(values):
        revenue = sum(float(r["amount"]) for r in values) if values else None
        count = len({r["order_id"] for r in values})
        return {
            "revenue": revenue,
            "orders": count,
            "average_order_value": revenue / count if count else None,
            "revenue_after_fee": revenue * (1 - rate) if revenue is not None else None,
        }

    countries = sorted({customers[int(r["customer_id"])] for r in orders})
    return {
        "metrics.overview": [aggregate(orders)],
        "metrics.by_country": [
            {
                "customer__country": country,
                **aggregate(
                    [r for r in orders if customers[int(r["customer_id"])] == country]
                ),
            }
            for country in countries
        ],
    }


def prepare_metric_project(export, project, config, rate):
    project.mkdir(parents=True)
    for path in export.glob("*.csv"):
        shutil.copyfile(path, project / path.name)
    (project / "semantic").mkdir()
    template = (ROOT / "source/semantic.template.yaml").read_text()
    (project / "semantic/sales.yaml").write_text(
        template.replace("revenue * 0.9", f"revenue * {1 - rate:.12g}")
    )
    metric_names = ["revenue", "orders", "average_order_value", "revenue_after_fee"]
    request = {
        "metric_names": metric_names,
        "where_constraints": [
            "{{ Dimension('order__status') }} = '" + config["included_status"] + "'"
        ],
    }
    expected = expected_values(export, config, rate)
    spec = {
        "semantic_directory": "semantic",
        "sources": {
            "main.orders": {
                "path": "orders.csv",
                "columns": {
                    "order_id": "BIGINT",
                    "customer_id": "BIGINT",
                    "ordered_at": "TIMESTAMP",
                    "amount": "DOUBLE",
                    "status": "VARCHAR",
                },
            },
            "main.customers": {
                "path": "customers.csv",
                "columns": {"customer_id": "BIGINT", "country": "VARCHAR"},
            },
            "main.time_spine": {
                "path": "time_spine.csv",
                "columns": {"date_day": "DATE"},
            },
        },
        "queries": {
            "metrics.overview": request,
            "metrics.by_country": {
                **request,
                "group_by_names": ["customer__country"],
                "order_by_names": ["customer__country"],
            },
        },
        "tests": {},
    }
    orders = [
        {
            **r,
            "order_id": int(r["order_id"]),
            "customer_id": int(r["customer_id"]),
            "amount": float(r["amount"]),
        }
        for r in rows(export / "orders.csv")
    ]
    customers = [
        {**r, "customer_id": int(r["customer_id"])}
        for r in rows(export / "customers.csv")
    ]
    for name, values in expected.items():
        inputs = {"main.orders": {"rows": orders}}
        if name.endswith("by_country"):
            inputs["main.customers"] = {"rows": customers}
        spec["tests"]["test_" + name.replace(".", "_")] = {
            "model": name,
            "inputs": inputs,
            "outputs": {"query": {"rows": values}},
        }
    (project / "project.json").write_text(json.dumps(spec, indent=2) + "\n")
    (project / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    return expected


def compare(actual, expected):
    assert set(actual) == set(expected)
    for name, wanted in expected.items():
        got = sorted(actual[name], key=lambda row: row.get("customer__country", ""))
        wanted = sorted(wanted, key=lambda row: row.get("customer__country", ""))
        assert len(got) == len(wanted), (name, got, wanted)
        for a, b in zip(got, wanted):
            assert set(a) == set(b)
            for key, value in b.items():
                if isinstance(value, (float, int)):
                    assert math.isclose(a[key], value, rel_tol=1e-9, abs_tol=1e-9), (
                        name,
                        key,
                        a,
                        b,
                    )
                else:
                    assert a[key] == value, (name, key, a, b)


def run_sample(
    output,
    config_path=ROOT / "config.json",
    framework_root=DEFAULT_FRAMEWORK_ROOT,
    python_paths=None,
):
    output, framework_root = Path(output).resolve(), Path(framework_root).resolve()
    config = json.loads(Path(config_path).read_text())
    validate_config(config)
    if output.exists():
        raise ValueError(
            "Output already exists; choose a new directory to preserve prior evidence"
        )
    output.mkdir(parents=True)
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    env = dict(
        os.environ,
        HAMILTON_TELEMETRY_ENABLED="false",
        SQLMESH__DISABLE_ANONYMIZED_ANALYTICS="true",
        SQLMESH_HOME=str(output / ".sqlmesh-user"),
        PYARROW_IGNORE_TIMEZONE="1",
    )
    env.pop("PYTHONPATH", None)
    python_paths = python_paths or {}

    def worker(kind, framework, *args):
        interpreter = python_paths.get(kind)
        command = [str(interpreter)] if interpreter else ["hatch", "run", "python"]
        command += [str(ROOT / "worker.py"), kind, *map(str, args)]
        log = output / f"{kind}-{len(list(output.glob(kind + '-*.log')))}.log"
        with log.open("w") as f:
            subprocess.run(
                command,
                cwd=framework_root / framework,
                env={**env, "PYTHONPATH": str(framework_root / framework)},
                stdout=f,
                stderr=subprocess.STDOUT,
                check=True,
            )

    worker("vault", "SQLMeshVault", ROOT / "source", output, output / "config.json")
    report = {
        "vault": json.loads((output / "vault-results.json").read_text()),
        "metrics": {},
    }
    scenarios = [
        ("baseline", "initial", config["fee_rate"]),
        ("config_changed", "initial", config["changed_fee_rate"]),
        ("incremental", "incremental", config["changed_fee_rate"]),
    ]
    for name, phase, rate in scenarios:
        project = output / name
        expected = prepare_metric_project(
            output / "exports" / phase, project, config, rate
        )
        worker("hamilton", "HamiltonFlow", project)
        worker("sqlmesh", "SQLMeshFlow", project)
        hamilton = json.loads((project / "hamilton-results.json").read_text())
        sqlmesh = json.loads((project / "sqlmesh-results.json").read_text())
        compare(hamilton, expected)
        compare(sqlmesh["models"], expected)
        compare(hamilton, sqlmesh["models"])
        report["metrics"][name] = {
            "fee_rate": rate,
            "expected": expected,
            "hamilton_matches": True,
            "sqlmesh_matches": True,
            "native_model_tests": sqlmesh["model_tests"],
        }
    report["status"] = "passed"
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--framework-root", type=Path, default=DEFAULT_FRAMEWORK_ROOT)
    for kind in ["vault", "hamilton", "sqlmesh"]:
        parser.add_argument(f"--{kind}-python", type=Path)
    args = parser.parse_args()
    interpreters = {
        k: getattr(args, k + "_python")
        for k in ["vault", "hamilton", "sqlmesh"]
        if getattr(args, k + "_python")
    }
    result = run_sample(args.output, args.config, args.framework_root, interpreters)
    print(json.dumps(result, indent=2))
