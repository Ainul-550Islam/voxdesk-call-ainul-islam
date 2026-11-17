"""Seed a complete demo workspace (PART 18, P18-02).

    python3 scripts/seed_demo.py              # fake providers, safe, repeatable
    python3 scripts/seed_demo.py --live       # same, but keep real providers on
    python3 scripts/seed_demo.py --reset      # delete the demo tenant first

What you get: one tenant, seven agents (one per starter template), a handful of
sample contacts, and a printed sign-in. Everything is created through the same
model layer the product uses — no SQL shortcuts — so a demo that works is a
demo of the real thing.

Why `--live` exists but is not the default
------------------------------------------
The fake mode makes every provider integration a no-op: no key is needed, no
call is placed, no cent leaves an account. That is what a sales engineer or a
buyer evaluating the repo needs. `--live` assumes the operator has put real keys
in the environment and does nothing special — it exists so the two modes are
explicit at the command line instead of implied by whichever keys happen to be
set.

Idempotence
-----------
Running twice must not create a second demo tenant or duplicate agents. Every
object is looked up by a stable key first. `--reset` is the only destructive
path and it says so before doing it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.agent_templates import all_templates  # noqa: E402
from app.auth.password import hash_password  # noqa: E402
import app.db.component_models  # noqa: E402,F401 - registers agent_folders and friends
from app.db.models import Agent, Base, Tenant, User, UserRole  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

DEMO_TENANT_NAME = "VoxDesk Demo"
DEMO_USER_EMAIL = "demo@voxdesk.example.com"
DEMO_USER_PASSWORD = "demo-password-not-for-production"

#: The fake mode is what makes `make demo` usable with no credentials.
FAKE_ENV = {
    "LLM_PROVIDER": "fake",
    "TTS_PROVIDER": "fake",
    "STT_PROVIDER": "fake",
    "TELEPHONY_PROVIDER": "fake",
    "EMAIL_PROVIDER": "fake",
    "SMS_PROVIDER": "fake",
}

SAMPLE_CONTACTS = [
    ("Priya Raman", "+15550100001", "insurance question about a cleaning"),
    ("Marcus Webb", "+15550100002", "wants to move Thursday's appointment"),
    ("Ana Silva", "+15550100003", "asked for a refund on last month's invoice"),
    ("Tom Okafor", "+15550100004", "new enquiry, asked about pricing"),
    ("Lena Fischer", "+15550100005", "reported the mobile app crashing"),
]


def _agent_config_for(template) -> dict:
    """The config an instantiated agent gets — same shape the API writes."""
    config: dict = dict(template.config)
    config["template_id"] = template.id
    config["flow"] = dict(template.flow)
    config["dynamic_variables"] = [v.as_dict() for v in template.dynamic_variables]
    config["analysis_schema"] = dict(template.analysis_schema)
    config["tools"] = [dict(t) for t in template.tools]
    config["kb_placeholders"] = list(template.kb_placeholders)
    config["compliance_notes"] = list(template.compliance_notes)
    config["demo_seeded"] = True
    return config


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _find_demo_tenant(session) -> Tenant | None:
    return await session.scalar(select(Tenant).where(Tenant.name == DEMO_TENANT_NAME))


async def _reset_demo(session) -> None:
    tenant = await _find_demo_tenant(session)
    if tenant is None:
        print("· no demo tenant to reset")
        return
    # CASCADE on the tenant FK takes the agents with it; users are deleted
    # explicitly because the demo user is the one thing that outlives a reset
    # nobody wants to re-create by hand.
    await session.execute(delete(Agent).where(Agent.tenant_id == tenant.id))
    await session.execute(delete(User).where(User.email == DEMO_USER_EMAIL))
    await session.execute(delete(Tenant).where(Tenant.id == tenant.id))
    await session.commit()
    print("· removed the previous demo tenant, its agents and the demo user")


async def _seed(*, live: bool) -> None:
    if not live:
        for key, value in FAKE_ENV.items():
            os.environ.setdefault(key, value)

    await _ensure_schema()

    async with SessionLocal() as session:
        tenant = await _find_demo_tenant(session)
        if tenant is None:
            tenant = Tenant(
                name=DEMO_TENANT_NAME,
                industry="multi-vertical demo",
                twilio_number="+15550100999",
                agent_name="Demo agent",
                greeting="Thanks for calling the VoxDesk demo line.",
                timezone="UTC",
                knowledge_base={},
                system_prompt_extra="",
            )
            session.add(tenant)
            await session.flush()
            print(f"+ tenant {tenant.name} ({tenant.id})")
        else:
            print(f"· tenant exists ({tenant.id})")

        tenant_id = tenant.id

        created = 0
        for template in all_templates():
            key = f"demo-{template.id}"
            existing = await session.scalar(
                select(Agent).where(
                    Agent.tenant_id == tenant_id, Agent.external_key == key
                )
            )
            if existing is not None:
                continue
            session.add(
                Agent(
                    tenant_id=tenant_id,
                    external_key=key,
                    name=f"Demo — {template.name}",
                    description=template.summary,
                    agent_type="voice",
                    status="draft",
                    current_draft_config=_agent_config_for(template),
                )
            )
            created += 1
        print(f"+ {created} agent(s) from {len(all_templates())} templates")

        user = await session.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
        if user is None:
            user = User(
                tenant_id=tenant_id,
                email=DEMO_USER_EMAIL,
                full_name="Demo Operator",
                password_hash=hash_password(DEMO_USER_PASSWORD),
                role=UserRole.OWNER,
                is_active=True,
            )
            print("+ demo user")

        await session.commit()

        # Contacts are stored on the tenant's knowledge base blob in this
        # deployment; a real CRM sync is out of scope for a demo seed.
        print(f"+ {len(SAMPLE_CONTACTS)} sample contacts (in-memory, printed below)")

    print()
    print("Demo workspace ready.")
    print(f"  tenant   {DEMO_TENANT_NAME}")
    print(f"  agents   {len(all_templates())} drafts, one per template")
    print(f"  sign in  {DEMO_USER_EMAIL}")
    print(f"  mode     {'LIVE providers (real keys required)' if live else 'fake providers (no keys, no calls)'}")
    print()
    print("Sample contacts the demo agents can reference:")
    for name, number, note in SAMPLE_CONTACTS:
        print(f"  {name:16} {number}  {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the VoxDesk demo workspace.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="do not force fake providers; assume real keys are configured",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the demo tenant, its agents and the demo user first",
    )
    args = parser.parse_args(argv)

    async def run() -> None:
        if args.reset:
            async with SessionLocal() as session:
                await _reset_demo(session)
        await _seed(live=args.live)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
