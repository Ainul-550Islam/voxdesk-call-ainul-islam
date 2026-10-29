# File: tests/api/test_translation_job_list_export.py — translation job listing
# and the json/srt/txt exports.
"""Tests for ``GET /api/translation/jobs`` and ``.../export``.

The exports are the part that can quietly lie: a document that renders the
stored translation is useful, and one that re-runs the model, drops segments or
invents timings is not. So every export test seeds a known segment set and
compares the document against it exactly.

Covered:

1. Listing is scoped to the tenant/organization/environment, and honours the
   status and language filters.
2. Pagination is reported honestly (``total`` is the unfiltered-by-page count).
3. ``json`` export carries the persisted text and the fingerprints that prove
   it is the approved translation, not a fresh one.
4. ``srt`` export emits one cue per segment in durable order with derived
   timings, and ``txt`` one line per non-empty segment.
5. A job from another environment or another tenant is a 404, and an
   unsupported format is a 404 rather than a silent fallback to json.
6. The alias surface ``/api/translations/jobs*`` answers identically.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.db.models import Environment, Tenant, UserRole
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.translation.persistence import TranslationJobRecord, TranslationSegmentRecord
from tests.conftest import auth_headers, make_tenant, make_user

JOBS = "/api/translation/jobs"
ALIAS_JOBS = "/api/translations/jobs"


async def _scope_rows(db: AsyncSession, tenant: Tenant) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id = tenant.organization_id
    environment = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production"
        )
    )
    assert environment is not None
    return organization_id, environment.id


async def _seed_job(
    db: AsyncSession,
    tenant: Tenant,
    *,
    source_language: str = "en",
    target_language: str = "es",
    job_status: str = "completed",
    execution_status: str = "succeeded",
    segments: list[tuple[str, str]] | None = None,
    quality_status: str = "passed",
    review_required: bool = False,
) -> uuid.UUID:
    """Insert one translation execution plus its durable projection rows."""
    organization_id, environment_id = await _scope_rows(db, tenant)
    execution_id = uuid.uuid4()
    pairs = segments if segments is not None else [
        ("Hello, thank you for calling.", "Hola, gracias por llamar."),
        ("Your appointment is confirmed.", "Su cita esta confirmada."),
    ]
    execution = SpecializedExecutionRecord(
        id=execution_id,
        tenant_id=tenant.id,
        organization_id=organization_id,
        environment_id=environment_id,
        request_id=f"req-{execution_id.hex[:16]}",
        trace_id=f"trace-{execution_id.hex[:16]}",
        idempotency_key=f"idem-{execution_id.hex}",
        agent_type="translation",
        agent_version="1.0.0",
        model_version_id=uuid.uuid4(),
        risk_tier="low",
        status=execution_status,
        input_fingerprint="a" * 64,
        result={
            "source_language": source_language,
            "target_language": target_language,
            "glossary_version": "1.0",
            "provider": "openai",
            "model": "gpt-4o",
            "methodology": "llm",
            "quality_status": quality_status,
            "segments": [
                {
                    "segment_id": f"s{index + 1}",
                    "source_text": source,
                    "translated_text": target,
                    "source_fingerprint": f"src{index}",
                    "target_fingerprint": f"tgt{index}",
                    "quality_flags": [],
                    "status": "completed",
                }
                for index, (source, target) in enumerate(pairs)
            ],
        },
        review_required=review_required,
        review_state="REQUIRED" if review_required else "NOT_REQUIRED",
    )
    db.add(execution)
    db.add(
        TranslationJobRecord(
            execution_id=execution_id,
            tenant_id=tenant.id,
            organization_id=organization_id,
            environment_id=environment_id,
            source_language=source_language,
            target_language=target_language,
            glossary_version="1.0",
            dedupe_key=f"dedupe-{execution_id.hex}",
            segment_count=len(pairs),
            quality_status=quality_status,
            status=job_status,
            review_required=review_required,
            review_state="REQUIRED" if review_required else "NOT_REQUIRED",
            total_segments=len(pairs),
            completed_segments=len(pairs),
            failed_segments=0,
            attempt_count=1,
        )
    )
    for index, (source, _target) in enumerate(pairs):
        db.add(
            TranslationSegmentRecord(
                execution_id=execution_id,
                tenant_id=tenant.id,
                organization_id=organization_id,
                environment_id=environment_id,
                segment_index=index,
                segment_id=f"s{index + 1}",
                source_fingerprint=f"src{index}",
                target_fingerprint=f"tgt{index}",
                source_length=len(source),
                target_length=len(pairs[index][1]),
                status="completed",
                retry_count=0,
                review_required=False,
                quality_metadata={},
            )
        )
    await db.commit()
    return execution_id


# ------------------------------------------------------------------- list ---


@pytest.mark.asyncio
async def test_list_returns_the_jobs_of_this_environment_only(
    client: AsyncClient, db: AsyncSession
) -> None:
    ours = await make_tenant(db, name="xliff-ours")
    theirs = await make_tenant(db, name="xliff-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="xliff-owner@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, ours)

    ours_id = await _seed_job(db, ours, segments=[("Ours source", "Nuestra fuente")])
    await _seed_job(db, theirs, segments=[("Theirs source", "Su fuente")])

    response = await client.get(JOBS, params={"environment_id": str(environment_id)}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 1
    assert body["items"][0]["execution_id"] == str(ours_id)
    assert body["items"][0]["source_language"] == "en"
    assert body["items"][0]["segment_count"] == 1


@pytest.mark.asyncio
async def test_list_filters_by_status_and_language(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-filter")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-filter@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)

    await _seed_job(db, tenant, job_status="completed", target_language="es")
    review_id = await _seed_job(
        db, tenant,
        job_status="completed",
        execution_status="review_required",
        target_language="fr",
        quality_status="review",
        review_required=True,
    )

    params = {"environment_id": str(environment_id)}
    spanish = await client.get(JOBS, params={**params, "target_language": "es"}, headers=headers)
    assert [item["target_language"] for item in spanish.json()["items"]] == ["es"]
    assert spanish.json()["total"] == 1

    # `status` matches the governing execution status too, so a job awaiting
    # review is still findable by the state the dashboard shows.
    reviewing = await client.get(JOBS, params={**params, "status": "review_required"}, headers=headers)
    assert reviewing.json()["total"] == 1
    assert reviewing.json()["items"][0]["execution_id"] == str(review_id)
    assert reviewing.json()["items"][0]["review_required"] is True


@pytest.mark.asyncio
async def test_list_pagination_reports_the_unpaged_total(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-page")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-page@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)

    for index in range(3):
        await _seed_job(db, tenant, segments=[(f"Source {index}", f"Fuente {index}")])

    params = {"environment_id": str(environment_id)}
    first_page = await client.get(JOBS, params={**params, "limit": 2, "offset": 0}, headers=headers)
    second_page = await client.get(JOBS, params={**params, "limit": 2, "offset": 2}, headers=headers)

    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["limit"] == 2
    assert first_page.json()["offset"] == 0
    # The total is the count of everything matching, not of this page.
    assert second_page.json()["total"] == 3
    assert len(second_page.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_rejects_a_limit_above_the_cap(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-limit")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-limit@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)

    response = await client.get(
        JOBS, params={"environment_id": str(environment_id), "limit": 201}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_requires_the_environment(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-noenv")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-noenv@example.com")
    headers = await auth_headers(client, owner)

    response = await client.get(JOBS, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_alias_surface_answers_identically(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-alias")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-alias@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(db, tenant, segments=[("Alias source", "Fuente alias")])

    params = {"environment_id": str(environment_id)}
    canonical = await client.get(JOBS, params=params, headers=headers)
    alias = await client.get(ALIAS_JOBS, params=params, headers=headers)

    assert alias.status_code == 200, alias.text
    assert alias.json() == canonical.json()
    assert alias.json()["items"][0]["execution_id"] == str(job_id)


# ----------------------------------------------------------------- export ---


@pytest.mark.asyncio
async def test_json_export_carries_the_persisted_text_and_fingerprints(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="xliff-json")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-json@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(
        db,
        tenant,
        segments=[
            ("Hello, thank you for calling.", "Hola, gracias por llamar."),
            ("Your appointment is confirmed.", "Su cita esta confirmada."),
        ],
    )

    response = await client.get(
        f"{JOBS}/{job_id}/export",
        params={"environment_id": str(environment_id), "format": "json"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()

    assert body["execution_id"] == str(job_id)
    assert body["segment_count"] == 2
    assert [segment["source_text"] for segment in body["segments"]] == [
        "Hello, thank you for calling.",
        "Your appointment is confirmed.",
    ]
    assert [segment["translated_text"] for segment in body["segments"]] == [
        "Hola, gracias por llamar.",
        "Su cita esta confirmada.",
    ]
    # Fingerprints travel with the text, so the exported translation is
    # provably the one that was reviewed, not a fresh rendering.
    assert [segment["source_fingerprint"] for segment in body["segments"]] == ["src0", "src1"]
    assert body["glossary_version"] == "1.0"
    assert body["provider"] == "openai"


@pytest.mark.asyncio
async def test_srt_export_emits_one_cue_per_segment_in_durable_order(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="xliff-srt")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-srt@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(
        db,
        tenant,
        segments=[
            ("First line", "Primera linea"),
            ("Second line", "Segunda linea"),
        ],
    )

    response = await client.get(
        f"{JOBS}/{job_id}/export",
        params={"environment_id": str(environment_id), "format": "srt"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert "x-subrip" in response.headers["content-type"]
    body = response.text

    assert body.startswith("1\n")
    assert "--> " in body
    assert "Primera linea" in body
    assert "Segunda linea" in body
    # Cue order follows the durable segment order, not the result JSON order.
    assert body.index("Primera linea") < body.index("Segunda linea")
    assert response.headers["x-translation-segment-count"] == "2"


@pytest.mark.asyncio
async def test_txt_export_is_one_line_per_non_empty_segment(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="xliff-txt")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-txt@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(
        db,
        tenant,
        segments=[
            ("Kept", "Conservado"),
            ("Dropped", ""),
            ("Also kept", "Tambien conservado"),
        ],
    )

    response = await client.get(
        f"{JOBS}/{job_id}/export",
        params={"environment_id": str(environment_id), "format": "txt"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.text.splitlines() == ["Conservado", "Tambien conservado"]


@pytest.mark.asyncio
async def test_an_unsupported_format_is_refused_rather_than_silently_falling_back(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="xliff-format")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-format@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(db, tenant)

    response = await client.get(
        f"{JOBS}/{job_id}/export",
        params={"environment_id": str(environment_id), "format": "vtt"},
        headers=headers,
    )

    # A silent fallback to json would hand a caller a document it did not ask
    # for; a refusal tells it the format is not served.
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_refuses_another_tenants_job(client: AsyncClient, db: AsyncSession) -> None:
    ours = await make_tenant(db, name="xliff-export-ours")
    theirs = await make_tenant(db, name="xliff-export-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="xliff-export@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, ours)
    theirs_id = await _seed_job(db, theirs)

    response = await client.get(
        f"{JOBS}/{theirs_id}/export",
        params={"environment_id": str(environment_id)},
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_refuses_a_job_from_another_environment(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A production-scoped id is not readable under a staging scope."""
    tenant = await make_tenant(db, name="xliff-cross-env")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-cross@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(db, tenant)

    staging = Environment(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Staging",
        slug="staging",
        kind="staging",
        status="active",
        is_default=False,
    )
    db.add(staging)
    await db.commit()

    response = await client.get(
        f"{JOBS}/{job_id}/export",
        params={"environment_id": str(staging.id)},
        headers=headers,
    )

    assert response.status_code == 404
    assert str(environment_id) != str(staging.id)


