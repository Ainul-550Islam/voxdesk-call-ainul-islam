"""PART 10 gate guard — the migration chain must have exactly one head.

Two heads mean two people wrote migrations against the same parent and nobody noticed: the first
`alembic upgrade head` in production then fails, usually during a deploy. This is a ten-line test
that has saved more incidents than most of the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# Annotations are optional and both styles are in the tree:
#   revision = "0069_..."            (older files)
#   revision: str = "0070_..."       (newer files)
_REVISION_RE = re.compile(
    r'^revision\s*(?::\s*[\w\[\]\|, ]+)?=\s*["\']([^"\']+)["\']', re.MULTILINE
)
_DOWN_RE = re.compile(
    r'^down_revision\s*(?::\s*[\w\[\]\|, ]+)?=\s*(.+)$', re.MULTILINE
)


def _parse() -> tuple[dict[str, str], set[str], set[str]]:
    revisions: dict[str, str] = {}
    parents: set[str] = set()
    files: set[str] = set()
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = _REVISION_RE.search(text)
        if not match:
            continue
        revision = match.group(1)
        revisions[path.name] = revision
        files.add(path.name)
        down = _DOWN_RE.search(text)
        if not down:
            continue
        for candidate in re.findall(r'["\']([^"\']+)["\']', down.group(1)):
            parents.add(candidate)
    return revisions, parents, files


def _alembic_heads() -> list[str] | None:
    """The head list from alembic itself, when it is installed.

    Alembic is the authority on the chain; the regex parser below is a dependency-free fallback for
    environments that only installed the test client.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ModuleNotFoundError:
        return None
    root = VERSIONS.parents[1]
    config = Config(str(root / "alembic.ini"))
    # script_location is the directory holding `env.py`/`versions/`, not `versions/` itself.
    config.set_main_option("script_location", str(VERSIONS.parent))
    try:
        return list(ScriptDirectory.from_config(config).get_heads())
    except Exception:
        return None


def test_exactly_one_head() -> None:
    heads = _alembic_heads()
    if heads is not None:
        assert len(heads) == 1, f"alembic reports {len(heads)} heads: {heads}"
        return
    revisions, parents, _ = _parse()
    assert revisions, "no migration files were found"
    heads = sorted(set(revisions.values()) - parents)
    assert len(heads) == 1, f"expected exactly one head, found {heads}"


def test_every_down_revision_exists() -> None:
    revisions, parents, _ = _parse()
    known = set(revisions.values())
    dangling = sorted(parent for parent in parents if parent not in known)
    assert dangling == [], f"down_revision points at unknown revisions: {dangling}"


def test_the_head_matches_the_documented_head() -> None:
    revisions, parents, _ = _parse()
    head = sorted(set(revisions.values()) - parents)[0]
    assert head.startswith("00"), f"unexpected revision id format: {head!r}"


def test_no_duplicate_revision_identifiers() -> None:
    revisions, _, _ = _parse()
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for filename, revision in revisions.items():
        if revision in seen:
            clashes.append(f"{revision}: {seen[revision]} and {filename}")
        seen[revision] = filename
    assert clashes == [], f"duplicate revision ids: {clashes}"
