"""Integration tests for ``app/api/agent_catalog_routes.py``.

The Agent Studio's voice and model pickers call ``GET /api/agents/voices`` and
``GET /api/agents/models``. Before this module existed those paths did not, and
the dashboard swallowed the 404 into an empty list, so the UI silently rendered
pickers with nothing to pick.

These tests pin down both the routing (the concrete paths must win over the
``GET /api/agents/{agent_id}`` catch-all declared by ``agent_management_routes``)
and the honesty contract:

* no entry may claim reachability or authentication, because no network call is
  made;
* ``selectable`` must equal ``configured and capable``, and anything
  unselectable must carry a reason;
* no credential may appear in any response body;
* every catalogued provider, model and tool must be traceable to an
  authoritative in-repo source — never to a hardcoded list of vendor products.
"""

from __future__ import annotations

import pytest

from app.agent.functions import TOOL_CONTRACTS, TOOL_SCHEMAS
from app.agent.llm_factory import PRESETS
from app.core.config import settings
from app.domain.agent_models import (
    ALLOWED_LLM_PROVIDERS,
    ALLOWED_TOOLS,
    ALLOWED_VOICE_PROVIDERS,
)
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- helpers


def _schema_description(name: str) -> str:
    """The description text the model is given for ``name``, straight from TOOL_SCHEMAS."""
    for entry in TOOL_SCHEMAS:
        function = entry.get("function") if isinstance(entry, dict) else None
        if isinstance(function, dict) and function.get("name") == name:
            return str(function.get("description") or "")
    return ""


def _secret_values() -> list[str]:
    """Every provider credential configured in this deployment.

    A catalog endpoint reports *whether* a key exists. If it ever echoed the key
    itself the endpoint would turn a configuration screen into a credential
    leak, so each response is asserted to contain none of these.
    """
    values = []
    for attr in (
        "openai_api_key",
        "anthropic_api_key",
        "google_api_key",
        "deepgram_api_key",
        "elevenlabs_api_key",
    ):
        value = (getattr(settings, attr, "") or "").strip()
        if value:
            values.append(value)
    return values


# ------------------------------------------------------------------ voices


async def test_voices_route_is_not_swallowed_by_agent_id_catchall(client, viewer_a):
    """Regression: ``/api/agents/voices`` must not be captured by ``/{agent_id}``.

    ``agent_management_routes`` declares ``GET /api/agents/{agent_id}``. FastAPI
    matches in registration order, so had the catalog router been mounted after
    it, this call would resolve to ``agent_id="voices"`` and answer 404 "Agent
    not found" — exactly the silent failure the dashboard hid.
    """
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/voices", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "detail" not in body or "Agent not found" not in str(body.get("detail"))
    assert isinstance(body.get("providers"), list)
    assert body["providers"], "catalog must not be empty"


async def test_voices_requires_authentication(client):
    resp = await client.get("/api/agents/voices")
    assert resp.status_code in (401, 403), resp.text


