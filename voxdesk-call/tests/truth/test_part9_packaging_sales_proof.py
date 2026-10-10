from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def test_fake_success_allowlist_is_empty() -> None:
    allowlist = ROOT / "scripts" / "fake_success_allowlist.txt"
    assert allowlist.exists()
    assert allowlist.read_text(encoding="utf-8").strip() == ""
    assert allowlist.stat().st_size == 0


def test_sboms_are_valid_cyclonedx_1_5() -> None:
    expected = {
        "python-backend.cdx.json": 40,
        "node-dashboard.cdx.json": 15,
        "go-services.cdx.json": 10,
        "rust-services.cdx.json": 50,
    }
    for filename, min_components in expected.items():
        path = ROOT / "sbom" / filename
        assert path.exists(), f"Missing SBOM {path}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["bomFormat"] == "CycloneDX"
        assert doc["specVersion"] == "1.5"
        assert len(doc.get("components", [])) >= min_components


def test_sales_and_due_diligence_docs_exist_and_obey_truth_rules() -> None:
    required_docs = [
        "LICENSE",
        "NOTICE",
        "SECURITY.md",
        "CHANGELOG.md",
        "THIRD_PARTY_LICENSES.md",
        "docs/QUICKSTART.md",
        "docs/OPS.md",
        "docs/DEMO/SCRIPT.md",
        "docs/api/openapi.json",
        "docs/api/index.html",
        "docs/DUE_DILIGENCE/ARCHITECTURE.md",
        "docs/DUE_DILIGENCE/GIT_HISTORY.md",
        "docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md",
        "docs/DUE_DILIGENCE/README.md",
        "docs/DUE_DILIGENCE/TEST_REPORT.md",
        "docs/SALES/FEATURE_MATRIX_VERIFIED.md",
        "docs/SALES/COMPETITIVE_COMPARISON.md",
        "docs/SALES/LISTING_COPY.md",
        "docs/SALES/PRICING_AND_TIERS.md",
    ]
    for rel in required_docs:
        p = ROOT / rel
        assert p.exists() and p.stat().st_size > 100, f"Missing or empty required file: {rel}"

    # Verify competitive comparison labels Retell claims as Vendor claim and maps #1..#41
    comp = (ROOT / "docs" / "SALES" / "COMPETITIVE_COMPARISON.md").read_text(encoding="utf-8")
    assert "Vendor claim:" in comp
    for fid in range(1, 42):
        assert f"**#{fid}:" in comp, f"Missing Matrix #{fid} in COMPETITIVE_COMPARISON.md"

    # Verify listing copy only lists 38 LIVE rows in its traceability table
    listing = (ROOT / "docs" / "SALES" / "LISTING_COPY.md").read_text(encoding="utf-8")
    assert "`#38`" in listing
    assert "| `#3` |" not in listing
    assert "| `#19` |" not in listing
    assert "| `#35` |" not in listing

    # Verify known limitations discloses all 3 non-LIVE rows
    lim = (ROOT / "docs" / "DUE_DILIGENCE" / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
    assert "`API_ONLY`" in lim
    assert "`NOT_CONFIGURED`" in lim
    assert "`PLANNED`" in lim

    # Verify git history documents all 6 commits and the NUL-byte incident
    hist = (ROOT / "docs" / "DUE_DILIGENCE" / "GIT_HISTORY.md").read_text(encoding="utf-8")
    assert "bc2803bc821ef06af51fa63ad894c7e541da412c" in hist
    assert "573df5850648234f90ab8f9b6aa4062da30ac8c0" in hist
    assert "dc85c3336f65fea8cc3c976b9c26b185bfa345b3" in hist


@pytest.mark.timeout(90)
def test_due_diligence_zip_and_sha256sums_verify() -> None:
    dist_dir = ROOT / "dist"
    zips = sorted(dist_dir.glob("due-diligence-*.zip"))
    if not zips:
        # Run generator if dist/ was cleaned
        import subprocess
        import sys

        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_due_diligence_pack.py")], cwd=ROOT, check=True)
        zips = sorted(dist_dir.glob("due-diligence-*.zip"))

    assert len(zips) >= 1
    zip_path = zips[-1]
    sha_file = dist_dir / "SHA256SUMS"
    assert sha_file.exists()

    lines = [ln.strip() for ln in sha_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 20
    first_digest, first_name = lines[0].split(None, 1)
    assert first_name == zip_path.name
    assert first_digest == _sha256(zip_path)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "SHA256SUMS" in names
        assert "PACK_MANIFEST.json" in names
        assert "docs/SALES/FEATURE_MATRIX_VERIFIED.md" in names
        assert "docs/DUE_DILIGENCE/README.md" in names
        assert "sbom/python-backend.cdx.json" in names
