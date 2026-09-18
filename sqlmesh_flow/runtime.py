from __future__ import annotations

import json
from pathlib import Path

from sqlmesh import Context


def apply(path: str | Path, execution_time: str = "2026-01-06T00:00:00Z") -> dict:
    path = Path(path).resolve()
    if (path / "metric.json").exists():
        from .calculated import read_config, validate_source

        config, database = read_config(path / "metric.json")
        validate_source(config, database)
    context = Context(paths=path)
    try:
        tests = context.test()
        if not tests.wasSuccessful():
            raise ValueError("SQLMesh model tests failed")
        context.plan(
            "prod", auto_apply=True, no_prompts=True, execution_time=execution_time
        )
        result = context.run(
            "prod",
            start="2026-01-01",
            end=execution_time,
            execution_time=execution_time,
            ignore_cron=True,
        )
        if result.is_failure:
            raise RuntimeError("SQLMesh run failed")
        manifest = json.loads((path / "manifest.json").read_text())
        tables = {}
        for name in manifest["models"]:
            frame = context.fetchdf(f"SELECT * FROM {name}")
            # Nullable SQL aggregates must remain JSON null, not pandas NaN/NaT.
            tables[name] = (
                frame.astype(object)
                .where(frame.notna(), None)
                .to_dict(orient="records")
            )
        return {"status": "success", "models": tables, "model_tests": tests.testsRun}
    finally:
        context.close()
