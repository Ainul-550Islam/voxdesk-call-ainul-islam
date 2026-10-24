"""SSRF regression corpus: every outbound sink must refuse these destinations.

The product's guard is ``app.core.ssrf.validate_outbound_url`` /
``validate_resolved_outbound_url``. This module does not re-implement it; it
feeds it a corpus of destinations that have historically broken naive filters
and asserts each one is refused — and that ordinary public destinations are
still accepted, because a guard that rejects everything is not a guard.

Two corpora:

``DANGEROUS_URLS``
    destinations that must raise ``OutboundUrlError``;
``SAFE_URLS``
    ordinary public destinations that must be accepted (the false-positive
    control).

The resolution-based check is exercised separately with a stubbed resolver, so
the test proves the *second* lookup happens without touching DNS — a
rebinding-style answer (public A record, private AAAA) must fail closed.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.core.ssrf import OutboundUrlError, validate_outbound_url

from security_harness.models import ERROR, FAIL, PASS, REVIEW, HarnessReport


@dataclasses.dataclass(frozen=True)
class UrlCase:
    url: str
    reason: str
    require_https: bool = False


#: One entry per bypass family. The comment names the family, so a failure
#: tells the reader which class of bug has reappeared.
DANGEROUS_URLS: tuple[UrlCase, ...] = (
    UrlCase("http://127.0.0.1/admin", "IPv4 loopback"),
    UrlCase("http://127.0.0.1:6379/", "IPv4 loopback, alternate port"),
    UrlCase("http://127.1.2.3/", "loopback range, not just .0.1"),
    UrlCase("http://0.0.0.0/", "unspecified address"),
    UrlCase("http://[::1]/", "IPv6 loopback"),
    UrlCase("http://[::ffff:127.0.0.1]/", "IPv4-mapped IPv6 loopback"),
    UrlCase("http://10.0.0.5/", "RFC 1918 class A"),
    UrlCase("http://172.16.4.4/", "RFC 1918 class B"),
    UrlCase("http://192.168.1.1/", "RFC 1918 class C"),
    UrlCase("http://169.254.169.254/latest/meta-data/", "cloud metadata (AWS/GCP)"),
    UrlCase("http://metadata.google.internal/", "cloud metadata hostname"),
    UrlCase("http://100.100.100.200/latest/meta-data/", "cloud metadata (Alibaba)"),
    UrlCase("http://localhost/", "localhost name"),
    UrlCase("http://app.localhost/", "*.localhost name"),
    UrlCase("http://service.internal/", "internal-only suffix"),
    UrlCase("http://printer.local/", "mDNS suffix"),
    UrlCase("file:///etc/passwd", "non-HTTP scheme"),
    UrlCase("gopher://example.com/", "non-HTTP scheme"),
    UrlCase("ftp://example.com/", "non-HTTP scheme"),
    UrlCase("http://user:pass@example.com/", "embedded credentials"),
    UrlCase("http:///path-without-host", "missing host"),
    UrlCase("", "empty destination"),
    UrlCase("http://example.com/hook", "plain HTTP where HTTPS is required", require_https=True),
    UrlCase("http://[::]/", "IPv6 unspecified"),
    UrlCase("http://198.51.100.7/", "documentation range, non-routable"),
    UrlCase("http://224.0.0.1/", "multicast"),
)

SAFE_URLS: tuple[UrlCase, ...] = (
    UrlCase("https://api.example.com/v1/hook", "ordinary public HTTPS"),
    UrlCase("https://8.8.8.8/", "public IP literal"),
    UrlCase("http://example.com/callback", "ordinary public HTTP where HTTPS is not required"),
)


def _attempt(url: str, *, require_https: bool) -> tuple[bool, str]:
    """Return ``(refused, reason)`` for one destination."""
    try:
        validate_outbound_url(url, require_https=require_https)
        return False, ""
    except OutboundUrlError as exc:
        return True, str(exc)


def run() -> HarnessReport:
    """Run both corpora through the production guard."""
    report = HarnessReport(suite="ssrf")
    accepted: list[dict[str, str]] = []
    for case in DANGEROUS_URLS:
        refused, detail = _attempt(case.url, require_https=case.require_https)
        if not refused:
            accepted.append({"url": case.url, "family": case.reason})
        elif not detail:
            # A refusal without a reason is still a refusal, but an operator
            # needs to know why: record it for review rather than silently pass.
            report.record(
                "ssrf_probe.reason_missing",
                REVIEW,
                f"refused without an explanatory message: {case.url}",
            )
    if accepted:
        report.record(
            "ssrf_probe.blocked_destinations",
            FAIL,
            f"{len(accepted)} destination(s) were accepted by the outbound guard",
            evidence={"accepted": accepted},
        )
    else:
        report.record(
            "ssrf_probe.blocked_destinations",
            PASS,
            f"{len(DANGEROUS_URLS)} dangerous destinations refused",
            evidence={"cases": len(DANGEROUS_URLS)},
        )

    rejected: list[dict[str, str]] = []
    for case in SAFE_URLS:
        refused, detail = _attempt(case.url, require_https=case.require_https)
        if refused:
            rejected.append({"url": case.url, "reason": case.reason, "detail": detail})
    if rejected:
        report.record(
            "ssrf_probe.public_destinations",
            FAIL,
            f"{len(rejected)} public destination(s) were refused; the guard is over-restrictive",
            evidence={"rejected": rejected},
        )
    else:
        report.record(
            "ssrf_probe.public_destinations",
            PASS,
            f"{len(SAFE_URLS)} public destinations accepted (false-positive control)",
        )
    return report


async def run_resolution_with(addresses: list[str], *, url: str = "https://rebind.example.com/x"):
    """Exercise the DNS path with a supplied answer set, without real DNS."""
    from unittest import mock

    from app.core import ssrf as ssrf_module

    infos = [(2, 1, 6, "", (address, 443)) for address in addresses]
    with mock.patch.object(ssrf_module.socket, "getaddrinfo", return_value=infos):
        try:
            await ssrf_module.validate_resolved_outbound_url(url)
            return True, ""
        except OutboundUrlError as exc:
            return False, str(exc)


async def run_resolution() -> HarnessReport:
    """Rebinding check: a hostname resolving to a private address must fail."""
    report = HarnessReport(suite="ssrf_resolution")
    cases: tuple[tuple[str, list[str], bool], ...] = (
        ("public_only", ["93.184.216.34"], True),
        ("private_answer", ["127.0.0.1"], False),
        ("mixed_public_and_private", ["93.184.216.34", "10.0.0.9"], False),
        ("metadata_answer", ["169.254.169.254"], False),
        ("no_answer", [], False),
    )
    for name, addresses, should_pass in cases:
        try:
            accepted, detail = await run_resolution_with(addresses)
        except Exception as exc:  # noqa: BLE001 - an unexpected raise is a result
            report.record(f"ssrf_probe.resolution.{name}", ERROR, f"{type(exc).__name__}: {exc}")
            continue
        if accepted != should_pass:
            report.record(
                f"ssrf_probe.resolution.{name}",
                FAIL,
                f"expected {'accept' if should_pass else 'refusal'}, got the opposite",
                evidence={"addresses": addresses, "detail": detail},
            )
        else:
            report.record(
                f"ssrf_probe.resolution.{name}",
                PASS,
                f"answers {addresses or '[]'} handled correctly",
            )
    return report


def corpus_summary() -> dict[str, Any]:
    return {
        "dangerous": [case.url for case in DANGEROUS_URLS],
        "safe": [case.url for case in SAFE_URLS],
    }


__all__ = [
    "DANGEROUS_URLS",
    "SAFE_URLS",
    "UrlCase",
    "corpus_summary",
    "run",
    "run_resolution",
    "run_resolution_with",
]
