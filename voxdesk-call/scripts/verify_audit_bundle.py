#!/usr/bin/env python3
"""Verify a downloaded audit export bundle offline, with only the public key.

    python3 scripts/verify_audit_bundle.py bundle.zip --public-key key.pem
    python3 scripts/verify_audit_bundle.py bundle.zip --public-key-url https://.../key.pem

Exit codes: ``0`` the bundle verifies, ``1`` it does not, ``2`` the tool could not
run (missing file, unreadable key). A bundle that cannot be read is never
reported as verified.

The command checks what a customer would check years later, with nothing but the
file and the published key: the ZIP is intact, every member hashes as the
manifest says, the manifest signature verifies, and the exported events still
form a chain. It never contacts this deployment's database — an offline
verification that needs the server proves nothing about the server.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a signed audit export bundle offline")
    parser.add_argument("bundle", help="path to the bundle ZIP")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--public-key", help="path to a PEM public key")
    source.add_argument("--public-key-url", help="URL serving the PEM public key")
    parser.add_argument(
        "--skip-chain",
        action="store_true",
        help="check hashes and signature only, not the exported chain links",
    )
    return parser.parse_args(argv)


def _load_key(args: argparse.Namespace) -> str:
    if args.public_key:
        return Path(args.public_key).read_text(encoding="utf-8")
    with urllib.request.urlopen(args.public_key_url, timeout=15) as response:  # noqa: S310
        return response.read().decode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    path = Path(args.bundle)
    if not path.is_file():
        print(json.dumps({"ok": False, "reason": "bundle_missing", "bundle": str(path)}))
        return 2
    try:
        public_key_pem = _load_key(args)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        print(json.dumps({"ok": False, "reason": f"key_unreadable: {type(exc).__name__}"}))
        return 2

    from app.compliance_v2.audit_integrity.export_bundle import verify_bundle

    result = verify_bundle(path, public_key_pem=public_key_pem)
    body = result.as_dict()
    body["bundle"] = str(path)
    body["checked_chain"] = not args.skip_chain
    print(json.dumps(body, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
