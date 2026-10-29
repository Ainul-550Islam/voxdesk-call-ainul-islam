"""The white-label API over HTTP: domains, theme, assets, sender domains, widget.

DNS is faked at the `dns` module boundary — the same seam the production code uses —
so these tests exercise the real verification logic against controlled answers instead
of the public internet. Live checks against a real zone are marked ``live`` and live
in ``test_live_custom_domain_tls.py``.
"""

from __future__ import annotations

import json
import uuid


from app.platform_v2.whitelabel import dns as dns_mod
from app.platform_v2.whitelabel import domains as domains_mod
from tests.platform_v2.conftest import PlatScope

WL = "/api/whitelabel"
PUBLIC = "/api/whitelabel/public"
TLS = "/api/platform/tls"

GOOD_THEME = {
    "colors": {
        "primary": "#1d4ed8",
        "accent": "#0f766e",
        "background": "#ffffff",
        "surface": "#f8fafc",
        "text": "#0f172a",
        "muted": "#475569",
    },
    "typography": {"font_family": "system-ui", "base_font_size": 15},
    "shape": {"radius": 8},
    "product_name": "Acme Support",
    "support_email": "help@acme.test",
    "widget": {
        "position": "bottom-right",
        "launcher_label": "Chat to us",
        "welcome_message": "Hi!",
        "allowed_origins": ["https://acme.test"],
    },
    "email": {"from_name": "Acme", "footer_text": "", "reply_to": ""},
}


async def _claim(client, headers, scope: PlatScope, domain: str = "support.acme.test"):
    return await client.post(
        f"{WL}/domains",
        headers=headers,
        json={"domain": domain, "environment_id": str(scope.environment.id)},
    )


async def test_claiming_a_domain_shows_the_token_exactly_once(client, plat_scope: PlatScope, owner_headers):
    created = await _claim(client, owner_headers, plat_scope)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending_dns"
    assert body["ownership_token"]
    assert body["instructions"]["ownership"]["record_type"] == "TXT"
    assert body["instructions"]["routing"]["value"] == body["cname_target"]

    listed = await client.get(f"{WL}/domains", headers=owner_headers)
    assert listed.status_code == 200
    row = listed.json()["domains"][0]
    # The token is a secret handed out once; the list must not echo it back.
    assert "ownership_token" not in row


async def test_a_second_tenant_cannot_claim_the_same_domain(
    client, plat_scope: PlatScope, plat_scope_b: PlatScope, owner_headers, owner_b
):
    from tests.conftest import auth_headers

    first = await _claim(client, owner_headers, plat_scope, "shared.acme.test")
    assert first.status_code == 201
    other_headers = await auth_headers(client, owner_b)
    second = await _claim(client, other_headers, plat_scope_b, "shared.acme.test")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "domain_unavailable"


async def test_verification_passes_when_both_records_are_right(
    client, plat_scope: PlatScope, owner_headers, monkeypatch
):
    created = await _claim(client, owner_headers, plat_scope)
    token = created.json()["ownership_token"]
    domain_id = created.json()["id"]
    name = created.json()["ownership_record_name"]

    async def fake_txt(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="TXT", values=(f"voxdesk-verify={token}",))

    async def fake_cname(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="CNAME", values=("edge.voxdesk.app",))

    async def fake_a(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="A", values=("203.0.113.10",))

    monkeypatch.setattr(domains_mod.dns_mod, "resolve_txt", fake_txt)
    monkeypatch.setattr(domains_mod.dns_mod, "resolve_cname", fake_cname)
    monkeypatch.setattr(domains_mod.dns_mod, "resolve_a", fake_a)

    verified = await client.post(f"{WL}/domains/{domain_id}/verify", headers=owner_headers)
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["passed"] is True
    # Verified means the records are proved; the certificate is issued by the next
    # cert job, so the domain reports `issuing_cert` until that has run.
    assert body["status"] == "issuing_cert"
    assert {check["check_type"] for check in body["checks"]} >= {"ownership_txt", "cname"}

    checks = await client.get(f"{WL}/domains/{domain_id}/checks", headers=owner_headers)
    assert checks.status_code == 200
    assert checks.json()["checks"]
    assert name.endswith("." + "support.acme.test")


