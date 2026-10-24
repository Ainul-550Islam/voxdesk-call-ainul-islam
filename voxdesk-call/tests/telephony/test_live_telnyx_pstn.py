"""Live Telnyx verification: real API key, real numbers, real media stream.

Every test here is marked ``live`` and skips unless the deployment holds real
Telnyx credentials, so CI stays green without them and nothing is ever faked when
they are present. When the credentials *are* present these tests spend real money
and touch the real carrier API; the assertions are about what Telnyx actually
answered, never about a stub.

Run with::

    TELNYX_API_KEY=... TELNYX_PUBLIC_KEY=... TELNYX_CONNECTION_ID=... \\
    TELNYX_TEST_NUMBER=+1555... TELNYX_CALLER_ID=+1555... \\
    pytest tests/telephony/test_telnyx_live_media.py -m live -v
"""

from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.telephony.provider_errors import TelephonyError
from app.telephony.providers.telnyx import TelnyxAdapter

pytestmark = [pytest.mark.live, pytest.mark.slow]


def _credential(name: str) -> str:
    return (os.getenv(name) or getattr(settings, name.lower(), "") or "").strip()


def _require(*names: str) -> None:
    missing = [name for name in names if not _credential(name)]
    if missing:
        pytest.skip("live Telnyx verification requires " + ", ".join(missing))


def test_live_api_key_is_accepted_by_telnyx():
    """A real ``GET /available_phone_numbers`` proves the key and the base URL."""
    _require("TELNYX_API_KEY")
    adapter = TelnyxAdapter()

    import asyncio

    numbers = asyncio.run(adapter.search_numbers(country="US", limit=1))

    assert isinstance(numbers, list)
    for number in numbers:
        assert number.e164.startswith("+")
        assert number.provider == "telnyx"


def test_live_capability_flips_only_when_the_whole_credential_set_exists():
    """The capability flag is a credential statement, not a vendor claim."""
    adapter = TelnyxAdapter()
    capability = adapter.media_stream_capability()

    if _credential("TELNYX_API_KEY") and _credential("TELNYX_PUBLIC_KEY") and _credential(
        "TELNYX_CONNECTION_ID"
    ):
        assert capability["supports_ai_media_stream"] is True
        assert capability["state"] == "READY"
        assert capability["missing"] == []
    else:
        assert capability["supports_ai_media_stream"] is False
        assert capability["state"] == "NOT_CONFIGURED"
        assert capability["missing"]


def test_live_bidirectional_media_stream_on_a_real_call():
    """Place a real call, attach a real media stream, then hang it up.

    This is the only test in the suite that proves Telnyx accepts
    ``streaming_start`` for this account. It is skipped unless the deployment can
    receive a webhook at a public ``wss://`` URL, because the stream URL is what
    Telnyx actually validates.
    """
    _require("TELNYX_API_KEY", "TELNYX_PUBLIC_KEY", "TELNYX_CONNECTION_ID", "TELNYX_TEST_NUMBER")

    public_base = (settings.public_base_url or "").strip()
    if not public_base.startswith("https://"):
        pytest.skip("streaming_start needs a public https base URL for the wss endpoint")

    caller = (os.getenv("TELNYX_CALLER_ID") or settings.twilio_phone_number or "").strip()
    if not caller:
        pytest.skip("live outbound test requires TELNYX_CALLER_ID or TWILIO_PHONE_NUMBER")

    adapter = TelnyxAdapter()
    stream_url = f"wss://{public_base.split('://', 1)[1].rstrip('/')}/telephony/telnyx/ws"

    import asyncio

    async def _run() -> None:
        call = await adapter.create_outbound(
            to_number=os.environ["TELNYX_TEST_NUMBER"], from_number=caller
        )
        assert call.external_id, "Telnyx did not return a call_control_id"
        try:
            started = await adapter.start_media_stream(call.external_id, stream_url=stream_url)
            assert isinstance(started, dict)
        finally:
            await adapter.hangup_call(call.external_id)

    try:
        asyncio.run(_run())
    except TelephonyError as exc:
        pytest.fail(f"live Telnyx media stream was refused: {exc.code}: {exc.message}")
