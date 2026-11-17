#!/usr/bin/env python3
"""Render the developer changelog Markdown as the docs page and an RSS feed."""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "CHANGELOG.md"
MARKDOWN_OUTPUT = ROOT / "docs-site" / "docs" / "changelog.md"
FEED_OUTPUTS = (
    ROOT / "docs-site" / "changelog.xml",
    ROOT / "docs-site" / "docs" / "changelog.xml",
)
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)


def parse_entries(markdown: str) -> list[tuple[str, date | None, str]]:
    """Split changelog sections headed by ``##`` into feed items."""
    lines = markdown.splitlines()
    headings = [index for index, line in enumerate(lines) if line.startswith("## ")]
    entries: list[tuple[str, date | None, str]] = []
    for pos, start in enumerate(headings):
        end = headings[pos + 1] if pos + 1 < len(headings) else len(lines)
        title = lines[start][3:].strip()
        body = "\n".join(lines[start + 1 : end]).strip()
        match = DATE_RE.search(title)
        entry_date = date.fromisoformat(match.group(1)) if match else None
        entries.append((title, entry_date, body))
    return entries


def _plain_text(markdown: str) -> str:
    """Remove simple Markdown syntax so a basic RSS reader gets readable text."""
    lines = []
    for line in markdown.splitlines():
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        line = line.replace("**", "").replace("`", "")
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def render_feed(markdown: str) -> str:
    entries = parse_entries(markdown)
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "VoxDesk Developer Changelog"
    ET.SubElement(channel, "link").text = "./"
    ET.SubElement(channel, "description").text = (
        "Release notes for the VoxDesk published API and developer documentation."
    )
    ET.SubElement(channel, "language").text = "en"

    newest_date = next((entry_date for _, entry_date, _ in entries if entry_date), None)
    if newest_date:
        built = datetime.combine(newest_date, time.min, tzinfo=timezone.utc)
        ET.SubElement(channel, "lastBuildDate").text = built.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for title, entry_date, body in entries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = "./"
        stable_key = entry_date.isoformat() if entry_date else re.sub(r"\W+", "-", title).strip("-").lower()
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"voxdesk:developer-changelog:{stable_key}"
        if entry_date:
            published = datetime.combine(entry_date, time.min, tzinfo=timezone.utc)
            ET.SubElement(item, "pubDate").text = published.strftime("%a, %d %b %Y %H:%M:%S +0000")
        ET.SubElement(item, "description").text = _plain_text(body) or title

    return "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" + ET.tostring(root, encoding="unicode") + "\n"


def build(check: bool = False) -> int:
    if not SOURCE.is_file():
        print(f"missing changelog source: {SOURCE}", file=sys.stderr)
        return 2
    markdown = SOURCE.read_text(encoding="utf-8")
    feed = render_feed(markdown)
    expected = {MARKDOWN_OUTPUT: markdown, **{path: feed for path in FEED_OUTPUTS}}
    stale = [path for path, content in expected.items() if not path.is_file() or path.read_text(encoding="utf-8") != content]
    if check:
        if stale:
            print("changelog outputs are stale:")
            for path in stale:
                print(f"  {path.relative_to(ROOT)}")
            return 1
        print("changelog page and RSS feeds are up to date")
        return 0
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"wrote changelog page and {len(FEED_OUTPUTS)} RSS feeds")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of updating stale outputs")
    args = parser.parse_args()
    return build(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
