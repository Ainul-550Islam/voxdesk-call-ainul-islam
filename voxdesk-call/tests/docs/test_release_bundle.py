"""Determinism and integrity tests for the buyer-facing release bundle."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from scripts import build_release
from scripts.build_release import _sha256, _zip_bytes


def test_release_zip_is_deterministic_and_has_content_hashes() -> None:
    members = {
        "docs/guide.md": b"A buyer-facing guide.\n",
        "developer-docs/index.html": b"<h1>Docs</h1>\n",
    }
    first = _zip_bytes(members, "abc123", "abc123", False)
    second = _zip_bytes(members, "abc123", "abc123", False)
    assert first == second

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        manifest = json.loads(archive.read("RELEASE-MANIFEST.json"))
        entries = {entry["path"]: entry for entry in manifest["files"]}
        assert entries["docs/guide.md"]["sha256"] == hashlib.sha256(members["docs/guide.md"]).hexdigest()
        assert entries["developer-docs/index.html"]["size_bytes"] == len(members["developer-docs/index.html"])
        assert "CONTENTS.md" in entries
        assert archive.testzip() is None


def test_prior_checksums_preserves_and_validates_due_diligence(tmp_path, monkeypatch) -> None:
    root = tmp_path
    output_dir = root / "dist"
    output_dir.mkdir()
    monkeypatch.setattr(build_release, "ROOT", root)

    due_diligence = output_dir / "due-diligence-current.zip"
    due_diligence.write_bytes(b"verified pack")
    old_release = output_dir / "voxdesk-developer-release-old.zip"
    old_release.write_bytes(b"obsolete bundle")
    sums = output_dir / "SHA256SUMS"
    sums.write_text(
        f"{_sha256(due_diligence.read_bytes())}  dist/{due_diligence.name}\n"
        f"{_sha256(old_release.read_bytes())}  dist/{old_release.name}\n",
        encoding="utf-8",
    )

    assert build_release._prior_checksums(output_dir) == [
        f"{_sha256(due_diligence.read_bytes())}  dist/{due_diligence.name}"
    ]
    due_diligence.write_bytes(b"modified pack")
    with pytest.raises(ValueError, match="stale"):
        build_release._prior_checksums(output_dir)
