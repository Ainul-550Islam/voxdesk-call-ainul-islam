"""Branding, projected to the three surfaces that can leak the vendor's name.

PART 18 (P18-06 / D-05) asked for ``app/tenancy/branding.py``. Before writing it
the existing implementation was audited, and most of the feature already exists
in :mod:`app.platform_v2.whitelabel` — versioned themes, WCAG contrast gates, a
font allowlist, custom domains with DNS proof, sender domains, themed email
templates, and a widget loader resolved by ``Host``. Re-implementing any of that
would have been the wrong answer.

What was genuinely missing was the last mile. Three surfaces still said
"VoxDesk" no matter what a tenant published:

* **the document shell** — ``app/layout.tsx`` hard-codes ``title: "VoxDesk"``;
* **the widget** — ``embed.js`` fell back to ``"Voice Widget"`` and
  ``#2563eb`` because the loader never handed it the resolved theme;
* **anything server-rendered** — there was no single object that said "for this
  tenant, at this surface, these are the values to use".

This module is that object. It is deliberately a *projection*, not a second
store: every value comes from the existing theme payload, and a surface it does
not know is a programming error rather than a silent default. The rule it
enforces is the one a white-label sale actually turns on:

    **If a surface can show the vendor's name, it must read it from here.**

The reverse is also enforced. A tenant who has *not* published a theme gets the
platform default, and the platform default is allowed to say "VoxDesk" — that is
the vendor's own product, not a branding failure. ``is_white_label`` is what
tells a caller which of those two situations it is in, so a report never claims
a tenant is branded when they are simply on the default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from app.platform_v2.whitelabel import branding as branding_mod

Surface = Literal["ui", "email", "widget", "document"]

SURFACES: tuple[Surface, ...] = ("ui", "email", "widget", "document")

#: The string that must never appear on a white-labelled surface. Kept as a
#: constant so the guard in ``assert_no_vendor_branding`` and the report in
#: ``coverage()`` cannot disagree about what "the vendor's name" means.
VENDOR_NAME = "VoxDesk"

#: What a surface falls back to when the tenant has published nothing. These are
#: the platform's own identity, not a tenant's brand.
PLATFORM_DEFAULTS: Mapping[str, str] = {
    "product_name": "VoxDesk",
    "support_email": "",
}


@dataclass(frozen=True)
class Brand:
    """One tenant's branding, projected for one surface.

    ``published`` is the field that stops this being a lie: a brand built from
    ``default_theme()`` has ``published=False`` and every consumer can say "this
    is the platform default" rather than pretending the tenant chose it.
    """

    tenant_id: str
    surface: Surface
    product_name: str
    primary: str
    accent: str
    background: str
    surface_color: str
    text: str
    muted: str
    radius: int
    font_family: str
    base_font_size: int
    support_email: str = ""
    logo_asset_id: str | None = None
    favicon_asset_id: str | None = None
    footer_text: str = ""
    from_name: str = ""
    position: str = "bottom-right"
    launcher_label: str = ""
    welcome_message: str = ""
    theme_version: int = 0
    published: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_white_label(self) -> bool:
        """True only when the tenant published a theme *and* it is not ours.

        A published theme that still says "VoxDesk" is not white-label — it is
        the vendor's product with a custom colour, and saying otherwise in a
        deal-room report would be untrue.
        """
        return self.published and self.product_name.strip() != VENDOR_NAME

    # -- CSS ---------------------------------------------------------------
    def css_variables(self) -> dict[str, str]:
        """The custom properties the dashboard can set on ``:root``.

        Named with a ``--brand-`` prefix so a tenant theme can never collide with
        a component's own variable and produce a cascade surprise.
        """
        return {
            "--brand-primary": self.primary,
            "--brand-accent": self.accent,
            "--brand-background": self.background,
            "--brand-surface": self.surface_color,
            "--brand-text": self.text,
            "--brand-muted": self.muted,
            "--brand-radius": f"{self.radius}px",
            "--brand-font": f"'{self.font_family}', system-ui, sans-serif",
            "--brand-font-size": f"{self.base_font_size}px",
        }

    def css_text(self, selector: str = ":root") -> str:
        body = ";".join(f"{key}:{value}" for key, value in self.css_variables().items())
        return f"{selector}{{{body}}}"

    # -- serialisation -----------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "surface": self.surface,
            "product_name": self.product_name,
            "colors": {
                "primary": self.primary,
                "accent": self.accent,
                "background": self.background,
                "surface": self.surface_color,
                "text": self.text,
                "muted": self.muted,
            },
            "shape": {"radius": self.radius},
            "typography": {
                "font_family": self.font_family,
                "base_font_size": self.base_font_size,
            },
            "support_email": self.support_email,
            "logo_asset_id": self.logo_asset_id,
            "favicon_asset_id": self.favicon_asset_id,
            "footer_text": self.footer_text,
            "from_name": self.from_name,
            "widget": {
                "position": self.position,
                "launcher_label": self.launcher_label,
                "welcome_message": self.welcome_message,
            },
            "theme_version": self.theme_version,
            "published": self.published,
            "is_white_label": self.is_white_label,
        }

    # -- the guard ---------------------------------------------------------
    def contains_vendor_branding(self) -> list[str]:
        """Fields that still carry the vendor's name, as ``field=value`` strings.

        ``from_name`` matters as much as ``product_name``: it is the sender a
        customer sees in their inbox, and the shipped default theme sets it to
        the vendor's name. A tenant who brands the dashboard and forgets the
        sender has not white-labelled anything — their customers still get mail
        from us.
        """
        needle = VENDOR_NAME.lower()
        hits: list[str] = []
        for field_name, value in (
            ("product_name", self.product_name),
            ("from_name", self.from_name),
            ("footer_text", self.footer_text),
            ("launcher_label", self.launcher_label),
        ):
            if needle in str(value or "").lower():
                hits.append(f"{field_name}={value!r}")
        return hits


def project(
    theme: Mapping[str, Any] | None,
    *,
    tenant_id: uuid.UUID | str,
    surface: Surface = "ui",
    theme_version: int = 0,
) -> Brand:
    """Project a theme payload onto one surface.

    ``theme=None`` means the tenant has published nothing, so the platform
    default is used with ``published=False``. The payload is validated here as
    well as at the API boundary, because this is also called from request paths
    that read the theme out of the database rather than from the request that
    validated it.
    """
    if surface not in SURFACES:
        raise ValueError(f"unknown branding surface {surface!r}; known: {', '.join(SURFACES)}")

    published = theme is not None
    if theme is None:
        payload = branding_mod.default_theme()
    else:
        payload, _report = branding_mod.validate_theme(dict(theme))

    colors = dict(payload.get("colors") or {})
    shape = dict(payload.get("shape") or {})
    typography = dict(payload.get("typography") or {})
    widget = dict(payload.get("widget") or {})
    email = dict(payload.get("email") or {})

    product_name = str(payload.get("product_name") or "")
    if not product_name and published:
        # A published theme with no name is the tenant's choice to keep the
        # platform name; record it rather than inventing one.
        product_name = PLATFORM_DEFAULTS["product_name"]

    return Brand(
        tenant_id=str(tenant_id),
        surface=surface,
        product_name=product_name or PLATFORM_DEFAULTS["product_name"],
        primary=str(colors.get("primary") or "#2563eb"),
        accent=str(colors.get("accent") or "#0ea5e9"),
        background=str(colors.get("background") or "#ffffff"),
        surface_color=str(colors.get("surface") or "#f8fafc"),
        text=str(colors.get("text") or "#0f172a"),
        muted=str(colors.get("muted") or "#64748b"),
        radius=int(shape.get("radius", 8)),
        font_family=str(typography.get("font_family") or "system-ui"),
        base_font_size=int(typography.get("base_font_size", 15)),
        support_email=str(payload.get("support_email") or ""),
        logo_asset_id=payload.get("logo_asset_id"),
        favicon_asset_id=payload.get("favicon_asset_id"),
        footer_text=str(email.get("footer_text") or ""),
        from_name=str(email.get("from_name") or ""),
        position=str(widget.get("position") or "bottom-right"),
        launcher_label=str(widget.get("launcher_label") or ""),
        welcome_message=str(widget.get("welcome_message") or ""),
        theme_version=theme_version,
        published=published,
    )


def document_metadata(brand: Brand) -> dict[str, Any]:
    """``<head>`` values for the dashboard document.

    Returned as a plain dict so the Next.js layout can spread it into
    ``generateMetadata`` without importing this module, and so a test can assert
    on the values without rendering React.
    """
    title = brand.product_name if brand.product_name else PLATFORM_DEFAULTS["product_name"]
    return {
        "title": title,
        "applicationName": title,
        "description": f"{title} workspace",
        # The favicon is served by asset id, not by a tenant-supplied URL: a URL
        # in metadata is a request to a host we do not control on every page.
        "favicon": f"/api/whitelabel/assets/{brand.favicon_asset_id}" if brand.favicon_asset_id else None,
        "theme_color": brand.primary,
        "background_color": brand.background,
    }


def widget_config_for(brand: Brand) -> dict[str, Any]:
    """The subset the embeddable widget reads."""
    return {
        "product_name": brand.product_name,
        "title": brand.launcher_label or brand.product_name,
        "subtitle": brand.welcome_message or "",
        "primary": brand.primary,
        "accent": brand.accent,
        "background": brand.background,
        "surface": brand.surface_color,
        "text": brand.text,
        "muted": brand.muted,
        "radius": brand.radius,
        "font_family": brand.font_family,
        "base_font_size": brand.base_font_size,
        "position": brand.position,
        "published": brand.published,
    }


def coverage(brand: Brand) -> dict[str, Any]:
    """What still shows the vendor's name on this surface.

    This is what belongs in a deal-room answer: not "white-label is supported"
    but "on the dashboard surface, zero fields carry the vendor name; on the
    email surface, the default footer does until you publish one".
    """
    hits = brand.contains_vendor_branding()
    return {
        "surface": brand.surface,
        "published": brand.published,
        "is_white_label": brand.is_white_label,
        "vendor_name_fields": hits,
        "clean": not hits,
    }
