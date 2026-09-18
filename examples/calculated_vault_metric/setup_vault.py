"""Run using SQLMeshVault's environment to create the actual sample vault."""

import argparse
from pathlib import Path

from sqlmesh_vault.compiler import build
from sqlmesh_vault.runtime import apply

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if args.output.exists():
    raise ValueError("Choose a new output directory")
build(Path(__file__).parent / "vault.json", args.output)
print(apply(args.output, "2026-01-07T00:00:00Z"))
