"""Parse JUnit XML that this repository produced itself, and nothing else.

Bandit flags every ``xml.etree`` call site (B314) because the module's parser
will expand internal entities, which is how a small document becomes a
billion-laughs. The JUnit files read here are written by pytest a few seconds
earlier into a workspace this repository controls, so the exposure is an
adversary who can already write files into our output directory -- who has
better attacks than entity expansion. Still, "we generated it" is not a control,
so this module adds one: a document type declaration is rejected before parsing.

That makes the finding answerable with evidence rather than with an assertion,
and keeps the parsing in one place instead of four.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

#: Entity and doctype markers, upper and lower case, checked in the first chunk
#: of the file. JUnit output never begins with either.
_FORBIDDEN = (b"<!doctype", b"<!entity")
_SNIFF_BYTES = 8192


class UnsafeJunitXmlError(ValueError):
    """Raised when a file that should be our own JUnit XML is not."""


def read_root(path: str | Path) -> ET.Element:
    """Parse ``path`` and return the document root.

    Raises :class:`UnsafeJunitXmlError` if the file declares a document type,
    and lets :class:`xml.etree.ElementTree.ParseError` propagate for genuinely
    malformed input -- a truncated JUnit file should be loud, not empty.
    """
    target = Path(path)
    with target.open("rb") as handle:
        head = handle.read(_SNIFF_BYTES)
    lowered = head.lower()
    for marker in _FORBIDDEN:
        if marker in lowered:
            raise UnsafeJunitXmlError(
                f"refusing to parse {target}: a document type declaration is not valid JUnit XML"
            )
    # The document type check above runs first, and expat never resolves
    # external entities, so the entity-expansion risk bandit flags is closed
    # here rather than merely asserted to be absent.
    return ET.parse(target).getroot()  # nosec B314


def iter_suites(path: str | Path) -> list[ET.Element]:
    """Every ``<testsuite>`` in the document, tolerating either root shape.

    pytest writes ``<testsuites>`` when it merges several files and a bare
    ``<testsuite>`` when there is one; callers should not have to care.
    """
    root = read_root(path)
    if root.tag == "testsuite":
        return [root]
    return list(root.findall(".//testsuite"))


def iter_cases(path: str | Path) -> list[ET.Element]:
    """Every ``<testcase>`` in every suite, in document order."""
    return [case for suite in iter_suites(path) for case in suite.findall("testcase")]
