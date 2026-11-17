"""Opt-in SIPp-to-gateway call test.

This test really originates a call through the configured gateway and starts a
SIPp UAS scenario. It is intentionally gated by explicit lab settings; it must
never dial a carrier destination by guessing one from ordinary CI config.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.telephony.sip.sip_gateway_client import SipGatewayClient

def _settings() -> dict[str, str]:
    names = {
        "gateway_url": "SIP_STACK_GATEWAY_URL",
        "token": "SIP_GATEWAY_TOKEN",
        "caller": "SIP_STACK_CALLER",
        "target": "SIP_STACK_TARGET",
        "sipp_host": "SIP_STACK_SIPP_HOST",
    }
    values = {key: os.environ.get(name, "").strip() for key, name in names.items()}
    missing = [names[key] for key, value in values.items() if not value]
    if missing:
        pytest.skip("set the explicit lab variables to run a SIPp call: " + ", ".join(missing))
    return values


def _validate_target_is_local_sipp(target: str, sipp_host: str, sipp_port: int) -> None:
    """Reject anything except a SIP URI addressed to the configured lab UAS."""
    if not target.lower().startswith("sip:"):
        raise ValueError("SIP_STACK_TARGET must be a sip: URI, not a carrier or PSTN destination")
    try:
        parsed = urlsplit("sip://" + target[4:])
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("SIP_STACK_TARGET is malformed") from exc
    expected_host = sipp_host.strip().strip("[]").lower()
    actual_host = (parsed.hostname or "").strip("[]").lower()
    if (
        not parsed.username
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or ";" in parsed.netloc
        or actual_host != expected_host
        or parsed_port != sipp_port
    ):
        raise ValueError(
            "SIP_STACK_TARGET must name a user at SIP_STACK_SIPP_HOST:SIP_STACK_SIPP_PORT"
        )


@pytest.mark.unit
def test_sipp_stack_target_is_limited_to_the_configured_lab_uas() -> None:
    _validate_target_is_local_sipp("sip:callee@192.0.2.20:5060", "192.0.2.20", 5060)
    for unsafe in (
        "sip:callee@carrier.example:5060",
        "sip:callee@192.0.2.20:5061",
        "sips:callee@192.0.2.20:5060",
        "+15550001111",
    ):
        with pytest.raises(ValueError):
            _validate_target_is_local_sipp(unsafe, "192.0.2.20", 5060)


@pytest.mark.stack
@pytest.mark.asyncio
async def test_sipp_answer_scenario_completes_a_real_gateway_call() -> None:
    """Place one SIP call to a disposable SIPp UAS; never targets a PSTN number."""
    config = _settings()
    sipp = shutil.which("sipp")
    if sipp is None:
        pytest.skip("SIPp is not installed; use the SIP stack container or install SIPp")

    scenario = Path(__file__).with_name("sipp") / "outbound-answer.xml"
    port = int(os.environ.get("SIP_STACK_SIPP_PORT", "5060"))
    media_port = int(os.environ.get("SIP_STACK_SIPP_MEDIA_PORT", "40000"))
    if not 1024 <= port <= 65535 or not 1024 <= media_port <= 65535:
        pytest.fail("SIP_STACK_SIPP_PORT and SIP_STACK_SIPP_MEDIA_PORT must be unprivileged ports")
    if config["sipp_host"].strip().lower() in {"0.0.0.0", "::"}:
        pytest.fail("SIP_STACK_SIPP_HOST must be a concrete address reachable by the gateway")
    try:
        _validate_target_is_local_sipp(config["target"], config["sipp_host"], port)
    except ValueError as exc:
        pytest.fail(str(exc))

    command = [
        sipp,
        "-sf",
        str(scenario),
        "-i",
        config["sipp_host"],
        "-p",
        str(port),
        "-mi",
        config["sipp_host"],
        "-mp",
        str(media_port),
        "-m",
        "1",
        "-timeout",
        "30s",
        "-trace_msg",
        "-trace_err",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = SipGatewayClient(
        base_url=config["gateway_url"],
        token=config["token"],
        timeout_seconds=15.0,
    )
    call_id = f"sipp-{uuid.uuid4().hex[:24]}"
    call = None
    try:
        # Give the UAS time to bind its signalling socket before originating.
        await asyncio.sleep(0.5)
        if process.poll() is not None:
            output, _ = process.communicate(timeout=2)
            pytest.fail(f"SIPp exited before the call (status {process.returncode}):\n{output[-4000:]}")

        call = await client.originate(
            to_number=config["target"],
            from_number=config["caller"],
            call_id=call_id,
            idempotency_key=f"{call_id}-idempotency",
        )
        assert call.id == call_id, call.as_dict()
        assert call.status in {"active", "terminated"}, call.as_dict()

        # The SIPp scenario sends a BYE after its answer. Verify the gateway
        # observes that dialog end rather than leaving an active RTP lease.
        final = call
        for _ in range(30):
            final = await client.call_status(call_id)
            if final.status in {"terminated", "failed"}:
                break
            await asyncio.sleep(0.2)
        assert final.status == "terminated", final.as_dict()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        output, _ = process.communicate(timeout=2)
        if call is not None and process.returncode not in {0, -15, 143}:
            pytest.fail(f"SIPp exited with status {process.returncode}:\n{output[-4000:]}")