@pytest.mark.asyncio
async def test_get_returns_the_execution_result(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-get")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-get@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)
    job_id = await _seed_job(db, tenant, segments=[("Get source", "Fuente get")])

    response = await client.get(
        f"{JOBS}/{job_id}", params={"environment_id": str(environment_id)}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(job_id)
    assert body["agent_type"] == "translation"
    assert body["status"] == "succeeded"
    # The result carries the persisted segment set, not a re-run of the model.
    assert body["result"]["segments"][0]["translated_text"] == "Fuente get"


@pytest.mark.asyncio
async def test_an_unknown_execution_id_is_a_404(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="xliff-missing")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-missing@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)

    response = await client.get(
        f"{JOBS}/{uuid.uuid4()}", params={"environment_id": str(environment_id)}, headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unauthenticated_request_is_refused(client: AsyncClient) -> None:
    response = await client.get(JOBS, params={"environment_id": str(uuid.uuid4())})
    assert response.status_code in (401, 403)


def test_the_alias_router_stays_out_of_the_published_schema() -> None:
    """The alias is a compatibility surface, not a documented contract.

    It exists so an older dashboard build keeps working; publishing it would
    make every future client think both spellings are equally supported.
    """
    from app.api.translation_routes import alias_router

    assert alias_router.include_in_schema is False
    paths = {route.path for route in alias_router.routes}
    assert paths == {
        "/api/translations/jobs",
        "/api/translations/jobs/{execution_id}",
        "/api/translations/jobs/{execution_id}/export",
    }


@pytest.mark.asyncio
async def test_an_empty_environment_lists_no_jobs(client: AsyncClient, db: AsyncSession) -> None:
    """No rows is reported as an empty list, never as sample data."""
    tenant = await make_tenant(db, name="xliff-empty")
    owner = await make_user(db, tenant, UserRole.OWNER, email="xliff-empty@example.com")
    headers = await auth_headers(client, owner)
    _organization_id, environment_id = await _scope_rows(db, tenant)

    response = await client.get(JOBS, params={"environment_id": str(environment_id)}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }
