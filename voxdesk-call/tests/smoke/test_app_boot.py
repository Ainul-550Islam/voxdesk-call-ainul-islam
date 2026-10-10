"""Smoke tests for FastAPI boot, OpenAPI generation, and route inventory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.main import app
from scripts.route_inventory import build_inventory

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_fastapi_app_instance_and_route_table() -> None:
    assert isinstance(app, FastAPI)
    assert len(app.routes) >= 150
    api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(api_routes) >= 1000
    for route in api_routes:
        assert route.path.startswith("/")
        assert callable(route.endpoint)
        assert route.methods


@pytest.mark.unit
def test_openapi_schema_generates_cleanly_with_unique_operation_ids() -> None:
    schema = app.openapi()
    assert isinstance(schema, dict)
    assert schema.get("openapi", "").startswith("3.")
    paths = schema.get("paths")
    assert isinstance(paths, dict)
    assert len(paths) >= 500
    assert "components" in schema

    operation_ids: list[str] = []
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() in http_methods and isinstance(op, dict):
                op_id = op.get("operationId")
                assert op_id, f"Missing operationId on {method.upper()} {path}"
                operation_ids.append(op_id)

    duplicates = [op_id for op_id in set(operation_ids) if operation_ids.count(op_id) > 1]
    assert duplicates == [], f"Duplicate operationIds found: {duplicates}"

    inv = build_inventory()
    boot_numbers: dict[str, Any] = {
        "total_routes_on_app": inv["summary"]["total_routes_on_app"],
        "api_route_count": inv["summary"]["api_route_count"],
        "websocket_route_count": inv["summary"]["websocket_route_count"],
        "methods": inv["summary"]["methods"],
        "openapi_version": schema.get("openapi"),
        "openapi_path_count": len(paths),
        "openapi_operation_count": len(operation_ids),
        "openapi_bytes": len(json.dumps(schema).encode("utf-8")),
        "duplicate_operation_ids": len(duplicates),
    }
    out_path = ROOT / "reports" / "check" / "boot_numbers.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(boot_numbers, indent=2) + "\n", encoding="utf-8")


@pytest.mark.unit
def test_route_inventory_has_no_duplicates_or_unallowlisted_public_routes() -> None:
    inv = build_inventory()
    assert inv["duplicates"] == []
    assert inv["unauthenticated_outside_allowlist"] == []
    assert inv["endpoint_n_routes"] == []
    assert inv["banner_routes"] == []
    assert inv["summary"]["api_route_count"] >= 1000
    assert inv["summary"]["websocket_route_count"] >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_and_openapi_endpoints_over_asgi() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health_resp = await client.get("/health")
        assert health_resp.status_code == 200
        body = health_resp.json()
        assert body.get("status") == "ok"

        openapi_resp = await client.get("/openapi.json")
        assert openapi_resp.status_code == 200
        oa = openapi_resp.json()
        assert "paths" in oa
        assert "/health" in oa["paths"]
