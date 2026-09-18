"""Query a generated calculated-metric project directly against the read-only vault."""

import argparse
import json
from pathlib import Path

import duckdb
from hamilton_flow import HamiltonFlow
from hamilton_flow.client import DuckDBClient
from hamilton_flow.requests import request_from_dict

parser = argparse.ArgumentParser()
parser.add_argument("project", type=Path)
args = parser.parse_args()
config = json.loads((args.project / "metric.json").read_text())
manifest = json.loads((args.project / "manifest.json").read_text())
with duckdb.connect() as db:
    database = config["source"]["database"].replace("'", "''")
    catalog = Path(config["source"]["database"]).stem
    db.execute(f"ATTACH '{database}' AS \"{catalog}\" (READ_ONLY)")
    db.execute("CREATE SCHEMA mart")
    db.execute(
        "CREATE VIEW mart.current_orders AS "
        + (args.project / "current.sql").read_text()
    )
    db.execute(
        "CREATE VIEW mart.time_spine AS "
        + (args.project / "time_spine.sql").read_text()
    )
    flow = HamiltonFlow(args.project / "semantic", DuckDBClient(db))
    values = {
        name: list(flow.query(request_from_dict(spec["request"])).rows)
        for name, spec in manifest["models"].items()
    }
print(json.dumps(values, indent=2))
