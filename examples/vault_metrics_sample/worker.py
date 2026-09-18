"""Framework workers run in their respective dependency environments."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import duckdb


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str) + "\n")


def snapshot(db_path, prefix):
    with duckdb.connect(str(db_path), read_only=True) as db:
        db.execute("SET TimeZone='UTC'")
        names = [
            "hub_customer",
            "hub_order",
            "link_customer_order",
            "sat_customer",
            "sat_order",
        ]
        return {
            name: sorted(
                db.execute(f"SELECT * FROM {prefix}{name}").fetchall(), key=str
            )
            for name in names
        }


def export_current(db_path, output):
    output.mkdir(parents=True)
    # Latest known state, not an as-of join: this explicit mart policy is part of the sample.
    queries = {
        "customers": """SELECT CAST(h.CUSTOMER_ID AS BIGINT) customer_id, s.COUNTRY country
            FROM vault.hub_customer h JOIN vault.sat_customer s USING (CUSTOMER_HK)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY h.CUSTOMER_HK ORDER BY s.LOAD_DTS DESC)=1""",
        "orders": """SELECT CAST(o.ORDER_ID AS BIGINT) order_id, CAST(c.CUSTOMER_ID AS BIGINT) customer_id,
            s.EFFECTIVE_AT ordered_at, CAST(s.AMOUNT AS DOUBLE) amount, LOWER(s.STATUS) status
            FROM vault.link_customer_order l JOIN vault.hub_customer c USING (CUSTOMER_HK)
            JOIN vault.hub_order o USING (ORDER_HK) JOIN vault.sat_order s USING (CUSTOMER_ORDER_HK)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY l.CUSTOMER_ORDER_HK ORDER BY s.LOAD_DTS DESC)=1""",
        "time_spine": "SELECT CAST(d AS DATE) date_day FROM generate_series(DATE '2026-01-01', DATE '2026-01-07', INTERVAL 1 DAY) t(d)",
    }
    with duckdb.connect(str(db_path), read_only=True) as db:
        db.execute("SET TimeZone='UTC'")
        for name, sql in queries.items():
            result = db.execute(sql)
            with (output / f"{name}.csv").open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([c[0] for c in result.description])
                writer.writerows(result.fetchall())


def vault(args):
    from sqlmesh_vault.compiler import build
    from sqlmesh_vault.runtime import apply
    from vault.v2.engine import run, test_database

    source, output, config_path = map(Path, args)
    config = json.loads(config_path.read_text())
    reference_source = output / "hamilton-source"
    shutil.copytree(source, reference_source)
    reference_db = output / "hamilton-vault.duckdb"
    native = output / "sqlmesh-vault"
    build(reference_source / "vault.json", native)
    summary = {}
    for index, phase in enumerate(["initial", "incremental"]):
        if index:
            for stage in ["crm", "erp"]:
                lines = (source / f"{stage}_delta.csv").read_text().splitlines()[1:]
                for path in [
                    reference_source / f"{stage}.csv",
                    native / "sources" / f"{stage}.csv",
                ]:
                    with path.open("a") as f:
                        f.write("\n".join(lines) + "\n")
        at = config["execution_times"][index]
        run(reference_source / "vault.json", reference_db, phase, at)
        loaded = apply(native, at)
        left = snapshot(reference_db, "")
        right = snapshot(native / "warehouse.duckdb", "vault.")
        assert left == right, f"{phase}: vault row parity failed"
        assert (
            test_database(reference_source / "vault.json", reference_db)["status"]
            == "pass"
        )
        expected = [3, 4, 4, 3 + index, 4 + 2 * index]
        assert list(loaded["counts"].values()) == expected, loaded
        assert (
            run(reference_source / "vault.json", reference_db, phase, at)["status"]
            == "already_loaded"
        )
        apply(native, at)
        assert snapshot(native / "warehouse.duckdb", "vault.") == right, (
            "Replay changed rows"
        )
        assert snapshot(reference_db, "") == left
        export_current(native / "warehouse.duckdb", output / "exports" / phase)
        summary[phase] = {
            "counts": loaded["counts"],
            "row_parity": True,
            "replay_unchanged": True,
        }
    # An invalid historical rewrite must leave both databases unchanged.
    reference_before = snapshot(reference_db, "")
    native_before = snapshot(native / "warehouse.duckdb", "vault.")
    for path in [reference_source / "crm.csv", native / "sources/crm.csv"]:
        original = path.read_text()
        path.write_text(original.replace("Zurich", "Lausanne"))
        try:
            try:
                if path.parent == reference_source:
                    run(
                        reference_source / "vault.json",
                        reference_db,
                        "invalid",
                        "2026-01-09T00:00:00Z",
                    )
                else:
                    apply(native, "2026-01-09T00:00:00Z")
            except ValueError as exc:
                summary[
                    "rejected_"
                    + ("hamilton" if path.parent == reference_source else "sqlmesh")
                ] = str(exc)
            else:
                raise AssertionError("Historical rewrite was accepted")
        finally:
            path.write_text(original)
    assert snapshot(reference_db, "") == reference_before
    assert snapshot(native / "warehouse.duckdb", "vault.") == native_before
    write_json(output / "vault-results.json", summary)


def hamilton(args):
    from hamilton_flow import HamiltonFlow
    from hamilton_flow.client import DuckDBClient
    from hamilton_flow.requests import request_from_dict

    project = Path(args[0])
    spec = json.loads((project / "project.json").read_text())
    results = {}
    with duckdb.connect(str(project / "hamilton-metrics.duckdb")) as db:
        for name, source in spec["sources"].items():
            columns = ", ".join(
                f"{column} {kind}" for column, kind in source["columns"].items()
            )
            db.execute(f"CREATE TABLE {name} ({columns})")
            db.execute(
                f"INSERT INTO {name} SELECT * FROM read_csv(?, header=true)",
                [str(project / source["path"])],
            )
        flow = HamiltonFlow(project / "semantic", DuckDBClient(db))
        for name, query in spec["queries"].items():
            request = request_from_dict(query)
            explanation = flow.explain(request)
            (project / (name + ".hamilton.sql")).write_text(
                explanation.sql_statement.sql
            )
            result = flow.query(request)
            # Names come from native SQL execution; use cursor metadata for JSON mapping.
            cursor = db.execute(explanation.sql_statement.sql)
            columns = [c[0] for c in cursor.description]
            results[name] = [dict(zip(columns, row)) for row in result.rows]
    write_json(project / "hamilton-results.json", results)


def sqlmesh(args):
    from sqlmesh_flow.compiler import build
    from sqlmesh_flow.runtime import apply

    project = Path(args[0])
    build(project / "project.json", project / "sqlmesh")
    result = apply(project / "sqlmesh", "2026-01-08T00:00:00Z")
    assert result["model_tests"] == 2
    first = result["models"]
    replay = apply(project / "sqlmesh", "2026-01-08T00:00:00Z")["models"]
    assert {k: sorted(v, key=str) for k, v in first.items()} == {
        k: sorted(v, key=str) for k, v in replay.items()
    }
    write_json(project / "sqlmesh-results.json", result)


if __name__ == "__main__":
    {"vault": vault, "hamilton": hamilton, "sqlmesh": sqlmesh}[sys.argv[1]](
        sys.argv[2:]
    )
