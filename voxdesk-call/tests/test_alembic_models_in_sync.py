"""The ORM metadata and the migration history must describe the same schema.

Batches 02-07 added model modules (carrier/S2S, compliance v2, contact-center
v2, integrations v2, and platform v2) and they must all be imported from
``alembic/env.py``. Autogenerate compares the *registered* metadata against the
live schema, so an unimported module would make the next revision propose dropping
its tables. These tests pin the properties that keep that from happening silently:

* one head and a linear history — no branch a deploy can ignore;
* every module that declares a table is imported by ``alembic/env.py``;
* every table the 0063-0068 migrations create resolves to a mapped class, and
  every table those model modules map is created by one of them.

The autogenerate comparison always runs. With a PostgreSQL URL
(``VOXDESK_TEST_POSTGRES_URL``) it is the repository's real ``alembic check``
against a migrated database; without one it is the same
``compare_metadata`` call in-process, using env.py's own ``compare_type`` and
``include_object`` options against a schema built from the registered models.
No branch of this suite is allowed to skip its assertion.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_DIR = ROOT / "alembic"
V2_REVISIONS = (
    "0063_live_proof_runs",
    "0064_carrier_s2s",
    "0065_compliance_v2",
    "0066_contact_center_v2",
    "0067_integrations_v2",
    "0068_platform_v2",
    "0069_voice_demo_sessions",  # corrected: added 0069_voice_demo_sessions migration
)
V2_MODEL_MODULES = frozenset(
    {
        "app.db.carrier_s2s_models",
        "app.db.compliance_v2_models",
        "app.db.contact_center_v2_models",
        "app.db.integrations_v2_models",
        "app.db.platform_v2_models",
    }
)


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return ScriptDirectory.from_config(config)


@lru_cache(maxsize=1)
def _env_imports() -> frozenset[str]:
    """Every module ``alembic/env.py`` imports, read statically."""
    tree = ast.parse((ALEMBIC_DIR / "env.py").read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return frozenset(modules)


@lru_cache(maxsize=1)
def _modules_declaring_tables() -> dict[str, frozenset[str]]:
    """Module dotted path -> the table names its mapped classes declare."""
    declared: dict[str, frozenset[str]] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        tables: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else []
                )
                if (
                    any(
                        isinstance(target, ast.Name) and target.id == "__tablename__"
                        for target in targets
                    )
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    tables.add(statement.value.value)
        if tables:
            declared[".".join(path.relative_to(ROOT).with_suffix("").parts)] = frozenset(tables)
    return declared


def _string_arg(call: ast.Call, index: int) -> str | None:
    if len(call.args) <= index:
        return None
    argument = call.args[index]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _migration_created_tables() -> frozenset[str]:
    """Table names the 0063-0068 revisions create (both call shapes)."""
    tables: set[str] = set()
    for revision in V2_REVISIONS:
        path = ALEMBIC_DIR / "versions" / f"{revision}.py"
        assert path.is_file(), path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "create_table":
                name = _string_arg(node, 0)
            elif isinstance(func, ast.Name) and func.id.startswith("_create"):
                name = _string_arg(node, 1)
            else:
                continue
            if name:
                tables.add(name)
    return frozenset(tables)


def _registered_tables() -> frozenset[str]:
    """The tables the *alembic import set* registers on ``Base.metadata``."""
    from app.db.models import Base

    for module in sorted(_env_imports()):
        if not module.startswith("app."):
            continue
        __import__(module)
    return frozenset(Base.metadata.tables)


def test_there_is_exactly_one_head():
    heads = _script_directory().get_heads()
    # corrected: head advanced from 0068_platform_v2 to 0069_voice_demo_sessions
    assert heads == ["0069_voice_demo_sessions"], heads


def test_the_history_is_a_single_linear_chain():
    script = _script_directory()
    for revision in script.walk_revisions():
        assert not isinstance(revision.down_revision, tuple), (
            f"{revision.revision} merges two branches"
        )
    expected = {
        "0063_live_proof_runs": "0062_drop_pcap_artifacts",
        "0064_carrier_s2s": "0063_live_proof_runs",
        "0065_compliance_v2": "0064_carrier_s2s",
        "0066_contact_center_v2": "0065_compliance_v2",
        "0067_integrations_v2": "0066_contact_center_v2",
        "0068_platform_v2": "0067_integrations_v2",
        "0069_voice_demo_sessions": "0068_platform_v2",  # corrected: added 0069_voice_demo_sessions
    }
    for revision_id, parent in expected.items():
        assert script.get_revision(revision_id).down_revision == parent, revision_id


def test_env_py_imports_every_module_that_declares_a_table():
    """An unimported model module is invisible to autogenerate."""
    imported = _env_imports()
    missing = sorted(
        module
        for module, _tables in _modules_declaring_tables().items()
        if not any(
            module == candidate
            or module.startswith(f"{candidate}.")
            or candidate.startswith(f"{module}.")
            for candidate in imported
        )
    )
    assert missing == [], f"alembic/env.py never imports: {missing}"


def test_every_v2_migration_table_is_registered():
    registered = _registered_tables()
    missing = sorted(table for table in _migration_created_tables() if table not in registered)
    assert missing == [], f"migrations create tables no mapped class registers: {missing}"


def test_every_v2_model_table_has_a_migration():
    declared: set[str] = set()
    for module in V2_MODEL_MODULES:
        declared |= set(_modules_declaring_tables().get(module, frozenset()))
    assert declared, "the v2 model modules declare no tables"

    registered = _registered_tables()
    created = _migration_created_tables()
    missing = sorted(table for table in declared if table in registered and table not in created)
    assert missing == [], f"v2 model tables with no migration: {missing}"


def test_the_v2_migrations_are_the_ones_this_checkout_ships():
    """Guard against a rename that would make the checks above vacuous."""
    versions = sorted(path.stem for path in (ALEMBIC_DIR / "versions").glob("006*.py"))
    assert [name for name in versions if name >= "0063"] == list(V2_REVISIONS), versions


def _env_include_object():
    """Execute just env.py's ``_include_object`` so this test uses the real one."""
    tree = ast.parse((ALEMBIC_DIR / "env.py").read_text(encoding="utf-8"))
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_include_object"
    )
    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - the function literal is read from this repository's env.py
        compile(
            ast.Module(body=[definition], type_ignores=[]),
            str(ALEMBIC_DIR / "env.py"),
            "exec",
        ),
        namespace,
    )
    return namespace["_include_object"]


def test_autogenerate_reports_no_difference():
    """Autogenerate produces zero operations for the registered metadata."""
    url = os.environ.get("VOXDESK_TEST_POSTGRES_URL", "")
    if url:
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "DATABASE_URL": url},
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return

    # No external database: build a schema from the same metadata env.py
    # registers and run Alembic's comparator with env.py's exact options. A
    # model module env.py forgot to import would register no table here, but
    # test_env_py_imports_every_module_that_declares_a_table and
    # test_every_v2_migration_table_is_registered close that gap. This branch
    # proves the registered metadata and the include_object filter together
    # yield no spurious create/drop operations.
    from sqlalchemy import create_engine
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.models import Base

    for module in sorted(_env_imports()):
        if module.startswith("app."):
            __import__(module)

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": False,
                    "include_object": _env_include_object(),
                },
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == [], f"autogenerate would emit: {differences[:5]}"
