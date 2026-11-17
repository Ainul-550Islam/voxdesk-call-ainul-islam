"""Integration library (PART 14, P14-04).

The catalogue is data: for each integration, how it authenticates, which scopes
its actions need, and what those actions are called. Two rules follow from
having it as data rather than as seven bespoke modules:

* the permission review shown to a tenant is generated from the same rows the
  executor uses, so the screen and the behaviour cannot drift;
* an integration we have not built can still be listed -- with `available=False`
  and a reason -- instead of being silently absent from the UI.

Scope names are the vendors' own (`chat:write`, `https://www.googleapis.com/
auth/drive.readonly`). Translating them into a private vocabulary would make the
consent screen easier to read and the OAuth request harder to get right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

AUTH_TYPES = ("oauth2", "api_key", "webhook", "none")


class CatalogError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    #: Scopes this single action needs. An install's review is the union of the
    #: actions the tenant enabled -- never the union of everything available.
    scopes: tuple[str, ...] = ()
    #: What the action does to the outside world. `read` actions never write.
    effect: str = "read"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "scopes": list(self.scopes),
            "effect": self.effect,
        }


@dataclass(frozen=True)
class Integration:
    key: str
    name: str
    category: str
    auth: str
    actions: dict[str, Action] = field(default_factory=dict)
    available: bool = True
    reason: str | None = None
    docs_url: str = ""

    def __post_init__(self) -> None:
        if self.auth not in AUTH_TYPES:
            raise CatalogError("invalid_auth", f"{self.key}: unknown auth type {self.auth!r}")
        if self.available and not self.actions:
            raise CatalogError("no_actions", f"{self.key}: an available integration needs actions")

    def action(self, name: str) -> Action:
        try:
            return self.actions[name]
        except KeyError:
            raise CatalogError(
                "unknown_action", f"{self.key} has no action {name!r}"
            ) from None

    def scopes_for(self, actions: Iterable[str]) -> list[str]:
        scopes: list[str] = []
        for name in actions:
            for scope in self.action(name).scopes:
                if scope not in scopes:
                    scopes.append(scope)
        return scopes

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "auth": self.auth,
            "available": self.available,
            "reason": self.reason,
            "docs_url": self.docs_url,
            "actions": [action.as_dict() for action in self.actions.values()],
        }


def _webhook_like(key: str, name: str) -> Integration:
    return Integration(
        key=key,
        name=name,
        category="generic",
        auth="webhook",
        actions={
            "post": Action(
                "post",
                "Send a JSON payload to a URL the tenant configured",
                scopes=("outbound:https",),
                effect="write",
            )
        },
    )


CATALOG: dict[str, Integration] = {
    "slack": Integration(
        key="slack",
        name="Slack",
        category="messaging",
        auth="oauth2",
        actions={
            "post_message": Action(
                "post_message",
                "Post a message to a channel",
                scopes=("chat:write",),
                effect="write",
            ),
            "list_channels": Action(
                "list_channels", "List public channels", scopes=("channels:read",)
            ),
        },
        docs_url="https://api.slack.com/authentication/oauth-v2",
    ),
    "google_drive": Integration(
        key="google_drive",
        name="Google Drive",
        category="storage",
        auth="oauth2",
        actions={
            "list_files": Action(
                "list_files",
                "List files in a folder (knowledge sync source)",
                scopes=("https://www.googleapis.com/auth/drive.readonly",),
            ),
            "export_doc": Action(
                "export_doc",
                "Export a document as text for the knowledge base",
                scopes=("https://www.googleapis.com/auth/drive.readonly",),
            ),
        },
        docs_url="https://developers.google.com/drive/api/guides/about-auth",
    ),
    "salesforce": Integration(
        key="salesforce",
        name="Salesforce",
        category="crm",
        auth="oauth2",
        actions={
            "upsert_contact": Action(
                "upsert_contact", "Create or update a contact", scopes=("api",), effect="write"
            ),
            "log_call": Action(
                "log_call", "Attach the call summary as a task", scopes=("api",), effect="write"
            ),
        },
    ),
    "hubspot": Integration(
        key="hubspot",
        name="HubSpot",
        category="crm",
        auth="oauth2",
        actions={
            "upsert_contact": Action(
                "upsert_contact",
                "Create or update a contact",
                scopes=("crm.objects.contacts.write",),
                effect="write",
            ),
            "log_call": Action(
                "log_call",
                "Create a call engagement with the summary",
                scopes=("crm.objects.contacts.write",),
                effect="write",
            ),
        },
    ),
    "google_calendar": Integration(
        key="google_calendar",
        name="Google Calendar",
        category="calendar",
        auth="oauth2",
        actions={
            "book_slot": Action(
                "book_slot",
                "Create an event on a calendar",
                scopes=("https://www.googleapis.com/auth/calendar.events",),
                effect="write",
            ),
            "list_slots": Action(
                "list_slots",
                "List free/busy windows",
                scopes=("https://www.googleapis.com/auth/calendar.readonly",),
            ),
        },
    ),
    "outlook": Integration(
        key="outlook",
        name="Outlook Calendar",
        category="calendar",
        auth="oauth2",
        actions={
            "book_slot": Action(
                "book_slot", "Create an event", scopes=("Calendars.ReadWrite",), effect="write"
            )
        },
        # Microsoft Graph requires an app registration per deployment; without
        # one the integration is present in the library but not installable.
        available=False,
        reason="The Microsoft Graph app registration is not configured for this deployment.",
    ),
    "webhook": _webhook_like("webhook", "Outbound webhook"),
    "generic_http": _webhook_like("generic_http", "Generic HTTP action"),
}


def get(key: str) -> Integration:
    try:
        return CATALOG[key]
    except KeyError:
        raise CatalogError("unknown_integration", f"{key!r} is not in the library") from None


def list_catalog(*, only_available: bool = False) -> list[dict[str, Any]]:
    rows = [entry.as_dict() for entry in CATALOG.values()]
    if only_available:
        rows = [row for row in rows if row["available"]]
    return rows


def permissions_for(key: str, actions: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(scopes, unknown_actions)`` for a proposed install."""
    integration = get(key)
    known = [name for name in actions if name in integration.actions]
    unknown = [name for name in actions if name not in integration.actions]
    return integration.scopes_for(known), unknown


def review_text(key: str, actions: Iterable[str]) -> str:
    """One sentence a non-engineer can sign off on."""
    integration = get(key)
    chosen = [integration.action(name) for name in actions if name in integration.actions]
    if not chosen:
        return f"{integration.name}: nothing enabled."
    writing = [action.name for action in chosen if action.effect == "write"]
    detail = f"{len(chosen)} action(s)"
    if writing:
        detail += f", {len(writing)} of which change data outside VoxDesk: {', '.join(writing)}"
    return f"{integration.name} — {detail}."


def coverage() -> Mapping[str, int]:
    """How many actions each effect class exposes. Used by the library page."""
    counts: dict[str, int] = {}
    for integration in CATALOG.values():
        for action in integration.actions.values():
            counts[action.effect] = counts.get(action.effect, 0) + 1
    return counts
