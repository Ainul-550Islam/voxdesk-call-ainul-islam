"""Live checks against the real Vonage Voice API.

Every test here is marked ``live``. Without a Vonage voice credential the
module skips -- it never fails because a key is missing, and it never
pretends that a mocked response was a real one.

The checks are read-only on purpose: the JWT is signed with the deployment's
private key and presented to Vonage, and the account settings are read back.
No call is placed, so running the live suite never spends money.
"""

from __future__ import annotations

import pytest

from app.carriers_v2.settings import get_carrier_v2_settings

pytestmark = pytest.mark.live


def _require_voice_credentials() -> None:
    settings = get_carrier_v2_settings()
    missing: list[str] = []
    if not (settings.vonage_api_key or "").strip():
        missing.append("VONAGE_API_KEY")
    if not (settings.vonage_api_secret or "").strip():
        missing.append("VONAGE_API_SECRET")
    if not (settings.vonage_application_id or "").strip():
        missing.append("VONAGE_APPLICATION_ID")
    if not settings.vonage_private_key_available:
        missing.append("VONAGE_PRIVATE_KEY_PATH")
    if missing:
        pytest.skip(
            "the live Vonage voice credential is missing: " + ", ".join(missing)
        )


async def test_the_application_jwt_is_accepted_by_vonage() -> None:
    """The deployment's private key must produce a JWT Vonage accepts."""
    _require_voice_credentials()

    import httpx

    from app.telephony.providers.vonage import VonageAdapter

    settings = get_carrier_v2_settings()
    base = (settings.vonage_api_base or "https://api.nexmo.com").rstrip("/")
    adapter = VonageAdapter()
    token = adapter.application_jwt()
    assert token.count(".") == 2, "the application JWT is not a JWT"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{base}/account/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert isinstance(body, dict)
    assert body, "Vonage returned an empty account settings document"


async def test_the_media_capability_reports_ready_with_the_full_credential_set() -> None:
    """With every credential present, the media path claims to work."""
    _require_voice_credentials()

    from app.telephony.providers.vonage import vonage_media_capabilities

    row = vonage_media_capabilities()["vonage"]
    if not row["supported"]:
        # The signature secret is the last credential of the media path and
        # is not part of the Voice API JWT check above.
        pytest.skip("the Vonage media credential set is incomplete: " + row["reason"])
    assert row["serializer"] == "VonageFrameSerializer"
    assert row["encodings"] == ["audio/l16;rate=16000"]
    assert row["sample_rates"] == [16000]


async def test_the_voice_adapter_reports_a_healthy_account() -> None:
    """The health probe of the adapter must succeed against the real API."""
    _require_voice_credentials()

    from app.telephony.providers.vonage import VonageAdapter

    result = VonageAdapter().health()
    assert result.ok is True, result.reason
    assert result.provider == "vonage"
