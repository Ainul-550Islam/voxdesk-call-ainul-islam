"""Widget isolation tests: CSP, origin checks, loader escaping, unknown hosts.

The widget is served from a tenant's own domain and framed by a page we do not control,
so these are the properties that stop it becoming a hole in somebody else's site.
"""

from __future__ import annotations

import json

import pytest

from app.platform_v2.errors import NotFound, PermissionDenied, ValidationFailed
from app.platform_v2.whitelabel import branding, widget


def themed(**widget_overrides):
    payload = branding.default_theme()
    payload["product_name"] = "Acme Support"
    payload["widget"].update(widget_overrides)
    normalised, _report = branding.validate_theme(payload)
    return normalised


def test_an_empty_allowlist_means_self_not_everyone():
    theme = themed()
    assert widget.frame_ancestors(theme) == ["'self'"]
    policy = widget.content_security_policy(theme)
    # `frame-ancestors *` (or its absence) would let any site frame a signed-in agent.
    assert "frame-ancestors 'self'" in policy
    assert "*" not in policy.replace("'none'", "")


def test_allowlisted_origins_become_the_frame_ancestors():
    theme = themed(allowed_origins=["https://acme.test", "https://www.acme.test"])
    policy = widget.content_security_policy(theme)
    assert "frame-ancestors https://acme.test https://www.acme.test" in policy


def test_csp_denies_the_dangerous_sources():
    policy = widget.content_security_policy(themed())
    for directive in ("default-src 'none'", "form-action 'none'", "base-uri 'none'"):
        assert directive in policy
    assert "'unsafe-eval'" not in policy
    assert "object-src http" not in policy


def test_a_nonce_replaces_the_host_source_for_scripts():
    with_nonce = widget.content_security_policy(themed(), nonce="abc123")
    without = widget.content_security_policy(themed())
    assert "script-src 'nonce-abc123'" in with_nonce
    assert "script-src 'self'" in without


def test_headers_carry_the_policy_and_do_not_set_a_broken_frame_option():
    headers = widget.headers(themed())
    assert "Content-Security-Policy" in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    # X-Frame-Options: DENY would break the embedding the tenant is paying for.
    assert "X-Frame-Options" not in headers
    assert headers["Cache-Control"] == f"public, max-age={widget.LOADER_MAX_AGE_SECONDS}"


def test_config_contains_no_secret_and_no_tenant_identifier():
    import uuid

    tenant_id = uuid.uuid4()
    config = widget.config_for(tenant_id, themed())
    assert config["public_key"] == widget.public_key_for(tenant_id)
    blob = json.dumps(config)
    assert str(tenant_id) not in blob
    assert "token" not in blob.lower()
    assert "secret" not in blob.lower()


def test_public_key_is_derived_and_stable():
    import uuid

    tenant_id = uuid.uuid4()
    assert widget.public_key_for(tenant_id) == widget.public_key_for(tenant_id)
    assert widget.public_key_for(tenant_id) != widget.public_key_for(uuid.uuid4())
    assert widget.public_key_for(tenant_id).startswith("vd_")


def test_loader_script_escapes_a_product_name_that_would_end_the_script_tag():
    theme = themed()
    theme["product_name"] = "Acme</script><img src=x onerror=alert(1)>"
    script = widget.loader_script(widget.config_for(__import__("uuid").uuid4(), theme))
    assert "</script>" not in script
    assert "<img" not in script
    assert "\\u003c" in script


def test_loader_script_escapes_quotes_and_ampersands():
    theme = themed()
    theme["product_name"] = 'Acme & "Sons"'
    script = widget.loader_script(widget.config_for(__import__("uuid").uuid4(), theme))
    assert "\\u0026" in script
    # The JSON literal must still parse once the HTML escapes are decoded.
    start = script.index("var config = ") + len("var config = ")
    end = script.index(";\n", start)
    decoded = script[start:end].replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    assert json.loads(decoded)["product_name"] == 'Acme & "Sons"'
    # The raw literal is still valid JSON: escaping `<`, `>` and `&` changes nothing
    # about the value, it only stops the sequence ending the surrounding <script>.
    assert json.loads(script[start:end])["product_name"] == 'Acme & "Sons"' 


def test_embed_origin_must_be_present_and_authorised():
    theme = themed(allowed_origins=["https://acme.test"])
    widget.validate_embed_origin("https://acme.test", theme)
    with pytest.raises(PermissionDenied):
        widget.validate_embed_origin("https://evil.test", theme)
    with pytest.raises(PermissionDenied):
        # A missing Origin cannot be proven to be allowed.
        widget.validate_embed_origin(None, theme)


def test_self_allowlist_refuses_a_browser_origin():
    """`'self'` is not an origin a browser sends; only an authorised origin passes."""
    theme = themed()
    with pytest.raises(PermissionDenied):
        widget.validate_embed_origin("https://acme.test", theme)


def test_post_message_needs_both_the_origin_and_the_message_shape():
    allowed = ["https://acme.test"]
    assert widget.validate_message("https://acme.test", allowed, {"type": "voxdesk:ready"}) is True
    assert widget.validate_message("https://evil.test", allowed, {"type": "voxdesk:ready"}) is False
    assert widget.validate_message("https://acme.test", allowed, {"type": "other"}) is False
    assert widget.validate_message("https://acme.test", allowed, "voxdesk:ready") is False
    assert widget.validate_message("https://acme.test", allowed, {"type": 7}) is False


def test_post_message_self_allowlist_compares_against_the_public_origin():
    allowed = ["'self'"]
    assert (
        widget.validate_message(
            "https://app.acme.test", allowed, {"type": "voxdesk:ready"}, public_origin="https://app.acme.test"
        )
        is True
    )
    assert widget.validate_message("https://evil.test", allowed, {"type": "voxdesk:ready"}, public_origin="https://app.acme.test") is False
    assert widget.validate_message("https://acme.test", allowed, {"type": "voxdesk:ready"}) is False


def test_require_theme_refuses_an_unknown_host():
    with pytest.raises(NotFound):
        widget.require_theme(None, "unknown.example.com")
    theme = themed()
    assert widget.require_theme(theme, "support.acme.test") is theme


def test_position_is_a_closed_set():
    assert widget.validate_position("bottom-right") == "bottom-right"
    with pytest.raises(ValidationFailed):
        widget.validate_position("centre")
