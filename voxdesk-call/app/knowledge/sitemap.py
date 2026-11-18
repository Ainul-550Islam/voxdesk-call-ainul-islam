"""Sitemap discovery and diff-aware re-crawl planning (PART 14, P14-08).

A knowledge sync that re-ingests a whole site every night spends embedding
budget re-embedding pages that did not change, and a sync that trusts `lastmod`
alone re-embeds pages whose content changed while their `lastmod` did not.
This module does the bookkeeping:

* `parse_sitemap`     -- sitemap.xml and sitemap index, one level of nesting;
* `allowed`           -- robots.txt `Disallow` and `Allow` with longest-match;
* `diff_pages`        -- added / updated / removed against the previous crawl;
* `plan_recrawl`      -- what to fetch now, bounded by a budget.

No HTTP here: the caller injects the fetcher, so the parser and the diff are
testable with fixtures and a re-crawl plan can be produced without a network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree

MAX_URLS = 5000
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class SitemapError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UrlEntry:
    loc: str
    lastmod: str | None = None
    priority: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"loc": self.loc, "lastmod": self.lastmod, "priority": self.priority}


@dataclass(frozen=True)
class Sitemap:
    urls: tuple[UrlEntry, ...] = ()
    children: tuple[str, ...] = ()
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "urls": [entry.as_dict() for entry in self.urls],
            "children": list(self.children),
            "truncated": self.truncated,
        }


def canonical(url: str) -> str:
    """Normalise a URL for comparison: no fragment, no default port, no trailing slash."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    path = parsed.path.rstrip("/") or "/"
    netloc = parsed.netloc.lower()
    if (parsed.scheme == "https" and netloc.endswith(":443")) or (
        parsed.scheme == "http" and netloc.endswith(":80")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _tag(element: ElementTree.Element, name: str) -> str | None:
    value = element.findtext(f"{_SITEMAP_NS}{name}")
    if value is None:
        value = element.findtext(name)
    return value.strip() if value else None


def parse_sitemap(xml_text: str, *, limit: int = MAX_URLS) -> Sitemap:
    """Parse a sitemap or sitemap index. Raises on anything unparseable."""
    if not xml_text or not xml_text.strip():
        raise SitemapError("empty", "the sitemap is empty")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SitemapError("invalid_xml", f"the sitemap is not valid XML: {exc}") from None

    tag = root.tag.split("}")[-1]
    urls: list[UrlEntry] = []
    children: list[str] = []
    truncated = False

    if tag == "sitemapindex":
        for child in root:
            loc = _tag(child, "loc")
            if loc:
                children.append(loc)
                if len(children) >= limit:
                    truncated = True
                    break
        return Sitemap(urls=(), children=tuple(children), truncated=truncated)

    if tag != "urlset":
        raise SitemapError("not_a_sitemap", f"the root element is <{tag}>, not <urlset>")

    seen: set[str] = set()
    for child in root:
        loc = _tag(child, "loc")
        if not loc:
            continue
        key = canonical(loc)
        if key in seen:
            # Duplicate entries in one sitemap are common; ingesting twice costs
            # embedding money and produces two chunks for one fact.
            continue
        seen.add(key)
        priority_text = _tag(child, "priority")
        urls.append(
            UrlEntry(
                loc=key,
                lastmod=_tag(child, "lastmod"),
                priority=float(priority_text) if priority_text else None,
            )
        )
        if len(urls) >= limit:
            truncated = True
            break
    return Sitemap(urls=tuple(urls), children=tuple(children), truncated=truncated)


@dataclass(frozen=True)
class RobotsRules:
    #: Longest-match wins; a rule with `allow=True` beats an equal-length disallow.
    rules: tuple[tuple[str, bool], ...] = ()
    crawl_delay: float | None = None

    def allows(self, path: str) -> bool:
        best: tuple[int, bool] | None = None
        for pattern, allow in self.rules:
            if not _matches(pattern, path):
                continue
            candidate = (len(pattern), allow)
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and allow and not best[1]
            ):
                best = candidate
        return True if best is None else best[1]


