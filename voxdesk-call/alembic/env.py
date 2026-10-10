"""Alembic environment. Reads the URL from app settings, not from alembic.ini."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import settings
from app.db.models import Base
from app.db.migration_compatibility import normalize_legacy_revision_aliases
import app.ai.models  # noqa: F401
import app.anomaly.persistence  # noqa: F401
import app.auth.identity.models  # noqa: F401
import app.compliance.models  # noqa: F401
import app.contact_center.models  # noqa: F401
import app.db.enterprise_models  # noqa: F401
import app.db.retell_models  # noqa: F401
import app.db.telephony_models  # noqa: F401
import app.deployment.models  # noqa: F401
import app.governance  # noqa: F401
import app.governance.models  # noqa: F401
import app.leads.models  # noqa: F401
import app.legal.persistence  # noqa: F401
import app.outbox.models  # noqa: F401
import app.qa.models  # noqa: F401
import app.qa.outcomes  # noqa: F401
import app.review.models  # noqa: F401
import app.roi.service  # noqa: F401
import app.specialized_agents.executor  # noqa: F401
import app.telephony.call_events  # noqa: F401
import app.telephony.consent  # noqa: F401
import app.telephony.number_provisioning  # noqa: F401
import app.telephony.number_trust  # noqa: F401
import app.telephony.providers.factory  # noqa: F401
import app.telephony.qos  # noqa: F401
import app.telephony.recording  # noqa: F401
import app.telephony.recording_policy  # noqa: F401
import app.telephony.transcription  # noqa: F401
import app.translation.persistence  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object_, name, type_, reflected, compare_to):
    if type_ in ("index", "unique_constraint", "foreign_key_constraint"):
        return False
    if type_ == "column" and (reflected or compare_to is not None):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    # Normalize only known historical overlength revision markers before
    # Alembic reads alembic_version. This is metadata-only compatibility; it
    # does not touch application rows or migration schema.
    normalize_legacy_revision_aliases(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=False,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
