from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import duckdb
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
from .warehouse import relation, validate
from .warehouse_client import WarehouseClient


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){0,2}", value):
        raise ValueError(f"Invalid relation/column name: {value!r}")
    return value


def build(spec_path: str | Path, output: str | Path) -> dict:
    """Compile name-based native MetricFlow query options into SQLMesh FULL models."""
    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    spec = json.loads(spec_path.read_text())
    if set(spec) - {"semantic_directory", "sources", "queries", "tests", "warehouse"}:
        raise ValueError("Unknown project fields")
    warehouse = validate(spec["warehouse"]) if "warehouse" in spec else None
    dialect = warehouse["type"] if warehouse else "duckdb"
    semantic_path = spec_path.parent / spec["semantic_directory"]
    manifest = parse_directory_of_yaml_files_to_semantic_manifest(
        str(semantic_path)
    ).semantic_manifest
    SemanticManifestValidator[PydanticSemanticManifest]().checked_validations(manifest)
    for directory in ["models", "seeds", "tests"]:
        (output / directory).mkdir(parents=True, exist_ok=True)
    models = {}
    for name, source in spec["sources"].items():
        identifier(name)
        if warehouse:
            relation(name, dialect)
        seed_name = name + ".csv"
        shutil.copyfile(spec_path.parent / source["path"], output / "seeds" / seed_name)
        columns = []
        for col, dtype in source["columns"].items():
            identifier(col)
            if dtype.upper() not in {
                "INTEGER",
                "BIGINT",
                "DOUBLE",
                "VARCHAR",
                "DATE",
                "TIMESTAMP",
                "BOOLEAN",
            }:
                raise ValueError(f"Unsupported seed type: {dtype}")
            columns.append(
                f"{col} {exp.DataType.build(dtype, dialect='duckdb').sql(dialect=dialect)}"
            )
        (output / "models" / f"{name}.sql").write_text(
            f"MODEL (name {name}, kind SEED(path '../seeds/{seed_name}'), dialect {dialect}, columns ({', '.join(columns)}));\n"
        )
    with duckdb.connect() as connection:
        client = WarehouseClient(warehouse) if warehouse else DuckDBClient(connection)
        engine = MetricFlowEngine(SemanticManifestLookup(manifest), client)
        for name, request in spec["queries"].items():
            identifier(name)
            if name in spec["sources"]:
                raise ValueError("Metric model cannot overwrite a source model")
            if warehouse:
                relation(name, dialect)
            statement = engine.explain(request_from_dict(request)).sql_statement
            if statement.bind_parameter_set.param_items:
                raise ValueError("Persisted models require resolved query parameters")
            query_sql = (
                client.prepare(statement.sql, statement.bind_parameter_set)
                if warehouse
                else statement.sql
            )
            (output / "models" / f"{name}.sql").write_text(
                f"MODEL (name {name}, kind FULL, dialect {dialect});\n\n{query_sql};\n"
            )
            models[name] = {"request": request, "sql": query_sql}
    (output / "config.py").write_text("""from pathlib import Path
from sqlmesh.core.config import Config, GatewayConfig, DuckDBConnectionConfig, ModelDefaultsConfig
config = Config(
    gateways={"local": GatewayConfig(connection=DuckDBConnectionConfig(database=str(Path(__file__).parent / "warehouse.duckdb"), concurrent_tasks=1))},
    default_gateway="local", model_defaults=ModelDefaultsConfig(dialect="duckdb", start="2026-01-01"),
)
""")
    if warehouse:
        (output / "warehouse.json").write_text(json.dumps(warehouse, indent=2))
        (output / "config.py").write_text(
            "from pathlib import Path\nimport json\nfrom sqlmesh_flow.warehouse import sqlmesh_config\nroot=Path(__file__).parent\nconfig=sqlmesh_config(json.loads((root/'warehouse.json').read_text()),root)\n"
        )
    if spec.get("tests"):
        (output / "tests/test_metrics.yaml").write_text(
            json.dumps(spec["tests"], indent=2)
        )
    result = {"engine": "sqlmesh", "compiler": "metricflow", "models": models}
    (output / "manifest.json").write_text(json.dumps(result, indent=2))
    return result
