"""``contracts/openapi.json`` must be the schema the running app actually serves.

Twenty-three route modules were mounted in Batch 06, so the committed contract
was 222 operations behind the application. A stale contract is worse than none:
generators build clients from it, and the frontend type-checks against it, so the
divergence shows up as a "missing endpoint" bug in a buyer's integration rather
than as a failing build here.

``scripts/build_api_docs.sh`` is the repository's generator; this file only
verifies its output. When it fails, regenerate rather than edit the JSON:

    bash scripts/build_api_docs.sh

The structural assertions around the equality check are deliberate: an empty or
truncated file would otherwise compare equal to a broken app.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi.json"
DOCS_COPY = ROOT / "docs" / "api" / "openapi.json"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")

#: Prefixes Batch 06 mounted. Each must be discoverable in the shipped contract,
#: because that is how a client finds it at all.
BATCH_SURFACE_PREFIXES = (
    "/api/v1/carriers",
    "/api/v1/s2s",
    "/api/cc2/dialer",
    "/api/cc2/softphone",
    "/api/cc2/assist",
    "/api/cc2/acd",
    "/api/cc2/agent-states",
    "/api/cc2/supervisor",
    "/api/v1/integrations",
    "/api/v1/helpdesk",
    "/api/v1/collab",
    "/api/v1/agent-tools",
    "/webhooks/int2",
    "/telephony/vonage",
)


def _live_schema() -> dict:
    from app.main import app

    return app.openapi()


def _committed() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_committed_contract_matches_the_live_application():
    live = json.loads(json.dumps(_live_schema(), sort_keys=True))
    committed = json.loads(json.dumps(_committed(), sort_keys=True))

    live_paths = set(live["paths"])
    committed_paths = set(committed["paths"])
    missing = sorted(live_paths - committed_paths)
    stale = sorted(committed_paths - live_paths)
    assert missing == [], f"the committed contract is missing {len(missing)} paths, e.g. {missing[:5]}"
    assert stale == [], f"the committed contract still lists {len(stale)} removed paths: {stale[:5]}"
    assert live == committed, "the committed contract differs from the live schema"


def test_the_docs_copy_is_byte_identical_to_the_contract():
    assert DOCS_COPY.is_file(), "docs/api/openapi.json is missing; run scripts/build_api_docs.sh"
    assert DOCS_COPY.read_text(encoding="utf-8") == CONTRACT.read_text(encoding="utf-8")


def test_the_contract_is_a_substantial_openapi_31_document():
    schema = _committed()
    assert schema["openapi"].startswith("3.1"), schema["openapi"]
    assert schema["info"]["title"] == "VoxDesk"
    operations = [
        (path, method)
        for path, item in schema["paths"].items()
        for method in item
        if method in HTTP_METHODS
    ]
    assert len(operations) > 1000, len(operations)


def test_every_operation_has_a_unique_operation_id_and_a_response():
    schema = _committed()
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation_id = operation.get("operationId")
            assert operation_id, f"{method.upper()} {path} has no operationId"
            if operation_id in seen:
                duplicates.append(f"{operation_id}: {seen[operation_id]} and {method.upper()} {path}")
            seen[operation_id] = f"{method.upper()} {path}"
            assert operation.get("responses"), f"{method.upper()} {path} declares no responses"
            assert operation.get("tags"), f"{method.upper()} {path} has no tags"
    assert duplicates == [], duplicates


def test_every_batch_surface_is_in_the_shipped_contract():
    schema = _committed()
    missing = [
        prefix
        for prefix in BATCH_SURFACE_PREFIXES
        if not any(path.startswith(prefix) for path in schema["paths"])
    ]
    assert missing == [], f"batch surfaces absent from the contract: {missing}"


def test_the_contract_publishes_no_unauthenticated_private_operation():
    """Public operations are limited to the reviewed allowlist families."""
    from security_harness.authz_matrix import PUBLIC_ROUTES

    schema = _committed()
    public_paths: list[str] = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            security = operation.get("security")
            parameters = operation.get("parameters") or []
            declares_auth = any(
                parameter.get("name", "").lower() == "authorization"
                for parameter in parameters
                if isinstance(parameter, dict)
            )
            if security == [] and not declares_auth:
                public_paths.append(path)
    unexplained = sorted({path for path in public_paths if path not in PUBLIC_ROUTES})
    assert unexplained == [], f"public operations off the reviewed allowlist: {unexplained[:10]}"
