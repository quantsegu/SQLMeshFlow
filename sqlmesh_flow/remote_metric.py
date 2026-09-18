"""Offline remote calculated-metric compilation; no credentials or connections needed."""

from __future__ import annotations

import json
from pathlib import Path

import sqlglot
import yaml
from metricflow.engine.metricflow_engine import MetricFlowEngine
from metricflow_semantic_interfaces.parsing.dir_to_model import (
    parse_directory_of_yaml_files_to_semantic_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from sqlglot import exp

from .calculated import name, scalar_expression
from .requests import request_from_dict
from .warehouse import Connection, relation, render, table, validate
from .warehouse_client import WarehouseClient


def build(path, output):
    config = json.loads(Path(path).read_text())
    if set(config) != {"warehouse", "source", "metric", "target", "group_by"}:
        raise ValueError(
            "Remote metric requires warehouse, source, metric, target, group_by"
        )
    warehouse = validate(config["warehouse"])
    engine = warehouse["type"]
    source, metric, target = config["source"], config["metric"], config["target"]
    if set(source) != {
        "table",
        "business_key",
        "load_timestamp",
        "time_column",
        "columns",
    }:
        raise ValueError("Remote source uses a warehouse table, not a database file")
    relation(source["table"], engine)
    if set(target) - {"catalog", "schema", "table", "grouped_table"}:
        raise ValueError("Remote target uses catalog/schema/table, not a file path")
    namespace = ".".join(
        name(target[k]) for k in ["catalog", "schema"] if target.get(k)
    )
    relation(namespace, engine, schema=True)
    total = namespace + "." + name(target.get("table", metric["name"]))
    grouped = (
        namespace
        + "."
        + name(target.get("grouped_table", metric["name"] + "_by_group"))
    )
    current = namespace + ".mf_current_" + name(metric["name"])
    spine = namespace + ".mf_spine_" + name(metric["name"])
    if (
        len(
            {
                source["table"].lower(),
                total.lower(),
                grouped.lower(),
                current.lower(),
                spine.lower(),
            }
        )
        != 5
    ):
        raise ValueError("Source, target and helper relations must be distinct")
    allowed_types = {
        "VARCHAR",
        "DOUBLE",
        "INTEGER",
        "BIGINT",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "DATE",
        "BLOB",
    }
    for column, dtype in source["columns"].items():
        name(column)
        if dtype not in allowed_types:
            raise ValueError(f"Unsupported column type {dtype}")
    for column in [
        source["business_key"],
        source["load_timestamp"],
        source["time_column"],
        *config["group_by"],
    ]:
        if column not in source["columns"]:
            raise ValueError(f"Unknown column {column}")
    if metric["aggregation"] not in {"sum", "average", "min", "max"}:
        raise ValueError("Unsupported aggregation")
    expression = scalar_expression(metric["calculation"], source["columns"])
    formula = sqlglot.parse_one(expression, read="duckdb")
    column_names = {c.lower(): c for c in source["columns"]}
    for column in formula.find_all(exp.Column):
        column.set(
            "this", exp.to_identifier(column_names[column.name.lower()], quoted=True)
        )
    expression = formula.sql(dialect="duckdb")

    def nr(value):
        parts = [
            exp.to_identifier(p, quoted=True).sql(dialect=engine)
            for p in value.split(".")
        ]
        return {
            "alias": parts[-1],
            "schema_name": parts[-2],
            **({"database": parts[-3]} if len(parts) == 3 else {}),
        }

    documents = [
        {
            "semantic_model": {
                "name": "vault_orders",
                "node_relation": nr(current),
                "defaults": {"agg_time_dimension": "event_time"},
                "entities": [
                    {
                        "name": "vault_order",
                        "type": "primary",
                        "expr": '"' + source["business_key"] + '"',
                    }
                ],
                "measures": [
                    {
                        "name": metric["name"] + "_value",
                        "expr": render(expression, engine),
                        "agg": metric["aggregation"],
                    }
                ],
                "dimensions": [
                    {
                        "name": "event_time",
                        "expr": '"' + source["time_column"] + '"',
                        "type": "time",
                        "type_params": {"time_granularity": "day"},
                    }
                ]
                + [
                    {"name": c.lower(), "expr": '"' + c + '"', "type": "categorical"}
                    for c in config["group_by"]
                ],
            }
        },
        {
            "metric": {
                "name": metric["name"],
                "type": "simple",
                "type_params": {"measure": metric["name"] + "_value"},
            }
        },
        {
            "project_configuration": {
                "time_spines": [
                    {
                        "node_relation": nr(spine),
                        "primary_column": {
                            "name": "date_day",
                            "time_granularity": "day",
                        },
                    }
                ]
            }
        },
    ]
    output = Path(output)
    if output.exists():
        raise ValueError("Output already exists")
    (output / "semantic").mkdir(parents=True)
    (output / "models").mkdir()
    (output / "semantic/metric.yaml").write_text(
        yaml.safe_dump_all(documents, sort_keys=False)
    )
    manifest = parse_directory_of_yaml_files_to_semantic_manifest(
        str(output / "semantic")
    ).semantic_manifest
    client = WarehouseClient(warehouse)
    compiler = MetricFlowEngine(SemanticManifestLookup(manifest), client)
    queries = {total: {"metric_names": [metric["name"]]}}
    if config["group_by"]:
        groups = ["vault_order__" + c.lower() for c in config["group_by"]]
        queries[grouped] = {
            "metric_names": [metric["name"]],
            "group_by_names": groups,
            "order_by_names": groups,
        }
    models = {}
    for target_name, request in queries.items():
        statement = compiler.explain(request_from_dict(request)).sql_statement
        sql = client.prepare(statement.sql, statement.bind_parameter_set)
        tree = sqlglot.parse_one(sql, read=engine)
        for item in list(tree.find_all(exp.Table)):
            parts = ".".join(p.name for p in item.parts)
            if parts.lower() == current.lower():
                replacement = table(current)
                if item.args.get("alias"):
                    replacement.set("alias", item.args["alias"].copy())
                item.replace(replacement)
        sql = tree.sql(dialect=engine)
        models[target_name] = {"request": request, "sql": sql}
        (output / "models" / f"{target_name}.sql").write_text(
            f"MODEL (name {table(target_name).sql(dialect=engine)}, kind FULL, dialect {engine});\n{sql};\n"
        )
    q = lambda n: '"' + name(n) + '"'
    casts = ", ".join(
        f"CAST({q(c)} AS {t}) AS {q(c)}" for c, t in source["columns"].items()
    )
    src = table(source["table"]).sql()
    current_sql = render(
        f"SELECT {casts} FROM {src} QUALIFY ROW_NUMBER() OVER(PARTITION BY {q(source['business_key'])} ORDER BY CAST({q(source['load_timestamp'])} AS TIMESTAMPTZ) DESC)=1",
        engine,
    )
    # Simple aggregates do not join a time spine; distinct dates provide their metadata relation.
    spine_sql = render(
        f"SELECT DISTINCT CAST({q(source['time_column'])} AS DATE) AS date_day FROM {table(current).sql()}",
        engine,
    )
    for target_name, sql in [(current, current_sql), (spine, spine_sql)]:
        (output / "models" / f"{target_name}.sql").write_text(
            f"MODEL (name {table(target_name).sql(dialect=engine)}, kind FULL, dialect {engine});\n{sql};\n"
        )
    pk, ldts = q(source["business_key"]), q(source["load_timestamp"])
    checks = [
        render(
            f"SELECT 1 FROM {src} WHERE {pk} IS NULL OR {ldts} IS NULL LIMIT 1", engine
        ),
        render(
            f"SELECT {pk}, CAST({ldts} AS TIMESTAMPTZ) FROM {src} GROUP BY {pk}, CAST({ldts} AS TIMESTAMPTZ) HAVING COUNT(*)>1 LIMIT 1",
            engine,
        ),
    ]
    result = {
        "engine": "sqlmesh",
        "warehouse": warehouse,
        "models": models,
        "target": target,
        "checks": checks,
        "validation": "offline compilation only; live warehouse execution not verified",
        "clickhouse_metricflow_bridge": engine == "clickhouse",
    }
    (output / "manifest.json").write_text(json.dumps(result, indent=2))
    (output / "remote_metric.json").write_text(json.dumps(config, indent=2))
    (output / "external_models.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": source["table"],
                    "columns": {
                        c: exp.DataType.build(t, dialect="duckdb").sql(dialect=engine)
                        for c, t in source["columns"].items()
                    },
                }
            ]
        )
    )
    (output / "config.py").write_text(
        "from pathlib import Path\nimport json\nfrom sqlmesh_flow.warehouse import sqlmesh_config\nroot=Path(__file__).parent\nconfig=sqlmesh_config(json.loads((root/'manifest.json').read_text())['warehouse'],root)\n"
    )
    return result


def preflight(path):
    manifest = json.loads((Path(path) / "manifest.json").read_text())
    client = Connection(manifest["warehouse"])
    try:
        for query in manifest["checks"]:
            if client.query(query)[1]:
                raise ValueError(
                    "Remote source has null keys/timestamps or ambiguous history"
                )
    finally:
        client.close()
