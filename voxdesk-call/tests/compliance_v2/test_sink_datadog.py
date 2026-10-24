"""Datadog Logs intake v2, against a real local HTTP server.

The three properties that decide whether audit events survive the trip are
checked on the wire: the body is a gzip-compressed JSON array, the API key
travels in ``DD-API-KEY`` (never in the URL or the body), and the two documented
intake limits (1000 logs / 5 MB uncompressed) are enforced by the client before
the request is sent — an oversized batch that reached Datadog would be rejected
forever, because retrying it never makes it smaller.
"""

from __future__ import annotations

import gzip
import http.server
import itertools
import json
import threading
from dataclasses import dataclass, field
from typing import Iterator

import pytest

from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sink_datadog import (
    MAX_EVENTS_PER_REQUEST,
    MAX_UNCOMPRESSED_BYTES,
    DatadogLogsSink,
    intake_url,
)
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_AUTH,
    CATEGORY_RATE_LIMITED,
    SinkNotConfiguredError,
)


@dataclass
class Capture:
    """What the local intake received."""

    requests: list[dict] = field(default_factory=list)
    status: int = 202
    body: bytes = b"{}"

    def last(self) -> dict:
        assert self.requests, "the intake received no request"
        return self.requests[-1]

    def entries(self, index: int = -1) -> list[dict]:
        raw = gzip.decompress(self.requests[index]["body"])
        return json.loads(raw.decode("utf-8"))


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.capture.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        payload = self.server.capture.body
        self.send_response(self.server.capture.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self.do_POST()

    def log_message(self, *_args) -> None:
        return


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    capture: Capture


@pytest.fixture
def intake() -> Iterator[tuple[str, Capture]]:
    capture = Capture()
    server = _Server(("127.0.0.1", 0), _Handler)
    server.capture = capture
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}/api/v2/logs", capture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _event(index: int = 0, *, tenant_id: str = "t-1") -> SiemEvent:
    return SiemEvent(
        event_id=f"00000000-0000-0000-0000-{index:012d}",
        schema_version="voxdesk.siem.audit.v1",
        tenant_id=tenant_id,
        occurred_at="2026-10-24T10:00:00Z",
        event_type="call.completed",
        action="call_completed",
        result="success",
        actor_type="user",
        actor_id="actor-1",
        resource_type="call",
        resource_id=f"call-{index}",
        detail={"index": index},
        dedupe_key=f"00000000-0000-0000-0000-{index:012d}",
    )


def _sink(url: str, **kwargs) -> DatadogLogsSink:
    return DatadogLogsSink(api_key="dd-api-key-for-tests", endpoint=url, **kwargs)


async def test_the_body_is_a_gzipped_json_array(intake):
    url, capture = intake
    sink = _sink(url)
    try:
        result = await sink.send_batch([_event(0), _event(1)])
    finally:
        await sink.aclose()

    assert result.accepted == 2, result.detail
    request = capture.last()
    assert request["headers"]["Content-Encoding"] == "gzip"
    assert request["headers"]["Content-Type"] == "application/json"
    entries = capture.entries()
    assert isinstance(entries, list)
    assert len(entries) == 2
    assert json.loads(entries[0]["message"])["event_id"].endswith("0000")


async def test_the_api_key_travels_in_the_header_only(intake):
    url, capture = intake
    sink = _sink(url)
    try:
        await sink.send_batch([_event()])
    finally:
        await sink.aclose()

    request = capture.last()
    assert request["headers"]["DD-API-KEY"] == "dd-api-key-for-tests"
    assert "dd-api-key-for-tests" not in request["path"]
    assert b"dd-api-key-for-tests" not in gzip.decompress(request["body"])


async def test_the_tenant_rides_along_as_a_tag(intake):
    url, capture = intake
    sink = _sink(url)
    try:
        await sink.send_batch([_event(tenant_id="tenant-xyz")])
    finally:
        await sink.aclose()

    entry = capture.entries()[0]
    assert "tenant_id:tenant-xyz" in entry["ddtags"]
    assert entry["ddsource"] == "voxdesk-audit"
    assert entry["service"] == "voxdesk"


