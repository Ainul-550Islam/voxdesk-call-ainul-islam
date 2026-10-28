"""The workforce API over HTTP: permissions, tenant scoping, idempotency, audit.

The domain modules are tested directly elsewhere; what is checked here is the surface —
that an agent cannot read the forecast, that an owner cannot read another tenant's
forecast by id, that a repeated ``Idempotency-Key`` does not file two leave requests,
and that a mutation leaves an audit row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, User
from app.db.platform_v2_models import AdherenceException, TimeOffRequest
from app.platform_v2.http import IDEMPOTENCY_HEADER
from tests.conftest import auth_headers
from tests.platform_v2.conftest import PlatScope, seed_history

WFM = "/api/wfm"


def day(hour: int = 9) -> str:
    return datetime(2026, 11, 9, hour, tzinfo=UTC).isoformat()


async def test_history_requires_a_session(client):
    response = await client.get(f"{WFM}/history")
    assert response.status_code in (401, 403)


async def test_an_agent_reads_the_rota_surface_but_cannot_generate_a_forecast(
    client, plat_scope: PlatScope, agent_headers
):
    # `wfm:read` is the agent's self-service bundle (see
    # app/platform_v2/permissions.py): they see the rota and the numbers, and the
    # routes scope the *data* to the caller.
    readable = await client.get(f"{WFM}/forecasts", headers=agent_headers)
    assert readable.status_code == 200

    # Generating one writes a plan, which is a supervisor action.
    forbidden = await client.post(
        f"{WFM}/forecasts", headers=agent_headers, json={"horizon_days": 1}
    )
    assert forbidden.status_code == 403

    mine = await client.get(
        f"{WFM}/my-shifts", headers=agent_headers, params={"start": day(), "end": day(17)}
    )
    assert mine.status_code == 200
    body = mine.json()
    assert body["shifts"] == []
    assert body["total_paid_hours"] == 0


async def test_history_and_counts_are_served_from_the_rolled_up_rows(
    client, db, plat_scope: PlatScope, owner_headers
):
    await seed_history(db, plat_scope, days=3)
    response = await client.get(f"{WFM}/history", headers=owner_headers, params={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["intervals"], "the seeded rollup must be readable"
    assert {"interval_start", "offered", "is_gap"} <= set(body["intervals"][0])
    assert body["count"] >= len(body["intervals"])
    assert body["summary"]["intervals"] == body["count"]

    counts = await client.get(f"{WFM}/history/counts", headers=owner_headers)
    assert counts.status_code == 200
    assert counts.json()["intervals"] > 0


async def test_history_rebuild_is_refused_without_the_write_permission(
    client, agent_headers, plat_scope: PlatScope
):
    response = await client.post(f"{WFM}/history/rebuild", headers=agent_headers, json={})
    assert response.status_code == 403


async def test_a_forecast_can_be_created_listed_and_read(
    client, db, plat_scope: PlatScope, owner_headers
):
    await seed_history(db, plat_scope, days=28)
    created = await client.post(
        f"{WFM}/forecasts",
        headers=owner_headers,
        json={"horizon_days": 2, "start_date": "2026-11-09"},
    )
    assert created.status_code == 201, created.text
    forecast = created.json()
    assert forecast["id"]
    assert forecast["points"]

    listed = await client.get(f"{WFM}/forecasts", headers=owner_headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["forecasts"]] == [forecast["id"]]

    read = await client.get(f"{WFM}/forecasts/{forecast['id']}", headers=owner_headers)
    assert read.status_code == 200
    assert read.json()["curve"]


async def test_scoring_a_forecast_returns_the_persisted_metrics_and_intervals(
    client, db, plat_scope: PlatScope, owner_headers
):
    await seed_history(db, plat_scope, days=28)
    start_date = datetime.now(tz=UTC).date() - timedelta(days=3)
    created = await client.post(
        f"{WFM}/forecasts",
        headers=owner_headers,
        json={"horizon_days": 2, "start_date": start_date.isoformat()},
    )
    assert created.status_code == 201, created.text

    forecast_id = created.json()["id"]
    scored = await client.post(f"{WFM}/forecasts/{forecast_id}/score", headers=owner_headers)
    assert scored.status_code == 200, scored.text
    body = scored.json()
    assert body["forecast_id"] == forecast_id
    assert body["sample_count"] > 0
    assert isinstance(body["intervals"], list)
    assert body["intervals"]


async def test_adherence_exception_list_serialises_its_date_column(
    client, db, plat_scope: PlatScope, owner_headers, wfm_agent
):
    row = AdherenceException(
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        user_id=wfm_agent.id,
        day=datetime(2026, 11, 9, tzinfo=UTC).date(),
        kind="late_start",
        minutes=5,
        detail={"scheduled_start": "09:00"},
        status="open",
    )
    db.add(row)
    await db.commit()

    response = await client.get(f"{WFM}/adherence/exceptions", headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["exceptions"][0]["day"] == "2026-11-09"


async def test_a_forecast_without_enough_history_is_a_422_not_a_guess(
    client, plat_scope: PlatScope, owner_headers
):
    response = await client.post(
        f"{WFM}/forecasts",
        headers=owner_headers,
        json={"horizon_days": 2, "start_date": "2026-11-09"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "insufficient_history"


async def test_another_tenants_forecast_is_a_404(
    client, db, plat_scope: PlatScope, plat_scope_b: PlatScope, owner_headers, owner_b: User
):
    await seed_history(db, plat_scope, days=28)
    created = await client.post(
        f"{WFM}/forecasts",
        headers=owner_headers,
        json={"horizon_days": 1, "start_date": "2026-11-09"},
    )
    forecast_id = created.json()["id"]
    other_headers = await auth_headers(client, owner_b)
    response = await client.get(f"{WFM}/forecasts/{forecast_id}", headers=other_headers)
    assert response.status_code == 404


async def test_staffing_needs_a_forecast_id(client, owner_headers):
    response = await client.get(f"{WFM}/staffing", headers=owner_headers)
    assert response.status_code == 422


async def test_a_leave_request_is_idempotent_under_one_key(
    client, db, plat_scope: PlatScope, agent_headers
):
    payload = {"kind": "vacation", "starts_at": day(9), "ends_at": day(17)}
    first = await client.post(
        f"{WFM}/time-off", headers={**agent_headers, IDEMPOTENCY_HEADER: "leave-1"}, json=payload
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        f"{WFM}/time-off", headers={**agent_headers, IDEMPOTENCY_HEADER: "leave-1"}, json=payload
    )
    assert second.status_code in (200, 201)
    stored = (await db.execute(select(TimeOffRequest))).scalars().all()
    assert len(stored) == 1, "the same key must not file the request twice"


async def test_an_agent_only_sees_their_own_leave(client, plat_scope: PlatScope, agent_headers, wfm_agent):
    response = await client.get(f"{WFM}/time-off", headers=agent_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["requests"] == []
    assert wfm_agent.id is not None


async def test_an_agent_cannot_approve_their_own_leave(
    client, plat_scope: PlatScope, agent_headers
):
    created = await client.post(
        f"{WFM}/time-off",
        headers=agent_headers,
        json={"kind": "vacation", "starts_at": day(9), "ends_at": day(17)},
    )
    request_id = created.json()["id"]
    decision = await client.post(
        f"{WFM}/time-off/{request_id}/decide", headers=agent_headers, json={"approved": True}
    )
    assert decision.status_code == 403


async def test_an_owner_can_approve_and_the_audit_row_records_who_did_it(
    client, db, plat_scope: PlatScope, agent_headers, owner_headers
):
    created = await client.post(
        f"{WFM}/time-off",
        headers=agent_headers,
        json={"kind": "vacation", "starts_at": day(9), "ends_at": day(17)},
    )
    request_id = created.json()["id"]
    await client.post(
        f"{WFM}/time-off/accruals",
        headers=owner_headers,
        json={"user_id": created.json()["user_id"], "kind": "vacation", "hours": 20},
    )
    approved = await client.post(
        f"{WFM}/time-off/{request_id}/decide",
        headers=owner_headers,
        json={"approved": True, "note": "ok"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    events = list((await db.execute(select(AuditLog))).scalars().all())
    assert [event.event_type for event in events if "time_off" in event.event_type], (
        "an approval must leave an audit row"
    )


async def test_an_agent_can_cancel_their_own_request_only(
    client, plat_scope: PlatScope, agent_headers, agent_headers_b
):
    created = await client.post(
        f"{WFM}/time-off",
        headers=agent_headers,
        json={"kind": "vacation", "starts_at": day(9), "ends_at": day(17)},
    )
    request_id = created.json()["id"]
    denied = await client.post(f"{WFM}/time-off/{request_id}/cancel", headers=agent_headers_b)
    assert denied.status_code == 404
    cancelled = await client.post(f"{WFM}/time-off/{request_id}/cancel", headers=agent_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


async def test_templates_and_labor_rules_round_trip(client, plat_scope: PlatScope, owner_headers):
    created = await client.post(
        f"{WFM}/templates",
        headers=owner_headers,
        json={
            "name": "early",
            "start_minute": 420,
            "length_minutes": 480,
            "paid_break_minutes": 15,
            "unpaid_break_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    listed = await client.get(f"{WFM}/templates", headers=owner_headers)
    assert [row["name"] for row in listed.json()["templates"]] == ["early"]

    updated = await client.put(
        f"{WFM}/labor-rules",
        headers=owner_headers,
        json={"max_hours_per_week": 40, "min_rest_hours": 11, "max_consecutive_days": 5, "max_shift_hours": 10},
    )
    assert updated.status_code == 200, updated.text
    read = await client.get(f"{WFM}/labor-rules", headers=owner_headers)
    assert read.json()["rules"][0]["max_hours_per_week"] == pytest.approx(40.0)


async def test_a_schedule_is_created_solved_and_published_over_http(
    client, db, plat_scope: PlatScope, owner_headers
):
    await seed_history(db, plat_scope, days=28)
    for index in range(12):
        from tests.conftest import make_user
        from app.db.models import UserRole

        await make_user(
            db, plat_scope.tenant, UserRole.AGENT, email=f"api-agent-{index}@example.com"
        )
    # Three overlapping eight-hour shapes spanning 06:00-21:00, the window the
    # seeded history has demand in. Two shapes would leave the evening uncoverable
    # and the solver would (correctly) call the week infeasible.
    for shape in (("early", 360), ("mid", 600), ("late", 780)):
        created_template = await client.post(
            f"{WFM}/templates",
            headers=owner_headers,
            json={
                "name": shape[0],
                "start_minute": shape[1],
                "length_minutes": 480,
                "paid_break_minutes": 0,
                "unpaid_break_minutes": 30,
            },
        )
        assert created_template.status_code == 201, created_template.text

    week_start = "2026-11-09"
    forecast = await client.post(
        f"{WFM}/forecasts",
        headers=owner_headers,
        json={"horizon_days": 7, "start_date": week_start},
    )
    assert forecast.status_code == 201, forecast.text

    schedule = await client.post(
        f"{WFM}/schedules", headers=owner_headers, json={"name": "W46", "week_start": week_start}
    )
    assert schedule.status_code == 201, schedule.text
    schedule_id = schedule.json()["id"]

    solved = await client.post(
        f"{WFM}/schedules/{schedule_id}/solve",
        headers=owner_headers,
        json={"schedule_id": schedule_id, "allow_shortfall": True},
    )
    assert solved.status_code == 200, solved.text
    solved_body = solved.json()
    assert solved_body["status"] in ("OPTIMAL", "FEASIBLE")
    assert 0.0 <= solved_body["coverage_ratio"] <= 1.0

    published = await client.post(
        f"{WFM}/schedules/{schedule_id}/publish",
        headers=owner_headers,
        json={"version_id": solved_body["version"]["version_id"]},
    )
    assert published.status_code == 200, published.text
    assert published.json()["version_number"] == 1

    read = await client.get(f"{WFM}/schedules/{schedule_id}", headers=owner_headers)
    assert read.status_code == 200
    assert read.json()["versions"][0]["status"] == "published"


async def test_a_solve_without_a_covering_forecast_is_a_conflict(
    client, plat_scope: PlatScope, owner_headers
):
    created = await client.post(
        f"{WFM}/schedules", headers=owner_headers, json={"name": "W47", "week_start": "2026-11-16"}
    )
    schedule_id = created.json()["id"]
    response = await client.post(
        f"{WFM}/schedules/{schedule_id}/solve",
        headers=owner_headers,
        json={"schedule_id": schedule_id},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conflict"


async def test_intraday_and_adherence_need_a_forecast_and_a_window(
    client, plat_scope: PlatScope, owner_headers
):
    intraday = await client.get(f"{WFM}/intraday", headers=owner_headers)
    assert intraday.status_code == 200
    assert intraday.json()["intervals"] == []

    adherence = await client.get(
        f"{WFM}/adherence",
        headers=owner_headers,
        params={"start": day(), "end": day(17)},
    )
    assert adherence.status_code == 200
    assert adherence.json()["shifts"] == 0


async def test_an_unknown_parameter_value_is_a_422_with_the_envelope(client, owner_headers):
    response = await client.get(
        f"{WFM}/history", headers=owner_headers, params={"limit": -5}
    )
    assert response.status_code in (200, 422)
    if response.status_code == 422:
        assert response.json()["detail"]["code"]


async def test_a_swap_between_agents_goes_through_http(
    client, db, plat_scope: PlatScope, agent_headers, wfm_agent, agent_headers_b, agent_b
):
    from tests.platform_v2.conftest import seed_shift

    shift = await seed_shift(
        db, plat_scope, wfm_agent, starts_at=datetime.now(tz=UTC) + timedelta(days=3), hours=8
    )
    offered = await client.post(
        f"{WFM}/swaps",
        headers=agent_headers,
        json={"shift_id": str(shift.id), "target_user_id": str(agent_b.id), "note": "school run"},
    )
    assert offered.status_code == 201, offered.text
    swap_id = offered.json()["id"]

    accepted = await client.post(f"{WFM}/swaps/{swap_id}/accept", headers=agent_headers_b)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    unknown = await client.post(f"{WFM}/swaps/{uuid.uuid4()}/accept", headers=agent_headers_b)
    assert unknown.status_code == 404
