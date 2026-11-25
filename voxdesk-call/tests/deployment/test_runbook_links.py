"""Every runbook an alert or a document points at must exist on disk.

PART 17B (P17B-05). The alert rules carry ``annotations.runbook`` paths of the
form ``docs/RUNBOOKS/<name>.md``. Those paths were correct in the rules and
wrong on disk: the directory was ``docs/runbooks`` (lowercase). On a laptop that
is invisible — case-insensitive filesystems resolve both — and on the Linux host
where the alert actually fires, every runbook link was dead. So the page arrived
with a link to a 404 at 03:00.

Three checks, all cheap, all on the live tree:

1. every ``runbook`` annotation in the canonical alert rules resolves to a file;
2. every markdown link into ``RUNBOOKS/`` from any tracked document resolves;
3. no document or module still spells the path in lowercase, because a second
   spelling is how the drift starts again (historical `reports/` are exempt: a
   delivery report must be able to *describe* the rename).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_DIR = ROOT / "docs" / "RUNBOOKS"
ALERT_FILES = (
    ROOT / "observability" / "alerts.yml",
    ROOT / "infra" / "helm" / "voxdesk" / "alerts.yml",
)
LINK = re.compile(r"\(([^()\s]*RUNBOOKS/[^()\s]+\.md)(?:#[^()\s]*)?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)
LOWERCASE = re.compile(r"docs/runbooks/")


def _slug(text: str) -> str:
    """GitHub's heading anchor for a heading line (enough of it to be useful).

    Lowercase, drop anything that is not a word character, space or hyphen, then
    turn runs of spaces into single hyphens. ``#### VoxDeskInstanceDown`` becomes
    ``voxdeskinstancedown``; ``### Alert Summaries & Immediate Actions`` becomes
    ``alert-summaries--immediate-actions``.
    """
    cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"\s+", "-", cleaned)


def _anchors(path: Path) -> set[str]:
    return {_slug(line) for line in HEADING.findall(path.read_text(encoding="utf-8"))}


def _rules(path: Path) -> list[dict]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [rule for group in document.get("groups", []) for rule in group.get("rules", [])]


@pytest.mark.parametrize("alert_file", ALERT_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_alert_runbook_annotation_exists(alert_file: Path) -> None:
    rules = _rules(alert_file)
    assert rules, f"{alert_file} has no alert rules"
    missing = []
    for rule in rules:
        target = (rule.get("annotations") or {}).get("runbook")
        if not target:
            continue
        # A rule may point at a runbook by repository path, or by an https URL for
        # a third-party integration; only the repository paths are ours to keep.
        if "://" in target:
            continue
        file_part, _, anchor = target.partition("#")
        resolved = ROOT / file_part
        if not resolved.is_file():
            missing.append(f"{rule.get('alert')} -> {target}")
            continue
        # An annotation that links into a section must land on a real heading: a
        # dead fragment scrolls to the top of the page and the reader loses the one
        # paragraph that told them what to do.
        if anchor and anchor not in _anchors(resolved):
            missing.append(f"{rule.get('alert')} -> {target} (no such heading)")
    assert not missing, "alert runbook annotations point at files that do not exist: " + ", ".join(missing)


def test_markdown_links_into_runbooks_resolve() -> None:
    checked = 0
    missing = []
    for document in ROOT.rglob("*.md"):
        parts = document.parts
        if any(part in {".git", "node_modules", "dashboard-next", ".venv"} for part in parts):
            continue
        text = document.read_text(encoding="utf-8", errors="replace")
        for match in LINK.finditer(text):
            target = match.group(1)
            resolved = (document.parent / target).resolve()
            checked += 1
            if not resolved.is_file():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert checked, "no RUNBOOKS links found; the scan is broken, not the docs"
    assert not missing, "markdown links point at missing runbooks: " + ", ".join(missing)


def test_no_document_uses_the_lowercase_spelling() -> None:
    # `reports/` and this file are excluded on purpose: a delivery report has to be
    # able to say "the directory used to be spelled with a lower-case r" — that is a
    # description of the fix, not a link a reader is expected to follow. Everything
    # that a responder or a CI job *acts on* is still covered.
    offenders = []
    for pattern in ("*.md", "*.py", "*.yml", "*.yaml", "*.sh", "*.json"):
        for path in ROOT.rglob(pattern):
            if any(part in {"node_modules", ".git", ".venv", "dist", "build", "reports"} for part in path.parts):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue  # this test names the spelling it forbids
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file, not a doc bug
                continue
            if LOWERCASE.search(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "these files still reference the lower-case runbook directory: " + ", ".join(sorted(offenders))
    )


def test_no_second_runbook_directory_exists() -> None:
    """A rename is only finished when the old path is gone, not merely unreferenced.

    Every other check here looks at file *contents*; a case-variant directory with
    even one file in it would sail past all of them while quietly recreating the
    original bug, because the next runbook added by hand lands in the copy that
    the links do not point at.
    """
    assert not (ROOT / "docs" / "runbooks").exists(), (
        "docs/runbooks exists again next to docs/RUNBOOKS — two spellings, and the "
        "lower-case one wins on macOS while losing on Linux"
    )


def test_runbook_directory_is_not_empty_and_index_lists_it() -> None:
    runbooks = sorted(p.name for p in RUNBOOK_DIR.glob("*.md"))
    assert len(runbooks) >= 8, runbooks
    index = (RUNBOOK_DIR / "README.md").read_text(encoding="utf-8")
    unlisted = [name for name in runbooks if name != "README.md" and name not in index]
    assert not unlisted, f"runbooks missing from docs/RUNBOOKS/README.md: {unlisted}"
