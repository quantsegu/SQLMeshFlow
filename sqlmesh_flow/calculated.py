"""Config-defined scalar calculations over the latest rows of a vault satellite."""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import sqlglot
import yaml
from metricflow.engine.metricflow_engine import MetricFlowEngine
from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantic_interfaces.parsing.dir_to_model import (
    parse_directory_of_yaml_files_to_semantic_manifest,
)
from metricflow_semantic_interfaces.validations.semantic_manifest_validator import (
    SemanticManifestValidator,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from sqlglot import exp

from .client import DuckDBClient
from .requests import request_from_dict


def name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def quoted(value: str) -> str:
    return '"' + name(value) + '"'


def scalar_expression(expression: str, columns: dict) -> str:
    """Permit arithmetic and explicit null handling, never arbitrary SQL statements."""
    parsed = sqlglot.parse(expression, read="duckdb")
    if len(parsed) != 1 or parsed[0] is None:
        raise ValueError("Calculation must be one scalar expression")
    allowed = (
        exp.Column,
        exp.Identifier,
        exp.Literal,
        exp.Paren,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Div,
        exp.Neg,
        exp.Coalesce,
        exp.Nullif,
        exp.Null,
    )
    node = parsed[0]
    known = {c.upper() for c in columns}
    for part in node.walk():
        if not isinstance(part, allowed):
            raise ValueError(f"Unsupported calculation syntax: {type(part).__name__}")  # noqa: TRY004
        if isinstance(part, exp.Literal) and part.is_string:
            raise ValueError("Only numeric literals are supported in calculations")
        if isinstance(part, exp.Column) and (
            part.table or part.name.upper() not in known
        ):
            raise ValueError(f"Unknown or qualified calculation column: {part.sql()}")
    if not list(node.find_all(exp.Column)):
        raise ValueError("Calculation must reference a source column")
    return node.sql(dialect="duckdb")


def read_config(path: str | Path) -> tuple[dict, Path]:
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if not {"source", "metric", "group_by"} <= set(config) or set(config) - {
        "source",
        "metric",
        "group_by",
        "target",
    }:
        raise ValueError("Expected source, metric, group_by and optional target fields")
    source, metric = config["source"], config["metric"]
    if set(source) != {
        "database",
        "table",
        "business_key",
        "load_timestamp",
        "time_column",
        "columns",
    }:
        raise ValueError("Invalid source configuration fields")
    if set(metric) != {"name", "calculation", "aggregation"}:
        raise ValueError("Invalid metric configuration fields")
    parts = source["table"].split(".")
    if len(parts) != 2:
        raise ValueError("Source table must be schema.table")
    for part in parts:
        name(part)
    types = {
        "VARCHAR",
        "DOUBLE",
        "BIGINT",
        "INTEGER",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "DATE",
        "BLOB",
    }
    for column, dtype in source["columns"].items():
        name(column)
        if dtype not in types:
            raise ValueError(f"Unsupported type: {dtype}")
    for key in [
        source["business_key"],
        source["load_timestamp"],
        source["time_column"],
        *config["group_by"],
    ]:
        if key not in source["columns"]:
            raise ValueError(f"Source column not declared: {key}")
    if source["columns"][source["load_timestamp"]] not in {"TIMESTAMP", "TIMESTAMPTZ"}:
        raise ValueError("load_timestamp requires a timestamp type")
    if source["columns"][source["time_column"]] not in {
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "DATE",
    }:
        raise ValueError("time_column requires a time type")
    if len(config["group_by"]) != len(set(config["group_by"])):
        raise ValueError("Duplicate group_by columns")
    name(metric["name"])
    if metric["name"] != metric["name"].lower():
        raise ValueError("Metric name must be lowercase")
    if metric["aggregation"] not in {"sum", "average", "min", "max"}:
        raise ValueError("Aggregation must be sum, average, min or max")
    scalar_expression(metric["calculation"], source["columns"])
    database = (path.parent / source["database"]).resolve()
    if not database.is_file():
        raise ValueError(f"Source database does not exist: {database}")
    # This path is also rendered by upstream DuckDB attach configuration.
    if "'" in str(database):
        raise ValueError(
            "Source database paths containing single quotes are unsupported"
        )
    name(database.stem)
    target = config.get("target", {})
    if not isinstance(target, dict) or set(target) - {
        "database",
        "schema",
        "table",
        "grouped_table",
    }:
        raise ValueError("Invalid target configuration fields")
    for field in ["schema", "table", "grouped_table"]:
        if field in target:
            name(target[field])
    if "database" in target:
        if not isinstance(target["database"], str) or not target["database"].strip():
            raise ValueError("target.database must be a non-empty file path")
        destination = (path.parent / target["database"]).resolve()
        if "'" in str(destination) or destination.is_dir():
            raise ValueError("Unsupported target database path")
        name(destination.stem)
        if destination == database or destination.stem.lower() == database.stem.lower():
            raise ValueError(
                "Target and source must use different files and catalog names"
            )
        target["database"] = str(destination)
    return config, database


def current_query(config: dict) -> str:
    source = config["source"]
    relation = ".".join(quoted(n) for n in source["table"].split("."))
    columns = ", ".join(
        f"CAST({quoted(c)} AS {t}) AS {quoted(c)}" for c, t in source["columns"].items()
    )
    key, loaded = quoted(source["business_key"]), quoted(source["load_timestamp"])
    return (
        f"SELECT {columns} FROM {quoted(Path(source['database']).stem)}.{relation} "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY CAST({loaded} AS TIMESTAMPTZ) DESC) = 1"
    )


