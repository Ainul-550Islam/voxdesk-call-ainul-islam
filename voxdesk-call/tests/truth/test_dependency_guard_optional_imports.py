"""Truth test: `make deps-check` distinguishes an optional import from a broken install.

PART 10 found the guard failing a clean checkout twice over, in both directions:

* ``bridge`` — the first-party package at ``services/sip-gateway/bridge``, which
  ``tests/telephony/test_sip_bridge.py`` reaches with a ``sys.path.insert`` — was
  reported as a missing *distribution*, which requirements.txt cannot fix.
* ``imageio_ffmpeg`` — imported inside ``try/except ModuleNotFoundError`` with a
  documented ``ffmpeg``-on-PATH fallback — was an error too, while the script's
  own author had already decided absence is supported.

Both were FAILs, so `make deps-check` exited 1 on a correctly installed tree, and
`make verify-sale` calls it. A gate that cries wolf on a clean install is muted
within a week, which is the failure mode this PART keeps finding. The guard now
reads the importer's ``try`` block: guarded absence is a warning, unguarded
absence stays an error — the assertions below pin both halves.
"""

from __future__ import annotations

import ast

from scripts.verify_dependencies import guarded_import_names

GUARDED = """\
def pick_encoder() -> str:
    try:
        import imageio_ffmpeg
    except ModuleNotFoundError:
        return "ffmpeg"
    return imageio_ffmpeg.get_ffmpeg_exe()
"""

TUPLE_HANDLER = """\
try:
    import orjson
except (ImportError, ValueError):
    import json as orjson
"""

UNGUARDED = """\
import requests
from cryptography.fernet import Fernet
"""

EXCEPTION_HANDLER = """\
try:
    import sentry_sdk
except Exception:
    sentry_sdk = None
"""

UNRELATED_HANDLER = """\
try:
    import yaml
except FileNotFoundError:
    yaml = None
"""


def _names(source: str) -> set[str]:
    return guarded_import_names(ast.parse(source))


def test_guarded_absence_is_recorded() -> None:
    assert _names(GUARDED) == {"imageio_ffmpeg"}
    assert _names(EXCEPTION_HANDLER) == {"sentry_sdk"}


def test_tuple_and_bare_handlers_are_understood() -> None:
    names = _names(TUPLE_HANDLER)
    assert "orjson" in names  # the `except (ImportError, ValueError)` handler counts
    assert "json" in names   # the fallback alias is collected too; stdlib filtering happens in scan_source
    # A handler that does not catch ImportError does not make the import optional.
    assert _names(UNRELATED_HANDLER) == set()


def test_unguarded_imports_are_not_excused() -> None:
    """The gate must keep its teeth: this is what makes an error an error."""

    assert _names(UNGUARDED) == set()


def test_relative_imports_are_not_candidates() -> None:
    assert _names("from . import sibling\nfrom ..pkg import other\n") == set()


def test_a_local_import_failure_is_still_fatal_on_the_repo_tree() -> None:
    """`bridge` must be exempt as a *local package*, not excused as optional.

    If it were merely guarded, the scanner would silently stop looking; the
    assertion pins the classification that is actually used.
    """

    from scripts.verify_dependencies import LOCAL_PACKAGES

    assert "bridge" in LOCAL_PACKAGES
    source = (
        "import sys\n"
        "sys.path.insert(0, 'services/sip-gateway')\n"
        "from bridge import codecs\n"
    )
    assert "bridge" not in _names(source)