def _matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    regex = "^" + re.escape(pattern).replace(r"\*", ".*")
    if pattern.endswith("$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def parse_robots(text: str, *, user_agent: str = "*") -> RobotsRules:
    """Parse the group that applies to us -- `*` unless a group matches exactly."""
    groups: list[tuple[list[str], list[tuple[str, bool]]]] = []
    current_agents: list[str] = []
    current_rules: list[tuple[str, bool]] = []
    delay: float | None = None
    in_rules = False

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if in_rules:
                groups.append((current_agents, current_rules))
                current_agents, current_rules = [], []
                in_rules = False
            current_agents.append(value)
        elif key in ("allow", "disallow"):
            in_rules = True
            if key == "disallow" and value == "":
                continue  # an empty Disallow means "everything is allowed"
            current_rules.append((value, key == "allow"))
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                delay = None
    if current_agents:
        groups.append((current_agents, current_rules))

    applied: list[tuple[str, bool]] = []
    for agents, rules in groups:
        lowered = [agent.lower() for agent in agents]
        if user_agent.lower() in lowered or "*" in lowered:
            applied.extend(rules)
    return RobotsRules(rules=tuple(applied), crawl_delay=delay)


def filter_urls(
    urls: Iterable[UrlEntry],
    *,
    host: str,
    robots: RobotsRules | None = None,
    include_prefix: str | None = None,
    exclude_patterns: Iterable[str] = (),
) -> list[UrlEntry]:
    """Keep only URLs we are allowed and willing to crawl."""
    excluded = [re.compile(pattern) for pattern in exclude_patterns]
    kept: list[UrlEntry] = []
    for entry in urls:
        parsed = urlparse(entry.loc)
        if parsed.netloc.lower() != host.lower():
            continue
        if include_prefix and not parsed.path.startswith(include_prefix):
            continue
        if robots is not None and not robots.allows(parsed.path):
            continue
        if any(pattern.search(entry.loc) for pattern in excluded):
            continue
        kept.append(entry)
    return kept


@dataclass(frozen=True)
class PageState:
    """What we last stored for a page: enough to decide whether to re-fetch."""

    url: str
    content_hash: str
    lastmod: str | None = None
    etag: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "content_hash": self.content_hash, "lastmod": self.lastmod, "etag": self.etag}


@dataclass
class PageDiff:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "updated": list(self.updated),
            "unchanged": list(self.unchanged),
            "removed": list(self.removed),
        }

    @property
    def work(self) -> int:
        return len(self.added) + len(self.updated) + len(self.removed)


def diff_pages(
    previous: Iterable[PageState], current: Iterable[UrlEntry]
) -> PageDiff:
    """Compare the stored state with a freshly filtered sitemap.

    A changed `lastmod` alone marks a page *updated* even when we cannot know the
    body changed; the re-fetch then decides. A page whose `lastmod` is unchanged
    is *not* automatically unchanged -- only the stored content hash proves that,
    and it is what we compare when we have it.
    """
    old = {canonical(state.url): state for state in previous}
    new = {canonical(entry.loc): entry for entry in current}
    result = PageDiff()
    for url, entry in new.items():
        state = old.get(url)
        if state is None:
            result.added.append(url)
        elif entry.lastmod and state.lastmod and entry.lastmod != state.lastmod:
            result.updated.append(url)
        else:
            # No new lastmod claims: we still cannot prove it is unchanged, so
            # a page with no stored hash is treated as unknown -> updated.
            (result.unchanged if state.content_hash else result.updated).append(url)
    for url in old:
        if url not in new:
            result.removed.append(url)
    return result


def plan_recrawl(
    diff: PageDiff, *, max_fetches: int = 100, removed_first: bool = True
) -> list[dict[str, Any]]:
    """What to do now, removals first (they free index space immediately)."""
    plan: list[dict[str, Any]] = []
    if removed_first:
        plan.extend({"url": url, "action": "delete"} for url in diff.removed)
    plan.extend({"url": url, "action": "ingest"} for url in diff.added)
    plan.extend({"url": url, "action": "refetch"} for url in diff.updated)
    return plan[:max_fetches]


async def discover(
    *,
    sitemap_url: str,
    fetch: Callable[[str], Any],
    host: str,
    robots: RobotsRules | None = None,
    include_prefix: str | None = None,
    exclude_patterns: Iterable[str] = (),
    max_children: int = 10,
    limit: int = MAX_URLS,
) -> list[UrlEntry]:
    """Fetch a sitemap (and up to `max_children` child sitemaps), then filter.

    `fetch` is awaited when it returns an awaitable, so the same function works
    with a sync test stub and with the real async HTTP client.
    """
    import inspect

    async def _fetch(url: str) -> str:
        result = fetch(url)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else str(result)

    root = parse_sitemap(await _fetch(sitemap_url), limit=limit)
    entries = list(root.urls)
    for child in root.children[:max_children]:
        child_map = parse_sitemap(await _fetch(child), limit=limit)
        entries.extend(child_map.urls)
    return filter_urls(
        entries,
        host=host,
        robots=robots,
        include_prefix=include_prefix,
        exclude_patterns=exclude_patterns,
    )
