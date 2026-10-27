"""Branded transactional email templates.

The templates are **code**, not tenant data. A tenant supplies a theme and a
handful of values per send; they never supply markup, a subject format string, or
a URL scheme. That is the difference between a white-label product and a
server-side template injection bug.

Three rules are enforced here:

* **Closed variable sets.** Each template declares exactly which variables it
  accepts. An unknown variable is refused instead of silently rendering an empty
  string, because "the agent's name was blank in every email" is a support ticket
  that takes a day to diagnose.
* **Escaped values, escaped subject.** Every substituted value goes through
  ``html.escape`` in the HTML part, and control characters are stripped from
  anything that reaches a header (a newline in a subject is header injection).
* **Links are checked.** A URL that is not ``https://`` or an application-relative
  path is refused, so a tenant-controlled value cannot produce a ``javascript:``
  link inside a mail client.

Every template also renders a plain-text alternative. Mail without one is a
deliverability problem, and with a white-labelled sender domain it lands in spam
for a tenant who did nothing wrong.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import branding as branding_mod

MAX_SUBJECT = 160
MAX_VARIABLE = 400

_SAFE_LINK_RE = re.compile(r"^(https://[^\s]+|/[^\s]*)$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Template:
    """One transactional email.

    ``body_key`` names the message catalogue entry used for the body text, so the
    copy lives in one place and the theme only changes how it looks.
    """

    name: str
    subject: str
    body_key: str
    variables: tuple[str, ...]
    optional: tuple[str, ...] = ()
    cta_label: str = ""
    cta_variable: str = ""
    category: str = "transactional"


@dataclass(frozen=True)
class RenderedEmail:
    template: str
    subject: str
    html: str
    text: str
    from_name: str
    reply_to: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
            "from_name": self.from_name,
            "reply_to": self.reply_to,
            "metadata": self.metadata,
        }


#: The catalogue. `body_key` values are stable strings used by the copy table
#: below; a template without copy is a bug the `tests/platform_v2/test_branded_templates.py`
#: module catches at import time by walking this dict.
TEMPLATES: dict[str, Template] = {
    "rota_published": Template(
        name="rota_published",
        subject="Your rota for the week of {week_start} is published",
        body_key="rota_published",
        variables=("week_start", "week_end"),
        optional=("shift_count", "cta_url"),
        cta_label="View your rota",
        cta_variable="cta_url",
    ),
    "shift_swap_approved": Template(
        name="shift_swap_approved",
        subject="Your shift swap on {shift_date} is approved",
        body_key="shift_swap_approved",
        variables=("shift_date", "shift_window"),
        optional=("counterparty", "cta_url"),
        cta_label="Open the schedule",
        cta_variable="cta_url",
    ),
    "time_off_decided": Template(
        name="time_off_decided",
        subject="Your time-off request was {decision}",
        body_key="time_off_decided",
        variables=("decision", "window_start", "window_end"),
        optional=("decided_by", "note", "cta_url"),
        cta_label="View request",
        cta_variable="cta_url",
    ),
    "domain_verified": Template(
        name="domain_verified",
        subject="{domain} is now live on your workspace",
        body_key="domain_verified",
        variables=("domain",),
        optional=("cta_url",),
        cta_label="Open domain settings",
        cta_variable="cta_url",
    ),
    "sso_domain_verified": Template(
        name="sso_domain_verified",
        subject="{domain} is verified for single sign-on",
        body_key="sso_domain_verified",
        variables=("domain",),
        optional=("cta_url",),
        cta_label="Open identity settings",
        cta_variable="cta_url",
    ),
}


def _copy(body_key: str, variables: dict[str, str]) -> tuple[str, str]:
    """Headline and paragraphs for a template, in plain and HTML form.

    Returning ``(headline, paragraphs_joined_by_newline)`` keeps one source of
    truth for wording; the HTML renderer escapes and rewraps it.
    """
    catalogue: dict[str, tuple[str, tuple[str, ...]]] = {
        "rota_published": (
            "Your rota is published",
            (
                "The rota for {week_start} – {week_end} has been published.",
                "Open your workspace to see your shifts, breaks and any open shifts you can pick up.",
            ),
        ),
        "shift_swap_approved": (
            "Shift swap approved",
            (
                "Your shift on {shift_date} ({shift_window}) has been swapped.",
                "No action is needed; your schedule has already been updated.",
            ),
        ),
        "time_off_decided": (
            "Time-off request {decision}",
            (
                "Your request for {window_start} – {window_end} was {decision}.",
                "If you have questions about this decision, reply to this message.",
            ),
        ),
        "domain_verified": (
            "Custom domain verified",
            (
                "{domain} is verified and now serves your workspace.",
                "Certificates are issued automatically the first time somebody opens the domain.",
            ),
        ),
        "sso_domain_verified": (
            "SSO domain verified",
            (
                "{domain} is now verified for single sign-on.",
                "Users with an address on this domain can sign in through your identity provider.",
            ),
        ),
    }
    if body_key not in catalogue:
        raise ValidationFailed(f"no copy registered for template {body_key}")
    headline, paragraphs = catalogue[body_key]
    return headline, "\n\n".join(paragraph.format(**variables) for paragraph in paragraphs)


def _validate_link(value: str, *, variable: str) -> str:
    if not _SAFE_LINK_RE.match(value):
        raise ValidationFailed(
            f"{variable} must be an https:// URL or an application-relative path",
            detail={variable: value[:80]},
        )
    return value


def _clean(value: Any, *, variable: str) -> str:
    if not isinstance(value, str):
        raise ValidationFailed(f"{variable} must be a string", detail={variable: type(value).__name__})
    text = value.strip()
    if len(text) > MAX_VARIABLE:
        raise ValidationFailed(f"{variable} must be at most {MAX_VARIABLE} characters")
    # Header injection: a CR/LF in anything that can reach a subject or a `from`
    # name splits the header. Strip controls before the value is used anywhere.
    text = _CONTROL_RE.sub(" ", text)
    return text


def render(name: str, variables: dict[str, Any], theme: dict[str, Any] | None = None) -> RenderedEmail:
    """Render one template for one recipient.

    ``theme`` is the tenant's brand payload (``branding.publish`` output shape). A
    missing theme is not an error — the default theme renders — but a theme that
    fails validation is, because an invalid theme means an invalid brand.
    """
    template = TEMPLATES.get(name)
    if template is None:
        raise ValidationFailed(
            "unknown template", detail={"template": name, "known": sorted(TEMPLATES)}
        )
    if not isinstance(variables, dict):
        raise ValidationFailed("variables must be an object")

    allowed = set(template.variables) | set(template.optional)
    unknown = sorted(set(variables) - allowed)
    if unknown:
        raise ValidationFailed(
            "template does not accept these variables",
            detail={"unknown": unknown, "allowed": sorted(allowed)},
        )
    missing = [variable for variable in template.variables if not str(variables.get(variable, "")).strip()]
    if missing:
        raise ValidationFailed("template is missing required variables", detail={"missing": missing})

    cleaned: dict[str, str] = {}
    for key, value in variables.items():
        cleaned[key] = _clean(value, variable=key)
    if template.cta_variable and cleaned.get(template.cta_variable):
        cleaned[template.cta_variable] = _validate_link(
            cleaned[template.cta_variable], variable=template.cta_variable
        )

    if theme is None:
        brand = branding_mod.default_theme()
    else:
        # Re-validate rather than trust: the renderer is also called from background
        # jobs whose theme came out of the database, not from the API request that
        # validated it.
        brand, _report = branding_mod.validate_theme(theme)

    headline, body = _copy(template.body_key, cleaned)
    subject = template.subject.format(**cleaned)[:MAX_SUBJECT]
    subject = _CONTROL_RE.sub(" ", subject)

    colors = brand["colors"]
    product = brand["product_name"]
    support = brand.get("support_email") or ""
    footer = brand.get("email", {}).get("footer_text") or ""
    radius = brand["shape"]["radius"]
    font = brand["typography"]["font_family"]
    font_size = brand["typography"]["base_font_size"]

    paragraphs_html = "".join(
        f'<p style="margin:0 0 14px;font-size:{font_size}px;line-height:1.55;color:{colors["text"]}">'
        f"{html.escape(paragraph)}</p>"
        for paragraph in body.split("\n\n")
    )

    cta_html = ""
    if template.cta_variable and cleaned.get(template.cta_variable):
        url = html.escape(cleaned[template.cta_variable], quote=True)
        cta_html = (
            f'<p style="margin:22px 0 0"><a href="{url}" '
            f'style="background:{colors["primary"]};color:{colors["background"]};'
            f"text-decoration:none;padding:12px 20px;border-radius:{radius}px;"
            f'display:inline-block;font-weight:600;font-size:{font_size}px">'
            f"{html.escape(template.cta_label)}</a></p>"
        )

    footer_bits = [f'<p style="margin:0;font-size:12px;color:{colors["muted"]}">{html.escape(product)}</p>']
    if footer:
        footer_bits.append(
            f'<p style="margin:6px 0 0;font-size:12px;color:{colors["muted"]}">{html.escape(footer)}</p>'
        )
    if support:
        footer_bits.append(
            '<p style="margin:6px 0 0;font-size:12px;color:'
            + colors["muted"]
            + '">Questions? '
            + f'<a href="mailto:{html.escape(support, quote=True)}" style="color:{colors["primary"]}">'
            + f"{html.escape(support)}</a></p>"
        )

    html_body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="color-scheme" content="light"><title>{html.escape(subject)}</title></head>'
        f'<body style="margin:0;padding:24px;background:{colors["background"]};'
        f"font-family:'{html.escape(font)}',system-ui,sans-serif;color:{colors['text']}\">"
        f'<div style="max-width:560px;margin:0 auto;background:{colors["surface"]};'
        f"border:1px solid {colors['muted']}33;border-radius:{radius}px;padding:28px\">"
        f'<h1 style="margin:0 0 18px;font-size:{font_size + 6}px;line-height:1.25;color:{colors["text"]}">'
        f"{html.escape(headline)}</h1>"
        f"{paragraphs_html}{cta_html}"
        f'<hr style="border:0;border-top:1px solid {colors["muted"]}33;margin:24px 0 16px">'
        + "".join(footer_bits)
        + "</div></body></html>"
    )

    text_lines = [headline, "", body]
    if template.cta_variable and cleaned.get(template.cta_variable):
        text_lines.extend(["", f"{template.cta_label}: {cleaned[template.cta_variable]}"])
    text_lines.extend(["", product])
    if footer:
        text_lines.append(footer)
    if support:
        text_lines.append(f"Questions? {support}")

    return RenderedEmail(
        template=name,
        subject=subject,
        html=html_body,
        text="\n".join(text_lines).strip() + "\n",
        from_name=brand.get("email", {}).get("from_name") or product,
        reply_to=brand.get("email", {}).get("reply_to") or support,
        metadata={"body_key": template.body_key, "category": template.category},
    )


def available() -> list[dict[str, Any]]:
    """Catalogue for the console."""
    return [
        {
            "name": template.name,
            "subject": template.subject,
            "required": list(template.variables),
            "optional": list(template.optional),
            "cta_label": template.cta_label,
            "category": template.category,
        }
        for template in TEMPLATES.values()
    ]


__all__ = ["MAX_SUBJECT", "TEMPLATES", "RenderedEmail", "Template", "available", "render"]
