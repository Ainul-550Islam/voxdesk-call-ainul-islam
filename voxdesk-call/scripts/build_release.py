#!/usr/bin/env python3
"""Build a deterministic, buyer-facing VoxDesk developer release bundle.

This is a documentation/evidence bundle, not a deployable application image.
The deployment artifacts and full due-diligence archive remain separate. The
bundle contains an allowlisted set of buyer materials plus the built static
developer site; it deliberately excludes environment files, raw evidence,
customer data, databases, caches and arbitrary workspace files.

The script writes ``dist/voxdesk-developer-release-<commit>[-worktree].zip``
and updates ``dist/SHA256SUMS`` while preserving any current due-diligence
manifest entries. Run checksum verification from the repository root:
``sha256sum -c dist/SHA256SUMS``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "dist"
SITE_DIR = ROOT / "build" / "docs-site"
REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_LICENSES.md",
    "SECURITY.md",
    "docs/CHANGELOG.md",
    "docs/SALES/DEAL_ROOM_CHECKLIST.md",
    "docs/SALES/FEATURE_MATRIX_VERIFIED.md",
    "docs/SALES/COMPETITIVE_COMPARISON.md",
    "docs/SALES/PRICING_AND_TIERS.md",
    "docs/SALES/LISTING_COPY.md",
    "docs/SALES/LICENSE_TERMS_TEMPLATE.md",
    "docs/DEPLOYMENT.md",
    "docs/BUYER_DEPLOY_GUIDE.md",
    "docs/CURRENT-ARCHITECTURE.md",
    "docs/CURRENT-SECURITY-STATUS.md",
    "docs/COMPLIANCE.md",
    "docs/COMPLIANCE/DATA_FLOW.md",
    "docs/COMPLIANCE/SUBPROCESSORS.md",
    "docs/BCP.md",
    "reports/PART_18_REPORT.md",
)
OPTIONAL_FILES = ("docs/CAPACITY_MODEL.md", "docs/DR_RUNBOOK.md")


def _git(args: list[str], fallback: str = "unknown") -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return fallback
    return result.stdout.strip() or fallback


def _source_identity() -> tuple[str, str, bool]:
    commit = _git(["rev-parse", "--short=12", "HEAD"], "source")
    dirty = bool(_git(["status", "--porcelain", "--untracked-files=normal"], ""))
    release_id = commit + ("-worktree" if dirty else "")
    return commit, release_id, dirty


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect_members() -> dict[str, bytes]:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    if not (SITE_DIR / "index.html").is_file():
        missing.append("build/docs-site/index.html (run mkdocs build -f docs-site/mkdocs.yml)")
    if missing:
        raise FileNotFoundError("required release inputs are missing:\n  " + "\n  ".join(missing))

    members: dict[str, bytes] = {}
    for name in REQUIRED_FILES:
        members[name] = (ROOT / name).read_bytes()
    for name in OPTIONAL_FILES:
        source = ROOT / name
        if source.is_file():
            members[name] = source.read_bytes()

    for path in sorted(SITE_DIR.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(SITE_DIR).as_posix()
        members[f"developer-docs/{relative}"] = path.read_bytes()

    sbom_dir = ROOT / "sbom"
    if sbom_dir.is_dir():
        for path in sorted(sbom_dir.glob("*.cdx.json")):
            if path.is_file() and not path.is_symlink():
                members[f"sbom/{path.name}"] = path.read_bytes()

    return dict(sorted(members.items()))


def _bundle_readme(members: dict[str, bytes]) -> bytes:
    lines = [
        "# VoxDesk developer release bundle",
        "",
        "This is a buyer-facing documentation and evidence bundle. It is not a",
        "deployable application image. Use the release deployment guide and the",
        "verified container/chart artifacts for installation.",
        "",
        "## Included files",
        "",
    ]
    lines.extend(f"- `{name}`" for name in sorted(members))
    lines.extend([
        "",
        "Verify the outer archive with `sha256sum -c dist/SHA256SUMS` from the",
        "repository root. `RELEASE-MANIFEST.json` contains a SHA-256 digest for",
        "every file in this bundle, excluding the manifest itself.",
        "",
        "Capability status is evidence-scoped. A test pass is not a third-party",
        "certification or proof of a live provider account.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def _zip_bytes(members: dict[str, bytes], commit: str, release_id: str, dirty: bool) -> bytes:
    all_members = dict(members)
    all_members["CONTENTS.md"] = _bundle_readme(members)
    manifest = {
        "bundle_kind": "buyer-facing developer documentation and evidence",
        "release_id": release_id,
        "git_commit": commit,
        "worktree_dirty": dirty,
        "file_count": len(all_members),
        "files": [
            {"path": name, "size_bytes": len(data), "sha256": _sha256(data)}
            for name, data in sorted(all_members.items())
        ],
    }
    all_members["RELEASE-MANIFEST.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.comment = b"VoxDesk developer release bundle; see RELEASE-MANIFEST.json"
        for name, data in sorted(all_members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.flag_bits = 0x800
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _prior_checksums(output_dir: Path) -> list[str]:
    """Keep and validate existing due-diligence checksums, dropping old release ZIPs."""
    manifest = output_dir / "SHA256SUMS"
    if not manifest.is_file():
        return []

    kept: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"malformed {manifest.relative_to(ROOT)} line {line_number}")
        expected, recorded_name = parts
        recorded_name = recorded_name.lstrip("*")
        if Path(recorded_name).name.startswith("voxdesk-developer-release-") and recorded_name.endswith(".zip"):
            continue

        candidate = ROOT / recorded_name
        if not candidate.is_file():
            candidate = output_dir / recorded_name
        if not candidate.is_file():
            raise ValueError(f"checksum input is missing: {recorded_name}")
        actual = _sha256(candidate.read_bytes())
        if actual != expected:
            raise ValueError(
                f"existing checksum is stale for {recorded_name}; rebuild the due-diligence pack first"
            )
        normalized = candidate.relative_to(ROOT).as_posix()
        if normalized not in seen:
            kept.append(f"{actual}  {normalized}")
            seen.add(normalized)
    return kept


def build(output_dir: Path = OUTPUT_DIR, check: bool = False) -> int:
    try:
        output_dir.resolve().relative_to(ROOT.resolve())
    except ValueError:
        print("release output directory must be inside the repository", file=sys.stderr)
        return 2
    try:
        members = _collect_members()
        prior_lines = _prior_checksums(output_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    commit, release_id, dirty = _source_identity()
    archive_name = f"voxdesk-developer-release-{release_id}.zip"
    archive_path = output_dir / archive_name
    archive_bytes = _zip_bytes(members, commit, release_id, dirty)
    manifest_path = output_dir / "SHA256SUMS"

    # Preserve a current due-diligence manifest (including its inner file
    # hashes), then add the developer bundle. The pack script writes
    # repository-root-relative paths so this one manifest verifies from ROOT.
    checksum_lines = list(prior_lines)
    seen_paths = {line.split(None, 1)[1] for line in checksum_lines}
    for path in sorted(output_dir.glob("due-diligence-*.zip")):
        relative = path.relative_to(ROOT).as_posix()
        if relative not in seen_paths:
            checksum_lines.insert(0, f"{_sha256(path.read_bytes())}  {relative}")
            seen_paths.add(relative)
    checksum_lines.append(
        f"{_sha256(archive_bytes)}  {archive_path.relative_to(ROOT).as_posix()}"
    )
    checksum_text = "\n".join(checksum_lines) + "\n"

    if check:
        if not archive_path.is_file() or archive_path.read_bytes() != archive_bytes:
            print(f"release bundle is stale or missing: {archive_path}", file=sys.stderr)
            return 1
        if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != checksum_text:
            print(f"release checksums are stale or missing: {manifest_path}", file=sys.stderr)
            return 1
        print(f"release bundle is current: {archive_path.relative_to(ROOT)}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("voxdesk-developer-release-*.zip"):
        if stale != archive_path:
            stale.unlink()
    archive_path.write_bytes(archive_bytes)
    manifest_path.write_text(checksum_text, encoding="utf-8")
    print(f"wrote {archive_path.relative_to(ROOT)} ({len(archive_bytes)} bytes, {len(members)} inputs)")
    print(f"wrote {manifest_path.relative_to(ROOT)} ({len(checksum_lines)} checksummed file(s))")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUTPUT_DIR), help="output directory (default: dist)")
    parser.add_argument("--check", action="store_true", help="verify the current bundle without writing")
    args = parser.parse_args()
    output_dir = Path(args.out)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    return build(output_dir=output_dir, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
