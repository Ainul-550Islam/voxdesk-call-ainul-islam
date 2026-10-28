"""DNS lookups and hostname validation for white-label custom domains.

Two jobs, both of which are security decisions rather than convenience:

**Validation.** A tenant supplies a hostname. Before it becomes a row it has to be
a hostname at all: lower-case, dot-separated labels, no scheme, no port, no path,
no wildcard, no IP literal, no underscore, and not a name we already serve. A
permissive validator here becomes a certificate for somebody else's domain.

**Resolution.** Ownership is proven two ways and routing is proven separately:

* a ``TXT`` record at ``_voxdesk-challenge.<domain>`` containing the domain's
  ownership token (the token is stored as a SHA-256 digest, so a database leak
  does not hand out the ability to claim domains);
* a ``CNAME`` from the domain to the configured edge target, **or** an ``A``/``AAAA``
  record that already points at the edge.

Both are checked on every verification, and every check is stored with the
resolvers it was run against and the values that were observed — so "verification
failed" is never a mystery to the tenant.

Every lookup is bounded by ``DNS_CHECK_TIMEOUT_SECONDS``. A resolver outage is
reported as a failed check with the error text; it never propagates out of the
API as a 500, because a DNS outage is not the tenant's fault and not our crash.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.platform_v2.errors import ValidationFailed

#: RFC 1035 label: 1-63 chars, alphanumeric at both ends, hyphens inside. The
#: underscore form is deliberately absent: `_` is legal in a TXT owner name but
#: not in a hostname we would ever issue a certificate for.
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

MAX_DOMAIN_LENGTH = 253
MAX_LABEL_LENGTH = 63

#: Prefixes a tenant may put in front of the token in the TXT record. We ask for
#: the bare token (it is what the UI shows and what fits in a 255-byte record),
#: but a prefixed record is the same proof and refusing it would only generate
#: support tickets.
TOKEN_PREFIXES = ("voxdesk-verify=", "voxdesk-domain-verification=", "voxdesk=")

#: Records whose owner name our own platform already uses.
RESERVED_SUFFIXES = ("localhost", "local", "internal", "invalid", "example")

#: Public suffixes we refuse as a *whole* domain, because claiming `co.uk` or
#: `com` would be a request for a certificate no registry will ever issue and an
#: `ask` hook that must never say yes.
_PUBLIC_SUFFIX_BLOCKLIST = frozenset(
    {
        "com", "net", "org", "io", "co", "uk", "us", "de", "fr", "in", "ai", "app",
        "dev", "cloud", "co.uk", "com.au", "co.in", "com.br", "co.jp",
    }
)


def normalize_domain(raw: str) -> str:
    """Return the canonical form of a hostname or raise ``ValidationFailed``.

    Canonical means: lower-case, no trailing dot, no scheme, no port, no path.
    The error messages name the actual problem, because a tenant reading
    "invalid domain" learns nothing and files a support ticket.
    """
    if not isinstance(raw, str):
        raise ValidationFailed("domain must be a string")
    candidate = raw.strip().lower()
    if not candidate:
        raise ValidationFailed("domain must not be empty")
    if "://" in candidate:
        raise ValidationFailed("domain must not include a scheme; supply the hostname only")
    if "/" in candidate or "?" in candidate or "#" in candidate:
        raise ValidationFailed("domain must not include a path, query or fragment")
    if "@" in candidate:
        raise ValidationFailed("domain must be a hostname, not an email address")
    if ":" in candidate:
        raise ValidationFailed("domain must not include a port")
    if candidate.startswith("*."):
        raise ValidationFailed("wildcard domains cannot be claimed; claim each hostname")
    if candidate.endswith("."):
        candidate = candidate[:-1]
    if len(candidate) > MAX_DOMAIN_LENGTH:
        raise ValidationFailed(f"domain must be at most {MAX_DOMAIN_LENGTH} characters")

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise ValidationFailed("an IP address cannot be a custom domain")

    labels = candidate.split(".")
    if len(labels) < 2:
        raise ValidationFailed("domain must contain at least one dot")
    for label in labels:
        if not label:
            raise ValidationFailed("domain must not contain empty labels")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValidationFailed(f"each label must be at most {MAX_LABEL_LENGTH} characters")
        if "_" in label:
            raise ValidationFailed("domain labels must not contain underscores")
        if not _LABEL_RE.match(label):
            raise ValidationFailed(
                "domain labels must start and end with a letter or digit and contain only letters, digits and hyphens"
            )

    if candidate in _PUBLIC_SUFFIX_BLOCKLIST:
        raise ValidationFailed("domain must be a fully qualified hostname, not a public suffix")
    return candidate


def reject_platform_domain(domain: str, platform_hosts: list[str]) -> None:
    """Refuse to white-label a hostname the platform already answers on."""
    for host in platform_hosts:
        if not host:
            continue
        candidate = host.strip().lower()
        if domain == candidate or domain.endswith("." + candidate):
            raise ValidationFailed(
                "domain belongs to the platform itself and cannot be claimed as a custom domain"
            )


def challenge_name(domain: str) -> str:
    """Owner name of the TXT record that proves control of ``domain``."""
    return f"_voxdesk-challenge.{domain}"


def new_token(length: int = 40) -> str:
    """A fresh ownership token.

    ``secrets.choice`` over an explicit alphabet, not ``token_urlsafe``, so the
    token is safe inside a TXT record on every resolver (no ``-``/``_`` padding
    surprises) and has a predictable length for the UI to display.
    """
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def token_digest(token: str) -> str:
    """Store this, never the token itself."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def digest_matches(token: str, stored_digest: str) -> bool:
    return secrets.compare_digest(token_digest(token), stored_digest)


