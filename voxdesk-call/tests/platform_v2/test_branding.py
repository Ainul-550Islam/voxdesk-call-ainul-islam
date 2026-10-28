"""Theme validation and WCAG contrast.

A theme is the one place where a tenant's taste can make the product unusable, so the
validator refuses rather than warns: text at 4.478:1 on white is a real failure of AA and
is treated as one.
"""

from __future__ import annotations

import copy

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import branding


def theme(**overrides):
    payload = branding.default_theme()
    payload.update(overrides)
    return payload


def test_default_theme_passes_its_own_validation():
    normalised, report = branding.validate_theme(branding.default_theme())
    assert report["passed"] is True
    assert report["failures"] == []
    assert normalised["colors"]["primary"] == "#1d4ed8"


def test_default_theme_is_returned_as_a_copy():
    first = branding.default_theme()
    first["colors"]["primary"] = "#000000"
    assert branding.default_theme()["colors"]["primary"] != "#000000"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("#000000", "#ffffff", 21.0),
        ("#ffffff", "#ffffff", 1.0),
        ("#1d4ed8", "#ffffff", 6.7),
        ("#777777", "#ffffff", 4.478),
    ],
    ids=["black-on-white", "white-on-white", "primary-on-white", "gray-aa-boundary"],
)
def test_contrast_ratio_matches_wcag(first, second, expected):
    assert branding.contrast_ratio(first, second) == pytest.approx(expected, abs=0.01)
    # Order must not matter.
    assert branding.contrast_ratio(second, first) == branding.contrast_ratio(first, second)


def test_parse_hex_accepts_three_six_and_eight_digit_forms():
    assert branding.parse_hex("#fff") == (255, 255, 255)
    assert branding.parse_hex("#1D4ED8") == (29, 78, 216)
    # Alpha is accepted and ignored: contrast is measured as if opaque, which can only
    # overstate it, so a passing theme stays passing.
    assert branding.parse_hex("#1d4ed8ff") == (29, 78, 216)
    with pytest.raises(ValidationFailed):
        branding.parse_hex("rgb(0,0,0)")
    with pytest.raises(ValidationFailed):
        branding.parse_hex("#12")


def test_a_theme_that_fails_contrast_is_refused_with_every_failure_listed():
    payload = theme()
    payload["colors"]["text"] = "#777777"
    with pytest.raises(ValidationFailed) as caught:
        branding.validate_theme(payload)
    assert "contrast" in str(caught.value.message)
    failures = caught.value.detail["contrast"]
    assert any("text on background" in item for item in failures)
    # The pairs come back too, so the editor can show the numbers rather than a sentence.
    assert caught.value.detail["pairs"]


def test_every_problem_is_reported_at_once():
    payload = theme()
    payload["colors"]["primary"] = "blue"
    payload["typography"]["font_family"] = "Comic Sans MS"
    payload["shape"]["radius"] = 99
    with pytest.raises(ValidationFailed) as caught:
        branding.validate_theme(payload)
    detail = caught.value.detail
    assert "colors.primary" in detail
    assert "typography.font_family" in detail
    assert "shape.radius" in detail


def test_unknown_keys_are_refused_rather_than_ignored():
    payload = theme(extra_branding={"logo": "x"})
    with pytest.raises(ValidationFailed) as caught:
        branding.validate_theme(payload)
    assert "unknown theme keys" in caught.value.detail["unknown_keys"]

    payload = theme()
    payload["colors"]["danger"] = "#ff0000"
    with pytest.raises(ValidationFailed) as caught:
        branding.validate_theme(payload)
    assert "colors.unknown" in caught.value.detail


def test_product_name_is_required_and_control_characters_are_refused():
    with pytest.raises(ValidationFailed):
        branding.validate_theme(theme(product_name=""))
    with pytest.raises(ValidationFailed):
        branding.validate_theme(theme(product_name="Acme\nCorp"))


def test_support_email_and_reply_to_must_look_like_addresses():
    with pytest.raises(ValidationFailed):
        branding.validate_theme(theme(support_email="not-an-address"))
    payload = theme(support_email="help@acme.test")
    normalised, _report = branding.validate_theme(payload)
    # An unset reply_to stays empty so the mailer omits the header rather than
    # guessing an address the tenant never agreed to.
    assert normalised["email"]["reply_to"] == ""
    with pytest.raises(ValidationFailed) as caught:
        branding.validate_theme(theme(email={"reply_to": "no-at-sign"}))
    assert "email.reply_to" in caught.value.detail


def test_widget_origins_must_be_scheme_and_host():
    payload = theme()
    payload["widget"]["allowed_origins"] = ["https://acme.test", "https://acme.test/"]
    normalised, _report = branding.validate_theme(payload)
    assert normalised["widget"]["allowed_origins"] == ["https://acme.test", "https://acme.test"]

    for bad in ["*", "acme.test", "https://acme.test/path", "ftp://acme.test"]:
        payload = theme()
        payload["widget"]["allowed_origins"] = [bad]
        with pytest.raises(ValidationFailed):
            branding.validate_theme(payload)


def test_widget_position_is_a_closed_set():
    payload = theme()
    payload["widget"]["position"] = "middle"
    with pytest.raises(ValidationFailed):
        branding.validate_theme(payload)


def test_theme_must_be_an_object():
    for bad in (None, [], "theme", 7):
        with pytest.raises(ValidationFailed):
            branding.validate_theme(bad)


def test_oversized_payload_is_refused(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    # The settings object is cached for the process, so the override is undone in a
    # finally block; otherwise every later test in this module inherits the 200-byte cap.
    settings_mod.reset_plat2_settings_cache()
    monkeypatch.setenv("BRANDING_MAX_THEME_BYTES", "200")
    try:
        payload = theme()
        payload["email"]["footer_text"] = "x" * 400
        with pytest.raises(ValidationFailed) as caught:
            branding.validate_theme(payload)
        assert "at most" in caught.value.message
    finally:
        settings_mod.reset_plat2_settings_cache()


def test_contrast_report_marks_each_pair():
    report = branding.contrast_report(branding.default_theme()["colors"])
    assert len(report["pairs"]) == len(branding.CONTRAST_PAIRS)
    for pair in report["pairs"]:
        assert set(pair) == {"foreground", "background", "ratio", "minimum", "passed"}
        assert pair["passed"] == (pair["ratio"] >= pair["minimum"])


def test_normalisation_keeps_the_payload_shape_the_ui_expects():
    payload = copy.deepcopy(branding.default_theme())
    normalised, _report = branding.validate_theme(payload)
    assert set(normalised) == branding.TOP_LEVEL_KEYS
    assert set(normalised["widget"]) == branding.WIDGET_KEYS
    assert set(normalised["email"]) == branding.EMAIL_KEYS
    assert set(normalised["colors"]) == set(branding.COLOR_KEYS)
