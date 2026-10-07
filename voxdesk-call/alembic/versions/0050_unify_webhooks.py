"""Unify webhook storage; preserve legacy records in sealed quarantine.

Historical delivery claims cannot be recertified as observed HTTP success.
Legacy endpoint configuration is migrated; full old rows are sealed for review.
Legacy attempts are quarantined and cannot automatically resend arbitrary old
payloads. Downgrade recreates empty legacy tables as specified, not old rows.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0050_unify_webhooks"
down_revision = "0049_runtime_schema_alignment"
branch_labels = None
depends_on = None

LEGACY_DDL = ['\nCREATE TABLE webhook_endpoints (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\turl VARCHAR(500) NOT NULL, \n\tdescription VARCHAR(500) NOT NULL, \n\tsecret VARCHAR(128) NOT NULL, \n\tevents JSON NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tretry_policy JSON NOT NULL, \n\tcreated_by UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlast_delivery_at TIMESTAMP WITH TIME ZONE, \n\tfailure_count INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)\n\n', 'CREATE INDEX ix_webhook_endpoints_tenant ON webhook_endpoints (tenant_id)', '\nCREATE TABLE webhook_delivery_attempts (\n\tid UUID NOT NULL, \n\tendpoint_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tevent_type VARCHAR(80) NOT NULL, \n\tpayload JSON NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\thttp_status INTEGER NOT NULL, \n\tresponse_body TEXT NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tnext_retry_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdelivered_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(endpoint_id) REFERENCES webhook_endpoints (id) ON DELETE CASCADE, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)\n\n', 'CREATE INDEX ix_webhook_attempts_tenant ON webhook_delivery_attempts (tenant_id)', 'CREATE INDEX ix_webhook_attempts_endpoint ON webhook_delivery_attempts (endpoint_id)']


def _columns():
    return {
        "webhook_subscriptions": [
            sa.Column("description", sa.String(500), nullable=False, server_default=""),
            sa.Column("delivery_options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("headers_envelope", sa.Text(), nullable=False, server_default=""),
            sa.Column("legacy_envelope", sa.Text(), nullable=False, server_default=""),
        ],
        "webhook_deliveries": [
            sa.Column("http_status", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.Column("response_envelope", sa.Text(), nullable=False, server_default=""),
            sa.Column("legacy_envelope", sa.Text(), nullable=False, server_default=""),
            sa.Column("attempt_limit", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in _columns().items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    metadata = sa.MetaData()
    metadata.reflect(bind=bind, only=["webhook_endpoints", "webhook_delivery_attempts",
                                      "webhook_subscriptions", "webhook_deliveries", "outbox_events"])
    endpoints = metadata.tables["webhook_endpoints"]
    attempts = metadata.tables["webhook_delivery_attempts"]
    subscriptions = metadata.tables["webhook_subscriptions"]
    deliveries = metadata.tables["webhook_deliveries"]
    outbox = metadata.tables["outbox_events"]
    old_endpoints = list(bind.execute(sa.select(endpoints)).mappings())
    old_attempts = list(bind.execute(sa.select(attempts)).mappings())
    if old_endpoints or old_attempts:
        from app.auth.identity.secrets import encrypt_text
        from app.webhooks.repository import validate_options
        from app.webhooks.delivery import validate_headers
        from app.core.ssrf import validate_outbound_url

        def seal(tenant, value):
            return encrypt_text(value, tenant_id=str(tenant), purpose="outbound_webhook")[0]

        for row in old_endpoints:
            old = dict(row)
            prior = bind.execute(sa.select(subscriptions).where(subscriptions.c.id == row["id"])).mappings().first()
            if prior:
                if prior["tenant_id"] != row["tenant_id"] or prior["endpoint"] != row["url"] or not prior["legacy_envelope"]:
                    raise RuntimeError("Webhook migration identity collision; refusing to overwrite")
                continue
            policy = row["retry_policy"] or {}
            headers = policy.get("headers", {})
            settings = {key: policy[key] for key in ("max_attempts", "backoff_seconds", "max_backoff_seconds", "timeout_seconds") if key in policy}
            enabled = bool(row["is_active"] and row["secret"])
            try:
                settings = validate_options(settings)
                validate_headers(headers)
                validate_outbound_url(row["url"], require_https=True)
            except ValueError:
                enabled = False
                settings = validate_options({})
                headers = {}
            bind.execute(subscriptions.insert().values(
                id=row["id"], tenant_id=row["tenant_id"], environment_id=None,
                endpoint=row["url"], description=row["description"], enabled=enabled,
                event_types=row["events"], secret_version=1,
                secret_envelope=seal(row["tenant_id"], row["secret"]) if row["secret"] else "",
                delivery_options=settings, headers_envelope=seal(row["tenant_id"], json.dumps(headers)),
                legacy_envelope=seal(row["tenant_id"], json.dumps(old, default=str, sort_keys=True)),
                created_at=row["created_at"], updated_at=row["updated_at"],
            ))
        for row in old_attempts:
            prior = bind.execute(sa.select(deliveries).where(deliveries.c.id == row["id"])).mappings().first()
            if prior:
                if prior["tenant_id"] != row["tenant_id"] or not prior["legacy_envelope"]:
                    raise RuntimeError("Webhook delivery migration collision")
                continue
            endpoint = bind.execute(sa.select(subscriptions).where(
                subscriptions.c.id == row["endpoint_id"], subscriptions.c.tenant_id == row["tenant_id"],
            )).mappings().first()
            if endpoint is None:
                raise RuntimeError("Legacy delivery references a foreign or missing tenant endpoint")
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, "voxdesk:legacy-webhook:" + str(row["id"]))
            if not bind.execute(sa.select(outbox.c.id).where(outbox.c.id == event_id)).scalar():
                bind.execute(outbox.insert().values(
                    id=event_id, tenant_id=row["tenant_id"], environment_id=None,
                    event_type="webhook.legacy_quarantine", event_version=1,
                    aggregate_type="webhook_subscription", aggregate_id=str(row["endpoint_id"]),
                    payload={"legacy_delivery_id": str(row["id"]), "requires_review": True},
                    status="dead_letter", attempt_count=0, max_attempts=8, replay_count=0,
                    available_at=datetime.now(timezone.utc), delivered_at=None,
                    last_error_category="legacy_unverified", last_error="Historical delivery requires review",
                    idempotency_key="legacy-webhook:" + str(row["id"]), created_at=row["created_at"],
                    updated_at=datetime.now(timezone.utc),
                ))
            bind.execute(deliveries.insert().values(
                id=row["id"], tenant_id=row["tenant_id"], environment_id=None,
                subscription_id=row["endpoint_id"], event_id=str(event_id),
                attempt=row["attempts"], attempt_limit=8, status="dead_letter",
                http_status=row["http_status"], latency_ms=None, response_envelope="",
                legacy_envelope=seal(row["tenant_id"], json.dumps(dict(row), default=str, sort_keys=True)),
                next_attempt_at=None, completed_at=None, last_error_category="legacy_unverified",
                replay_count=0, created_at=row["created_at"],
            ))
    op.drop_table("webhook_delivery_attempts")
    op.drop_table("webhook_endpoints")


def downgrade() -> None:
    for statement in LEGACY_DDL:
        op.execute(sa.text(statement))
    # Preserve canonical rows. Archive these added fields before downgrade if
    # the sealed legacy evidence is needed: reverse schema changes drop fields.
    for table, columns in reversed(list(_columns().items())):
        for column in reversed(columns):
            op.drop_column(table, column.name)
