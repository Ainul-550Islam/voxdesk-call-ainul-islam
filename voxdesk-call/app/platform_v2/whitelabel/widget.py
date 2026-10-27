"""The embeddable widget: configuration, origin isolation, and the loader script.

A widget is the most exposed surface in this batch. It is served from the tenant's
custom domain, embedded in *the tenant's own website*, and it decides what that
website is allowed to do with us. Two properties matter and both are enforced here
rather than in the router:

**Isolation.** The widget is served as a document with
``Content-Security-Policy: frame-ancestors <the origins the tenant listed>``. An
empty allowlist does **not** mean "anyone": it means "nobody but this domain", which
is the conservative reading and the one that prevents a third-party site from
framing a signed-in agent's session. ``script-src`` carries a per-response nonce, so
the loader runs and inline script injected into the page does not.

**No ambient authority.** The embed script is public and cacheable. It carries no
token, no tenant secret, and no signed URL: it takes an opaque public key that only
resolves a theme, and everything privileged happens over an authenticated API call
from the widget's own origin.

The loader is assembled with ``json.dumps`` for every interpolated value. Hand-built
string concatenation in a JavaScript template is how a product name with an
apostrophe becomes a syntax error, and a product name with a quote becomes an
injection.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.platform_v2.errors import NotFound, PermissionDenied, ValidationFailed
from app.platform_v2.whitelabel import branding as branding_mod

#: Version of the loader contract. Bumped when the embed snippet's *shape*
#: changes, because tenants paste the snippet and keep it for years.
LOADER_VERSION = "1.4.0"

#: Cache lifetime for the loader script at the edge (also set in the Caddy file).
LOADER_MAX_AGE_SECONDS = 60

#: What the widget frame is allowed to load. No third-party script, no inline
#: script, no eval; images may come from our own origin only.
BASE_CSP_DIRECTIVES = (
    "default-src 'none'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "connect-src 'self'",
    "font-src 'self'",
    "form-action 'none'",
    "base-uri 'none'",
)


def public_key_for(tenant_id: uuid.UUID) -> str:
    """Opaque embed key.

    Derived, not stored: it is a name, not a credential — anyone can see it in the
    page source — so it must not be mistaken for a secret by anybody reading this
    file. Row-level scoping still happens on the API call the widget makes.
    """
    return f"vd_{uuid.uuid5(uuid.NAMESPACE_URL, f'voxdesk-widget:{tenant_id}').hex}"


def frame_ancestors(theme: dict[str, Any]) -> list[str]:
    """Origins allowed to frame the widget.

    An empty allowlist yields ``'self'`` — never ``*``, and never "no directive",
    which is the same as allowing everything.
    """
    origins = [origin for origin in (theme.get("widget", {}) or {}).get("allowed_origins", []) if origin]
    return origins or ["'self'"]


def content_security_policy(theme: dict[str, Any], *, nonce: str | None = None) -> str:
    """CSP for the widget document."""
    directives = list(BASE_CSP_DIRECTIVES)
    if nonce:
        directives.append(f"script-src 'nonce-{nonce}'")
    else:
        directives.append("script-src 'self'")
    directives.append("frame-ancestors " + " ".join(frame_ancestors(theme)))
    return "; ".join(directives)


def headers(theme: dict[str, Any], *, nonce: str | None = None) -> dict[str, str]:
    """Response headers for anything served to a tenant's embedded page."""
    return {
        "Content-Security-Policy": content_security_policy(theme, nonce=nonce),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # `frame-ancestors` is the modern control; X-Frame-Options is left out on
        # purpose, because setting it to DENY would break the very embedding the
        # tenant paid for, and setting it to ALLOW-FROM is obsolete and ignored.
        "Cache-Control": f"public, max-age={LOADER_MAX_AGE_SECONDS}",
    }


def validate_embed_origin(origin: str | None, theme: dict[str, Any]) -> None:
    """Refuse an embed request from an origin the tenant did not authorise.

    An absent ``Origin`` header (server-side fetch, curl) is refused too: the
    loader is meant for browsers, and "no origin" cannot be shown to be allowed.
    """
    allowed = frame_ancestors(theme)
    if not origin:
        raise PermissionDenied("an Origin header is required to serve the widget")
    if origin in allowed:
        return
    if "'self'" in allowed and origin == "self":
        return
    raise PermissionDenied(
        "origin is not allowed to embed this widget",
        detail={"origin": origin, "allowed_origins": allowed},
    )