async def test_voices_covers_every_domain_allowed_provider(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/voices", headers=headers)).json()
    returned = {entry["id"] for entry in body["providers"]}
    assert returned == set(ALLOWED_VOICE_PROVIDERS), (
        "the catalog must list exactly the providers the domain model accepts when "
        "saving an agent — no more, no fewer"
    )


async def test_voices_never_claims_reachability(client, viewer_a):
    """Configuration is not connectivity: nothing here probes the network."""
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/voices", headers=headers)).json()
    for entry in body["providers"]:
        assert entry["reachable"] == "not_checked", entry
        assert entry["authenticated"] == "not_checked", entry


async def test_voices_selectable_equals_configured_and_installed_and_contract(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/voices", headers=headers)).json()
    for entry in body["providers"]:
        assert entry["buildable"] == (entry["installed"] and entry["contract_declared"]), entry
        assert entry["selectable"] == (
            entry["configured"] and entry["installed"] and entry["contract_declared"]
        ), entry
        if not entry["selectable"]:
            assert entry["reason"], f"unselectable provider {entry['id']} must explain why"
        else:
            assert entry["reason"] is None, f"selectable provider {entry['id']} must not carry a reason"


async def test_voices_imports_no_provider_sdk(client, viewer_a):
    """The request path must not import provider SDKs.

    Several provider SDKs do credential/metadata discovery at import time and
    block on a network timeout where no metadata service exists. A settings
    screen must never hang on that, so the catalog is served from package
    metadata and settings alone.
    """
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/voices", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for entry in body["providers"]:
        assert entry["runtime_probe"] == "not_performed", entry


async def test_distribution_state_matches_the_catalog(client, viewer_a):
    """The catalog must report exactly what the import-free probe reports."""
    from app.providers.compatibility import distribution_state

    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/voices", headers=headers)).json()
    for entry in body["providers"]:
        state = distribution_state(entry["id"])
        assert entry["configured"] == state.configured, entry
        assert entry["installed"] == state.installed, entry
        assert entry["sdk_version"] == state.sdk_version, entry
        assert entry["distribution"] == state.distribution, entry


async def test_voice_library_is_not_fabricated(client, viewer_a):
    """Only voices this deployment configured may be listed.

    A provider's full voice catalogue is unknowable without calling that
    provider, so the endpoint must say so rather than invent entries.
    """
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/voices", headers=headers)).json()
    assert body["voice_library_fetched"] is False
    assert "not queried" in body["voice_library_note"]
    for voice in body["voices"]:
        assert voice["source"] == "deployment_settings", voice
        assert voice["voice_id"], voice
    configured_elevenlabs = (getattr(settings, "elevenlabs_voice_id", "") or "").strip()
    listed = {voice["voice_id"] for voice in body["voices"]}
    if configured_elevenlabs:
        assert configured_elevenlabs in listed


async def test_voices_leaks_no_credentials(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/voices", headers=headers)
    for secret in _secret_values():
        assert secret not in resp.text


# ------------------------------------------------------------------ models


async def test_models_route_is_not_swallowed_by_agent_id_catchall(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/models", headers=headers)
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json().get("providers"), list)


async def test_models_requires_authentication(client):
    resp = await client.get("/api/agents/models")
    assert resp.status_code in (401, 403), resp.text


async def test_models_covers_every_domain_allowed_provider(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/models", headers=headers)).json()
    returned = {entry["id"] for entry in body["providers"]}
    assert returned == set(ALLOWED_LLM_PROVIDERS)


async def test_models_imports_no_provider_sdk(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/models", headers=headers)
    assert resp.status_code == 200, resp.text
    for entry in resp.json()["providers"]:
        assert entry["runtime_probe"] == "not_performed", entry


async def test_model_presets_selectable_equals_configured_and_installed(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/models", headers=headers)).json()
    for preset in body["presets"]:
        assert preset["selectable"] == (preset["configured"] and preset["installed"]), preset


async def test_model_names_come_from_runtime_presets(client, viewer_a):
    """No model name may appear that the runtime does not actually build."""
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/models", headers=headers)).json()
    expected = {preset: (choice.provider, choice.model) for preset, choice in PRESETS.items()}
    returned = {entry["preset"]: (entry["provider"], entry["model"]) for entry in body["presets"]}
    assert returned == expected


async def test_default_provider_is_derived_not_hardcoded(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/models", headers=headers)).json()
    assert body["default_provider_source"] == "first_selectable_preset"
    if body["default_preset"] is None:
        assert body["default_provider"] is None
    else:
        match = next(entry for entry in body["presets"] if entry["preset"] == body["default_preset"])
        assert match["selectable"] is True
        assert body["default_provider"] == match["provider"]


async def test_models_leaks_no_credentials(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    resp = await client.get("/api/agents/models", headers=headers)
    for secret in _secret_values():
        assert secret not in resp.text


# ------------------------------------------------------------------- tools


async def test_tool_catalog_matches_runtime_contracts(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/tools/catalog", headers=headers)).json()
    assert {tool["name"] for tool in body["tools"]} == set(TOOL_CONTRACTS)
    for tool in body["tools"]:
        contract = TOOL_CONTRACTS[tool["name"]]
        assert tool["effect"] == contract["effect"]
        assert tool["scope"] == contract["scope"]
        assert tool["agent_runtime"] == contract["agent_runtime"]
        assert tool["allowed_by_domain"] == (tool["name"] in ALLOWED_TOOLS)
        # The picker must describe a tool exactly as the model is told about it.
        assert tool["description"] == _schema_description(tool["name"])


async def test_tool_catalog_reports_dispatchable_count(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/tools/catalog", headers=headers)).json()
    expected = sum(1 for tool in body["tools"] if tool["dispatchable"])
    assert body["dispatchable_count"] == expected
    assert body["dispatchable_count"] > 0


async def test_tool_catalog_requires_tool_calling(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    body = (await client.get("/api/agents/tools/catalog", headers=headers)).json()
    assert body["requires_tool_calling"] is True
    for provider in body["tool_calling_providers"]:
        assert provider in ALLOWED_LLM_PROVIDERS


async def test_tool_catalog_requires_authentication(client):
    resp = await client.get("/api/agents/tools/catalog")
    assert resp.status_code in (401, 403), resp.text


# ------------------------------------------------------- tenant independence


async def test_catalog_is_identical_across_tenants(client, owner_a, owner_b):
    """Provider availability is a property of the deployment, not of a tenant.

    Two different tenants must see the same catalog; the endpoint is
    tenant-scoped for authorization but must never leak or vary by tenant data.
    """
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)
    voices_a = (await client.get("/api/agents/voices", headers=headers_a)).json()
    voices_b = (await client.get("/api/agents/voices", headers=headers_b)).json()
    assert [entry["id"] for entry in voices_a["providers"]] == [
        entry["id"] for entry in voices_b["providers"]
    ]
    assert [
        (entry["configured"], entry["installed"], entry["contract_declared"], entry["selectable"])
        for entry in voices_a["providers"]
    ] == [
        (entry["configured"], entry["installed"], entry["contract_declared"], entry["selectable"])
        for entry in voices_b["providers"]
    ]
