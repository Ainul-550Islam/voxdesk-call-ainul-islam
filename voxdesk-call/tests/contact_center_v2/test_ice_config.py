import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timezone

import pytest

from app.contact_center_v2.errors import NotConfigured
from app.contact_center_v2.settings import ContactCenterV2Settings
from app.contact_center_v2.softphone.ice_config import ice_servers


def test_turn_rest_credentials_include_all_transports_and_exact_hmac(
    monkeypatch: pytest.MonkeyPatch,
):
    user_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.contact_center_v2.softphone.ice_config.utcnow",
        lambda: datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
    )
    config = ContactCenterV2Settings(
        stun_url="stun:stun.example.test:3478",
        turn_udp_url="turn:turn.example.test:3478?transport=udp",
        turn_tcp_url="turn:turn.example.test:3478?transport=tcp",
        turns_url="turns:turn.example.test:5349?transport=tcp",
        turn_static_secret="unit-test-coturn-secret",
        turn_ttl_seconds=300,
    )
    servers = ice_servers(user_id, config, production=True)
    assert servers[0] == {"urls": ["stun:stun.example.test:3478"]}
    turn = servers[1]
    assert turn["urls"] == [config.turn_udp_url, config.turn_tcp_url, config.turns_url]
    expected_username = f"{int(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()) + 300}:{user_id}"
    assert turn["username"] == expected_username
    assert turn["credential"] == base64.b64encode(
        hmac.new(b"unit-test-coturn-secret", expected_username.encode(), hashlib.sha1).digest()
    ).decode("ascii")


def test_ice_fails_closed_for_missing_relay_secret_or_invalid_scheme():
    user_id = uuid.uuid4()
    with pytest.raises(NotConfigured, match="TURN relay"):
        ice_servers(user_id, ContactCenterV2Settings(stun_url="stun:example.test"), production=True)
    with pytest.raises(NotConfigured, match="shared secret|CC2_TURN_STATIC_SECRET"):
        ice_servers(
            user_id,
            ContactCenterV2Settings(turn_udp_url="turn:turn.example.test", turn_static_secret=""),
            production=False,
        )
    with pytest.raises(NotConfigured, match="STUN URLs"):
        ice_servers(user_id, ContactCenterV2Settings(stun_url="https://not-stun.example"), production=False)