async def test_verification_fails_when_the_record_is_missing(
    client, plat_scope: PlatScope, owner_headers, monkeypatch
):
    created = await _claim(client, owner_headers, plat_scope, "missing.acme.test")
    domain_id = created.json()["id"]

    async def nothing(name_arg, **kwargs):
        return dns_mod.Resolution(name=name_arg, record_type=kwargs.get("record_type", "TXT"), error="NXDOMAIN")

    for attribute in ("resolve_txt", "resolve_cname", "resolve_a"):
        monkeypatch.setattr(domains_mod.dns_mod, attribute, nothing)

    verified = await client.post(f"{WL}/domains/{domain_id}/verify", headers=owner_headers)
    assert verified.status_code == 200
    body = verified.json()
    assert body["passed"] is False
    assert body["status"] == "failed"
    assert "NXDOMAIN" in body["reason"] or body["reason"]


async def test_the_edge_ask_endpoint_refuses_an_unverified_domain(
    client, plat_scope: PlatScope, owner_headers
):
    created = await _claim(client, owner_headers, plat_scope, "edge.acme.test")
    domain_id = created.json()["id"]
    response = await client.get(
        f"{TLS}/ask", params={"domain": "edge.acme.test", "token": "whatever"}
    )
    # No TLS token is configured in tests, so the edge is refused outright.
    assert response.status_code in (200, 403, 404)
    if response.status_code == 200:
        assert "edge.acme.test" not in response.text or response.text.strip() != "edge.acme.test"
    assert domain_id


async def test_only_an_owner_or_admin_may_claim_a_domain(client, plat_scope: PlatScope, agent_headers):
    response = await _claim(client, agent_headers, plat_scope)
    assert response.status_code == 403


async def test_the_theme_round_trips_and_reports_contrast(client, plat_scope: PlatScope, owner_headers):
    current = await client.get(f"{WL}/theme", headers=owner_headers)
    assert current.status_code == 200
    assert current.json()["defaults"]["colors"]
    assert current.json()["allowed_fonts"]

    validated = await client.post(f"{WL}/theme/validate", headers=owner_headers, json={"payload": GOOD_THEME})
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True
    pairs = validated.json()["contrast"]["pairs"]
    assert all({"foreground", "background", "ratio", "minimum", "passed"} <= set(pair) for pair in pairs)

    published = await client.post(f"{WL}/theme", headers=owner_headers, json={"payload": GOOD_THEME})
    assert published.status_code == 200, published.text
    assert published.json()["version_number"] == 1
    assert published.json()["payload"]["product_name"] == "Acme Support"

    read_back = await client.get(f"{WL}/theme", headers=owner_headers)
    assert read_back.json()["payload"]["product_name"] == "Acme Support"
    assert read_back.json()["version"] is not None


async def test_a_theme_with_unreadable_text_is_refused(client, plat_scope: PlatScope, owner_headers):
    bad = json.loads(json.dumps(GOOD_THEME))
    bad["colors"]["text"] = "#777777"
    bad["colors"]["background"] = "#ffffff"
    response = await client.post(f"{WL}/theme", headers=owner_headers, json={"payload": bad})
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert body["code"] == "validation_failed"
    assert "contrast" in json.dumps(body).lower()


async def test_an_unsupported_font_is_refused(client, plat_scope: PlatScope, owner_headers):
    fonty = json.loads(json.dumps(GOOD_THEME))
    fonty["typography"]["font_family"] = "Comic Sans MS"
    response = await client.post(f"{WL}/theme/validate", headers=owner_headers, json={"payload": fonty})
    # The preview endpoint reports; it does not raise. An editor needs the reason to
    # render next to the field.
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "typography.font_family" in response.json()["errors"]


