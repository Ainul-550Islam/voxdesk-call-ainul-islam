"""The three mobile SDKs must speak this repository's API, not an imagined one.

A SDK that calls a path the API does not serve fails at runtime on a user's phone,
which is the most expensive place to find out. These tests extract every
``(method, path)`` pair from the React Native, Kotlin and Swift sources and check it
against the live OpenAPI document, then pin the cross-language agreements (quiet-hours
ceiling, device registration flow, no admin routes in a self-service client).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from app.platform_v2.settings import get_plat2_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "verify_mobile_sdks", REPO_ROOT / "scripts" / "verify_mobile_sdks.py"
)
verify = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(verify)

SDK_SOURCES = {
    "react-native": ("typescript", REPO_ROOT / "sdk/react-native/src"),
    "android": ("kotlin", REPO_ROOT / "sdk/android/voxdesk-sdk/src/main"),
    "ios": ("swift", REPO_ROOT / "sdk/ios/Sources"),
}


@pytest.fixture(scope="module")
def operations() -> dict[str, set[str]]:
    return verify.openapi_operations()


def calls_for(name: str) -> set[tuple[str, str]]:
    language, root = SDK_SOURCES[name]
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".kt", ".swift"}:
            continue
        for verb, raw in verify.extract_calls(path.read_text(), language):
            if raw.startswith("/api/"):
                found.add((verb, verify.normalise_path(raw)))
    return found


@pytest.mark.parametrize("name", sorted(SDK_SOURCES))
def test_every_sdk_call_is_a_route_the_api_serves(name: str, operations: dict[str, set[str]]):
    unknown = []
    for verb, path in sorted(calls_for(name)):
        if path not in operations:
            unknown.append(f"{verb} {path} — no such route")
        elif verb not in operations[path]:
            unknown.append(f"{verb} {path} — route allows {sorted(operations[path])}")
    assert not unknown, unknown


@pytest.mark.parametrize("name", sorted(SDK_SOURCES))
def test_no_sdk_calls_an_admin_surface(name: str):
    """A client on a phone must never hold a branding, domain or WFM-admin route."""
    admin = ("/api/whitelabel/", "/api/wfm/schedules", "/api/wfm/forecasts", "/api/wfm/history")
    offenders = [f"{verb} {path}" for verb, path in calls_for(name) if path.startswith(admin)]
    assert not offenders, offenders


@pytest.mark.parametrize("name", sorted(SDK_SOURCES))
def test_every_sdk_registers_devices_through_the_api(name: str):
    """Push registration goes through /devices + /devices/{param}/token, not the provider."""
    calls = calls_for(name)
    paths = {path for _verb, path in calls}
    assert "/api/mobile/devices" in paths
    assert "/api/mobile/devices/{param}/token" in paths, (
        "every SDK must attach tokens through the token endpoint, so the server can "
        "invalidate a dead handset"
    )


def test_the_quiet_hours_ceiling_agrees_between_client_and_server():
    """The SDK's local ceiling is a courtesy; the server's setting is the truth."""
    server_default = get_plat2_settings().push_quiet_hours_max_span
    source = (REPO_ROOT / "sdk/react-native/src/devices.ts").read_text()
    match = re.search(r"maxSpanHours\s*=\s*(\d+)", source)
    assert match, "the React Native SDK must default its quiet-hours ceiling explicitly"
    assert int(match.group(1)) == server_default, (
        f"SDK allows {match.group(1)}h but the server default is {server_default}h"
    )


def test_path_normalisation_handles_all_three_interpolation_styles():
    assert verify.normalise_path("/api/mobile/devices/${deviceId}/token") == (
        "/api/mobile/devices/{param}/token"
    )
    assert verify.normalise_path("/api/mobile/devices/$deviceId/token?x=1") == (
        "/api/mobile/devices/{param}/token"
    )
    assert verify.normalise_path("/api/mobile/devices/\\(deviceID)/token") == (
        "/api/mobile/devices/{param}/token"
    )
    assert verify.normalise_path("/api/mobile/devices/{device_id}/token") == (
        "/api/mobile/devices/{param}/token"
    )


def test_the_balance_check_catches_a_broken_file_and_survives_an_apostrophe():
    assert verify.balanced("fun f() {\n  return 1\n") != []
    assert verify.balanced("fun f() { return 1 }\n") == []
    # An apostrophe in a comment must not be mistaken for a literal.
    assert verify.balanced("// the customer's app bundle\nfun f() { return 1 }\n") == []


@pytest.mark.parametrize("name", sorted(SDK_SOURCES))
def test_no_shipped_sdk_source_contains_a_placeholder(name: str):
    language, root = SDK_SOURCES[name]
    offenders = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".kt", ".swift"}:
            continue
        text = path.read_text()
        if verify.FORBIDDEN_MARKERS.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, offenders


def test_the_ios_privacy_manifest_declares_what_app_store_review_requires():
    import plistlib

    manifests = list((REPO_ROOT / "sdk/ios").rglob("PrivacyInfo.xcprivacy"))
    assert manifests, "the iOS SDK ships no privacy manifest"
    for path in manifests:
        payload = plistlib.loads(path.read_bytes())
        assert "NSPrivacyTracking" in payload
        assert "NSPrivacyAccessedAPITypes" in payload
        assert "NSPrivacyCollectedDataTypes" in payload
