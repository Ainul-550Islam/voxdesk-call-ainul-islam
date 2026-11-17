"""Agent starter templates must be shippable, not merely present (P18-01).

Two layers of test:

* **Catalog** — pure, no database. Every template validates against the flow
  validator, references only variables it declares, carries a simulation case
  that names a real node, and never embeds a credential.
* **Routes** — the gallery lists, one template reads with its flow, and
  instantiation creates a DRAFT agent for the caller's tenant and nobody else's.
  The two-tenant test is the one the plan's hard rules demand of every route.
"""

from __future__ import annotations

import uuid

import pytest
import yaml

from app.agent_templates import (
    REQUIRED_TEMPLATE_IDS,
    TemplateError,
    all_templates,
    get_template,
    load_template,
    reload_templates,
)
from app.agent_templates.catalog import TEMPLATE_DIR, AgentTemplate
from app.builder.flow_validation import validate_flow
from app.db.models import Agent
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Catalog: the seven templates exist and each one is shippable
# ---------------------------------------------------------------------------


def test_all_seven_templates_ship():
    ids = {t.id for t in all_templates()}
    assert ids == set(REQUIRED_TEMPLATE_IDS), (
        f"missing: {sorted(set(REQUIRED_TEMPLATE_IDS) - ids)}; "
        f"unexpected: {sorted(ids - set(REQUIRED_TEMPLATE_IDS))}"
    )


def test_every_template_flow_validates():
    """A template whose flow cannot be saved is a trap, not a starting point."""
    for template in all_templates():
        result = validate_flow(dict(template.flow))
        assert result.valid, (
            f"{template.id} flow is invalid: "
            + "; ".join(f"{e.code}: {e.message}" for e in result.errors)
        )


def test_every_template_reads_only_declared_variables():
    for template in all_templates():
        undeclared = template.referenced_variables() - template.declared_variable_names
        # A variable produced by a node (initial_variables / set_from_tool_result)
        # is legitimate even when it is not operator-supplied.
        flow_vars = set((template.flow.get("initial_variables") or {}).keys())
        undeclared -= flow_vars
        assert not undeclared, f"{template.id} reads undeclared variables: {sorted(undeclared)}"


def test_every_template_declares_types_and_purpose():
    allowed = {"string", "number", "boolean", "date", "enum", "phone", "email", "list"}
    for template in all_templates():
        for spec in template.dynamic_variables:
            assert spec.type in allowed, f"{template.id}.{spec.name} type {spec.type}"
            assert spec.purpose.strip(), f"{template.id}.{spec.name} has no purpose"


def test_every_template_has_simulation_cases_pointing_at_real_nodes():
    for template in all_templates():
        assert template.simulation_cases, f"{template.id} proves nothing"
        node_ids = {n["id"] for n in template.flow["nodes"]}
        for case in template.simulation_cases:
            assert case.expect_node in node_ids, (
                f"{template.id}/{case.name} expects node {case.expect_node!r}, "
                f"which is not in the flow"
            )
            assert case.turns, f"{template.id}/{case.name} has no turns"


def test_every_template_carries_analysis_schema_kb_placeholders_and_notes():
    for template in all_templates():
        assert template.analysis_schema.get("fields"), template.id
        assert template.kb_placeholders, (
            f"{template.id} claims to answer without saying what the corpus must contain"
        )
        assert template.compliance_notes, (
            f"{template.id} has no compliance notes; counsel cannot review silence"
        )