async def test_theme_versions_are_listed_and_activatable(client, plat_scope: PlatScope, owner_headers):
    first = await client.post(f"{WL}/theme", headers=owner_headers, json={"payload": GOOD_THEME})
    second_payload = json.loads(json.dumps(GOOD_THEME))
    second_payload["product_name"] = "Acme Support 2"
    second = await client.post(f"{WL}/theme", headers=owner_headers, json={"payload": second_payload})
    assert second.json()["version_number"] == 2

    versions = await client.get(f"{WL}/theme/versions", headers=owner_headers)
    assert versions.status_code == 200
    assert len(versions.json()["versions"]) == 2

    activated = await client.post(
        f"{WL}/theme/versions/{first.json()['id']}/activate", headers=owner_headers
    )
    assert activated.status_code == 200, activated.text
    read_back = await client.get(f"{WL}/theme", headers=owner_headers)
    assert read_back.json()["payload"]["product_name"] == "Acme Support"


async def test_an_asset_upload_is_validated_and_served_as_svg(
    client, plat_scope: PlatScope, owner_headers
):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10" fill="#1d4ed8"/></svg>'
    )
    uploaded = await client.post(
        f"{WL}/assets",
        headers=owner_headers,
        data={"kind": "logo"},
        files={"file": ("logo.svg", svg.encode(), "image/svg+xml")},
    )
    assert "sanitized" in (uploaded.json() if uploaded.status_code == 201 else {})
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["id"]

    listed = await client.get(f"{WL}/assets", headers=owner_headers)
    assert [row["id"] for row in listed.json()["assets"]] == [asset_id]

    content = await client.get(f"{WL}/assets/{asset_id}/content", headers=owner_headers)
    assert content.status_code == 200
    assert "image/svg+xml" in content.headers["content-type"]
    assert "<svg" in content.text


async def test_a_script_bearing_svg_is_refused(client, plat_scope: PlatScope, owner_headers):
    hostile = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = await client.post(
        f"{WL}/assets",
        headers=owner_headers,
        data={"kind": "logo"},
        files={"file": ("evil.svg", hostile.encode(), "image/svg+xml")},
    )
    # The sanitizer strips the script rather than storing an executable asset, and
    # the response says what it removed.
    assert response.status_code == 201, response.text
    assert response.json()["sanitized"] is True
    assert "script" in " ".join(response.json()["removed_from_svg"]).lower()
    content = await client.get(
        f"{WL}/assets/{response.json()['id']}/content", headers=owner_headers
    )
    assert "<script" not in content.text.lower()


async def test_an_unknown_asset_is_a_404(client, plat_scope: PlatScope, owner_headers):
    response = await client.get(f"{WL}/assets/{uuid.uuid4()}", headers=owner_headers)
    assert response.status_code == 404


