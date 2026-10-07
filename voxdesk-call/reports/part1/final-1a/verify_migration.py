"""Exercise 0049 -> 0050 on the explicitly isolated local PostgreSQL database.

Never accepts a user/production database URL. The fixture contains synthetic
legacy secrets; the AES test key is random and is never printed or persisted.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DEST = Path(__file__).resolve().parent
URL = "postgresql+asyncpg://user@127.0.0.1:55432/webhook_test"
ENV = {**os.environ, "DATABASE_URL": URL, "IDENTITY_ENCRYPTION_KEYS":
       "migration-test:" + base64.urlsafe_b64encode(os.urandom(32)).decode(), "CRM_ENCRYPTION_KEYS": ""}


def alembic(name, *args, success=True, env=None):
    run = subprocess.run([str(ROOT / ".venv/bin/alembic"), *args], cwd=ROOT,
                         env=env or ENV, capture_output=True, text=True)
    (DEST / name).write_text(run.stdout + run.stderr)
    assert (run.returncode == 0) == success, run.stdout + run.stderr
    return run


async def main():
    alembic("setup-head.log", "upgrade", "head")
    alembic("downgrade-initial.log", "downgrade", "-1")
    connection = await asyncpg.connect("postgresql://user@127.0.0.1:55432/webhook_test")
    tenant, endpoint, attempt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    organization = uuid.uuid4()
    await connection.execute("INSERT INTO organizations(id,name,slug,status,created_at,updated_at) VALUES($1,$2,$3,'active',now(),now())",
                             organization, "Migration fixture organization", str(organization))
    await connection.execute("INSERT INTO tenants(id,name,twilio_number,organization_id) VALUES($1,$2,$3,$4)",
                             tenant, "Migration fixture", "+1555" + str(uuid.uuid4().int % 10**7).zfill(7), organization)
    await connection.execute("""INSERT INTO webhook_endpoints
        (id,tenant_id,url,description,secret,events,is_active,retry_policy,created_at,updated_at,failure_count)
        VALUES($1,$2,$3,$4,$5,$6,true,$7,now(),now(),0)""",
        endpoint, tenant, "https://8.8.8.8/hooks", "Preserved legacy description",
        "migration-signing-secret", json.dumps(["call.failed"]),
        json.dumps({"max_attempts": 3, "headers": {"X-Client-Key": "migration-header-secret"}}))
    await connection.execute("""INSERT INTO webhook_delivery_attempts
        (id,endpoint_id,tenant_id,event_type,payload,status,http_status,response_body,attempts,created_at,delivered_at)
        VALUES($1,$2,$3,$4,$5,'delivered',200,$6,1,now(),now())""",
        attempt, endpoint, tenant, "call.failed", json.dumps({"secret": "legacy-payload-secret"}),
        "legacy-response-sensitive")
    alembic("migration-missing-key-rejected.log", "upgrade", "head", success=False,
            env={**ENV, "IDENTITY_ENCRYPTION_KEYS": "", "CRM_ENCRYPTION_KEYS": ""})
    assert await connection.fetchval("SELECT count(*) FROM webhook_endpoints WHERE id=$1", endpoint) == 1
    assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='webhook_subscriptions' AND column_name='legacy_envelope')")
    alembic("migration-seeded-upgrade.log", "upgrade", "head")
    row = await connection.fetchrow("SELECT * FROM webhook_subscriptions WHERE id=$1", endpoint)
    delivery = await connection.fetchrow("SELECT * FROM webhook_deliveries WHERE id=$1", attempt)
    assert row["description"] == "Preserved legacy description"
    assert row["secret_envelope"].startswith("v1.")
    assert "migration-header-secret" not in row["headers_envelope"]
    assert "legacy-payload-secret" not in delivery["legacy_envelope"]
    assert delivery["status"] == "dead_letter" and delivery["last_error_category"] == "legacy_unverified"
    assert delivery["completed_at"] is None
    os.environ.update({name: ENV[name] for name in ("IDENTITY_ENCRYPTION_KEYS", "CRM_ENCRYPTION_KEYS")})
    from app.auth.identity.secrets import decrypt_text
    old = json.loads(decrypt_text(delivery["legacy_envelope"], tenant_id=str(tenant), purpose="outbound_webhook"))
    assert old["payload"]["secret"] == "legacy-payload-secret"
    assert old["response_body"] == "legacy-response-sensitive"
    assert old["status"] == "delivered"  # evidence retained, not recertified
    assert await connection.fetchval("SELECT to_regclass('webhook_endpoints')") is None
    assert await connection.fetchval("SELECT to_regclass('webhook_delivery_attempts')") is None
    alembic("migration-idempotent-head.log", "upgrade", "head")
    assert await connection.fetchval("SELECT count(*) FROM webhook_deliveries WHERE id=$1", attempt) == 1
    alembic("downgrade.log", "downgrade", "-1")
    assert await connection.fetchval("SELECT count(*) FROM webhook_endpoints") == 0
    assert await connection.fetchval("SELECT count(*) FROM webhook_delivery_attempts") == 0
    alembic("reupgrade.log", "upgrade", "head")
    heads = alembic("heads.log", "heads")
    assert heads.stdout.count("(head)") == 1 and "0050_unify_webhooks" in heads.stdout
    await connection.close()
    print("PASS: seeded migration, missing-key rollback, sealed legacy preservation, quarantine, idempotent head, downgrade/re-upgrade, one head")


if __name__ == "__main__":
    asyncio.run(main())
