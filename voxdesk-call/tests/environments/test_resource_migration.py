"""Backfill binds each tenant's rows to that tenant's production environment."""

from __future__ import annotations

import importlib.util
import uuid

import sqlalchemy as sa


def _load():
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "0019_env_scope_business_res.py"
    )
    spec = importlib.util.spec_from_file_location("m0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(conn) -> None:
    conn.execute(sa.text(
        "CREATE TABLE environments (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), "
        "kind VARCHAR(16), is_default INTEGER)"
    ))
    for table in (
        "calls", "leads", "appointments", "knowledge_documents", "knowledge_chunks",
        "automations", "automation_runs", "notifications", "inbox_thread_states", "usage_events",
    ):
        conn.execute(sa.text(
            f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), "
            "environment_id VARCHAR(36))"
        ))


def test_empty_database_backfill_is_a_no_op(tmp_path):
    module = _load()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        _schema(conn)
        assert module.backfill_environment_scope(conn) == {table: 0 for table in module.SCOPED_TABLES}
        assert module.unbound_counts(conn) == {table: 0 for table in module.SCOPED_TABLES}


def test_many_tenants_bind_only_to_their_own_production(tmp_path):
    module = _load()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'many.db'}")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    env_a, env_b = uuid.uuid4(), uuid.uuid4()
    call_a, lead_b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        _schema(conn)
        conn.execute(sa.text(
            "INSERT INTO environments (id, tenant_id, kind, is_default) VALUES "
            "(:ea, :ta, 'production', 1), (:eb, :tb, 'production', 1), "
            "(:es, :ta, 'staging', 0)"
        ), {"ea": str(env_a), "ta": str(tenant_a), "eb": str(env_b), "tb": str(tenant_b),
            "es": str(uuid.uuid4())})
        conn.execute(sa.text(
            "INSERT INTO calls (id, tenant_id, environment_id) VALUES (:id, :tenant, NULL)"
        ), {"id": str(call_a), "tenant": str(tenant_a)})
        conn.execute(sa.text(
            "INSERT INTO leads (id, tenant_id, environment_id) VALUES (:id, :tenant, NULL)"
        ), {"id": str(lead_b), "tenant": str(tenant_b)})
        first = module.backfill_environment_scope(conn)
        assert first["calls"] == 1
        assert first["leads"] == 1
        assert module.backfill_environment_scope(conn)["calls"] == 0
        bound_call = conn.execute(sa.text("SELECT tenant_id, environment_id FROM calls")).one()
        bound_lead = conn.execute(sa.text("SELECT tenant_id, environment_id FROM leads")).one()
        assert bound_call == (str(tenant_a), str(env_a))
        assert bound_lead == (str(tenant_b), str(env_b))
        assert module.unbound_counts(conn)["calls"] == 0
