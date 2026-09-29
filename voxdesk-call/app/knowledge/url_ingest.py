"""Secure URL ingestion built on the existing document pipeline."""

from __future__ import annotations
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.ssrf import OutboundUrlError, validate_resolved_outbound_url, validate_outbound_url
from app.db.models import KnowledgeSource
from app.knowledge.ingest import IngestResult, create_document, DuplicateDocument


class CrawlError(RuntimeError):
    """A URL fetch, redirect, response, or extraction limit failed."""


@dataclass(frozen=True)
class CrawlResult:
    source: KnowledgeSource
    documents: tuple[IngestResult, ...]
    visited: tuple[str, ...]


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            value = dict(attrs).get("href")
            if value:
                self.links.append(value)


def canonicalize(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError
        path = parts.path or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    except ValueError:
        raise CrawlError("URL is malformed") from None


async def crawl(
    session: AsyncSession,
    *,
    tenant_id,
    url: str,
    max_depth: int = 0,
    max_pages: int = 20,
    environment_id=None,
) -> CrawlResult:
    root = canonicalize(url)
    if max_depth < 0 or max_depth > 3 or max_pages < 1 or max_pages > 100:
        raise CrawlError("crawl limits are outside the allowed range")
    try:
        validate_outbound_url(root, require_https=True)
    except OutboundUrlError as exc:
        raise CrawlError("URL is not an allowed HTTPS destination") from exc
    root_host = urlsplit(root).hostname
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.tenant_id == tenant_id,
                KnowledgeSource.environment_id == environment_id,
                KnowledgeSource.canonical_url == root,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = KnowledgeSource(
            tenant_id=tenant_id,
            environment_id=environment_id,
            canonical_url=root,
            max_depth=max_depth,
            status="processing",
        )
        session.add(source)
        await session.flush()
    else:
        source.status = "processing"
        source.last_error = None
    queue: list[tuple[str, int]] = [(root, 0)]
    seen: set[str] = set()
    docs: list[IngestResult] = []
    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=False,
            headers={"User-Agent": "VoxDeskKnowledgeCrawler/1.0"},
        ) as client:
            while queue and len(seen) < max_pages:
                current, depth = queue.pop(0)
                current = canonicalize(current)
                if current in seen:
                    continue
                try:
                    await validate_resolved_outbound_url(current, require_https=True)
                except OutboundUrlError:
                    continue
                if urlsplit(current).hostname != root_host:
                    continue
                seen.add(current)
                try:
                    response = await client.get(current)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    raise CrawlError("URL fetch failed") from exc
                if 300 <= response.status_code < 400 or response.status_code >= 400:
                    raise CrawlError(f"URL fetch returned HTTP {response.status_code}")
                if len(response.content) > 20 * 1024 * 1024:
                    raise CrawlError("URL response exceeds the knowledge upload limit")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                filename = (urlsplit(current).path.rsplit("/", 1)[-1] or "page") + (
                    ".html"
                    if "html" in content_type and not urlsplit(current).path.endswith(".html")
                    else ""
                )
                try:
                    docs.append(
                        await create_document(
                            session,
                            tenant_id=tenant_id,
                            data=response.content,
                            filename=filename,
                            title=current,
                            declared_mime=content_type,
                            metadata={"source_url": current},
                            environment_id=environment_id,
                        )
                    )
                except DuplicateDocument as exc:
                    docs.append(IngestResult(exc.document, 0, reused=True))
                if depth < max_depth and "html" in content_type:
                    parser = _Links()
                    parser.feed(response.text)
                    for link in parser.links:
                        candidate = urljoin(current, link)
                        candidate_parts = urlsplit(candidate)
                        if candidate_parts.scheme.lower() not in {"http", "https"}:
                            continue
                        child = canonicalize(candidate)
                        if urlsplit(child).hostname == root_host and child not in seen:
                            queue.append((child, depth + 1))
    except CrawlError as exc:
        source.status, source.last_error = "failed", str(exc)[:500]
        await session.commit()
        raise
    source.status, source.last_error = "completed", None
    await session.commit()
    return CrawlResult(source, tuple(docs), tuple(sorted(seen)))
