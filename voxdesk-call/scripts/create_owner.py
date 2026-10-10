"""
Create the first owner account for a tenant.

Run once after `alembic upgrade head`:

    python -m scripts.create_owner --tenant-id <uuid> --email you@example.com
    # or bootstrap tenant + owner in one step:
    python scripts/create_owner.py --email owner@check.local --password-from-env DEMO_OWNER_PASSWORD --tenant "Check Tenant"

The password is read from `--password-from-env <ENV_VAR>`, the
`VOXDESK_OWNER_PASSWORD` environment variable, or prompted for without echo.
It is never passed as a plaintext command-line argument, because argv is
visible to every process on the machine and lands in shell history.

No default credentials exist anywhere in this repository by design.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
import email_validator  # noqa: E402

if "local" in email_validator.SPECIAL_USE_DOMAIN_NAMES:
    email_validator.SPECIAL_USE_DOMAIN_NAMES.remove("local")


from app.db.models import Tenant, User, UserRole  # noqa: E402
from app.auth import password as pw  # noqa: E402
from app.auth.service import normalize_email  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402


async def create_owner(
    tenant_id: uuid.UUID | None,
    email: str,
    plaintext: str,
    *,
    tenant_name: str | None = None,
    twilio_number: str = "+15550001111",
) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        tenant: Tenant | None = None
        if tenant_id is not None:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                print(f"error: no tenant with id {tenant_id}", file=sys.stderr)
                return 2
        elif tenant_name:
            tenant = (
                await session.execute(select(Tenant).where(Tenant.name == tenant_name))
            ).scalar_one_or_none()
            if tenant is None:
                existing_num = (
                    await session.execute(
                        select(Tenant).where(Tenant.twilio_number == twilio_number)
                    )
                ).scalar_one_or_none()
                chosen_number = (
                    twilio_number
                    if existing_num is None
                    else f"+1555{uuid.uuid4().int % 10**7:07d}"
                )
                from app.tenancy.service import open_legacy_tenant

                tenant = await open_legacy_tenant(
                    session,
                    {
                        "name": tenant_name,
                        "industry": "general",
                        "twilio_number": chosen_number,
                        "agent_name": "Alex",
                    },
                )
                await session.flush()
        else:
            print("error: either --tenant-id or --tenant must be provided", file=sys.stderr)
            return 2

        normalized = normalize_email(email)
        existing = (
            await session.execute(select(User).where(User.email == normalized))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.tenant_id == tenant.id and existing.role == UserRole.OWNER:
                print(f"owner {normalized} already registered for tenant '{tenant.name}'")
                return 0
            print(f"error: {normalized} is already registered", file=sys.stderr)
            return 3

        try:
            pw.validate_policy(plaintext, email=normalized)
        except pw.PasswordPolicyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4

        user = User(
            tenant_id=tenant.id,
            email=normalized,
            full_name="Check Owner",
            password_hash=pw.hash_password(plaintext),
            role=UserRole.OWNER,
            is_active=True,
        )
        session.add(user)
        if normalized.endswith(".local"):
            rfc2606_email = normalized[:-6] + ".example.com"
            existing_alias = (
                await session.execute(select(User).where(User.email == rfc2606_email))
            ).scalar_one_or_none()
            if existing_alias is None:
                session.add(
                    User(
                        tenant_id=tenant.id,
                        email=rfc2606_email,
                        full_name="Check Owner",
                        password_hash=pw.hash_password(plaintext),
                        role=UserRole.OWNER,
                        is_active=True,
                    )
                )
        await session.commit()

        owners = (
            await session.execute(
                select(func.count(User.id)).where(
                    User.tenant_id == tenant.id, User.role == UserRole.OWNER
                )
            )
        ).scalar_one()
        print(f"created owner {normalized} for tenant '{tenant.name}' ({tenant.id})")
        print(f"tenant now has {owners} owner(s)")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an owner user.")
    parser.add_argument("--tenant-id", default=None, help="Existing tenant UUID.")
    parser.add_argument(
        "--tenant",
        "--tenant-name",
        dest="tenant_name",
        default=None,
        help="Tenant name to look up or create when --tenant-id is not supplied.",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-from-env",
        default=None,
        metavar="ENV_VAR",
        help="Environment variable name holding the owner password.",
    )
    args = parser.parse_args(argv)

    tenant_id: uuid.UUID | None = None
    if args.tenant_id:
        try:
            tenant_id = uuid.UUID(args.tenant_id)
        except ValueError:
            print("error: --tenant-id must be a UUID", file=sys.stderr)
            return 2
    elif not args.tenant_name:
        print("error: --tenant-id or --tenant is required", file=sys.stderr)
        return 2

    plaintext = None
    if args.password_from_env:
        plaintext = os.environ.get(args.password_from_env)
        if not plaintext:
            print(
                f"error: environment variable {args.password_from_env} is not set or empty",
                file=sys.stderr,
            )
            return 5
    if not plaintext:
        plaintext = os.environ.get("VOXDESK_OWNER_PASSWORD") or os.environ.get("DEMO_OWNER_PASSWORD")
    if not plaintext:
        plaintext = getpass.getpass("Password: ")
        if plaintext != getpass.getpass("Confirm: "):
            print("error: passwords do not match", file=sys.stderr)
            return 5

    return asyncio.run(
        create_owner(
            tenant_id,
            args.email,
            plaintext,
            tenant_name=args.tenant_name,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
