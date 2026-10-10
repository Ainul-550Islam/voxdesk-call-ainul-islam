#!/usr/bin/env python3
"""Collect compliance evidence artifacts into ``evidence/<date>/`` with ``SHA256SUMS``.

Gathers:
- Repository verification & gate reports (``reports/PART_*_REPORT.md``)
- Deterministic Python + Node dependency SBOM (``sbom-dependencies.json``)
- Backup & restore script integrity manifest (``backup-restore-drill.json``)
- Optional CI test/audit logs passed via ``--artifact``
- ``evidence-manifest.json`` and ``SHA256SUMS`` over every file in ``evidence/<date>/``

Never claims any certification (HIPAA, SOC 2, ISO 27001); records
``certification_claimed: false`` in the manifest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata as importlib_metadata
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head_sha() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def _build_sbom() -> dict[str, Any]:
    python_reqs: list[dict[str, str]] = []
    req_file = REPO_ROOT / "requirements.txt"
    if req_file.exists():
        for raw_line in req_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            python_reqs.append({"requirement": line})

    installed = sorted(
        (
            {"name": dist.metadata["Name"], "version": dist.version}
            for dist in importlib_metadata.distributions()
            if dist.metadata.get("Name")
        ),
        key=lambda item: item["name"].lower(),
    )

    node_packages: dict[str, Any] = {}
    pkg_json = REPO_ROOT / "dashboard-next" / "package.json"
    if pkg_json.exists():
        try:
            parsed = json.loads(pkg_json.read_text(encoding="utf-8"))
            node_packages = {
                "dependencies": parsed.get("dependencies", {}),
                "devDependencies": parsed.get("devDependencies", {}),
            }
        except Exception:
            node_packages = {}

    return {
        "bomFormat": "CycloneDX-lite",
        "specVersion": "1.5",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": _git_head_sha(),
        "python_requirements": python_reqs,
        "installed_python_distributions_count": len(installed),
        "installed_python_distributions": installed,
        "dashboard_next_packages": node_packages,
    }


def _build_backup_drill_manifest() -> dict[str, Any]:
    scripts_checked = []
    for rel in ("scripts/backup.sh", "scripts/backup_verify.sh", "scripts/restore.sh"):
        full = REPO_ROOT / rel
        if full.exists():
            scripts_checked.append(
                {
                    "path": rel,
                    "exists": True,
                    "size_bytes": full.stat().st_size,
                    "sha256": _sha256_file(full),
                }
            )
        else:
            scripts_checked.append({"path": rel, "exists": False})
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scripts": scripts_checked,
    }


def collect_compliance_evidence(
    *,
    date_str: str | None = None,
    output_root: Path | None = None,
    extra_artifacts: list[Path] | None = None,
) -> Path:
    target_date = date_str or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    base_dir = output_root or (REPO_ROOT / "evidence" / target_date)
    base_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write SBOM
    sbom_path = base_dir / "sbom-dependencies.json"
    sbom_path.write_text(
        json.dumps(_build_sbom(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 2. Write backup-restore script verification manifest
    backup_path = base_dir / "backup-restore-drill.json"
    backup_path.write_text(
        json.dumps(_build_backup_drill_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 3. Copy available PART_*_REPORT.md files
    copied_reports: list[str] = []
    reports_dir = REPO_ROOT / "reports"
    if reports_dir.exists():
        for report_file in sorted(reports_dir.glob("PART_*_REPORT.md")):
            dest = base_dir / report_file.name
            shutil.copyfile(report_file, dest)
            copied_reports.append(report_file.name)

    # 4. Copy any extra CI artifacts provided on the CLI
    copied_extras: list[str] = []
    for extra in extra_artifacts or []:
        if extra.exists() and extra.is_file():
            dest = base_dir / extra.name
            shutil.copyfile(extra, dest)
            copied_extras.append(extra.name)

    # 5. Write evidence-manifest.json
    manifest = {
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "date": target_date,
        "git_commit": _git_head_sha(),
        "certification_claimed": False,
        "controls_posture": "SOC 2-ready controls and HIPAA-ready configuration; no certification claimed",
        "reports_included": copied_reports,
        "extra_artifacts_included": copied_extras,
    }
    manifest_path = base_dir / "evidence-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6. Compute SHA256SUMS over all files in base_dir (excluding SHA256SUMS itself)
    sums_lines: list[str] = []
    for item in sorted(base_dir.iterdir(), key=lambda p: p.name):
        if item.is_file() and item.name != "SHA256SUMS":
            sums_lines.append(f"{_sha256_file(item)}  {item.name}")
    (base_dir / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    return base_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect compliance evidence bundle with SHA256SUMS."
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date folder name (YYYY-MM-DD); defaults to today UTC.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Explicit output directory (overrides evidence/<date>/).",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Additional CI report or log file to include in the evidence bundle.",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else None
    extras = [Path(a) for a in args.artifact]
    bundle_dir = collect_compliance_evidence(
        date_str=args.date,
        output_root=out_dir,
        extra_artifacts=extras,
    )
    print(f"Compliance evidence bundle written to: {bundle_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
