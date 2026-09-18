"""Run the documented upstream suites with their required working directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SQLMESH_HOME", str(ROOT / ".sqlmesh-user"))
os.environ["SQLMESH__DISABLE_ANONYMIZED_ANALYTICS"] = "true"
os.environ["HAMILTON_TELEMETRY_ENABLED"] = "false"
os.environ["PYTHONPATH"] = "."
(ROOT / "reports").mkdir(exist_ok=True)
statuses = {}
SUITES = [('sqlmesh', ['tests/core/test_audit.py', 'tests/core/test_dialect.py', 'tests/core/test_schema_diff.py', 'tests/utils/test_date.py'])]
for index, (source, args) in enumerate(SUITES):
    label = args[0].replace("/", "_").replace(".py", "") if source == "metricflow" else "upstream-sqlmesh"
    command = [sys.executable, "-m", "pytest", "-p", "no:rerunfailures", "-q", "--tb=short", *args,
               "--junitxml=" + str(ROOT / "reports" / (label + ".xml"))]
    statuses[label] = subprocess.call(command, cwd=ROOT / "vendor" / source)
(ROOT / "reports" / "upstream-exit-codes.json").write_text(json.dumps(statuses, indent=2) + "\n")
sys.exit(int(any(statuses.values())))
