"""Real Chromium smoke against the migrated disposable validation database.

Run with DATABASE_URL targeting voxdesk_test and VALIDATION_BASE_URL pointing
to the locally running API serving the built dashboard. Credentials are random,
kept in memory, never written to evidence. No provider call is requested.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from app.auth.password import hash_password  # noqa: E402
from app.db.models import Tenant, User, UserRole  # noqa: E402


async def main():
    dsn = os.environ["DATABASE_URL"]
    if not dsn.endswith("/voxdesk_test"):
        raise RuntimeError("Refusing to create fixtures outside disposable voxdesk_test")
    base = os.environ.get("VALIDATION_BASE_URL", "http://localhost:8000")
    evidence = ROOT / ".prompt8b"
    identity = uuid.uuid4().hex
    email = f"smoke-{identity}@example.com"
    password = "Smoke-" + secrets.token_urlsafe(24) + "-7!"
    engine = create_async_engine(dsn)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        tenant = Tenant(name="Disposable validation browser workspace", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7))
        db.add(tenant)
        await db.flush()
        db.add(User(tenant_id=tenant.id, email=email, full_name="Validation owner", password_hash=hash_password(password), role=UserRole.OWNER, is_active=True))
        await db.commit()
    await engine.dispose()
    result = {"http": [], "pages": [], "javascript_errors": [], "failed_requests": []}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        page.on("pageerror", lambda error: result["javascript_errors"].append(str(error)))
        page.on("requestfailed", lambda request: result["failed_requests"].append({"url": request.url, "failure": request.failure}))
        for path in ["/health", "/docs", "/openapi.json", "/", "/dashboard", "/login"]:
            response = await context.request.get(base + path)
            result["http"].append({"path": path, "status": response.status, "bytes": len(await response.body())})
            assert response.status == 200, (path, response.status)
        await page.goto(base + "/login", wait_until="domcontentloaded")
        await page.locator('input[type="email"]').fill(email)
        await page.locator('input[type="password"]').fill(password)
        await page.locator('button[type="submit"]').click()
        await page.wait_for_url("**/app/overview", timeout=30000)
        await page.locator("h1,h2").first.wait_for()
        token = await page.evaluate("localStorage.getItem('voxdesk_access_token')")
        assert token, "Real login did not establish a session"
        response = await context.request.post(base + "/api/v1/agents", headers={"Authorization": f"Bearer {token}"}, data={"name": "Browser smoke draft", "agent_type": "voice"})
        assert response.status == 201, (response.status, await response.text())
        agent = await response.json()
        paths = ["/app/overview", "/dashboard", "/dashboard/agents/new",
                 f"/dashboard/agents/{agent['id']}/builder", "/dashboard/playground",
                 "/dashboard/simulations", "/app#/calls", "/dashboard/analytics",
                 "/dashboard/phone-numbers", "/app#/agent", "/app#/billing"]
        for path in paths:
            response = await page.goto(base + path, wait_until="domcontentloaded")
            await page.locator("h1,h2").first.wait_for()
            text = await page.locator("body").inner_text()
            result["pages"].append({"path": path, "final_url": page.url, "status": response.status if response else None,
                                    "text_length": len(text), "headings": await page.locator("h1,h2").all_text_contents()})
            assert len(text) > 80, path
            assert "/login" not in page.url, path
        await page.screenshot(path=str(evidence / "browser-desktop.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.goto(base + "/dashboard/agents", wait_until="domcontentloaded")
        await page.screenshot(path=str(evidence / "browser-mobile.png"), full_page=True)
        await browser.close()
    result["status"] = "PASS" if not result["javascript_errors"] else "FAIL"
    (evidence / "browser-smoke.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert not result["javascript_errors"], result["javascript_errors"]


if __name__ == "__main__":
    asyncio.run(main())
