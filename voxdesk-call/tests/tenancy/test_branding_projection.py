"""Branding projection (PART 18, P18-06 / D-05).

What is tested here is the *last mile*, because that is what was missing. The
theme store, the contrast gate, the domain resolution and the email renderer
already existed and have their own tests. What did not exist was anything that
turned a stored theme into the values a surface actually renders — which is why
the dashboard's title, the widget's launcher and the email sender could all keep
saying "VoxDesk" while every underlying test passed.

The four properties defended here:

1. **Unpublished means default, and says so.** ``published=False`` is what lets a
   report be honest instead of claiming a tenant is branded when they are not.
2. **``is_white_label`` is not "has a theme".** A published theme that still says
   VoxDesk is the vendor's product with a colour change.
3. **The sender name is part of branding.** It is the field most often missed and
   the one a customer actually sees.
4. **Every surface is projected, or the call fails.** An unknown surface is an
   error, never a silent default, because a silent default is how the vendor's
   name reaches a customer.
"""

from __future__ import annotations

import copy

import pytest

from app.platform_v2.whitelabel.branding import default_theme
from app.tenancy import branding as tb

TENANT = "11111111-1111-1111-1111-111111111111"


def _theme(**overrides) -> dict:
    """A valid theme — the platform default with the stated changes."""
    theme = copy.deepcopy(default_theme())
    for key, value in overrides.items():
        if key in {"product_name", "support_email", "logo_asset_id", "favicon_asset_id"}:
            theme[key] = value
        elif key == "email":
            theme["email"].update(value)
        elif key == "widget":
            theme["widget"].update(value)
        elif key == "colors":
            theme["colors"].update(value)
        elif key == "shape":
            theme["shape"].update(value)
        else:  # pragma: no cover - a typo in a test should be loud
            raise AssertionError(f"unknown override {key}")
    return theme


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_an_unpublished_tenant_gets_the_platform_default():
    brand = tb.project(None, tenant_id=TENANT)
    assert brand.published is False
    assert brand.product_name == tb.VENDOR_NAME
    assert brand.is_white_label is False


def test_the_surface_is_required_to_be_known():
    with pytest.raises(ValueError):
        tb.project(None, tenant_id=TENANT, surface="carrier-pigeon")  # type: ignore[arg-type]


@pytest.mark.parametrize("surface", list(tb.SURFACES))
def test_every_surface_projects(surface: str):
    brand = tb.project(_theme(), tenant_id=TENANT, surface=surface)  # type: ignore[arg-type]
    assert brand.surface == surface
    assert brand.primary.startswith("#")


# ---------------------------------------------------------------------------
# What "white label" actually means
# ---------------------------------------------------------------------------


def test_a_published_theme_with_the_vendor_name_is_not_white_label():
    brand = tb.project(_theme(), tenant_id=TENANT)
    assert brand.published is True
    assert brand.is_white_label is False
    assert "product_name='VoxDesk'" in brand.contains_vendor_branding()


def test_a_fully_branded_tenant_is_white_label_and_clean():
    brand = tb.project(
        _theme(product_name="Acme Voice", email={"from_name": "Acme Voice"}),
        tenant_id=TENANT,
        theme_version=4,
    )
    assert brand.is_white_label is True
    assert brand.contains_vendor_branding() == []
    assert tb.coverage(brand)["clean"] is True
    assert brand.theme_version == 4


def test_the_email_sender_name_is_treated_as_branding():
    """Branding the dashboard and leaving the sender alone is a half-done job."""
    brand = tb.project(_theme(product_name="Acme Voice"), tenant_id=TENANT)
    hits = brand.contains_vendor_branding()
    assert any(hit.startswith("from_name=") for hit in hits)
    assert brand.is_white_label is True  # the name is theirs…
    assert tb.coverage(brand)["clean"] is False  # …but the mail is not


def test_the_widget_launcher_label_is_checked_too():
    brand = tb.project(
        _theme(product_name="Acme", email={"from_name": "Acme"}, widget={"launcher_label": "Ask VoxDesk"}),
        tenant_id=TENANT,
        surface="widget",
    )
    assert any(hit.startswith("launcher_label=") for hit in brand.contains_vendor_branding())


