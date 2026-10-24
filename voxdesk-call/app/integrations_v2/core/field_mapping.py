"""Bidirectional field-mapping & pure transform engine with dry-run preview (Batch 05)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.integrations_v2.core.errors import IntegrationV2Error

SUPPORTED_TRANSFORMS = frozenset(
    {
        "identity",
        "e164_phone",
        "lowercase_email",
        "iso8601_ts",
        "enum_map",
        "concat",
        "split_name",
        "default_if_null",
        "json_extract",
    }
)

SUPPORTED_DIRECTIONS = frozenset({"inbound", "outbound", "bidirectional"})
SUPPORTED_ENTITIES = frozenset({"Contact", "Lead", "Deal", "Ticket", "CallActivity"})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class MappingRuleSpec:
    entity_type: str
    voxdesk_field: str
    external_field: str
    direction: str = "bidirectional"
    transform: str = "identity"
    transform_config: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    default_value: str | None = None


@dataclass
class MappingPreviewResult:
    output: dict[str, Any]
    warnings: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _extract_dotted(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _assign_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for seg in parts[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[parts[-1]] = value


def normalize_e164(raw: Any, default_country_code: str = "+1") -> str:
    """Normalize phone strings into strict E.164 (``+<digits>``, 8..15 digits)."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    has_plus = text.startswith("+")
    digits = re.sub(r"\D+", "", text)
    if not digits:
        raise ValueError(f"Phone value {raw!r} contains no digits")
    if has_plus:
        candidate = f"+{digits}"
    elif len(digits) == 10 and default_country_code == "+1":
        candidate = f"+1{digits}"
    elif text.startswith("00") and len(digits) > 2:
        candidate = f"+{digits[2:]}"
    else:
        cc = default_country_code if default_country_code.startswith("+") else f"+{default_country_code}"
        candidate = f"{cc}{digits}"

    digit_count = len(candidate) - 1
    if digit_count < 7 or digit_count > 15:
        raise ValueError(f"Phone {candidate!r} outside E.164 length bounds (7..15 digits)")
    return candidate


def apply_transform(
    transform: str,
    raw_value: Any,
    *,
    source_record: dict[str, Any],
    config: dict[str, Any] | None = None,
    default_value: str | None = None,
) -> Any:
    """Pure, deterministic field transform execution."""
    cfg = config or {}
    if transform not in SUPPORTED_TRANSFORMS:
        raise IntegrationV2Error(f"Unsupported field transform {transform!r}")

    if raw_value is None or (isinstance(raw_value, str) and raw_value == ""):
        if transform == "default_if_null":
            return cfg.get("default", default_value)
        if transform not in ("concat", "json_extract"):
            return default_value if default_value is not None else None

    if transform == "identity":
        return raw_value

    if transform == "default_if_null":
        return raw_value if raw_value not in (None, "") else cfg.get("default", default_value)

    if transform == "e164_phone":
        cc = str(cfg.get("default_country_code") or "+1")
        return normalize_e164(raw_value, default_country_code=cc)

    if transform == "lowercase_email":
        cleaned = str(raw_value).strip().lower()
        if not _EMAIL_RE.fullmatch(cleaned):
            raise ValueError(f"Invalid email address {raw_value!r}")
        return cleaned

    if transform == "iso8601_ts":
        if isinstance(raw_value, (int, float)):
            # Epoch seconds or milliseconds
            ts = float(raw_value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if isinstance(raw_value, datetime):
            dt = raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        txt = str(raw_value).strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        parsed = datetime.fromisoformat(txt)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    if transform == "enum_map":
        mapping: dict[str, Any] = dict(cfg.get("mapping") or {})
        key = str(raw_value)
        if key in mapping:
            return mapping[key]
        if "fallback" in cfg:
            return cfg["fallback"]
        raise ValueError(f"Unmapped enum value {key!r}")

    if transform == "concat":
        fields: list[str] = list(cfg.get("fields") or [])
        sep = str(cfg.get("separator", " "))
        pieces = []
        for f_name in fields:
            val = _extract_dotted(source_record, f_name)
            if val not in (None, ""):
                pieces.append(str(val).strip())
        if not pieces and raw_value not in (None, ""):
            pieces.append(str(raw_value).strip())
        return sep.join(pieces) if pieces else (default_value or "")

    if transform == "split_name":
        full = str(raw_value or "").strip()
        parts = full.split(None, 1)
        part_which = str(cfg.get("part") or "both")
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else str(cfg.get("default_last_name") or "")
        if part_which == "first":
            return first
        if part_which == "last":
            return last
        return {"first_name": first, "last_name": last}

    if transform == "json_extract":
        path = str(cfg.get("path") or "")
        if not path:
            return raw_value
        if isinstance(raw_value, dict):
            return _extract_dotted(raw_value, path)
        return _extract_dotted(source_record, path)

    return raw_value


def execute_field_mapping(
    rules: list[MappingRuleSpec],
    payload: dict[str, Any],
    *,
    direction: str,
) -> MappingPreviewResult:
    """Apply ``rules`` to ``payload`` in ``direction`` ('inbound' or 'outbound').

    - ``inbound``: external vendor payload -> canonical VoxDesk payload
    - ``outbound``: canonical VoxDesk payload -> external vendor payload
    """
    if direction not in ("inbound", "outbound"):
        raise IntegrationV2Error(f"Invalid mapping execution direction {direction!r}")

    output: dict[str, Any] = {}
    warnings: list[str] = []
    errors: list[str] = []

    for rule in rules:
        if rule.direction not in ("bidirectional", direction):
            continue
        src_field = rule.external_field if direction == "inbound" else rule.voxdesk_field
        dst_field = rule.voxdesk_field if direction == "inbound" else rule.external_field

        raw_val = _extract_dotted(payload, src_field)
        try:
            transformed = apply_transform(
                rule.transform,
                raw_val,
                source_record=payload,
                config=rule.transform_config,
                default_value=rule.default_value,
            )
        except Exception as exc:
            errors.append(f"{src_field} -> {dst_field} ({rule.transform}): {exc}")
            continue

        if transformed is None or transformed == "":
            if rule.required:
                errors.append(f"Required field {dst_field!r} (from {src_field!r}) is missing")
                continue
            warnings.append(f"Optional field {src_field!r} was empty; skipped {dst_field!r}")
            continue

        _assign_dotted(output, dst_field, transformed)

    return MappingPreviewResult(output=output, warnings=warnings, errors=errors)


DEFAULT_CONTACT_RULES: tuple[MappingRuleSpec, ...] = (
    MappingRuleSpec("Contact", "first_name", "first_name"),
    MappingRuleSpec("Contact", "last_name", "last_name"),
    MappingRuleSpec("Contact", "email", "email", transform="lowercase_email"),
    MappingRuleSpec("Contact", "phone", "phone", transform="e164_phone"),
    MappingRuleSpec("Contact", "company", "company"),
)
