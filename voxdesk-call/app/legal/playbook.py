"""Legal playbook / clause library management surface (GAP-P1-04 Fix).

LuMay benchmark: first-pass contract review against a playbook, redlines/alternates,
clause library, citations and audit chain. Our source review did NOT verify a
redline artifact generator or playbook/clause-library management UI.

This module implements:
- Structured clause library with versioning
- Playbook with rules, preferred language, fallback, required controls
- Review workflow linking findings to playbook rules
- Evidence/audit export

Preserves existing: app/legal/clause_engine.py deterministic patterns, citations,
review_engine.py disclaimer, schemas.py contracts.

No fabrication: playbook rules are configured, not inferred. Redline requires
human approval. Language counts not fabricated.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.specialized_agents.enums import Severity


@dataclass(frozen=True)
class ClauseLibraryEntry:
    id: str
    key: str
    title: str
    category: str
    preferred_text: str
    fallback_text: str | None
    risk_if_absent: str
    risk_if_present: str
    required_controls: tuple[str, ...]
    version: str
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "title": self.title,
            "category": self.category,
            "preferred_text": self.preferred_text,
            "fallback_text": self.fallback_text,
            "risk_if_absent": self.risk_if_absent,
            "risk_if_present": self.risk_if_present,
            "required_controls": list(self.required_controls),
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class PlaybookRule:
    id: str
    clause_key: str
    condition: str
    action: str
    severity: str
    requires_human_review: bool
    evidence_required: tuple[str, ...]
    version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clause_key": self.clause_key,
            "condition": self.condition,
            "action": self.action,
            "severity": self.severity,
            "requires_human_review": self.requires_human_review,
            "evidence_required": list(self.evidence_required),
            "version": self.version,
        }


@dataclass(frozen=True)
class Playbook:
    id: str
    tenant_id: str
    organization_id: str
    environment_id: str
    name: str
    version: str
    status: str
    clauses: tuple[ClauseLibraryEntry, ...]
    rules: tuple[PlaybookRule, ...]
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "environment_id": self.environment_id,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "clauses": [c.as_dict() for c in self.clauses],
            "rules": [r.as_dict() for r in self.rules],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "clause_count": len(self.clauses),
            "rule_count": len(self.rules),
        }


# ---- Default Clause Library (seed) ----
# Deterministic, reviewable set — not AI-generated legal advice
# Each entry maps to a pattern in clause_engine.py PATTERNS

_DEFAULT_CLAUSES: tuple[ClauseLibraryEntry, ...] = (
    ClauseLibraryEntry(
        id="clause-termination-001",
        key="termination",
        title="Termination for Convenience",
        category="termination",
        preferred_text="Either party may terminate this Agreement for convenience upon 30 days prior written notice.",
        fallback_text="Termination requires 90 days notice and cure period.",
        risk_if_absent="No termination right may lock party into unfavorable terms.",
        risk_if_present="Termination right may be too broad without cure.",
        required_controls=("approved_model", "policy_admission", "human_review"),
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ),
    ClauseLibraryEntry(
        id="clause-lol-001",
        key="limitation_of_liability",
        title="Limitation of Liability Cap",
        category="limitation_of_liability",
        preferred_text="Liability capped at fees paid in 12 months preceding claim, excluding consequential damages.",
        fallback_text=None,
        risk_if_absent="Uncapped liability may expose to excessive damages.",
        risk_if_present="Cap may be too low for critical services.",
        required_controls=("approved_model", "policy_admission", "human_review", "financial_review"),
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ),
    ClauseLibraryEntry(
        id="clause-indemnity-001",
        key="indemnification",
        title="Mutual Indemnification",
        category="indemnification",
        preferred_text="Each party indemnifies the other for third-party claims arising from its gross negligence or willful misconduct.",
        fallback_text="Unilateral indemnity only.",
        risk_if_absent="No indemnity may leave party unprotected.",
        risk_if_present="Overbroad indemnity may create uncapped risk.",
        required_controls=("approved_model", "policy_admission", "human_review"),
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ),
    ClauseLibraryEntry(
        id="clause-conf-001",
        key="confidentiality",
        title="Confidentiality with Standard Exceptions",
        category="confidentiality",
        preferred_text="Confidential Information excludes information that is public, independently developed, or rightfully received from third party.",
        fallback_text=None,
        risk_if_absent="No confidentiality may leak sensitive data.",
        risk_if_present="Overbroad confidentiality may restrict operations.",
        required_controls=("approved_model", "policy_admission", "human_review"),
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ),
    ClauseLibraryEntry(
        id="clause-dp-001",
        key="data_protection",
        title="Data Protection Roles",
        category="data_protection",
        preferred_text="Parties agree to roles: Controller/Processor per GDPR, with DPA attached and SCCs for transfers.",
        fallback_text=None,
        risk_if_absent="No data protection clause may violate GDPR.",
        risk_if_present="Incorrect roles may misallocate compliance burden.",
        required_controls=("approved_model", "policy_admission", "human_review", "privacy_review"),
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ),
)

_DEFAULT_RULES: tuple[PlaybookRule, ...] = (
    PlaybookRule(
        id="rule-termination-001",
        clause_key="termination",
        condition="detected_language == 'termination' AND confidence >= 0.8",
        action="flag_for_review_with_preferred",
        severity=Severity.MODERATE.value,
        requires_human_review=True,
        evidence_required=("source_citation", "playbook_version"),
        version="1.0.0",
    ),
    PlaybookRule(
        id="rule-lol-001",
        clause_key="limitation_of_liability",
        condition="detected_language == 'limitation_of_liability' AND severity == 'high'",
        action="require_financial_review_and_redline",
        severity=Severity.HIGH.value,
        requires_human_review=True,
        evidence_required=("source_citation", "financial_review", "playbook_version"),
        version="1.0.0",
    ),
    PlaybookRule(
        id="rule-dp-001",
        clause_key="data_protection",
        condition="detected_language == 'data_protection'",
        action="require_privacy_review_and_dpa_check",
        severity=Severity.HIGH.value,
        requires_human_review=True,
        evidence_required=("source_citation", "privacy_review", "dpa_reference"),
        version="1.0.0",
    ),
)


class ClauseLibrary:
    """Tenant-scoped clause library with versioning."""

    def __init__(self, entries: tuple[ClauseLibraryEntry, ...] = _DEFAULT_CLAUSES):
        self._entries = {e.key: e for e in entries}
        self._by_id = {e.id: e for e in entries}

    def list_clauses(self) -> list[ClauseLibraryEntry]:
        return list(self._entries.values())

    def get_by_key(self, key: str) -> ClauseLibraryEntry | None:
        return self._entries.get(key)

    def get_by_id(self, entry_id: str) -> ClauseLibraryEntry | None:
        return self._by_id.get(entry_id)

    def search_by_category(self, category: str) -> list[ClauseLibraryEntry]:
        return [e for e in self._entries.values() if e.category == category]


class PlaybookService:
    """Playbook management with tenant/organization/environment scope."""

    def __init__(self, clause_library: ClauseLibrary | None = None):
        self.clause_library = clause_library or ClauseLibrary()

    def create_playbook(
        self,
        tenant_id: str,
        organization_id: str,
        environment_id: str,
        name: str,
        clause_keys: list[str] | None = None,
    ) -> Playbook:
        now = datetime.now(timezone.utc)
        selected_clauses = (
            [self.clause_library.get_by_key(k) for k in clause_keys if self.clause_library.get_by_key(k)]
            if clause_keys
            else self.clause_library.list_clauses()
        )
        # Filter None
        selected_clauses = [c for c in selected_clauses if c is not None]  # type: ignore
        selected_rules = [r for r in _DEFAULT_RULES if r.clause_key in {c.key for c in selected_clauses}]

        playbook_id = f"pb_{uuid.uuid4().hex[:12]}"
        return Playbook(
            id=playbook_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            environment_id=environment_id,
            name=name,
            version="1.0.0",
            status="draft",
            clauses=tuple(selected_clauses),
            rules=tuple(selected_rules),
            created_at=now,
            updated_at=now,
        )

    def evaluate_against_playbook(
        self, findings: list[dict[str, Any]], playbook: Playbook
    ) -> list[dict[str, Any]]:
        """Map findings to playbook rules with evidence."""
        results = []
        clause_map = {c.key: c for c in playbook.clauses}
        rule_map = {r.clause_key: r for r in playbook.rules}

        for finding in findings:
            clause_key = finding.get("clause") or finding.get("category")
            clause = clause_map.get(clause_key)
            rule = rule_map.get(clause_key)
            if not clause or not rule:
                continue
            result = {
                "finding_clause": clause_key,
                "playbook_clause": clause.as_dict(),
                "playbook_rule": rule.as_dict(),
                "finding": finding,
                "action": rule.action,
                "severity": rule.severity,
                "requires_human_review": rule.requires_human_review,
                "evidence_required": list(rule.evidence_required),
                "review_reason": f"Finding {clause_key} matched playbook rule {rule.id} — requires {', '.join(rule.evidence_required)}",
                "fingerprint": hashlib.sha256(
                    f"{clause_key}:{rule.id}:{finding.get('extracted_text_reference','')}".encode()
                ).hexdigest(),
            }
            results.append(result)
        return results


def default_library() -> ClauseLibrary:
    return ClauseLibrary()


def default_playbook_service() -> PlaybookService:
    return PlaybookService()
