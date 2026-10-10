#!/usr/bin/env python3
"""Re-seal stored secrets onto the active encryption key with dry-run, resume, and audit logging.

Supports rotating:
1. ``CrmIntegration`` (``credentials_encrypted``, ``encryption_key_id``)
2. ``CalendarIntegration`` (``credentials_encrypted``, ``encryption_key_id``)
3. ``MFAFactor`` (``secret_encrypted``, ``secret_key_id``)
4. ``SSOConnection`` (``client_secret_encrypted``, ``idp_metadata_encrypted``, ``certificate_bundle_encrypted``)
5. ``AgentTool`` (``auth_binding.secret_ref`` when using ``secret://encrypted/...``)

Features:
- ``--dry-run``: verifies decryption and reports every row that would be re-sealed without writing.
- ``--resume-file <path>``: checkpoints completed ``<table_name>:<row_id>`` identifiers so interrupted runs resume safely.
- Writes an ``AuditLog`` entry (``AuditAction.GOVERNANCE_EVENT``) in the same transaction for each rotated record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.auth.identity.events import emit  # noqa: E402
from app.auth.identity.models import MFAFactor, SSOConnection  # noqa: E402
from app.auth.identity.secrets import (  # noqa: E402
    PURPOSE_OIDC_CLIENT_SECRET,
    PURPOSE_SAML_CERTIFICATE,
    PURPOSE_SAML_METADATA,
    PURPOSE_TOTP,
    decrypt_json,
    decrypt_text,
    encrypt_json,
    encrypt_text,
    key_ring as get_identity_key_ring,
)
from app.db.enterprise_models import AgentTool  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditAction,
    CalendarIntegration,
    CrmIntegration,
)
from app.integrations.crm import crypto  # noqa: E402
from app.security.secret_store import EncryptedSecretStore  # noqa: E402


def _load_resume_set(resume_path: Path | None) -> set[str]:
    if resume_path is None or not resume_path.exists():
        return set()
    try:
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        return set(data.get("completed", []))
    except Exception:
        return set()


def _save_resume_set(resume_path: Path | None, completed: set[str]) -> None:
    if resume_path is None:
        return
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_text(
        json.dumps({"completed": sorted(completed)}, indent=2) + "\n",
        encoding="utf-8",
    )


async def rotate_all_secrets(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    resume_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect and re-seal all encrypted secret rows onto the active key in the key ring."""
    crm_ring = crypto.key_ring_from_settings()
    id_ring = get_identity_key_ring()
    active_crm_key_id = crm_ring.active_id if crm_ring is not None else id_ring.active_id
    active_id_key_id = id_ring.active_id
    if crm_ring is None:
        crm_ring = id_ring

    completed = _load_resume_set(resume_path)
    store = EncryptedSecretStore()

    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "active_crm_key_id": active_crm_key_id,
        "active_identity_key_id": active_id_key_id,
        "inspected": 0,
        "rotated": 0,
        "already_current": 0,
        "skipped_resumed": 0,
        "records": [],
    }

    # 1. CrmIntegration rows
    crm_rows = list((await session.execute(select(CrmIntegration))).scalars().all())
    for row in crm_rows:
        if not row.credentials_encrypted:
            continue
        marker = f"crm_integrations:{row.id}"
        if marker in completed:
            stats["skipped_resumed"] += 1
            continue
        stats["inspected"] += 1
        provider_val = (
            row.provider.value if hasattr(row.provider, "value") else str(row.provider)
        )
        decrypted = crypto.decrypt_credentials(
            row.credentials_encrypted,
            tenant_id=str(row.tenant_id),
            provider=provider_val,
            key_ring=crm_ring,
        )
        if row.credentials_key_id == active_crm_key_id:
            stats["already_current"] += 1
            continue
        if not dry_run:
            new_env, new_kid = crypto.encrypt_credentials(
                decrypted,
                tenant_id=str(row.tenant_id),
                provider=provider_val,
                key_ring=crm_ring,
            )
            old_kid = row.credentials_key_id
            row.credentials_encrypted = new_env
            row.credentials_key_id = new_kid
            await emit(
                session,
                AuditAction.GOVERNANCE_EVENT,
                tenant_id=row.tenant_id,
                detail={
                    "event": "security.secret_rotated",
                    "target_type": "crm_integration",
                    "target_id": str(row.id),
                    "old_key_id": old_kid,
                    "new_key_id": new_kid,
                },
                commit=False,
            )
            completed.add(marker)
        stats["rotated"] += 1
        stats["records"].append(marker)

    # 2. CalendarIntegration rows
    cal_rows = list(
        (await session.execute(select(CalendarIntegration))).scalars().all()
    )
    for row in cal_rows:
        if not row.credentials_encrypted:
            continue
        marker = f"calendar_integrations:{row.id}"
        if marker in completed:
            stats["skipped_resumed"] += 1
            continue
        stats["inspected"] += 1
        provider_val = (
            row.provider.value if hasattr(row.provider, "value") else str(row.provider)
        )
        decrypted = crypto.decrypt_credentials(
            row.credentials_encrypted,
            tenant_id=str(row.tenant_id),
            provider=f"calendar:{provider_val}",
            key_ring=crm_ring,
        )
        if row.credentials_key_id == active_crm_key_id:
            stats["already_current"] += 1
            continue
        if not dry_run:
            new_env, new_kid = crypto.encrypt_credentials(
                decrypted,
                tenant_id=str(row.tenant_id),
                provider=f"calendar:{provider_val}",
                key_ring=crm_ring,
            )
            old_kid = row.credentials_key_id
            row.credentials_encrypted = new_env
            row.credentials_key_id = new_kid
            await emit(
                session,
                AuditAction.GOVERNANCE_EVENT,
                tenant_id=row.tenant_id,
                detail={
                    "event": "security.secret_rotated",
                    "target_type": "calendar_integration",
                    "target_id": str(row.id),
                    "old_key_id": old_kid,
                    "new_key_id": new_kid,
                },
                commit=False,
            )
            completed.add(marker)
        stats["rotated"] += 1
        stats["records"].append(marker)

    # 3. MFAFactor rows
    mfa_rows = list((await session.execute(select(MFAFactor))).scalars().all())
    for row in mfa_rows:
        if not row.secret_encrypted:
            continue
        marker = f"mfa_factors:{row.id}"
        if marker in completed:
            stats["skipped_resumed"] += 1
            continue
        stats["inspected"] += 1
        plaintext = decrypt_text(
            row.secret_encrypted,
            tenant_id=str(row.tenant_id),
            purpose=PURPOSE_TOTP,
        )
        if row.secret_key_id == active_id_key_id:
            stats["already_current"] += 1
            continue
        if not dry_run:
            new_env, new_kid = encrypt_text(
                plaintext,
                tenant_id=str(row.tenant_id),
                purpose=PURPOSE_TOTP,
            )
            old_kid = row.secret_key_id
            row.secret_encrypted = new_env
            row.secret_key_id = new_kid
            await emit(
                session,
                AuditAction.GOVERNANCE_EVENT,
                tenant_id=row.tenant_id,
                detail={
                    "event": "security.secret_rotated",
                    "target_type": "mfa_factor",
                    "target_id": str(row.id),
                    "old_key_id": old_kid,
                    "new_key_id": new_kid,
                },
                commit=False,
            )
            completed.add(marker)
        stats["rotated"] += 1
        stats["records"].append(marker)

    # 4. SSOConnection rows
    sso_rows = list((await session.execute(select(SSOConnection))).scalars().all())
    for row in sso_rows:
        marker = f"sso_connections:{row.id}"
        if marker in completed:
            stats["skipped_resumed"] += 1
            continue
        row_needed_rotation = False
        row_inspected = False
        if row.client_secret_encrypted:
            row_inspected = True
            secret_txt = decrypt_text(
                row.client_secret_encrypted,
                tenant_id=str(row.tenant_id),
                purpose=PURPOSE_OIDC_CLIENT_SECRET,
            )
            if row.client_secret_key_id != active_id_key_id:
                row_needed_rotation = True
                if not dry_run:
                    new_env, new_kid = encrypt_text(
                        secret_txt,
                        tenant_id=str(row.tenant_id),
                        purpose=PURPOSE_OIDC_CLIENT_SECRET,
                    )
                    row.client_secret_encrypted = new_env
                    row.client_secret_key_id = new_kid
        if row.idp_metadata_encrypted:
            row_inspected = True
            meta_doc = decrypt_json(
                row.idp_metadata_encrypted,
                tenant_id=str(row.tenant_id),
                purpose=PURPOSE_SAML_METADATA,
            )
            if row.idp_metadata_key_id != active_id_key_id:
                row_needed_rotation = True
                if not dry_run:
                    new_env, new_kid = encrypt_json(
                        meta_doc,
                        tenant_id=str(row.tenant_id),
                        purpose=PURPOSE_SAML_METADATA,
                    )
                    row.idp_metadata_encrypted = new_env
                    row.idp_metadata_key_id = new_kid
        if row.certificate_bundle_encrypted:
            row_inspected = True
            cert_doc = decrypt_json(
                row.certificate_bundle_encrypted,
                tenant_id=str(row.tenant_id),
                purpose=PURPOSE_SAML_CERTIFICATE,
            )
            if row.certificate_bundle_key_id != active_id_key_id:
                row_needed_rotation = True
                if not dry_run:
                    new_env, new_kid = encrypt_json(
                        cert_doc,
                        tenant_id=str(row.tenant_id),
                        purpose=PURPOSE_SAML_CERTIFICATE,
                    )
                    row.certificate_bundle_encrypted = new_env
                    row.certificate_bundle_key_id = new_kid
        if row_inspected:
            stats["inspected"] += 1
            if row_needed_rotation:
                if not dry_run:
                    await emit(
                        session,
                        AuditAction.GOVERNANCE_EVENT,
                        tenant_id=row.tenant_id,
                        detail={
                            "event": "security.secret_rotated",
                            "target_type": "sso_connection",
                            "target_id": str(row.id),
                            "new_key_id": active_id_key_id,
                        },
                        commit=False,
                    )
                    completed.add(marker)
                stats["rotated"] += 1
                stats["records"].append(marker)
            else:
                stats["already_current"] += 1

    # 5. AgentTool secret_ref bindings using EncryptedSecretStore
    tool_rows = list((await session.execute(select(AgentTool))).scalars().all())
    for row in tool_rows:
        binding = dict(row.auth_binding or {})
        secret_ref = binding.get("secret_ref")
        if not isinstance(secret_ref, str) or not secret_ref.startswith("secret://encrypted/"):
            continue
        marker = f"agent_tools:{row.id}"
        if marker in completed:
            stats["skipped_resumed"] += 1
            continue
        stats["inspected"] += 1
        purpose = str(binding.get("purpose") or f"tool:{row.name}")
        new_ref, was_rotated = store.rotate_reference(
            str(row.tenant_id), purpose, secret_ref
        )
        if not was_rotated:
            stats["already_current"] += 1
            continue
        if not dry_run:
            binding["secret_ref"] = new_ref
            row.auth_binding = binding
            await emit(
                session,
                AuditAction.GOVERNANCE_EVENT,
                tenant_id=row.tenant_id,
                detail={
                    "event": "security.secret_rotated",
                    "target_type": "agent_tool",
                    "target_id": str(row.id),
                    "new_key_id": active_id_key_id,
                },
                commit=False,
            )
            completed.add(marker)
        stats["rotated"] += 1
        stats["records"].append(marker)

    stats["rotated_count"] = stats["rotated"]
    if not dry_run:
        await session.commit()
        _save_resume_set(resume_path, completed)

    return stats


async def _cli_main(dry_run: bool, resume_file: str | None) -> int:
    from app.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    resume_path = Path(resume_file) if resume_file else None
    async with sessionmaker() as session:
        result = await rotate_all_secrets(
            session, dry_run=dry_run, resume_path=resume_path
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-seal stored secrets with the active key in the key ring."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify decryption and report rotation counts without mutating the database.",
    )
    parser.add_argument(
        "--resume-file",
        default=None,
        help="Optional JSON file path to checkpoint and resume rotated record IDs.",
    )
    args = parser.parse_args()
    return asyncio.run(_cli_main(dry_run=args.dry_run, resume_file=args.resume_file))


if __name__ == "__main__":
    raise SystemExit(main())
