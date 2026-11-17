"""PART 14 / P14-08 — sitemap parsing, robots, diff and re-crawl."""

from __future__ import annotations

import pytest

from app.knowledge.jobs import apply_recrawl, recrawl_summary
from app.knowledge.sitemap import (
    PageDiff,
    PageState,
    SitemapError,
    UrlEntry,
    canonical,
    diff_pages,
    discover,
    filter_urls,
    parse_robots,
    parse_sitemap,
    plan_recrawl,
)

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/pricing/</loc><lastmod>2026-11-01</lastmod></url>
  <url><loc>https://shop.test/pricing</loc></url>
  <url><loc>https://shop.test/faq</loc><lastmod>2026-10-01</lastmod></url>
</urlset>"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://shop.test/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://shop.test/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


def test_a_urlset_is_parsed_and_duplicates_collapse() -> None:
    sitemap = parse_sitemap(URLSET)
    assert [entry.loc for entry in sitemap.urls] == [
        "https://shop.test/pricing",
        "https://shop.test/faq",
    ]
    assert sitemap.urls[1].lastmod == "2026-10-01"


def test_a_sitemap_index_lists_children_without_urls() -> None:
    sitemap = parse_sitemap(INDEX)
    assert sitemap.urls == ()
    assert len(sitemap.children) == 2


def test_garbage_xml_and_a_wrong_root_are_refused() -> None:
    with pytest.raises(SitemapError) as bad:
        parse_sitemap("<urlset><url>")
    assert bad.value.code == "invalid_xml"
    with pytest.raises(SitemapError) as wrong:
        parse_sitemap("<rss><channel/></rss>")
    assert wrong.value.code == "not_a_sitemap"
    with pytest.raises(SitemapError) as empty:
        parse_sitemap("   ")
    assert empty.value.code == "empty"


def test_a_limit_marks_the_result_truncated() -> None:
    sitemap = parse_sitemap(URLSET, limit=1)
    assert len(sitemap.urls) == 1
    assert sitemap.truncated is True


def test_canonical_drops_fragments_trailing_slashes_and_default_ports() -> None:
    assert canonical("https://shop.test/pricing/#top") == "https://shop.test/pricing"
    assert canonical("https://shop.test:443/faq/") == "https://shop.test/faq"
    assert canonical("HTTP://Shop.Test/faq") == "http://shop.test/faq"


def test_robots_longest_match_wins_and_allow_beats_disallow_of_equal_length() -> None:
    rules = parse_robots(
        "User-agent: *\nDisallow: /admin\nAllow: /admin/public\nDisallow: /private\n",
    )
    assert rules.allows("/faq") is True
    assert rules.allows("/admin/settings") is False
    assert rules.allows("/admin/public/page") is True
    assert rules.allows("/private/x") is False


def test_a_named_agent_group_is_applied_and_an_empty_disallow_is_a_noop() -> None:
    rules = parse_robots(
        "User-agent: voxdesk\nDisallow: /nope\n\nUser-agent: other\nDisallow: /\n",
        user_agent="voxdesk",
    )
    assert rules.allows("/nope") is False
    assert rules.allows("/fine") is True
    open_rules = parse_robots("User-agent: *\nDisallow:\n")
    assert open_rules.allows("/anything") is True


def test_filtering_keeps_only_allowed_same_host_urls() -> None:
    entries = [
        UrlEntry("https://shop.test/pricing"),
        UrlEntry("https://shop.test/admin/secret"),
        UrlEntry("https://elsewhere.test/pricing"),
        UrlEntry("https://shop.test/blog/post"),
    ]
    kept = filter_urls(
        entries,
        host="shop.test",
        robots=parse_robots("User-agent: *\nDisallow: /admin\n"),
        include_prefix="/",
        exclude_patterns=[r"/blog/"],
    )
    assert [entry.loc for entry in kept] == ["https://shop.test/pricing"]


def test_diff_detects_added_updated_unchanged_and_removed() -> None:
    previous = [
        PageState("https://shop.test/pricing", "h1", "2026-11-01"),
        PageState("https://shop.test/faq", "h2", "2026-10-01"),
        PageState("https://shop.test/old", "h3", "2026-01-01"),
    ]
    current = [
        UrlEntry("https://shop.test/pricing", "2026-11-05"),
        UrlEntry("https://shop.test/faq", "2026-10-01"),
        UrlEntry("https://shop.test/new", "2026-11-06"),
    ]
    diff = diff_pages(previous, current)
    assert diff.updated == ["https://shop.test/pricing"]
    assert diff.unchanged == ["https://shop.test/faq"]
    assert diff.added == ["https://shop.test/new"]
    assert diff.removed == ["https://shop.test/old"]
    assert diff.work == 3


def test_a_page_without_a_stored_hash_is_treated_as_unknown() -> None:
    diff = diff_pages([PageState("https://shop.test/x", "")], [UrlEntry("https://shop.test/x")])
    assert diff.updated == ["https://shop.test/x"]


def test_the_plan_deletes_first_and_respects_the_fetch_budget() -> None:
    diff = PageDiff(added=["a"], updated=["b"], removed=["c"])
    plan = plan_recrawl(diff, max_fetches=2)
    assert [item["action"] for item in plan] == ["delete", "ingest"]
    assert recrawl_summary(plan) == {"ingest": 1, "refetch": 0, "delete": 1, "total": 2}


@pytest.mark.asyncio
async def test_discovery_follows_children_and_filters() -> None:
    pages = {
        "https://shop.test/sitemap.xml": INDEX,
        "https://shop.test/sitemap-1.xml": URLSET,
        "https://shop.test/sitemap-2.xml": (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://shop.test/contact</loc></url></urlset>"
        ),
    }

    async def fetch(url: str) -> str:
        return pages[url]

    entries = await discover(
        sitemap_url="https://shop.test/sitemap.xml",
        fetch=fetch,
        host="shop.test",
        robots=parse_robots("User-agent: *\nDisallow: /admin\n"),
    )
    assert [entry.loc for entry in entries] == [
        "https://shop.test/pricing",
        "https://shop.test/faq",
        "https://shop.test/contact",
    ]


@pytest.mark.asyncio
async def test_applying_a_plan_survives_a_failing_page() -> None:
    ingested: list[str] = []
    archived: list[str] = []

    async def fetch(url: str) -> str:
        if url.endswith("/bad"):
            raise RuntimeError("404")
        return "body"

    async def ingest(url: str, body: str) -> None:
        ingested.append(url)
        assert body == "body"

    async def archive(url: str) -> None:
        archived.append(url)

    report = await apply_recrawl(
        plan=plan_recrawl(PageDiff(added=["/good", "/bad"], removed=["/gone"])),
        fetch=fetch,
        ingest=ingest,
        archive=archive,
    )
    assert report["ingested"] == 1
    assert report["archived"] == 1
    assert report["failed"][0]["url"] == "/bad"
