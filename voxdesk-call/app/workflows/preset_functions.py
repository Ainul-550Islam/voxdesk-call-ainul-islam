"""Typed preset functions (PART 14, P14-04).

A preset is a workflow step with a name a tenant recognises, a typed argument
list and a declared side effect. The agent layer never invents these: the model
picks a preset by name and fills the arguments, and this module is what decides
whether those arguments are acceptable before anything leaves the process.

Two properties matter more than the catalogue itself:

* **validation happens here**, not in the vendor adapter, so a bad argument
  fails before a network call and the error names the field;
* **idempotency is declared**, so the durable executor knows which presets may
  be retried blindly (`send_sms`) and which may not (`end_call`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ARG_TYPES = ("string", "integer", "boolean", "number", "array", "object")
EFFECTS = ("message", "crm", "calendar", "call_control", "lookup")

E164 = re.compile(r"^\+[1-9]\d{7,15}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?$")


class PresetError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Argument:
    name: str
    type: str
    required: bool = False
    description: str = ""
    pattern: str | None = None
    maximum: int | None = None
    default: Any = None

    def __post_init__(self) -> None:
        if self.type not in ARG_TYPES:
            raise PresetError("invalid_type", f"{self.name}: unknown type {self.type!r}")


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    effect: str
    arguments: tuple[Argument, ...] = ()
    #: True when calling it twice with the same arguments is harmless. A preset
    #: that is not idempotent is only retried after the executor has asked the
    #: downstream system what happened.
    idempotent: bool = True
    integration: str | None = None

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise PresetError("invalid_effect", f"{self.name}: unknown effect {self.effect!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "effect": self.effect,
            "idempotent": self.idempotent,
            "integration": self.integration,
            "arguments": [
                {
                    "name": arg.name,
                    "type": arg.type,
                    "required": arg.required,
                    "description": arg.description,
                    "default": arg.default,
                }
                for arg in self.arguments
            ],
        }


PRESETS: dict[str, Preset] = {
    "send_sms": Preset(
        "send_sms",
        "Send an SMS to the caller or another number",
        "message",
        (
            Argument("to", "string", True, "E.164 destination", pattern="e164"),
            Argument("body", "string", True, "Message text", maximum=1600),
        ),
        idempotent=False,
        integration="generic_http",
    ),
    "send_email": Preset(
        "send_email",
        "Email a summary or a link",
        "message",
        (
            Argument("to", "string", True, "Recipient", pattern="email"),
            Argument("subject", "string", True, "Subject", maximum=200),
            Argument("body", "string", True, "Plain-text body", maximum=20000),
        ),
        idempotent=False,
    ),
    "book_appointment": Preset(
        "book_appointment",
        "Create a calendar event",
        "calendar",
        (
            Argument("start", "string", True, "ISO start time", pattern="iso_datetime"),
            Argument("end", "string", True, "ISO end time", pattern="iso_datetime"),
            Argument("title", "string", True, "Event title", maximum=200),
            Argument("attendee_email", "string", False, "Attendee", pattern="email"),
        ),
        integration="google_calendar",
    ),
    "lookup_contact": Preset(
        "lookup_contact",
        "Find a CRM contact by phone or email",
        "lookup",
        (
            Argument("phone", "string", False, "E.164 phone", pattern="e164"),
            Argument("email", "string", False, "Email address", pattern="email"),
        ),
        integration="hubspot",
    ),
    "create_ticket": Preset(
        "create_ticket",
        "Open a support ticket",
        "crm",
        (
            Argument("subject", "string", True, "Ticket subject", maximum=200),
            Argument("description", "string", True, "Details", maximum=5000),
            Argument("priority", "string", False, "low | normal | high", default="normal"),
        ),
        idempotent=False,
    ),
    "post_slack": Preset(
        "post_slack",
        "Post a message to a Slack channel",
        "message",
        (
            Argument("channel", "string", True, "Channel id or name", maximum=80),
            Argument("text", "string", True, "Message text", maximum=4000),
        ),
        idempotent=False,
        integration="slack",
    ),
    "end_call": Preset(
        "end_call",
        "Hang up the current call",
        "call_control",
        (Argument("reason", "string", False, "Why the call ended", maximum=200),),
        idempotent=False,
    ),
    "transfer_call": Preset(
        "transfer_call",
        "Transfer the caller to a human or another agent",
        "call_control",
        (
            Argument("destination", "string", True, "E.164, extension or SIP URI", maximum=120),
            Argument("style", "string", False, "whisper | three_way", default="whisper"),
        ),
        idempotent=False,
    ),
}


def get(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        raise PresetError("unknown_preset", f"{name!r} is not a preset function") from None


def _check_pattern(name: str, value: str, pattern: str | None) -> None:
    if pattern is None:
        return
    if pattern == "e164" and not E164.match(value):
        raise PresetError("invalid_argument", f"{name} must be an E.164 number")
    if pattern == "email" and not EMAIL.match(value):
        raise PresetError("invalid_argument", f"{name} must be an email address")
    if pattern == "iso_date" and not ISO_DATE.match(value):
        raise PresetError("invalid_argument", f"{name} must be an ISO date (YYYY-MM-DD)")
    if pattern == "iso_datetime" and not ISO_DATETIME.match(value):
        raise PresetError("invalid_argument", f"{name} must be an ISO date-time")


def validate_call(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Coerce and check one preset call. Raises :class:`PresetError` by field."""
    preset = get(name)
    supplied = dict(arguments or {})
    declared = {arg.name for arg in preset.arguments}
    extra = sorted(set(supplied) - declared)
    if extra:
        # An unknown argument is almost always a hallucinated field. Silently
        # dropping it hides the mistake until the vendor ignores it too.
        raise PresetError("unknown_argument", f"{name} does not take {', '.join(extra)}")

    cleaned: dict[str, Any] = {}
    for argument in preset.arguments:
        value = supplied.get(argument.name, argument.default)
        if value is None or value == "":
            if argument.required:
                raise PresetError("missing_argument", f"{name}.{argument.name} is required")
            continue
        if argument.type == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise PresetError(
                    "invalid_argument", f"{name}.{argument.name} must be an integer"
                ) from None
        elif argument.type == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise PresetError(
                    "invalid_argument", f"{name}.{argument.name} must be a number"
                ) from None
        elif argument.type == "boolean":
            value = bool(value)
        elif argument.type == "string":
            value = str(value)
            if argument.maximum is not None and len(value) > argument.maximum:
                raise PresetError(
                    "invalid_argument",
                    f"{name}.{argument.name} is longer than {argument.maximum} characters",
                )
            _check_pattern(argument.name, value, argument.pattern)
        cleaned[argument.name] = value

    if name == "lookup_contact" and not (cleaned.get("phone") or cleaned.get("email")):
        raise PresetError("missing_argument", "lookup_contact needs a phone or an email")
    if name == "book_appointment" and cleaned.get("start", "") >= cleaned.get("end", ""):
        raise PresetError("invalid_argument", "an appointment must end after it starts")
    return cleaned


def list_presets(*, effect: str | None = None) -> list[dict[str, Any]]:
    rows = [preset.as_dict() for preset in PRESETS.values()]
    if effect is not None:
        rows = [row for row in rows if row["effect"] == effect]
    return rows


def retry_safe(names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split a step list into "may retry blindly" and "must reconcile first"."""
    safe: list[str] = []
    unsafe: list[str] = []
    for name in names:
        (safe if get(name).idempotent else unsafe).append(name)
    return safe, unsafe