@dataclass(frozen=True)
class Resolution:
    """Outcome of one DNS query.

    ``error`` set means we could not ask, which is different from "asked and the
    answer was wrong": the first is retryable, the second needs the tenant to
    change a record. The UI shows them differently and so does the check log.
    """

    name: str
    record_type: str
    values: tuple[str, ...] = ()
    resolver: str = ""
    error: str = ""
    asked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def answered(self) -> bool:
        return not self.error

    @property
    def found(self) -> bool:
        return self.answered and bool(self.values)

    def as_detail(self) -> dict[str, object]:
        return {
            "name": self.name,
            "record_type": self.record_type,
            "values": list(self.values),
            "resolver": self.resolver,
            "error": self.error,
        }


def _client(nameserver: str | None = None):
    """An async resolver, or ``None`` when dnspython is not installed.

    Absent dnspython is a deployment error, not a tenant error: the check fails
    with an explicit message instead of silently passing.
    """
    try:
        import dns.asyncresolver
    except Exception:  # pragma: no cover - only when the dependency is missing
        return None
    resolver = dns.asyncresolver.Resolver()
    if nameserver:
        resolver.nameservers = [nameserver]
    return resolver


async def _query(
    name: str,
    record_type: str,
    *,
    timeout: float,
    resolver: Any | None = None,
    nameserver: str | None = None,
) -> Resolution:
    client = resolver if resolver is not None else _client(nameserver)
    if client is None:
        return Resolution(
            name=name,
            record_type=record_type,
            error="dns resolver unavailable: dnspython is not installed",
        )
    try:
        answer = await client.resolve(name, record_type, lifetime=timeout)
    except Exception as exc:  # noqa: BLE001 - every resolver error is a failed check
        # NXDOMAIN and "no answer" are the common cases and are not crashes; a
        # timeout is reported with its own text. All of them mean "not proven".
        return Resolution(name=name, record_type=record_type, error=f"{type(exc).__name__}: {exc}")

    values: list[str] = []
    for record in answer:
        if record_type == "TXT":
            chunks = getattr(record, "strings", ())
            if chunks:
                values.append(
                    b"".join(chunks).decode("utf-8", "replace") if isinstance(chunks[0], bytes) else "".join(chunks)
                )
            else:
                values.append(str(record).strip('"'))
        else:
            values.append(str(record).rstrip(".").lower())
    return Resolution(
        name=name,
        record_type=record_type,
        values=tuple(values),
        resolver=getattr(client, "nameservers", "") and ",".join(getattr(client, "nameservers")) or "",
    )


async def resolve_cname(domain: str, *, timeout: float = 5.0, resolver: Any | None = None) -> Resolution:
    return await _query(domain, "CNAME", timeout=timeout, resolver=resolver)


async def resolve_txt(name: str, *, timeout: float = 5.0, resolver: Any | None = None) -> Resolution:
    return await _query(name, "TXT", timeout=timeout, resolver=resolver)


async def resolve_a(domain: str, *, timeout: float = 5.0, resolver: Any | None = None) -> Resolution:
    return await _query(domain, "A", timeout=timeout, resolver=resolver)


async def resolve_aaaa(domain: str, *, timeout: float = 5.0, resolver: Any | None = None) -> Resolution:
    return await _query(domain, "AAAA", timeout=timeout, resolver=resolver)


def cname_points_at(resolution: Resolution, target: str) -> bool:
    """True when the CNAME answer is the target (case and trailing dot ignored)."""
    wanted = target.strip().lower().rstrip(".")
    return any(value.strip().lower().rstrip(".") == wanted for value in resolution.values)


def txt_contains(resolution: Resolution, token: str) -> bool:
    """True when a TXT answer carries the ownership token.

    Accepted forms, all equivalent proof of control: the bare token, or the token
    behind one of ``TOKEN_PREFIXES``. Comparison is constant-time and happens on
    the token itself, never on the whole record, so a record that merely *starts*
    with the token does not pass.
    """
    if not token:
        return False
    for value in resolution.values:
        for candidate in value.replace('"', " ").split():
            candidate = candidate.strip().strip(",")
            if secrets.compare_digest(candidate, token):
                return True
            for prefix in TOKEN_PREFIXES:
                if candidate.startswith(prefix) and secrets.compare_digest(candidate[len(prefix) :], token):
                    return True
    return False


def expected_txt_value(token: str) -> str:
    """The one record value the UI tells the tenant to create."""
    return token


def tls_ask_authorised(provided: str | None, expected: str) -> bool:
    """Constant-time check of the edge's ask token.

    Empty ``expected`` means the token is not configured; the caller decides what
    that means (the API refuses to serve ``ask`` at all in that case, because an
    unauthenticated enumeration endpoint is worse than a missing feature).
    """
    if not expected:
        return False
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


__all__ = [
    "MAX_DOMAIN_LENGTH",
    "MAX_LABEL_LENGTH",
    "RESERVED_SUFFIXES",
    "Resolution",
    "TOKEN_PREFIXES",
    "challenge_name",
    "cname_points_at",
    "digest_matches",
    "expected_txt_value",
    "new_token",
    "normalize_domain",
    "reject_platform_domain",
    "resolve_a",
    "resolve_aaaa",
    "resolve_cname",
    "resolve_txt",
    "tls_ask_authorised",
    "token_digest",
    "txt_contains",
]
