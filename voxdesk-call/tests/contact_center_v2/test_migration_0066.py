import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.db.contact_center_v2_models import MODELS, DialerPolicy


def load_migration():
    path = Path(__file__).parents[2] / "alembic" / "versions" / "0066_contact_center_v2.py"
    spec = importlib.util.spec_from_file_location("batch04_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_identity_and_full_sqlite_upgrade_downgrade():
    migration = load_migration()
    assert migration.revision == "0066_contact_center_v2"
    assert migration.down_revision == "0065_compliance_v2"
    assert migration.branch_labels is None
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            created = {
                row[0] for row in connection.execute(
                    sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cc2_%'")
                )
            }
            assert created == {model.__tablename__ for model in MODELS}
            for model in MODELS:
                table = model.__table__
                actual_columns = {
                    row[1]: {
                        "type": row[2].upper(), "nullable": not bool(row[3]), "primary_key": bool(row[5]),
                    }
                    for row in connection.execute(sa.text(f"PRAGMA table_info({table.name})"))
                }
                expected_columns = {
                    column.name: {
                        "type": column.type.compile(engine.dialect).upper(),
                        "nullable": column.nullable,
                        "primary_key": column.primary_key,
                    }
                    for column in table.columns
                }
                assert actual_columns == expected_columns, table.name
                actual_indexes = {
                    row[1] for row in connection.execute(sa.text(f"PRAGMA index_list({table.name})"))
                    if row[1].startswith("ix_")
                }
                expected_indexes = {
                    index.name for index in table.indexes if index.name and index.name.startswith("ix_")
                }
                assert actual_indexes == expected_indexes, table.name

            # Exercise the ORM against the Alembic-created schema, not only
            # against the metadata.create_all fixture used by unit tests.
            with Session(connection) as session:
                row = DialerPolicy(
                    tenant_id=uuid.uuid4(), environment_id=uuid.uuid4(), campaign_id=uuid.uuid4(),
                    mode="preview", caller_id_number="+15550001000", pacing_ratio=Decimal("1"),
                    abandonment_cap=Decimal("0.03"), max_inflight=1,
                    calling_windows=[], holiday_dates=[],
                    safe_harbor_message="Test business. Call +15550001000 to opt out.",
                    consent_required=True, jurisdiction="US",
                )
                session.add(row)
                session.flush()
                assert session.get(DialerPolicy, row.id) is not None
                session.rollback()
            migration.downgrade()
            remaining = connection.execute(
                sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE 'cc2_%'")
            ).scalar_one()
            assert remaining == 0
    engine.dispose()
