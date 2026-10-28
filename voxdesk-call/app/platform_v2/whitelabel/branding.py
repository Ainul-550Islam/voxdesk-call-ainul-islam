"""Brand themes: the tokens that make one deployment look like each tenant.

A theme is data, not CSS, and it is validated as data. The two failure modes this
module exists to prevent are the ones that actually happen:

* a tenant sets grey-on-grey and their agents cannot read the screen. So contrast
  is computed (WCAG 2.1 relative luminance) and a theme that fails is refused with
  the failing pairs listed — not silently darkened, because "we changed your
  colours for you" is its own support ticket;
* a theme grows unbounded and a 400 KB JSON blob ends up in every page load. So the
  payload size is capped (``BRANDING_MAX_THEME_BYTES``), unknown keys are rejected
  rather than stored, and fonts are chosen from an allowlist rather than passed
  through to a stylesheet.

A theme is versioned. Publishing writes a new immutable ``wl_brand_theme_versions``
row and repoints ``wl_brand_themes.current_version_id``, so a rollback is a pointer
move and the audit trail of "what did the product look like last Tuesday" is real.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import BrandTheme, BrandThemeVersion
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings

#: Colours a theme must define. Everything the UI paints with comes from here, so
#: there is no "theme sets the text colour, CSS default sets the background" trap.
COLOR_KEYS = ("primary", "accent", "background", "surface", "text", "muted")

#: Minimum contrast ratios (WCAG 2.1 AA). Body text 4.5:1; large text and UI
#: boundaries 3:1. These are the numbers a designer will be held to.
MIN_TEXT_CONTRAST = 4.5
MIN_UI_CONTRAST = 3.0

#: Pairs checked, as (foreground, background, minimum).
CONTRAST_PAIRS = (
    ("text", "background", MIN_TEXT_CONTRAST),
    ("text", "surface", MIN_TEXT_CONTRAST),
    ("muted", "background", MIN_UI_CONTRAST),
    ("primary", "background", MIN_UI_CONTRAST),
    ("accent", "background", MIN_UI_CONTRAST),
    ("background", "primary", MIN_UI_CONTRAST),
)

#: Fonts that are actually shipped with the dashboard. A free-form font family is
#: an injection point into a CSS declaration and a request to a third-party CDN.
ALLOWED_FONTS = (
    "system-ui",
    "Inter",
    "Source Sans 3",
    "IBM Plex Sans",
    "Atkinson Hyperlegible",
    "Noto Sans",
    "Noto Sans Bengali",
)

WIDGET_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")

MAX_PRODUCT_NAME = 60
MAX_SUPPORT_EMAIL = 254
_FONT_SIZE_RANGE = (12, 20)
_RADIUS_RANGE = (0, 24)
_HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

#: The complete set of keys a theme may contain. Anything else is refused, which
#: is what keeps a typo from becoming a silent no-op in the UI.
TOP_LEVEL_KEYS = frozenset(
    {
        "colors",
        "typography",
        "shape",
        "product_name",
        "support_email",
        "logo_asset_id",
        "favicon_asset_id",
        "widget",
        "email",
    }
)
WIDGET_KEYS = frozenset({"position", "launcher_label", "welcome_message", "allowed_origins"})
EMAIL_KEYS = frozenset({"from_name", "footer_text", "reply_to"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_hex(value: str) -> tuple[int, int, int]:
    """``#rgb``/``#rrggbb``/``#rrggbbaa`` → (r, g, b). Alpha is accepted and ignored
    for contrast, which is the conservative choice: a translucent panel can only
    reduce the contrast we measured, so a passing theme is checked as if opaque."""
    if not isinstance(value, str) or not _HEX_RE.match(value.strip().lower()):
        raise ValidationFailed("colour must be a hex value like #1f2937")
    raw = value.strip().lower().lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance."""

    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(component) for component in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """Contrast ratio between two colours, 1.0 (identical) to 21.0 (black/white)."""
    lum_a = relative_luminance(parse_hex(first))
    lum_b = relative_luminance(parse_hex(second))
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return round((lighter + 0.05) / (darker + 0.05), 3)


def contrast_report(colors: dict[str, str]) -> dict[str, Any]:
    """Every checked pair, its ratio, its minimum and whether it passed."""
    pairs: list[dict[str, Any]] = []
    failures: list[str] = []
    for foreground, background, minimum in CONTRAST_PAIRS:
        ratio = contrast_ratio(colors[foreground], colors[background])
        passed = ratio >= minimum
        pairs.append(
            {
                "foreground": foreground,
                "background": background,
                "ratio": ratio,
                "minimum": minimum,
                "passed": passed,
            }
        )
        if not passed:
            failures.append(f"{foreground} on {background} is {ratio}:1, needs {minimum}:1")
    return {"pairs": pairs, "failures": failures, "passed": not failures}


