"""PART 14 / P14-09 — tenant-selectable webhook events and fan-out."""

from __future__ import annotations

import pytest

from app.webhooks import event_selection
from app.webhooks.call_event_catalog import EVENT_NAMES
from app.webhooks.delivery import deliver_to_endpoints


def test_a_topic_expands_to_its_events() -> None:
    resolved = event_selection.validate_selection(["calls"])
    assert resolved == ["call_started", "call_ended", "call_analyzed"]


def test_the_wildcard_expands_to_the_events_that_exist_today() -> None:
    resolved = event_selection.validate_selection(["*"])
    assert set(resolved) == set(EVENT_NAMES)
    # A wildcard selection is stored expanded, so a future event type cannot
    # start arriving at a customer's URL without them re-saving.
    assert "*" not in resolved


def test_an_unknown_event_is_refused_with_its_name() -> None:
    with pytest.raises(event_selection.SelectionError) as exc:
        event_selection.validate_selection(["call_exploded"])
    assert exc.value.code == "unknown_events"
    assert "call_exploded" in str(exc.value)


def test_an_empty_selection_is_refused() -> None:
    with pytest.raises(event_selection.SelectionError) as exc:
        event_selection.validate_selection([])
    assert exc.value.code == "empty_selection"


def test_order_is_stable_and_duplicates_collapse() -> None:
    assert event_selection.expand(["recording_ready", "recordings", "recording_ready"]) == [
        "recording_ready"
    ]


def test_legacy_aliases_still_match() -> None:
    assert event_selection.accepts(["call.recording.ready"], "recording_ready") is True
    assert event_selection.accepts(["call.completed"], "call_ended", {"status": "completed"}) is True
    assert event_selection.accepts(["call.failed"], "call_ended", {"status": "completed"}) is False


def test_an_unselected_event_is_not_accepted() -> None:
    assert event_selection.accepts(["recording_ready"], "call_started") is False
    assert event_selection.accepts(["recording_ready"], "not_an_event") is False


def test_endpoint_selection_ignores_endpoints_that_do_not_want_the_event() -> None:
    endpoints = [
        event_selection.Endpoint("e1", "https://a.test/hook", ("calls",)),
        event_selection.Endpoint("e2", "https://b.test/hook", ("recordings",)),
    ]
    chosen = event_selection.select_endpoints(endpoints, "call_started", {"status": "ringing"})
    assert [endpoint.id for endpoint in chosen] == ["e1"]


def test_describe_publishes_a_schema_for_every_event() -> None:
    described = event_selection.describe()
    assert described["version"] == 1
    assert set(described["events"]) == set(EVENT_NAMES)
    assert described["documents"]["call_ended"]["type"] == "object"


@pytest.mark.asyncio
async def test_fan_out_delivers_only_to_subscribed_endpoints() -> None:
    delivered: list[str] = []

    class FakeHttp:
        async def __aenter__(self) -> "FakeHttp":
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def request(self, *_args, **_kwargs):  # pragma: no cover - not reached
            raise AssertionError("deliver must be stubbed")

    # `deliver_to_endpoints` calls `deliver`, which opens a socket. Patch it out
    # so this asserts the *routing* decision, which is the part under test.
    import app.webhooks.delivery as delivery_module

    original = delivery_module.deliver

    async def fake_deliver(**kwargs):
        delivered.append(kwargs["url"])
        return delivery_module.HttpResult(kind="delivered", status=200, latency_ms=1.0)

    delivery_module.deliver = fake_deliver
    try:
        endpoints = [
            event_selection.Endpoint("e1", "https://a.test/hook", ("recordings",)),
            event_selection.Endpoint("e2", "https://b.test/hook", ("calls",)),
        ]
        results = await deliver_to_endpoints(
            event="call_started",
            payload={"status": "ringing"},
            body=b"{}",
            secret_for=lambda endpoint: "secret-" + endpoint.id,
            endpoints=endpoints,
            event_id="evt-1",
        )
    finally:
        delivery_module.deliver = original

    assert delivered == ["https://b.test/hook"]
    assert results == [
        {"endpoint_id": "e2", "status": 200, "kind": "delivered", "latency_ms": 1.0}
    ]


@pytest.mark.asyncio
async def test_a_refused_url_is_recorded_per_endpoint_without_raising() -> None:
    import app.webhooks.delivery as delivery_module

    original = delivery_module.deliver

    async def refusing(**kwargs):
        raise delivery_module.OutboundUrlError("blocked host")

    delivery_module.deliver = refusing
    try:
        results = await deliver_to_endpoints(
            event="recording_ready",
            payload={"recording_id": "00000000-0000-0000-0000-000000000001"},
            body=b"{}",
            secret_for=lambda _endpoint: "s",
            endpoints=[event_selection.Endpoint("e1", "http://127.0.0.1/hook", ("recordings",))],
            event_id="evt-2",
        )
    finally:
        delivery_module.deliver = original

    assert results[0]["kind"] == "refused"
    assert "blocked host" in results[0]["error"]
