"""Keep the real-provider workflow scheduled, evidence-producing, and freshness-gated."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.real.conftest import PROVIDER_ENV_GATES

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "real-integrations.yml"


def _workflow() -> dict:
    # BaseLoader preserves GitHub's `on` key as a string (PyYAML's default YAML 1.1
    # resolver otherwise interprets it as boolean True).
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_real_integrations_runs_nightly_and_by_dispatch() -> None:
    config = _workflow()
    triggers = config["on"]
    assert triggers.get("schedule"), "real provider evidence must refresh nightly"
    assert any("cron" in entry for entry in triggers["schedule"])
    assert "workflow_dispatch" in triggers, "operators need a manual pre-release run"


def test_real_provider_step_maps_every_environment_gate() -> None:
    job = _workflow()["jobs"]["real-providers"]
    provider_step = next(step for step in job["steps"] if step.get("id") == "provider-tests")
    assert "tests/real" in provider_step["run"]
    env = provider_step["env"]
    assert env.get("VOXDESK_REAL_INTEGRATION") == "1"
    assert "VOXDESK_REAL_EVIDENCE_DIR" in env

    required = {name for names in PROVIDER_ENV_GATES.values() for name in names}
    missing = sorted(required - set(env))
    assert not missing, f"provider environment gates lack GitHub secret mappings: {missing}"


def test_workflow_refreshes_feature_junit_and_gates_freshness_after_generation() -> None:
    steps = _workflow()["jobs"]["real-providers"]["steps"]
    by_id = {step.get("id"): step for step in steps if step.get("id")}

    feature_run = by_id["feature-tests"]["run"]
    assert "feature_evidence.xml" in feature_run

    regenerate = by_id["regenerate-status"]["run"]
    assert "scripts/generate_feature_matrix.py" in regenerate
    assert "--max-age-days 7" in regenerate

    freshness = by_id["freshness-gate"]["run"]
    assert "--check" in freshness
    assert "--require-fresh-real-runs" in freshness
    assert "--max-age-days 7" in freshness


def test_manual_dispatch_can_request_an_explicit_n_call_pstn_study() -> None:
    config = _workflow()
    dispatch_input = config["on"]["workflow_dispatch"]["inputs"]["pstn_study_calls"]
    assert dispatch_input["type"] == "choice"
    assert dispatch_input["options"] == ["0", "1", "3", "5", "10"]
    assert dispatch_input["default"] == "0"

    steps = config["jobs"]["real-providers"]["steps"]
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    study = by_id["pstn-study"]
    assert "workflow_dispatch" in study["if"]
    assert "pstn_study_calls != '0'" in study["if"]
    assert "scripts/run_real_call_study.py" in study["run"]
    assert "--calls \"$PSTN_STUDY_CALLS\"" in study["run"]

    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "evidence/latency/" in upload["with"]["path"]
    final_gate = next(step for step in steps if "Fail if tests" in step.get("name", ""))
    assert "PSTN_STUDY_REQUESTED" in final_gate["env"]
    assert "PSTN_STUDY_OUTCOME" in final_gate["env"]
    assert "PSTN_STUDY_OUTCOME" in final_gate["run"]


def test_workflow_uploads_evidence_even_after_a_failed_provider_run() -> None:
    steps = _workflow()["jobs"]["real-providers"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload.get("if") == "always()"
    artifact_paths = upload["with"]["path"]
    assert "evidence/real-integrations/" in artifact_paths
    assert "docs/EVIDENCE_STATUS.md" in artifact_paths
    assert "docs/SALES/FEATURE_MATRIX_VERIFIED.md" in artifact_paths

    final_gate = next(step for step in steps if step.get("id") is None and "Fail if tests" in step.get("name", ""))
    assert final_gate.get("if") == "always()"
    assert "FRESHNESS_OUTCOME" in final_gate["env"]
    assert "PROVIDER_TEST_OUTCOME" in final_gate["env"]


async def test_telnyx_live_smoke_is_opt_in_and_emits_call_control_only_evidence(monkeypatch) -> None:
    from types import SimpleNamespace

    from tests.real import test_telnyx_live

    monkeypatch.setenv("VOXDESK_REAL_INTEGRATION", "1")
    monkeypatch.setenv("TELNYX_API_KEY", "fixture-key")
    monkeypatch.setenv("TELNYX_CONNECTION_ID", "fixture-connection")
    monkeypatch.setenv("TELNYX_FROM_NUMBER", "+15550001001")
    monkeypatch.setenv("TELNYX_TO_NUMBER", "+15550001002")
    monkeypatch.setenv("VOXDESK_PUBLIC_BASE_URL", "https://voxdesk.example.test")

    call_control_id = "v3:fixture-call"

    class Response:
        def __init__(self, status_code: int, body: dict) -> None:
            self.status_code = status_code
            self._body = body
            self.text = str(body)

        def json(self) -> dict:
            return self._body

    class FakeClient:
        def __init__(self) -> None:
            self.posts: list[tuple[str, dict]] = []
            self.lookups: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, json: dict) -> Response:
            self.posts.append((url, json))
            if url == "https://api.telnyx.com/v2/calls":
                return Response(201, {"data": {"call_control_id": call_control_id}})
            if url.endswith(f"/calls/{call_control_id}/actions/hangup"):
                return Response(202, {})
            raise AssertionError(f"Unexpected Telnyx API request: {url}")

        async def get(self, url: str) -> Response:
            self.lookups.append(url)
            assert url.endswith(f"/calls/{call_control_id}")
            return Response(200, {"data": {"call_control_id": call_control_id, "state": "ringing"}})

    fake_client = FakeClient()
    monkeypatch.setattr(test_telnyx_live.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    class EvidenceCapture:
        def __init__(self) -> None:
            self.entries: list[dict] = []

        def record(self, **entry) -> dict:
            self.entries.append(entry)
            return entry

    evidence = EvidenceCapture()
    await test_telnyx_live.test_telnyx_call_control_create_lookup_hangup_request(
        request=SimpleNamespace(node=SimpleNamespace(nodeid="controlled-telnyx-test")),
        evidence_writer=evidence,
    )

    assert len(fake_client.posts) == 2
    assert "stream_url" not in fake_client.posts[0][1]
    assert fake_client.posts[1][0].endswith("/actions/hangup")
    assert len(fake_client.lookups) == 1
    assert len(evidence.entries) == 1
    recorded = evidence.entries[0]
    assert recorded["status"] == "PASS"
    assert "latency_ms" not in recorded
    details = recorded["details"]
    assert details["evidence_scope"] == "call_control_create_lookup_hangup_request"
    assert details["media_stream_requested"] is False
    assert details["media_websocket_observed"] is False
    assert details["application_webhook_asserted"] is False
    assert details["hangup_action_accepted"] is True


async def test_sip_gateway_pcap_smoke_validates_metadata_without_persisting_payload(monkeypatch) -> None:
    import base64
    from types import SimpleNamespace

    from tests.real import test_sip_gateway_pcap

    env = {
        "VOXDESK_SIP_GATEWAY_URL": "https://sip-gateway.example.test",
        "VOXDESK_SIP_GATEWAY_TOKEN": "fixture-token",
        "VOXDESK_SIP_CAPTURE_TENANT_ID": "tenant-fixture",
        "VOXDESK_SIP_CAPTURE_CALL_ID": "call-fixture",
    }
    monkeypatch.setattr(
        test_sip_gateway_pcap,
        "require_provider_env",
        lambda *_args, **_kwargs: env,
    )
    packet = bytes(20)
    pcap = (
        b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00"
        + bytes(8)
        + (262).to_bytes(4, "little")
        + (101).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + bytes(4)
        + len(packet).to_bytes(4, "little")
        + len(packet).to_bytes(4, "little")
        + packet
    )
    response_body = {
        "content_type": "application/vnd.tcpdump.pcap",
        "data_base64": base64.b64encode(pcap).decode("ascii"),
        "packet_count": 1,
        "truncated": False,
    }

    class FakeResponse:
        status_code = 200

        def json(self):
            return response_body

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 20.0
            self.request = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            self.request = {"url": url, "headers": headers, "json": json}
            return FakeResponse()

    fake_client = FakeClient(timeout=20.0)
    def fake_client_factory(**kwargs):
        assert kwargs == {"timeout": 20.0}
        return fake_client

    monkeypatch.setattr(
        test_sip_gateway_pcap.httpx,
        "AsyncClient",
        fake_client_factory,
    )

    class EvidenceCapture:
        def __init__(self):
            self.entries = []

        def record(self, **entry):
            self.entries.append(entry)
            return entry

    evidence = EvidenceCapture()
    await test_sip_gateway_pcap.test_sip_gateway_captures_active_call_and_reports_metadata(
        request=SimpleNamespace(node=SimpleNamespace(nodeid="controlled-sip-pcap-test")),
        evidence_writer=evidence,
    )

    assert fake_client.request["url"].endswith("/v1/calls/call-fixture/pcap")
    assert fake_client.request["headers"]["Authorization"] == "Bearer fixture-token"
    assert fake_client.request["json"] == {
        "tenant_id": "tenant-fixture",
        "call_id": "call-fixture",
        "duration_seconds": 1,
        "snaplen": 262,
        "direction": "both",
    }
    assert len(evidence.entries) == 1
    recorded = evidence.entries[0]
    assert recorded["provider"] == "sip_gateway_pcap"
    assert recorded["status"] == "PASS"
    details = recorded["details"]
    assert details["packet_count"] == 1
    assert details["capture_persisted"] is False
    assert "tenant_id" not in details and "call_id" not in details


async def test_sip_pcap_s3_smoke_roundtrips_and_cleans_up_with_fake_storage(monkeypatch) -> None:
    from types import SimpleNamespace

    from tests.real import test_sip_gateway_pcap_s3

    env = {
        "VOXDESK_REAL_PCAP_S3_BUCKET": "fixture-bucket",
        "VOXDESK_REAL_PCAP_S3_REGION": "us-east-1",
        "VOXDESK_REAL_PCAP_S3_ENDPOINT_URL": "",
        "VOXDESK_REAL_PCAP_S3_SIGNING_SECRET": "fixture-signing-secret",
        "AWS_ACCESS_KEY_ID": "fixture-access-key",
        "AWS_SECRET_ACCESS_KEY": "fixture-secret-key",
    }
    monkeypatch.setattr(
        test_sip_gateway_pcap_s3,
        "require_provider_env",
        lambda *_args, **_kwargs: env,
    )

    class FakeClient:
        def __init__(self, objects):
            self.objects = objects

        def delete_object(self, *, Bucket, Key):
            assert Bucket == "fixture-bucket"
            self.objects.pop(Key, None)

    class FakeStorage:
        bucket = "fixture-bucket"

        def __init__(self):
            self.objects = {}
            self.client = FakeClient(self.objects)

        async def put(self, key, data, *, content_type=None):
            assert content_type == "application/vnd.tcpdump.pcap"
            self.objects[key] = bytes(data)
            return key

        async def get(self, key):
            return self.objects[key]

        def _get_client(self):
            return self.client

    backing = FakeStorage()

    def fake_s3_storage(*, bucket, region, endpoint_url):
        assert (bucket, region, endpoint_url) == ("fixture-bucket", "us-east-1", None)
        return backing

    monkeypatch.setattr(test_sip_gateway_pcap_s3, "S3Storage", fake_s3_storage)

    class EvidenceCapture:
        def __init__(self):
            self.entries = []

        def record(self, **entry):
            self.entries.append(entry)
            return entry

    evidence = EvidenceCapture()
    await test_sip_gateway_pcap_s3.test_sip_pcap_s3_roundtrip_signed_url_and_cleanup(
        request=SimpleNamespace(node=SimpleNamespace(nodeid="controlled-sip-pcap-s3-test")),
        evidence_writer=evidence,
    )

    assert backing.objects == {}
    assert len(evidence.entries) == 1
    recorded = evidence.entries[0]
    assert recorded["provider"] == "sip_gateway_s3"
    assert recorded["status"] == "PASS"
    assert recorded["details"]["app_signed_url_verified"] is True
    assert recorded["details"]["object_persisted_after_test"] is False