def _clean_text(value: Any, *, field: str, limit: int, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise ValidationFailed(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValidationFailed(f"{field} must be a string")
    text = value.strip()
    if len(text) > limit:
        raise ValidationFailed(f"{field} must be at most {limit} characters")
    # Control characters (newlines included) have no business in a display name
    # that gets interpolated into HTML and email headers.
    if any(ord(char) < 32 for char in text):
        raise ValidationFailed(f"{field} must not contain control characters")
    return text


def _clean_int(value: Any, *, field: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed(f"{field} must be an integer")
    if not low <= value <= high:
        raise ValidationFailed(f"{field} must be between {low} and {high}")
    return value


def validate_theme(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and normalise a theme payload.

    Returns ``(normalised_payload, contrast_report)``. Raises ``ValidationFailed``
    with a ``detail`` that lists *every* problem it found, because a designer
    fixing six fields one round-trip at a time is a wasted afternoon.
    """
    cfg = get_plat2_settings()
    if not isinstance(payload, dict):
        raise ValidationFailed("theme payload must be an object")

    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded) > cfg.branding_max_theme_bytes:
        raise ValidationFailed(
            f"theme payload must be at most {cfg.branding_max_theme_bytes} bytes (got {len(encoded)})"
        )

    problems: dict[str, str] = {}
    unknown = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown:
        problems["unknown_keys"] = f"unknown theme keys: {', '.join(unknown)}"

    colors_in = payload.get("colors") or {}
    if not isinstance(colors_in, dict):
        raise ValidationFailed("colors must be an object", detail={"colors": "must be an object"})
    colors: dict[str, str] = {}
    for key in COLOR_KEYS:
        raw = colors_in.get(key)
        if raw is None:
            problems[f"colors.{key}"] = "colour is required"
            continue
        try:
            parse_hex(raw)
        except ValidationFailed as exc:
            problems[f"colors.{key}"] = str(exc)
            continue
        colors[key] = raw.strip().lower()
    extra_colors = sorted(set(colors_in) - set(COLOR_KEYS))
    if extra_colors:
        problems["colors.unknown"] = f"unknown colour keys: {', '.join(extra_colors)}"

    typography_in = payload.get("typography") or {}
    if not isinstance(typography_in, dict):
        raise ValidationFailed("typography must be an object")
    font_family = typography_in.get("font_family", "system-ui")
    if font_family not in ALLOWED_FONTS:
        problems["typography.font_family"] = f"font_family must be one of: {', '.join(ALLOWED_FONTS)}"
    font_size = typography_in.get("base_font_size", 15)
    try:
        font_size = _clean_int(font_size, field="typography.base_font_size", low=_FONT_SIZE_RANGE[0], high=_FONT_SIZE_RANGE[1])
    except ValidationFailed as exc:
        problems["typography.base_font_size"] = str(exc)

    shape_in = payload.get("shape") or {}
    if not isinstance(shape_in, dict):
        raise ValidationFailed("shape must be an object")
    radius = shape_in.get("radius", 8)
    try:
        radius = _clean_int(radius, field="shape.radius", low=_RADIUS_RANGE[0], high=_RADIUS_RANGE[1])
    except ValidationFailed as exc:
        problems["shape.radius"] = str(exc)

    product_name = ""
    try:
        product_name = _clean_text(payload.get("product_name"), field="product_name", limit=MAX_PRODUCT_NAME, required=True)
    except ValidationFailed as exc:
        problems["product_name"] = str(exc)

    support_email = ""
    try:
        support_email = _clean_text(payload.get("support_email"), field="support_email", limit=MAX_SUPPORT_EMAIL)
    except ValidationFailed as exc:
        problems["support_email"] = str(exc)
    if support_email and not _EMAIL_RE.match(support_email):
        problems["support_email"] = "support_email must be a valid email address"

    widget_in = payload.get("widget") or {}
    if not isinstance(widget_in, dict):
        raise ValidationFailed("widget must be an object")
    unknown_widget = sorted(set(widget_in) - WIDGET_KEYS)
    if unknown_widget:
        problems["widget.unknown_keys"] = f"unknown widget keys: {', '.join(unknown_widget)}"
    position = widget_in.get("position", "bottom-right")
    if position not in WIDGET_POSITIONS:
        problems["widget.position"] = f"position must be one of: {', '.join(WIDGET_POSITIONS)}"
    try:
        launcher_label = _clean_text(widget_in.get("launcher_label", ""), field="widget.launcher_label", limit=40)
        welcome_message = _clean_text(
            widget_in.get("welcome_message", ""), field="widget.welcome_message", limit=280
        )
    except ValidationFailed as exc:
        problems["widget.text"] = str(exc)
        launcher_label = welcome_message = ""
    origins_in = widget_in.get("allowed_origins", [])
    origins: list[str] = []
    if origins_in:
        if not isinstance(origins_in, list) or len(origins_in) > 20:
            problems["widget.allowed_origins"] = "allowed_origins must be a list of at most 20 origins"
        else:
            for origin in origins_in:
                cleaned = _normalize_origin(origin)
                if cleaned is None:
                    problems["widget.allowed_origins"] = (
                        "each origin must look like https://example.com (scheme + host, no path)"
                    )
                    break
                origins.append(cleaned)

    email_in = payload.get("email") or {}
    if not isinstance(email_in, dict):
        raise ValidationFailed("email must be an object")
    unknown_email = sorted(set(email_in) - EMAIL_KEYS)
    if unknown_email:
        problems["email.unknown_keys"] = f"unknown email keys: {', '.join(unknown_email)}"
    try:
        from_name = _clean_text(email_in.get("from_name", product_name), field="email.from_name", limit=80)
        footer_text = _clean_text(email_in.get("footer_text", ""), field="email.footer_text", limit=280)
        reply_to = _clean_text(email_in.get("reply_to", support_email), field="email.reply_to", limit=MAX_SUPPORT_EMAIL)
    except ValidationFailed as exc:
        problems["email.text"] = str(exc)
        from_name = footer_text = ""
        reply_to = ""
    if reply_to and not _EMAIL_RE.match(reply_to):
        problems["email.reply_to"] = "email.reply_to must be a valid email address"

    if problems:
        raise ValidationFailed("theme is not valid", detail=problems)

    normalized = {
        "colors": colors,
        "typography": {"font_family": font_family, "base_font_size": font_size},
        "shape": {"radius": radius},
        "product_name": product_name,
        "support_email": support_email,
        "logo_asset_id": str(payload["logo_asset_id"]) if payload.get("logo_asset_id") else "",
        "favicon_asset_id": str(payload["favicon_asset_id"]) if payload.get("favicon_asset_id") else "",
        "widget": {
            "position": position,
            "launcher_label": launcher_label,
            "welcome_message": welcome_message,
            "allowed_origins": origins,
        },
        "email": {"from_name": from_name, "footer_text": footer_text, "reply_to": reply_to},
    }

    report = contrast_report(colors)
    if report["failures"]:
        raise ValidationFailed(
            "theme colours do not meet the minimum contrast",
            detail={"contrast": report["failures"], "pairs": report["pairs"]},
        )
    return normalized, report


def _normalize_origin(origin: Any) -> str | None:
    """``https://app.example.com`` → itself; anything with a path or a wildcard → None.

    A wildcard origin in ``frame-ancestors`` is the difference between "only our
    customer's site may embed this" and "any site may".
    """
    if not isinstance(origin, str):
        return None
    value = origin.strip().rstrip("/")
    if value == "*":
        return None
    if not re.match(r"^https?://[a-z0-9.-]+(?::\d{1,5})?$", value.lower()):
        return None
    return value.lower()


DEFAULT_THEME: dict[str, Any] = {
    "colors": {
        "primary": "#1d4ed8",
        "accent": "#0f766e",
        "background": "#ffffff",
        "surface": "#f8fafc",
        "text": "#0f172a",
        "muted": "#475569",
    },
    "typography": {"font_family": "system-ui", "base_font_size": 15},
    "shape": {"radius": 8},
    "product_name": "VoxDesk",
    "support_email": "",
    "logo_asset_id": "",
    "favicon_asset_id": "",
    "widget": {
        "position": "bottom-right",
        "launcher_label": "Chat with us",
        "welcome_message": "",
        "allowed_origins": [],
    },
    "email": {"from_name": "VoxDesk", "footer_text": "", "reply_to": ""},
}


def default_theme() -> dict[str, Any]:
    """The theme a tenant gets before they publish one.

    Returned by ``copy.deepcopy``-equivalent construction so a caller cannot
    mutate the module constant.
    """
    return json.loads(json.dumps(DEFAULT_THEME))


def describe_version(version: BrandThemeVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "theme_id": str(version.theme_id),
        "version_number": version.version_number,
        "payload": version.payload,
        "contrast": version.contrast,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


async def _theme_row(session: AsyncSession, tenant_id: uuid.UUID, *, create: bool = False) -> BrandTheme | None:
    row = await session.execute(
        select(BrandTheme).where(BrandTheme.tenant_id == tenant_id, BrandTheme.active_key == "active")
    )
    theme = row.scalar_one_or_none()
    if theme is None and create:
        theme = BrandTheme(id=uuid.uuid4(), tenant_id=tenant_id, name="Default", active_key="active")
        session.add(theme)
        await session.flush()
    return theme


async def publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: Any,
    created_by_user_id: uuid.UUID | None = None,
    activate: bool = True,
    name: str = "Default",
    commit: bool = False,
) -> BrandThemeVersion:
    """Validate a payload and write a new immutable version.

    ``activate=True`` repoints the theme's current version; ``False`` stores a
    draft that can be published later. Nothing here mutates an existing version —
    a published theme is a fact about what users saw.
    """
    normalized, report = validate_theme(payload)
    theme = await _theme_row(session, tenant_id, create=True)
    assert theme is not None  # create=True guarantees it
    if name and theme.name != name:
        theme.name = _clean_text(name, field="name", limit=60, required=True)

    latest = await session.execute(
        select(BrandThemeVersion.version_number)
        .where(BrandThemeVersion.theme_id == theme.id)
        .order_by(BrandThemeVersion.version_number.desc())
        .limit(1)
    )
    next_number = (latest.scalar_one_or_none() or 0) + 1

    version = BrandThemeVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        theme_id=theme.id,
        version_number=next_number,
        payload=normalized,
        contrast=report,
        created_by_user_id=created_by_user_id,
        published_at=_now() if activate else None,
    )
    session.add(version)
    await session.flush()
    if activate:
        theme.current_version_id = version.id
        theme.updated_at = _now()
    if commit:
        await session.commit()
    return version


async def activate_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID, commit: bool = False
) -> BrandThemeVersion:
    """Roll forward or back by moving the pointer."""
    theme = await _theme_row(session, tenant_id)
    if theme is None:
        raise NotFound("no theme exists for this tenant")
    row = await session.execute(
        select(BrandThemeVersion).where(
            BrandThemeVersion.id == version_id, BrandThemeVersion.tenant_id == tenant_id
        )
    )
    version = row.scalar_one_or_none()
    if version is None:
        raise NotFound("theme version not found")
    if theme.current_version_id == version.id:
        raise Conflict("that version is already active", detail={"version_id": str(version.id)})
    theme.current_version_id = version.id
    theme.updated_at = _now()
    version.published_at = version.published_at or _now()
    if commit:
        await session.commit()
    return version


async def current(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> tuple[dict[str, Any], BrandThemeVersion | None]:
    """The active theme payload, falling back to the default when unpublished."""
    theme = await _theme_row(session, tenant_id)
    if theme is None or theme.current_version_id is None:
        return default_theme(), None
    row = await session.execute(
        select(BrandThemeVersion).where(BrandThemeVersion.id == theme.current_version_id)
    )
    version = row.scalar_one_or_none()
    if version is None:
        return default_theme(), None
    return dict(version.payload), version


async def versions(
    session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 50
) -> list[BrandThemeVersion]:
    theme = await _theme_row(session, tenant_id)
    if theme is None:
        return []
    rows = await session.execute(
        select(BrandThemeVersion)
        .where(BrandThemeVersion.theme_id == theme.id)
        .order_by(BrandThemeVersion.version_number.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def theme_for_host(session: AsyncSession, *, host: str) -> dict[str, Any] | None:
    """Theme for a request Host, or ``None`` when the Host is not a live domain.

    ``None`` — not the default theme — for an unknown host: serving a branded page
    for a host we do not own is how a phishing page ends up on our certificate.
    """
    from app.platform_v2.whitelabel import domains as domains_mod

    resolved = await domains_mod.resolve_host(session, host)
    if resolved is None:
        return None
    payload, version = await current(session, tenant_id=resolved["tenant_id"])
    payload["_meta"] = {
        "tenant_id": str(resolved["tenant_id"]),
        "domain": resolved["domain"],
        "version": version.version_number if version else 0,
    }
    return payload


__all__ = [
    "ALLOWED_FONTS",
    "COLOR_KEYS",
    "CONTRAST_PAIRS",
    "DEFAULT_THEME",
    "MIN_TEXT_CONTRAST",
    "MIN_UI_CONTRAST",
    "TOP_LEVEL_KEYS",
    "WIDGET_POSITIONS",
    "activate_version",
    "contrast_ratio",
    "contrast_report",
    "current",
    "default_theme",
    "describe_version",
    "parse_hex",
    "publish",
    "relative_luminance",
    "theme_for_host",
    "validate_theme",
    "versions",
]
