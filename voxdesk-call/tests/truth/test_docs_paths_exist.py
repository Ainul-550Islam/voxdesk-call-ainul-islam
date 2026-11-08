"""Truth test: every backticked repo path in README, SALES, and DUE_DILIGENCE exists."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from scripts.verify_docs_paths import scan

ROOT = Path(__file__).resolve().parents[2]


def test_all_documented_paths_in_readme_sales_and_due_diligence_exist() -> None:
    missing = scan(ROOT)
    assert missing == [], f"Non-existent backticked paths in buyer docs: {missing}"

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_docs_paths.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def test_verify_docs_paths_detects_missing_files_and_widget_paths(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "embed.js").write_text("// ok\n", encoding="utf-8")
    (tmp_path / "docs" / "SALES").mkdir(parents=True)
    (tmp_path / "docs" / "DUE_DILIGENCE").mkdir(parents=True)

    (tmp_path / "README.md").write_text(
        "Valid `app/real.py` and `/widget/embed.js`, broken `app/ghost.py`.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "SALES" / "PRICING_AND_TIERS.md").write_text(
        "Broken widget `/widget/v1/embed.js` and `infra/keycloak/`.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "DUE_DILIGENCE" / "KNOWN_LIMITATIONS.md").write_text(
        "Broken `app/compliance/redaction.py`.\n",
        encoding="utf-8",
    )

    findings = scan(tmp_path)
    missing_paths = {item["path"] for item in findings}
    assert "app/ghost.py" in missing_paths
    assert "widget/v1/embed.js" in missing_paths
    assert "infra/keycloak/" in missing_paths
    assert "app/compliance/redaction.py" in missing_paths
    assert "app/real.py" not in missing_paths
    assert "widget/embed.js" not in missing_paths