def validate_source(config: dict, database: Path) -> None:
    source = config["source"]
    relation = ".".join(quoted(n) for n in source["table"].split("."))
    key, loaded = quoted(source["business_key"]), quoted(source["load_timestamp"])
    with duckdb.connect(str(database), read_only=True) as db:
        # Reject ambiguous history instead of choosing an arbitrary same-time row.
        if db.execute(
            f"SELECT COUNT(*) FROM {relation} WHERE {key} IS NULL OR {loaded} IS NULL"
        ).fetchone()[0]:
            raise ValueError("Vault business keys and load timestamps must not be NULL")
        if db.execute(
            f"SELECT 1 FROM {relation} GROUP BY {key}, CAST({loaded} AS TIMESTAMPTZ) HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone():
            raise ValueError(
                "Ambiguous satellite history: duplicate key/load timestamp"
            )
        projections = ", ".join(
            f"CAST({quoted(c)} AS {t})" for c, t in source["columns"].items()
        )
        db.execute(f"SELECT {projections} FROM {relation}").fetchall()


def build(config_path: str | Path, output: str | Path) -> dict:
    """Generate a MetricFlow semantic model and native SQLMesh direct-vault project."""
    config, database = read_config(config_path)
    validate_source(config, database)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new project directory")
    source, metric = config["source"], config["metric"]
    target = {
        "database": str(output / "metric_store.duckdb"),
        "schema": "metrics",
        "table": metric["name"],
        **config.get("target", {}),
    }
    target.setdefault("grouped_table", target["table"] + "_by_group")
    destination = Path(target["database"])
    if destination == database or destination.stem.lower() == database.stem.lower():
        raise ValueError("Target and source must use different files and catalog names")
    relations = [target["schema"] + "." + target["table"]]
    if config["group_by"]:
        relations.append(target["schema"] + "." + target["grouped_table"])
    normalized = [r.lower() for r in relations]
    if len(set(normalized)) != len(normalized) or set(normalized) & {
        "mart.current_orders",
        "mart.time_spine",
    }:
        raise ValueError(
            "Target relations must be distinct and cannot replace helper models"
        )
    if target["schema"].lower().startswith("sqlmesh"):
        raise ValueError("Target schema is reserved for SQLMesh internal state")
    config["target"] = target
    expression = scalar_expression(metric["calculation"], source["columns"])
    documents = [
        {
            "semantic_model": {
                "name": "vault_orders",
                "node_relation": {"schema_name": "mart", "alias": "current_orders"},
                "defaults": {"agg_time_dimension": "event_time"},
                "entities": [
                    {
                        "name": "vault_order",
                        "type": "primary",
                        "expr": source["business_key"],
                    }
                ],
                "measures": [
                    {
                        "name": metric["name"] + "_value",
                        "expr": expression,
                        "agg": metric["aggregation"],
                    }
                ],
                "dimensions": [
                    {
                        "name": "event_time",
                        "expr": source["time_column"],
                        "type": "time",
                        "type_params": {"time_granularity": "day"},
                    }
                ]
                + [
                    {"name": c.lower(), "expr": c, "type": "categorical"}
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
    ]
    documents.append(
        {
            "project_configuration": {
                "time_spines": [
                    {
                        "node_relation": {"schema_name": "mart", "alias": "time_spine"},
                        "primary_column": {
                            "name": "date_day",
                            "time_granularity": "day",
                        },
                    }
                ]
            }
        }
    )
    for directory in ["semantic", "models", "tests"]:
        (output / directory).mkdir(parents=True, exist_ok=True)
    (output / "semantic/metric.yaml").write_text(
        yaml.safe_dump_all(documents, sort_keys=False)
    )
    manifest = parse_directory_of_yaml_files_to_semantic_manifest(
        str(output / "semantic")
    ).semantic_manifest
    SemanticManifestValidator[PydanticSemanticManifest]().checked_validations(manifest)
    queries = {relations[0]: {"metric_names": [metric["name"]]}}
    if config["group_by"]:
        groups = ["vault_order__" + c.lower() for c in config["group_by"]]
        queries[relations[1]] = {
            "metric_names": [metric["name"]],
            "group_by_names": groups,
            "order_by_names": groups,
        }
    with duckdb.connect() as db:
        engine = MetricFlowEngine(SemanticManifestLookup(manifest), DuckDBClient(db))
        models = {}
        for relation, request in queries.items():
            sql = engine.explain(request_from_dict(request)).sql_statement.sql
            (output / "models" / (relation + ".sql")).write_text(
                f"MODEL (name {relation}, kind FULL, dialect duckdb);\n\n{sql};\n"
            )
            models[relation] = {"request": request, "sql": sql}
    query = current_query(config)
    (output / "current.sql").write_text(query + "\n")
    (output / "models/mart.current_orders.sql").write_text(
        "MODEL (name mart.current_orders, kind FULL, dialect duckdb);\n\n"
        + query
        + ";\n"
    )
    event_time = quoted(source["time_column"])
    spine = (
        "SELECT CAST(d AS DATE) AS date_day FROM generate_series("
        f"(SELECT MIN(CAST({event_time} AS DATE)) FROM mart.current_orders), "
        f"(SELECT MAX(CAST({event_time} AS DATE)) FROM mart.current_orders), "
        "INTERVAL '1 day') AS t(d)"
    )
    (output / "time_spine.sql").write_text(spine + "\n")
    (output / "models/mart.time_spine.sql").write_text(
        "MODEL (name mart.time_spine, kind FULL, dialect duckdb);\n\n" + spine + ";\n"
    )
    config["source"]["database"] = str(database)
    (output / "metric.json").write_text(json.dumps(config, indent=2) + "\n")
    (output / "external_models.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": database.stem + "." + source["table"],
                    "columns": source["columns"],
                }
            ]
        )
    )
    (output / "config.py").write_text("""import json
from pathlib import Path
from sqlmesh.core.config import Config, GatewayConfig, DuckDBConnectionConfig, ModelDefaultsConfig
from sqlmesh.core.config.connection import DuckDBAttachOptions
root = Path(__file__).parent
settings = json.loads((root / 'metric.json').read_text())
source, target = settings['source'], settings['target']
destination = Path(target['database'])
destination.parent.mkdir(parents=True, exist_ok=True)
config = Config(
    gateways={'local': GatewayConfig(connection=DuckDBConnectionConfig(
        concurrent_tasks=1,
        catalogs={destination.stem: str(destination), Path(source['database']).stem: DuckDBAttachOptions(type='duckdb', path=source['database'], read_only=True)}))},
    default_gateway='local', model_defaults=ModelDefaultsConfig(dialect='duckdb', start='2026-01-01'),
)
""")
    result = {
        "engine": "sqlmesh",
        "compiler": "metricflow",
        "models": models,
        "calculation": expression,
        "source": source["table"],
        "target": target,
    }
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