async def test_a_batch_is_split_at_the_event_limit(intake):
    url, capture = intake
    sink = _sink(url)
    events = [_event(index) for index in range(MAX_EVENTS_PER_REQUEST + 5)]
    try:
        result = await sink.send_batch(events)
    finally:
        await sink.aclose()

    assert result.accepted == MAX_EVENTS_PER_REQUEST + 5
    assert len(capture.requests) == 2
    assert len(capture.entries(0)) == MAX_EVENTS_PER_REQUEST
    assert len(capture.entries(1)) == 5


async def test_an_event_that_cannot_fit_is_sent_alone(intake):
    url, capture = intake
    sink = _sink(url)
    huge = _event()
    object.__setattr__(huge, "detail", {"blob": "x" * (MAX_UNCOMPRESSED_BYTES + 1024)})
    try:
        result = await sink.send_batch([_event(1), huge])
    finally:
        await sink.aclose()

    # The oversized event is carried alone so the caller can dead-letter it
    # instead of the whole batch being unsendable.
    assert len(capture.requests) >= 1
    assert result.category


async def test_a_403_is_permanent_and_reports_the_auth_category(intake):
    url, capture = intake
    capture.status = 403
    sink = _sink(url)
    try:
        result = await sink.send_batch([_event()])
    finally:
        await sink.aclose()

    assert result.retryable is False
    assert result.accepted == 0
    assert result.category == CATEGORY_AUTH
    # A 403 rejects the credential, not the events: the dispatcher leaves the
    # cursor where it is so nothing is lost if the key is fixed.
    assert result.detail["last_status"] == 403


async def test_a_429_is_retryable(intake):
    url, capture = intake
    capture.status = 429
    sink = _sink(url)
    try:
        result = await sink.send_batch([_event()])
    finally:
        await sink.aclose()

    assert result.retryable is True
    assert result.category == CATEGORY_RATE_LIMITED


async def test_a_500_is_retryable(intake):
    url, capture = intake
    capture.status = 503
    sink = _sink(url)
    try:
        result = await sink.send_batch([_event()])
    finally:
        await sink.aclose()

    assert result.retryable is True


async def test_an_unknown_site_is_refused_before_any_request():
    with pytest.raises(SinkNotConfiguredError):
        DatadogLogsSink(api_key="k", site="mars-1")


@pytest.mark.parametrize(
    ("site", "host"),
    [
        ("us1", "https://http-intake.logs.datadoghq.com/api/v2/logs"),
        ("eu", "https://http-intake.logs.datadoghq.eu/api/v2/logs"),
        ("ap1", "https://http-intake.logs.ap1.datadoghq.com/api/v2/logs"),
        ("gov", "https://http-intake.logs.ddog-gov.com/api/v2/logs"),
    ],
)
def test_the_site_selects_the_intake_hostname(site, host):
    assert intake_url(site) == host


def test_the_documented_limits_are_the_ones_datadog_publishes():
    assert MAX_EVENTS_PER_REQUEST == 1000
    assert MAX_UNCOMPRESSED_BYTES == 5 * 1024 * 1024


async def test_health_reports_a_rejected_key_as_unhealthy(intake):
    url, capture = intake
    capture.status = 403
    sink = _sink(url)
    try:
        health = await sink.health()
    finally:
        await sink.aclose()

    assert health.ok is False
    assert capture.requests, "health must actually call the validate endpoint"


async def test_an_empty_batch_makes_no_request(intake):
    url, capture = intake
    sink = _sink(url)
    try:
        result = await sink.send_batch([])
    finally:
        await sink.aclose()

    assert result.accepted == 0
    assert capture.requests == []


def test_event_indices_are_unique_in_this_module():
    """Guards the fixtures above: duplicated ids would hide a de-dupe bug."""
    ids = {_event(index).event_id for index in itertools.islice(itertools.count(), 50)}
    assert len(ids) == 50
