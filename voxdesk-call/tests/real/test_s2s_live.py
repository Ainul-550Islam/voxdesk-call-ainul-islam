"""Real OpenAI Realtime and Gemini Live speech-to-speech WebSocket round-trip tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import time
import wave

import pytest
import websockets

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


def _wav_to_pcm16_24k(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return raw or (b"\x00\x00" * 4800)


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_openai_realtime_s2s_audio_roundtrip_and_latency(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
    sample_speech_wav_bytes: bytes,
) -> None:
    env = require_provider_env("openai_realtime", request=request, evidence_writer=evidence_writer)
    model = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview").strip()
    pcm_bytes = _wav_to_pcm16_24k(sample_speech_wav_bytes)
    uri = f"wss://api.openai.com/v1/realtime?model={model}"
    headers = {
        "Authorization": f"Bearer {env['OPENAI_API_KEY']}",
        "OpenAI-Beta": "realtime=v1",
    }

    first_audio_ms: float | None = None
    audio_chunks: list[bytes] = []
    try:
        async with websockets.connect(uri, additional_headers=headers, open_timeout=20) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "modalities": ["audio", "text"],
                            "instructions": "Greet the caller in under five words.",
                            "input_audio_format": "pcm16",
                            "output_audio_format": "pcm16",
                        },
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm_bytes).decode("ascii"),
                    }
                )
            )
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            t0 = time.perf_counter()
            await ws.send(json.dumps({"type": "response.create"}))

            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                raw_msg = await ws.recv()
                event = json.loads(raw_msg)
                etype = event.get("type", "")
                if etype in ("response.audio.delta", "response.output_audio.delta"):
                    delta_b64 = event.get("delta") or ""
                    if delta_b64:
                        if first_audio_ms is None:
                            first_audio_ms = (time.perf_counter() - t0) * 1000.0
                        audio_chunks.append(base64.b64decode(delta_b64))
                elif etype == "response.done":
                    break
                elif etype == "error":
                    raise AssertionError(f"OpenAI Realtime error event: {event}")

        combined = b"".join(audio_chunks)
        assert first_audio_ms is not None and len(combined) > 0
        digest = hashlib.sha256(combined).hexdigest()
        evidence_writer.write_latency_artifact(
            "openai_realtime",
            {"stage": "s2s", "model": model, "first_audio_ms": round(first_audio_ms, 3), "pcm_bytes": len(combined)},
        )
        evidence_writer.record(
            provider="openai_realtime",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=first_audio_ms,
            model=model,
            details={"output_pcm_bytes": len(combined), "output_sha256": digest},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="openai_realtime",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_gemini_live_s2s_audio_roundtrip_and_latency(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
    sample_speech_wav_bytes: bytes,
) -> None:
    env = require_provider_env("gemini_live", request=request, evidence_writer=evidence_writer)
    model = os.environ.get("GEMINI_LIVE_MODEL", "models/gemini-2.0-flash-exp").strip()
    pcm_bytes = _wav_to_pcm16_24k(sample_speech_wav_bytes)
    uri = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
        f"?key={env['GOOGLE_API_KEY']}"
    )

    first_audio_ms: float | None = None
    audio_chunks: list[bytes] = []
    try:
        async with websockets.connect(uri, open_timeout=20) as ws:
            await ws.send(
                json.dumps(
                    {
                        "setup": {
                            "model": model,
                            "generationConfig": {"responseModalities": ["AUDIO"]},
                        }
                    }
                )
            )
            await ws.recv()  # setupComplete
            t0 = time.perf_counter()
            await ws.send(
                json.dumps(
                    {
                        "realtimeInput": {
                            "mediaChunks": [
                                {
                                    "mimeType": "audio/pcm;rate=16000",
                                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                                }
                            ]
                        }
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "clientContent": {
                            "turns": [{"role": "user", "parts": [{"text": "Say hello briefly."}]}],
                            "turnComplete": True,
                        }
                    }
                )
            )

            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                raw_msg = await ws.recv()
                event = json.loads(raw_msg)
                server_content = event.get("serverContent") or {}
                model_turn = server_content.get("modelTurn") or {}
                for part in model_turn.get("parts") or []:
                    inline = part.get("inlineData") or {}
                    if inline.get("data"):
                        if first_audio_ms is None:
                            first_audio_ms = (time.perf_counter() - t0) * 1000.0
                        audio_chunks.append(base64.b64decode(inline["data"]))
                if server_content.get("turnComplete"):
                    break

        combined = b"".join(audio_chunks)
        assert first_audio_ms is not None and len(combined) > 0
        digest = hashlib.sha256(combined).hexdigest()
        evidence_writer.write_latency_artifact(
            "gemini_live",
            {"stage": "s2s", "model": model, "first_audio_ms": round(first_audio_ms, 3), "pcm_bytes": len(combined)},
        )
        evidence_writer.record(
            provider="gemini_live",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=first_audio_ms,
            model=model,
            details={"output_pcm_bytes": len(combined), "output_sha256": digest},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="gemini_live",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