def test_a_tenant_cannot_white_label_by_replacing_the_name_with_whitespace():
    brand = tb.project(_theme(product_name="   ", email={"from_name": "Acme"}), tenant_id=TENANT)
    # The projection falls back to the platform name rather than emitting a blank
    # title, and then reports that as vendor branding instead of hiding it.
    assert brand.product_name == tb.VENDOR_NAME
    assert brand.is_white_label is False


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def test_css_variables_are_prefixed_so_they_cannot_collide():
    brand = tb.project(_theme(product_name="Acme"), tenant_id=TENANT)
    css = brand.css_variables()
    assert all(key.startswith("--brand-") for key in css)
    assert css["--brand-primary"] == brand.primary
    assert "--brand-radius" in css and "--brand-font" in css
    # And the textual form is a single rule, so it can be inlined in <head>.
    assert brand.css_text().startswith(":root{") and brand.css_text().endswith("}")


def test_document_metadata_uses_the_tenant_name():
    brand = tb.project(_theme(product_name="Acme Voice"), tenant_id=TENANT)
    meta = tb.document_metadata(brand)
    assert meta["title"] == "Acme Voice"
    assert meta["applicationName"] == "Acme Voice"
    assert meta["theme_color"] == brand.primary


def test_document_metadata_favicon_is_an_asset_route_not_a_url():
    """A tenant-supplied favicon URL would be a request to a host we do not own."""
    theme = _theme(product_name="Acme", favicon_asset_id="asset-123")
    meta = tb.document_metadata(tb.project(theme, tenant_id=TENANT))
    assert meta["favicon"] == "/api/whitelabel/assets/asset-123"


def test_no_favicon_means_no_icon_rather_than_a_vendor_one():
    meta = tb.document_metadata(tb.project(_theme(product_name="Acme"), tenant_id=TENANT))
    assert meta["favicon"] is None


def test_widget_config_carries_the_tenant_identity():
    brand = tb.project(
        _theme(
            product_name="Acme Voice",
            widget={"launcher_label": "Talk to Acme", "position": "bottom-left"},
        ),
        tenant_id=TENANT,
        surface="widget",
    )
    config = tb.widget_config_for(brand)
    assert config["product_name"] == "Acme Voice"
    assert config["title"] == "Talk to Acme"
    assert config["position"] == "bottom-left"
    assert config["published"] is True


def test_an_unbranded_widget_says_it_is_unpublished():
    """The widget must not silently present the platform theme as the tenant's."""
    config = tb.widget_config_for(tb.project(None, tenant_id=TENANT, surface="widget"))
    assert config["published"] is False


# ---------------------------------------------------------------------------
# The contrast gate still applies to the projection
# ---------------------------------------------------------------------------


def test_a_theme_that_fails_contrast_is_refused_by_the_projection():
    """The projection re-validates because it is also called from job code."""
    from app.platform_v2.errors import ValidationFailed

    bad = _theme(product_name="Acme", colors={"text": "#eeeeee", "background": "#ffffff"})
    with pytest.raises(ValidationFailed):
        tb.project(bad, tenant_id=TENANT)


def test_unknown_theme_keys_are_refused():
    from app.platform_v2.errors import ValidationFailed

    theme = _theme()
    theme["totally_not_a_key"] = True
    with pytest.raises(ValidationFailed):
        tb.project(theme, tenant_id=TENANT)


# ---------------------------------------------------------------------------
# The rendered surfaces agree with each other
# ---------------------------------------------------------------------------


def test_document_and_widget_projections_never_disagree_about_the_name():
    theme = _theme(product_name="Acme Voice", email={"from_name": "Acme Voice"})
    document = tb.project(theme, tenant_id=TENANT, surface="document")
    widget = tb.project(theme, tenant_id=TENANT, surface="widget")
    assert tb.document_metadata(document)["title"] == (
        tb.widget_config_for(widget)["product_name"]
    )