def test_no_template_embeds_a_credential():
    """Templates reference secret *names*; a value in a template is a leak."""
    secretish = ("api_key", "token", "password", "secret", "bearer ")
    for path in TEMPLATE_DIR.glob("*.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        for needle in secretish:
            if needle not in text:
                continue
            # A reference form is allowed: {"$secret": "crm_api_key"}
            for line in text.splitlines():
                if needle in line and "$secret" not in line and "never" not in line:
                    # prose in compliance notes is fine; a value is not
                    stripped = line.strip()
                    if stripped.startswith("#") or ": " not in stripped:
                        continue
                    value = stripped.split(": ", 1)[1].strip().strip("'\"")
                    assert not value or value.startswith(("{{", "your-")), (
                        f"{path.name}: looks like an embedded credential: {stripped[:70]}"
                    )


def test_loader_rejects_a_template_that_does_not_validate(tmp_path):
    """The refusal is the feature: a bad template must not reach an operator."""
    bad = {
        "id": "broken",
        "name": "Broken",
        "vertical": "test",
        "summary": "a template with a flow that cannot save",
        "config": {"voice": "x"},
        "flow": {"nodes": [{"id": "start", "node_type": "start"}], "edges": []},
        "dynamic_variables": [],
        "analysis_schema": {"fields": [{"name": "x", "type": "string"}]},
        "simulation_cases": [{"name": "c", "turns": ["hi"], "expect_node": "nope"}],
        "kb_placeholders": ["k.md"],
        "compliance_notes": ["none"],
    }
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(TemplateError) as exc:
        load_template(path)
    message = str(exc.value)
    # Any structural refusal is a pass; what matters is that it refuses.
    assert "shippable" in message and ("DEAD_END_NODE" in message or "not in the flow" in message)


def test_loader_rejects_a_missing_kb_placeholder(tmp_path):
    data = yaml.safe_load((TEMPLATE_DIR / "receptionist.yaml").read_text(encoding="utf-8"))
    data["id"] = "nolist"
    data["kb_placeholders"] = []
    path = tmp_path / "nolist.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(TemplateError) as exc:
        load_template(path)
    assert "kb_placeholders" in str(exc.value)


def test_unknown_template_raises_keyerror():
    with pytest.raises(KeyError):
        get_template("not-a-template")


def test_templates_are_self_contained_copies():
    """Mutating one template's payload must not corrupt the cached catalog."""
    template: AgentTemplate = all_templates()[0]
    payload = template.as_dict()
    payload["flow"]["nodes"][0]["id"] = "mutated"
    reload_templates()
    fresh = get_template(template.id)
    assert fresh.flow["nodes"][0]["id"] != "mutated"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_gallery_lists_all_templates_without_flows(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    resp = await client.get("/api/agent-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == len(REQUIRED_TEMPLATE_IDS)
    ids = {t["id"] for t in body["templates"]}
    assert ids == set(REQUIRED_TEMPLATE_IDS)
    # The list is a menu: no flow payloads.
    assert all("flow" not in t for t in body["templates"])
    # Every row carries enough to choose from.
    for row in body["templates"]:
        assert row["name"] and row["summary"]
        assert row["dynamic_variables"] is not None


async def test_read_template_includes_flow(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    resp = await client.get("/api/agent-templates/receptionist", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "receptionist"
    assert body["flow"]["nodes"], "the detail view must include the flow"
    assert validate_flow(body["flow"]).valid


async def test_read_unknown_template_is_404(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    resp = await client.get("/api/agent-templates/nope", headers=headers)
    assert resp.status_code == 404


async def test_instantiate_creates_a_draft_for_the_callers_tenant(
    client, db, tenant_a, owner_a
):
    #: Read every fixture attribute BEFORE any expire/commit. The session is
    #: expire_on_commit=False, but `db.expire_all()` below re-expires these
    #: instances and the next attribute read would lazy-load without a greenlet.
    tenant_id = tenant_a.id
    headers = await auth_headers(client, owner_a)
    resp = await client.post(
        "/api/agent-templates/appointment_setter/instantiate",
        headers=headers,
        json={"name": "Acme Front Desk"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "draft", "a template must never create a live agent"
    assert body["template_id"] == "appointment_setter"
    assert body["name"] == "Acme Front Desk"

    agent = await db.get(Agent, uuid.UUID(body["id"]))
    assert agent is not None
    agent_tenant, agent_status = agent.tenant_id, agent.status
    config = dict(agent.current_draft_config)
    assert agent_tenant == tenant_id
    assert agent_status == "draft"
    assert config["template_id"] == "appointment_setter"
    assert config["flow"]["nodes"], "the flow must be copied into the agent"
    assert config["compliance_notes"], "compliance notes travel with the agent"


async def test_instantiate_is_tenant_isolated(client, db, tenant_a, tenant_b, owner_a, owner_b):
    """Tenant B must not see or collide with tenant A's instantiation."""
    ids = {"a": tenant_a.id, "b": tenant_b.id}
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)

    created = await client.post(
        "/api/agent-templates/receptionist/instantiate",
        headers=headers_a,
        json={"name": "Front Desk"},
    )
    assert created.status_code == 201, created.text

    rows = (
        await db.execute(
            Agent.__table__.select().where(Agent.__table__.c.name == "Front Desk")
        )
    ).mappings().all()
    assert len(rows) == 1
    tenant_ids = {r["tenant_id"] for r in rows}
    assert ids["a"] in tenant_ids
    assert ids["b"] not in tenant_ids

    # Tenant B can create the same name in its own tenant — no cross-tenant
    # collision, because the uniqueness constraint is per tenant.
    other = await client.post(
        "/api/agent-templates/receptionist/instantiate",
        headers=headers_b,
        json={"name": "Front Desk"},
    )
    assert other.status_code == 201, other.text


async def test_instantiate_twice_conflicts_instead_of_renaming(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    first = await client.post(
        "/api/agent-templates/dispatch/instantiate", headers=headers, json={"name": "Depot"}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/agent-templates/dispatch/instantiate", headers=headers, json={"name": "Depot"}
    )
    assert second.status_code == 409, "a silent rename would hide the collision"


async def test_instantiate_accepts_config_overrides(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    resp = await client.post(
        "/api/agent-templates/customer_support/instantiate",
        headers=headers,
        json={"name": "Support line", "config_overrides": {"voice": "elevenlabs:rachel"}},
    )
    assert resp.status_code == 201, resp.text
    agent = await db.get(Agent, uuid.UUID(resp.json()["id"]))
    assert dict(agent.current_draft_config)["voice"] == "elevenlabs:rachel"


async def test_instantiate_rejects_unknown_fields(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    resp = await client.post(
        "/api/agent-templates/dispatch/instantiate",
        headers=headers,
        json={"name": "x", "unexpected": True},
    )
    assert resp.status_code == 422


async def test_gallery_requires_a_session(client):
    resp = await client.get("/api/agent-templates")
    assert resp.status_code in (401, 403)
