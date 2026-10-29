#!/usr/bin/env python3
"""Autogenerate-diff gate: the migrated database must match the ORM metadata.

Runs Alembic's own ``compare_metadata`` (the engine behind
``alembic revision --autogenerate``) between a database that has been brought to
``alembic upgrade head`` and ``app.db.models.Base.metadata``. The model modules
are imported exactly as ``alembic/env.py`` imports them, so a model module that
env.py forgets to register shows up here as missing tables.

Two comparisons are reported:

* **gate** — uses the same ``include_object`` rules as ``alembic/env.py``. The
  gate fails on any remaining operation.
* **strict** — compares indexes, unique constraints, foreign keys and columns as
  well. It is reported for visibility and is not a pass/fail condition, because
  ``env.py`` deliberately filters those kinds out of autogenerate.

Usage::

    DATABASE_URL=postgresql+asyncpg://user:pass@host/db python scripts/closer/autogenerate_empty_check.py

Exit code 0 when the gate comparison is empty, 1 otherwise, 2 when the database
URL is not set or does not point at a migrated database.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PY = REPO_ROOT / "alembic" / "env.py"


def registered_model_modules() -> list[str]:
    """Every ``app.*`` module that ``alembic/env.py`` imports, in file order."""
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            modules.append(node.module)
    return modules


def gate_include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001
    """Same rules as ``alembic/env.py::_include_object``."""
    if type_ in ("index", "unique_constraint", "foreign_key_constraint"):
        return False
    if type_ == "column" and (reflected or compare_to is not None):
        return False
    return True


def _describe(op: object) -> str:
    if isinstance(op, list):
        return "; ".join(_describe(item) for item in op)
    if isinstance(op, tuple):
        return str(op[0]) + " " + " ".join(str(part) for part in op[1:3])
    return str(op)


def _kind(op: object) -> str:
    """Operation name of an Alembic diff entry; grouped modifications are lists."""
    while isinstance(op, list):
        if not op:
            return "none"
        op = op[0]
    if isinstance(op, tuple) and op:
        return str(op[0])
    return type(op).__name__


async def _compare(url: str) -> tuple[list, list]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.models import Base

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:

            def _run(sync_connection):  # noqa: ANN001
                gated_ctx = MigrationContext.configure(
                    sync_connection,
                    opts={"compare_type": False, "include_object": gate_include_object},
                )
                strict_ctx = MigrationContext.configure(
                    sync_connection, opts={"compare_type": False}
                )
                return (
                    compare_metadata(gated_ctx, Base.metadata),
                    compare_metadata(strict_ctx, Base.metadata),
                )

            return await connection.run_sync(_run)
    finally:
        await engine.dispose()


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("FAIL  DATABASE_URL is not set; point it at a database after `alembic upgrade head`")
        return 2
    if not url.startswith("postgresql+asyncpg://"):
        print(f"FAIL  unsupported DATABASE_URL scheme for this check: {url.split(':', 1)[0]}")
        return 2

    for module in registered_model_modules():
        importlib.import_module(module)

    gated, strict = asyncio.run(_compare(url))
    report = {
        "gate_operations": len(gated),
        "gate_operations_detail": [_describe(op) for op in gated],
        "strict_operations": len(strict),
        "strict_operation_kinds": dict(Counter(_kind(op) for op in strict)),
        "strict_operations_detail": [_describe(op) for op in strict],
    }
    print(json.dumps(report, indent=2, default=str))
    if gated:
        print(f"FAIL  autogenerate is not empty: {len(gated)} operation(s)")
        return 1
    print("OK: autogenerate diff against the migrated database is empty (gate rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