def config_for(tenant_id: uuid.UUID, theme: dict[str, Any]) -> dict[str, Any]:
    """Public widget configuration. Contains nothing that is not on a web page."""
    widget = theme.get("widget", {}) or {}
    colors = theme.get("colors", {}) or {}
    return {
        "version": LOADER_VERSION,
        "public_key": public_key_for(tenant_id),
        "product_name": theme.get("product_name", ""),
        "position": widget.get("position", "bottom-right"),
        "launcher_label": widget.get("launcher_label", ""),
        "welcome_message": widget.get("welcome_message", ""),
        "theme": {
            "primary": colors.get("primary", ""),
            "accent": colors.get("accent", ""),
            "background": colors.get("background", ""),
            "surface": colors.get("surface", ""),
            "text": colors.get("text", ""),
            "radius": (theme.get("shape", {}) or {}).get("radius", 8),
            "font_family": (theme.get("typography", {}) or {}).get("font_family", "system-ui"),
            "base_font_size": (theme.get("typography", {}) or {}).get("base_font_size", 15),
        },
        "allowed_origins": frame_ancestors(theme),
    }


def _js_literal(value: Any) -> str:
    """JSON that is also safe to inline in an HTML ``<script>`` element.

    ``json.dumps`` alone leaves ``</script>`` intact, and the tenant pastes this
    loader *inside* a script tag — a product name containing ``</script>`` would
    end the element and turn the rest of the snippet into markup. Escaping the
    three HTML-significant characters keeps the literal valid JSON and inert HTML.
    """
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def loader_script(config: dict[str, Any]) -> str:
    """The JavaScript a tenant pastes into their site.

    Every interpolated value goes through ``json.dumps`` so a product name cannot
    terminate the literal, and the resulting snippet contains no credential: the
    public key resolves a theme and nothing else.
    """
    payload = _js_literal(config)
    return (
        "/* VoxDesk widget loader " + json.dumps(LOADER_VERSION) + " */\n"
        "(function () {\n"
        "  var config = " + payload + ";\n"
        "  if (window.__voxdeskWidgetLoaded) { return; }\n"
        "  window.__voxdeskWidgetLoaded = true;\n"
        "  var frame = document.createElement('iframe');\n"
        "  frame.title = config.product_name || 'Support';\n"
        "  frame.setAttribute('data-voxdesk-widget', config.public_key);\n"
        "  frame.style.cssText = 'position:fixed;border:0;width:380px;height:560px;max-width:100vw;max-height:100vh;z-index:2147483000;' +\n"
        "    (config.position.indexOf('top') === 0 ? 'top:16px;' : 'bottom:16px;') +\n"
        "    (config.position.indexOf('left') !== -1 ? 'left:16px;' : 'right:16px;');\n"
        "  frame.src = '/widget/' + encodeURIComponent(config.public_key) + '/frame';\n"
        "  frame.addEventListener('load', function () {\n"
        "    frame.contentWindow.postMessage({ type: 'voxdesk:config', config: config }, window.location.origin);\n"
        "  });\n"
        "  document.body.appendChild(frame);\n"
        "})();\n"
    )


def validate_message(
    origin: str,
    allowed: list[str],
    data: Any,
    *,
    public_origin: str = "",
) -> bool:
    """Guard for ``postMessage`` traffic arriving from an embed.

    Both the origin *and* the message shape are checked: a handler that trusts the
    payload is the classic widget vulnerability, and the origin alone does not tell
    you the sender is a VoxDesk frame.

    ``'self'`` in the allowlist means "our own origin", which for a ``postMessage``
    comparison is the deployment's public origin — hence ``public_origin``. Passing
    a bare ``'self'`` string here never matches, deliberately.
    """
    origin_ok = origin in allowed or (
        "'self'" in allowed and bool(public_origin) and origin == public_origin
    )
    if not origin_ok:
        return False
    if not isinstance(data, dict):
        return False
    message_type = data.get("type")
    return isinstance(message_type, str) and message_type.startswith("voxdesk:")


def require_theme(theme: dict[str, Any] | None, host: str) -> dict[str, Any]:
    """A theme for a host, or a 404 — never a default theme for an unknown host."""
    if theme is None:
        raise NotFound("host is not a configured tenant domain", detail={"host": host})
    return theme


def validate_position(value: str) -> str:
    if value not in branding_mod.WIDGET_POSITIONS:
        raise ValidationFailed(
            "position must be one of: " + ", ".join(branding_mod.WIDGET_POSITIONS),
            detail={"position": value},
        )
    return value


__all__ = [
    "BASE_CSP_DIRECTIVES",
    "LOADER_MAX_AGE_SECONDS",
    "LOADER_VERSION",
    "config_for",
    "content_security_policy",
    "frame_ancestors",
    "headers",
    "loader_script",
    "public_key_for",
    "require_theme",
    "validate_embed_origin",
    "validate_message",
    "validate_position",
]
