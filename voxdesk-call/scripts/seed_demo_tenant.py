"""Create a demo dental clinic so you can call your own number in 2 minutes.

    python -m scripts.seed_demo_tenant +15550001111
"""
from __future__ import annotations

import asyncio
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Base, Tenant  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402


async def main(number: str) -> None:
    if settings.app_env.lower() in {"development", "test"}:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Tenant).where(Tenant.twilio_number == number))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"Demo tenant already present: {existing.name} on {existing.twilio_number} ({existing.id})")
            return

        tenant = Tenant(
            name="Bright Smile Dental",
            industry="dental clinic",
            twilio_number=number,
            agent_name="Alex",
            greeting="Thanks for calling Bright Smile Dental, this is Alex. How can I help?",
            timezone="America/New_York",
            business_open=time(9, 0),
            business_close=time(17, 0),
            appointment_minutes=30,
            knowledge_base={
                "services": ["cleaning", "whitening", "root canal", "emergency visit"],
                "cleaning price": "one hundred twenty dollars",
                "insurance": "we accept most PPO plans, not HMO",
                "parking": "free lot behind the building",
                "address": "12 Main Street, Springfield",
            },
            system_prompt_extra=(
                "If the caller mentions severe pain, bleeding or swelling, "
                "treat it as urgent and call escalate_to_human immediately."
            ),
        )
        session.add(tenant)
        await session.commit()
        print(f"Seeded tenant {tenant.name} on {number}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "+15550001111"))
