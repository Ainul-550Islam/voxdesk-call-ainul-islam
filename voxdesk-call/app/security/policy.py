"""Conductor AI Control Plane Security & Policy Enforcement.

Enforces the core Conductor invariants:
1. Conductor may propose; the human must approve; only approved changes may create a new AgentVersion.
2. Never auto-publish or auto-deploy to production.
3. Strict allowlist of operations (`set`, `replace`, `add`, `remove`, `append`, `delete`, `move`).
4. Strict allowlist of mutable configuration paths.
5. Reject shell commands, SQL, Python, JavaScript, filesystem paths, secrets/credentials,
   cross-tenant/environment ownership mutations, and prompt-injection bypass payloads.
6. Enforce RBAC action permissions (`view_conductor`, `create_proposal`, `approve_proposal`,
   `reject_proposal`, `apply_proposal`).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from app.db.models import UserRole
from app.auth.dependencies import TenantContext
from app.auth.permissions import Permission
from app.auth.rbac import permissions_for
from app.core.errors import BadRequestError, PermissionDeniedError


class ConductorAction(str, Enum):
    VIEW_CONDUCTOR = "view_conductor"
    CREATE_PROPOSAL = "create_proposal"
    APPROVE_PROPOSAL = "approve_proposal"
    REJECT_PROPOSAL = "reject_proposal"
    APPLY_PROPOSAL = "apply_proposal"


ACTION_PERMISSION_MAP: dict[ConductorAction, Permission] = {
    ConductorAction.VIEW_CONDUCTOR: Permission.TENANT_READ,
    ConductorAction.CREATE_PROPOSAL: Permission.TENANT_UPDATE,
    ConductorAction.APPROVE_PROPOSAL: Permission.TENANT_UPDATE,
    ConductorAction.REJECT_PROPOSAL: Permission.TENANT_UPDATE,
    ConductorAction.APPLY_PROPOSAL: Permission.TENANT_UPDATE,
}

ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "set",
        "replace",
        "add",
        "remove",
        "append",
        "delete",
        "move",
    }
)

# Exact top-level or prefix paths that Conductor proposals are permitted to mutate
ALLOWED_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "greeting",
        "welcome_message",
        "system_prompt",
        "voice",
        "voice_id",
        "voice_provider",
        "voice_speed",
        "voice_temperature",
        "voice_volume",
        "llm_provider",
        "llm_model",
        "temperature",
        "max_tokens",
        "language",
        "fallback_language",
        "interruption_sensitivity",
        "responsiveness",
        "backchannel_enabled",
        "backchannel_frequency",
        "backchannel_words",
        "end_call_after_silence_ms",
        "max_call_duration_s",
        "reminder_trigger_ms",
        "reminder_max_count",
        "ambient_sound",
        "ambient_sound_volume",
        "voicemail_detection_enabled",
        "voicemail_message",
        "boosted_keywords",
        "tools",
        "knowledge_base_ids",
        "workflow_ids",
        "handoff_number",
    }
)

ALLOWED_PREFIX_PATHS: tuple[str, ...] = (
    "voice_config.",
    "llm_config.",
    "prompt.",
    "conversation.",
    "guardrails.",
    "handoff.",
    "transfer.",
    "flow.",
    "nodes.",
    "metadata.",
    "dynamic_variables.",
    "pronunciation_dictionary.",
    "post_call_analysis.",
)

FORBIDDEN_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "organization_id",
        "environment_id",
        "status",
        "lifecycle_status",
        "active_version_id",
        "active_version_number",
        "published",
        "production",
        "deploy",
        "auto_publish",
        "auto_deploy",
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
        "passwd",
        "private_key",
        "access_token",
        "refresh_token",
        "bearer",
        "client_secret",
        "webhook_secret",
        "credentials",
        "__proto__",
        "constructor",
        "prototype",
    }
)

_PATH_FORMAT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\[\]-]{0,180}$")

# Reject secret-shaped strings
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
)

# Reject code execution / shell / SQL / JS / filesystem traversal payloads
_CODE_EXECUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "shell_command",
        re.compile(
            r"(?:;\s*(?:rm|curl|wget|bash|sh|nc|chmod|chown)\s+-|\$\([^)]+\)|`[^`]+`|\b(?:rm\s+-rf|curl\s+http|wget\s+http|/bin/sh|/bin/bash)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "sql_statement",
        re.compile(
            r"\b(?:DROP\s+TABLE|ALTER\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+FROM\s+\w+|INSERT\s+INTO\s+\w+\s+VALUES|UNION\s+SELECT)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "python_exec",
        re.compile(
            r"\b(?:__import__\s*\(|os\.system\s*\(|subprocess\.(?:Popen|run|call)\s*\(|eval\s*\(|exec\s*\()",
            re.IGNORECASE,
        ),
    ),
    (
        "javascript_xss",
        re.compile(
            r"(?:<\s*script\b|javascript\s*:|onerror\s*=|onload\s*=|document\.cookie|window\.location)",
            re.IGNORECASE,
        ),
    ),
    (
        "filesystem_path",
        re.compile(
            r"(?:\.\./\.\./|^/etc/passwd|^/proc/self|^/var/run|^C:\\Windows\\System32)",
            re.IGNORECASE,
        ),
    ),
)

_PROMPT_INJECTION_BYPASS_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+(?:security\s+|system\s+)?instructions\s+and\s+(?:deploy|publish|execute|override|reveal|dump)|"
    r"reveal\s+(?:all\s+|tenant\s+)?secrets|"
    r"auto[\s_-]*deploy\s+to\s+production\s+without\s+approval|"
    r"bypass\s+(?:human\s+)?approval)",
    re.IGNORECASE,
)

MAX_PROPOSAL_CHANGES = 25
MAX_STRING_VALUE_LENGTH = 16000
MAX_LIST_VALUE_LENGTH = 100
MAX_OBJECT_KEYS = 64


def enforce_conductor_permission(
    ctx: TenantContext,
    action: ConductorAction | str,
) -> None:
    """Verify the authenticated caller holds the required RBAC permission for a Conductor action."""
    act = (
        action
        if isinstance(action, ConductorAction)
        else ConductorAction(str(action))
    )
    required_perm = ACTION_PERMISSION_MAP[act]
    raw_role = getattr(ctx.user, "role", None) or getattr(ctx, "role", UserRole.VIEWER)
    try:
        role_enum = (
            raw_role
            if isinstance(raw_role, UserRole)
            else UserRole(str(getattr(raw_role, "value", raw_role)).lower())
        )
    except ValueError:
        role_enum = UserRole.VIEWER
    granted = permissions_for(role_enum)
    if required_perm not in granted:
        raise PermissionDeniedError(
            f"Caller role '{role_enum.value}' is not permitted to perform Conductor action '{act.value}' (requires '{required_perm.value}')."
        )


def validate_mutation_operation(operation: str) -> str:
    """Validate that an operation is in the strict Conductor allowlist."""
    op_norm = str(operation or "").strip().lower()
    if op_norm not in ALLOWED_OPERATIONS:
        raise BadRequestError(
            f"Unsupported Conductor operation {operation!r}. Allowed operations: {sorted(ALLOWED_OPERATIONS)}."
        )
    return op_norm


def classify_path_section(path: str) -> str:
    """Group a configuration path into a canonical studio section for review & diffing."""
    clean = str(path or "").strip()
    if clean in {"greeting", "welcome_message", "system_prompt"} or clean.startswith("prompt."):
        return "prompt"
    if clean.startswith("voice") or clean.startswith("pronunciation_dictionary.") or clean in {"ambient_sound", "ambient_sound_volume"}:
        return "voice"
    if clean.startswith("llm") or clean in {"temperature", "max_tokens"}:
        return "model"
    if clean in {"language", "fallback_language", "boosted_keywords"} or clean.startswith("conversation.") or clean in {
        "interruption_sensitivity",
        "responsiveness",
        "backchannel_enabled",
        "backchannel_frequency",
        "backchannel_words",
        "end_call_after_silence_ms",
        "max_call_duration_s",
        "reminder_trigger_ms",
        "reminder_max_count",
        "voicemail_detection_enabled",
        "voicemail_message",
    }:
        return "conversation"
    if clean == "tools" or clean.startswith("tools.") or clean.startswith("tools["):
        return "tools"
    if clean == "knowledge_base_ids" or clean.startswith("knowledge_base"):
        return "knowledge"
    if clean == "workflow_ids" or clean.startswith("workflow") or clean.startswith("flow.") or clean.startswith("nodes."):
        return "workflow"
    if clean.startswith("guardrails."):
        return "guardrails"
    if clean == "handoff_number" or clean.startswith("handoff.") or clean.startswith("transfer."):
        return "transfer"
    if clean.startswith("dynamic_variables."):
        return "variables"
    if clean.startswith("metadata.") or clean in {"name", "description"}:
        return "metadata"
    return "general"


def classify_change_risk(path: str, operation: str, new_value: Any) -> str:
    """Assign a deterministic risk level (`low`, `medium`, `high`) to a proposed mutation."""
    section = classify_path_section(path)
    op = str(operation or "").lower()
    if section in {"transfer", "guardrails", "tools", "workflow"}:
        return "high" if op in {"delete", "remove", "replace"} else "medium"
    if path in {"llm_provider", "llm_model", "system_prompt"}:
        return "medium"
    if op in {"delete", "remove"}:
        return "medium"
    return "low"


def validate_mutation_path(path: str) -> str:
    """Validate that a configuration path is well-formed and on the mutable allowlist."""
    clean = str(path or "").strip()
    if not clean or not _PATH_FORMAT_RE.match(clean):
        raise BadRequestError(
            f"Invalid or malformed configuration path {path!r}."
        )
    if ".." in clean or clean.startswith(".") or clean.endswith("."):
        raise BadRequestError(
            f"Path traversal or relative path syntax is forbidden in {path!r}."
        )

    parts = [
        re.sub(r"\[\d+\]$", "", seg).lower()
        for seg in clean.split(".")
        if seg
    ]
    for token in parts:
        if token in FORBIDDEN_PATH_TOKENS:
            raise BadRequestError(
                f"Path {path!r} targets protected or forbidden field {token!r}."
            )

    if clean in ALLOWED_EXACT_PATHS:
        return clean
    if any(clean.startswith(prefix) for prefix in ALLOWED_PREFIX_PATHS):
        return clean

    # Also allow indexed array paths for allowed list fields like tools[0]
    root_name = re.sub(r"\[\d+\]$", "", clean.split(".")[0])
    if root_name in {"tools", "knowledge_base_ids", "workflow_ids", "boosted_keywords", "backchannel_words"}:
        return clean

    raise BadRequestError(
        f"Configuration path {path!r} is not on the Conductor mutable path allowlist."
    )


def validate_value_safety(value: Any, *, path: str = "", depth: int = 0) -> None:
    """Recursively inspect a proposed value for secrets, code execution, or unbounded structures."""
    if depth > 6:
        raise BadRequestError(
            f"Proposed value at {path or '<root>'} exceeds maximum nesting depth (6)."
        )

    if value is None or isinstance(value, (bool, int, float)):
        return

    if isinstance(value, str):
        if len(value) > MAX_STRING_VALUE_LENGTH:
            raise BadRequestError(
                f"Proposed string value at {path or '<root>'} exceeds maximum length ({MAX_STRING_VALUE_LENGTH})."
            )
        for pat in _SECRET_VALUE_PATTERNS:
            if pat.search(value):
                raise BadRequestError(
                    f"Proposed value at {path or '<root>'} contains a secret or credential-shaped token."
                )
        for kind, pat in _CODE_EXECUTION_PATTERNS:
            if pat.search(value):
                raise BadRequestError(
                    f"Proposed value at {path or '<root>'} contains forbidden {kind} payload."
                )
        if _PROMPT_INJECTION_BYPASS_RE.search(value):
            raise BadRequestError(
                f"Proposed value at {path or '<root>'} contains a forbidden prompt-injection or security-bypass directive."
            )
        return

    if isinstance(value, list):
        if len(value) > MAX_LIST_VALUE_LENGTH:
            raise BadRequestError(
                f"Proposed array at {path or '<root>'} exceeds maximum length ({MAX_LIST_VALUE_LENGTH})."
            )
        for idx, item in enumerate(value):
            validate_value_safety(item, path=f"{path}[{idx}]", depth=depth + 1)
        return

    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise BadRequestError(
                f"Proposed object at {path or '<root>'} exceeds maximum key count ({MAX_OBJECT_KEYS})."
            )
        for k, v in value.items():
            key_str = str(k).strip().lower()
            if key_str in FORBIDDEN_PATH_TOKENS or any(
                tok in key_str for tok in ("api_key", "secret", "password", "private_key", "access_token")
            ):
                raise BadRequestError(
                    f"Proposed object key {k!r} at {path or '<root>'} is a forbidden credential or ownership key."
                )
            validate_value_safety(v, path=f"{path}.{k}" if path else str(k), depth=depth + 1)
        return

    raise BadRequestError(
        f"Unsupported value type {type(value).__name__} at {path or '<root>'}."
    )


def validate_natural_language_request(
    request_text: str,
    *,
    auto_deploy: bool = False,
    auto_publish: bool = False,
) -> str:
    """Validate a user request to Conductor, blocking auto-production flags and code/secret payloads."""
    if auto_deploy or auto_publish:
        raise BadRequestError(
            "Conductor never auto-deploys or auto-publishes to production. Create and approve a version first, then use the explicit publish endpoint."
        )
    text = str(request_text or "").strip()
    if not text:
        raise BadRequestError("Conductor request text must not be empty.")
    if len(text) > 8000:
        raise BadRequestError("Conductor request text exceeds maximum length of 8000 characters.")

    if _PROMPT_INJECTION_BYPASS_RE.search(text):
        raise BadRequestError(
            "Request rejected by Conductor safety policy: prompt-injection attempt to bypass human approval or auto-deploy to production."
        )
    validate_value_safety(text, path="request_text")
    return text