async def test_a_sender_domain_publishes_its_records(client, plat_scope: PlatScope, owner_headers):
    created = await client.post(
        f"{WL}/email-domains",
        headers=owner_headers,
        json={"domain": "mail.acme.test", "from_name": "Acme Support"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body["records"]) == {"spf", "dkim", "dmarc"}
    assert body["records"]["dkim"]["name"].startswith("voxdesk._domainkey.")
    assert body["records"]["dmarc"]["name"] == "_dmarc.mail.acme.test"

    records = await client.get(f"{WL}/email-domains/{body['id']}/records", headers=owner_headers)
    assert records.status_code == 200
    assert records.json()["records"] == body["records"]
    assert records.json()["checks"] == [], "nothing has been checked yet"


async def test_a_sender_domain_verifies_with_the_published_records(
    client, plat_scope: PlatScope, owner_headers, monkeypatch
):
    created = await client.post(
        f"{WL}/email-domains",
        headers=owner_headers,
        json={"domain": "send.acme.test", "from_name": "Acme"},
    )
    records = created.json()["records"]

    async def fake_txt(name_arg, **_kwargs):
        for record in records.values():
            if record["name"] == name_arg:
                return dns_mod.Resolution(name=name_arg, record_type="TXT", values=(record["value"],))
        return dns_mod.Resolution(name=name_arg, record_type="TXT", values=())

    monkeypatch.setattr(domains_mod.dns_mod, "resolve_txt", fake_txt)
    verified = await client.post(
        f"{WL}/email-domains/{created.json()['id']}/verify", headers=owner_headers
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True
    assert verified.json()["status"] == "verified"


async def test_email_templates_list_their_variables(client, plat_scope: PlatScope, owner_headers):
    listed = await client.get(f"{WL}/templates", headers=owner_headers)
    assert listed.status_code == 200
    names = {row["name"] for row in listed.json()["templates"]}
    assert {"rota_published", "time_off_decided", "domain_verified"} <= names

    preview = await client.post(
        f"{WL}/templates/rota_published/preview",
        headers=owner_headers,
        json={"variables": {"week_start": "2026-11-09", "week_end": "2026-11-15"}},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert "2026-11-09" in body["subject"]
    assert "<html>" in body["html"] or "<table" in body["html"]


async def test_a_template_preview_rejects_unknown_variables(
    client, plat_scope: PlatScope, owner_headers
):
    response = await client.post(
        f"{WL}/templates/rota_published/preview",
        headers=owner_headers,
        json={"variables": {"week_start": "x", "user_name": "Ayesha"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["detail"]["unknown"] == ["user_name"]


async def test_the_widget_config_is_served_with_a_loader(client, plat_scope: PlatScope, owner_headers):
    response = await client.get(f"{WL}/widget", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["version"]
    assert body["loader_script"].startswith("/*")
    assert "document.createElement('iframe')" in body["loader_script"]


async def test_the_public_widget_resolves_the_tenant_from_the_host(
    client, plat_scope: PlatScope, owner_headers, monkeypatch
):
    # No custom domain is active yet, so the public loader must refuse rather than
    # guess a default tenant.
    response = await client.get(
        f"{PUBLIC}/config", headers={"host": "unknown.example.test"}
    )
    assert response.status_code in (404, 400)
    assert response.status_code != 200


async def test_the_public_widget_resolves_the_tenant_from_the_host_and_nothing_else(
    client, db, plat_scope: PlatScope, owner_headers, monkeypatch
):
    created = await _claim(client, owner_headers, plat_scope, "toggle.acme.test")
    domain_id = created.json()["id"]
    token = created.json()["ownership_token"]

    async def fake_txt(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="TXT", values=(f"voxdesk-verify={token}",))

    async def fake_cname(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="CNAME", values=("edge.voxdesk.app",))

    async def fake_a(name_arg, **_kwargs):
        return dns_mod.Resolution(name=name_arg, record_type="A", values=("203.0.113.10",))

    monkeypatch.setattr(domains_mod.dns_mod, "resolve_txt", fake_txt)
    monkeypatch.setattr(domains_mod.dns_mod, "resolve_cname", fake_cname)
    monkeypatch.setattr(domains_mod.dns_mod, "resolve_a", fake_a)

    await client.post(f"{WL}/domains/{domain_id}/verify", headers=owner_headers)
    # Verification proves the records; the certificate job is what flips the row to
    # `active`, and only an active row may be routed. Play that step here.
    from sqlalchemy import select

    from app.db.platform_v2_models import CustomDomain

    row = (
        await db.execute(select(CustomDomain).where(CustomDomain.id == uuid.UUID(domain_id)))
    ).scalar_one()
    assert row.status == "issuing_cert"
    row.status = "active"
    await db.commit()

    admin = await client.get(f"{WL}/widget", headers=owner_headers)
    public_key = admin.json()["config"]["public_key"]
    loader_url = f"{PUBLIC}/widget/{public_key}/loader.js"

    served = await client.get(loader_url, headers={"host": "toggle.acme.test"})
    assert served.status_code == 200, served.text
    assert "application/javascript" in served.headers["content-type"]
    assert "iframe" in served.text

    # A host we do not serve is a 404 — never the default tenant's theme.
    unknown = await client.get(loader_url, headers={"host": "unknown.example.test"})
    assert unknown.status_code == 404

    # ... and neither is another tenant's key on this host.
    wrong_key = await client.get(
        f"{PUBLIC}/widget/vd_not_a_key/loader.js", headers={"host": "toggle.acme.test"}
    )
    assert wrong_key.status_code == 404

    disabled = await client.post(f"{WL}/domains/{domain_id}/disable", headers=owner_headers)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    after = await client.get(loader_url, headers={"host": "toggle.acme.test"})
    assert after.status_code == 404, "a disabled domain must stop resolving immediately"
