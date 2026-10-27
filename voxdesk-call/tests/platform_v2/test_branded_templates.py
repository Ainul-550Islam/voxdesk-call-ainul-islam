"""Branded transactional email templates.

Templates are code, not tenant data. The tests below pin the three properties that keep it
that way: a closed variable set, escaped output, and no link scheme a mail client will
execute.
"""

from __future__ import annotations

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import branding, templates


def theme(**overrides):
    payload = branding.default_theme()
    payload["product_name"] = "Acme Support"
    payload["support_email"] = "help@acme.test"
    payload["email"]["footer_text"] = "Acme Ltd, 1 High Street"
    payload["email"]["from_name"] = "Acme Support"
    payload.update(overrides)
    normalised, _report = branding.validate_theme(payload)
    return normalised


def test_every_template_has_copy_and_renders_with_its_required_variables():
    required = {
        "rota_published": {"week_start": "2026-10-05", "week_end": "2026-10-11"},
        "shift_swap_approved": {"shift_date": "2026-10-07", "shift_window": "09:00–17:00"},
        "time_off_decided": {
            "decision": "approved",
            "window_start": "2026-10-12",
            "window_end": "2026-10-13",
        },
        "domain_verified": {"domain": "support.acme.test"},
        "sso_domain_verified": {"domain": "acme.test"},
    }
    assert set(required) == set(templates.TEMPLATES)
    for name, variables in required.items():
        rendered = templates.render(name, variables, theme())
        assert rendered.subject
        assert rendered.html.startswith("<!doctype html>")
        # A multipart message needs a text part or the branded mail lands in spam.
        assert rendered.text.strip()
        assert "Acme Support" in rendered.text


def test_the_subject_is_filled_in_not_left_as_a_placeholder():
    rendered = templates.render(
        "rota_published", {"week_start": "2026-10-05", "week_end": "2026-10-11"}, theme()
    )
    assert "2026-10-05" in rendered.subject
    assert "{" not in rendered.subject


def test_an_unknown_template_is_refused_with_the_known_names():
    with pytest.raises(ValidationFailed) as caught:
        templates.render("not_a_template", {}, theme())
    assert sorted(templates.TEMPLATES) == caught.value.detail["known"]


def test_an_unknown_variable_is_refused_rather_than_dropped():
    with pytest.raises(ValidationFailed) as caught:
        templates.render(
            "rota_published",
            {"week_start": "2026-10-05", "week_end": "2026-10-11", "tenant_secret": "x"},
            theme(),
        )
    assert caught.value.detail["unknown"] == ["tenant_secret"]


def test_a_missing_required_variable_is_named():
    with pytest.raises(ValidationFailed) as caught:
        templates.render("rota_published", {"week_start": "2026-10-05"}, theme())
    assert caught.value.detail["missing"] == ["week_end"]


def test_values_are_escaped_in_the_html_part():
    rendered = templates.render(
        "domain_verified",
        {"domain": "support.acme.test<script>alert(1)</script>"},
        theme(),
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_control_characters_cannot_split_a_header():
    rendered = templates.render(
        "domain_verified",
        {"domain": "acme.test\r\nBcc: attacker@evil.test"},
        theme(),
    )
    assert "\r" not in rendered.subject
    assert "\n" not in rendered.subject
    # The attempted header is flattened onto one line by the control-character strip;
    # what matters is that it cannot start a new header.
    assert rendered.subject.count("Bcc:") == 1
    assert rendered.subject.endswith("is now live on your workspace")


def test_only_https_and_relative_call_to_action_urls_are_allowed():
    good = templates.render(
        "rota_published",
        {"week_start": "2026-10-05", "week_end": "2026-10-11", "cta_url": "https://app.acme.test/rota"},
        theme(),
    )
    assert 'href="https://app.acme.test/rota"' in good.html
    assert "https://app.acme.test/rota" in good.text

    relative = templates.render(
        "rota_published",
        {"week_start": "2026-10-05", "week_end": "2026-10-11", "cta_url": "/rota"},
        theme(),
    )
    assert 'href="/rota"' in relative.html

    for bad in ("javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "http://insecure.test/x"):
        with pytest.raises(ValidationFailed):
            templates.render(
                "rota_published",
                {"week_start": "2026-10-05", "week_end": "2026-10-11", "cta_url": bad},
                theme(),
            )


def test_the_theme_drives_subject_adjacent_branding_and_from_name():
    rendered = templates.render("domain_verified", {"domain": "support.acme.test"}, theme())
    assert rendered.from_name == "Acme Support"
    assert rendered.reply_to == "help@acme.test"
    assert "#1d4ed8" in rendered.html  # primary colour from the theme
    assert "Acme Ltd, 1 High Street" in rendered.html
    assert "help@acme.test" in rendered.html


def test_a_missing_theme_renders_the_default_brand():
    rendered = templates.render("domain_verified", {"domain": "support.acme.test"})
    assert rendered.from_name == branding.default_theme()["product_name"]


def test_from_name_falls_back_to_the_product_name_when_email_from_name_is_empty():
    payload = branding.default_theme()
    payload["product_name"] = "Acme Support"
    payload["email"]["from_name"] = ""
    normalised, _report = branding.validate_theme(payload)
    rendered = templates.render("domain_verified", {"domain": "support.acme.test"}, normalised)
    assert rendered.from_name == "Acme Support"


def test_an_invalid_theme_is_refused_at_render_time():
    """Background jobs render with a theme read from the database, not from a request."""
    bad = branding.default_theme()
    bad["colors"]["text"] = "#777777"
    with pytest.raises(ValidationFailed):
        templates.render("domain_verified", {"domain": "support.acme.test"}, bad)


def test_catalogue_exposes_required_and_optional_variables():
    catalogue = {item["name"]: item for item in templates.available()}
    assert set(catalogue) == set(templates.TEMPLATES)
    rota = catalogue["rota_published"]
    assert rota["required"] == ["week_start", "week_end"]
    assert "cta_url" in rota["optional"]
    assert rota["cta_label"]
