"""Real streaming STT (Deepgram), TTS (ElevenLabs, Cartesia), and LLM (OpenAI, Anthropic) latency tests."""

from __future__ import annotations

import os
import time

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_deepgram_streaming_stt_ttfb(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
    sample_speech_wav_bytes: bytes,
) -> None:
    env = require_provider_env("deepgram", request=request, evidence_writer=evidence_writer)
    model = os.environ.get("DEEPGRAM_MODEL", "nova-2").strip()
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/listen",
                params={"model": model, "smart_format": "true", "punctuate": "true"},
                headers={
                    "Authorization": f"Token {env['DEEPGRAM_API_KEY']}",
                    "Content-Type": "audio/wav",
                },
                content=sample_speech_wav_bytes,
            )
            ttfb_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            channels = ((payload.get("results") or {}).get("channels") or [])
            assert channels, f"Unexpected Deepgram response: {payload}"

        evidence_writer.write_latency_artifact(
            "deepgram",
            {"stage": "stt", "model": model, "ttfb_ms": round(ttfb_ms, 3)},
        )
        evidence_writer.record(
            provider="deepgram",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=ttfb_ms,
            model=model,
            details={"stage": "stt", "audio_bytes": len(sample_speech_wav_bytes)},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="deepgram",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_elevenlabs_streaming_tts_ttfb(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("elevenlabs", request=request, evidence_writer=evidence_writer)
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip()
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5").strip()
    t0 = time.perf_counter()
    first_chunk_ms: float | None = None
    total_bytes = 0
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
                params={"output_format": "pcm_16000"},
                headers={
                    "xi-api-key": env["ELEVENLABS_API_KEY"],
                    "Content-Type": "application/json",
                },
                json={
                    "text": "Hello, thank you for calling VoxDesk support.",
                    "model_id": model_id,
                },
            ) as resp:
                assert resp.status_code == 200, (await resp.aread()).decode("utf-8", "replace")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        if first_chunk_ms is None:
                            first_chunk_ms = (time.perf_counter() - t0) * 1000.0
                        total_bytes += len(chunk)

        assert first_chunk_ms is not None and total_bytes > 0
        evidence_writer.write_latency_artifact(
            "elevenlabs",
            {"stage": "tts", "model": model_id, "ttfb_ms": round(first_chunk_ms, 3), "pcm_bytes": total_bytes},
        )
        evidence_writer.record(
            provider="elevenlabs",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=first_chunk_ms,
            model=model_id,
            details={"stage": "tts", "voice_id": voice_id, "pcm_bytes": total_bytes},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="elevenlabs",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_cartesia_streaming_tts_ttfb(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("cartesia", request=request, evidence_writer=evidence_writer)
    model_id = os.environ.get("CARTESIA_MODEL_ID", "sonic-english").strip()
    voice_id = os.environ.get("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091").strip()
    t0 = time.perf_counter()
    first_chunk_ms: float | None = None
    total_bytes = 0
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": env["CARTESIA_API_KEY"],
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json",
                },
                json={
                    "model_id": model_id,
                    "transcript": "Your appointment is confirmed for Tuesday at ten AM.",
                    "voice": {"mode": "id", "id": voice_id},
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": 16000,
                    },
                },
            ) as resp:
                assert resp.status_code == 200, (await resp.aread()).decode("utf-8", "replace")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        if first_chunk_ms is None:
                            first_chunk_ms = (time.perf_counter() - t0) * 1000.0
                        total_bytes += len(chunk)

        assert first_chunk_ms is not None and total_bytes > 0
        evidence_writer.write_latency_artifact(
            "cartesia",
            {"stage": "tts", "model": model_id, "ttfb_ms": round(first_chunk_ms, 3), "pcm_bytes": total_bytes},
        )
        evidence_writer.record(
            provider="cartesia",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=first_chunk_ms,
            model=model_id,
            details={"stage": "tts", "voice_id": voice_id, "pcm_bytes": total_bytes},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="cartesia",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_openai_streaming_llm_ttft(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("openai", request=request, evidence_writer=evidence_writer)
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    t0 = time.perf_counter()
    ttft_ms: float | None = None
    content_parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {env['OPENAI_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "stream": True,
                    "max_tokens": 32,
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": "Reply concisely in one sentence."},
                        {"role": "user", "content": "Confirm appointment slot 10:00 AM."},
                    ],
                },
            ) as resp:
                assert resp.status_code == 200, (await resp.aread()).decode("utf-8", "replace")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                        continue
                    chunk_json = __import__("json").loads(line[6:])
                    choices = chunk_json.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - t0) * 1000.0
                            content_parts.append(delta)

        assert ttft_ms is not None and "".join(content_parts).strip()
        evidence_writer.write_latency_artifact(
            "openai",
            {"stage": "llm", "model": model, "ttft_ms": round(ttft_ms, 3)},
        )
        evidence_writer.record(
            provider="openai",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=ttft_ms,
            model=model,
            details={"stage": "llm", "reply_chars": len("".join(content_parts))},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="openai",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_anthropic_streaming_llm_ttft(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("anthropic", request=request, evidence_writer=evidence_writer)
    model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
    t0 = time.perf_counter()
    ttft_ms: float | None = None
    content_parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": env["ANTHROPIC_API_KEY"],
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "stream": True,
                    "max_tokens": 32,
                    "messages": [
                        {"role": "user", "content": "Confirm appointment slot 10:00 AM in one short sentence."}
                    ],
                },
            ) as resp:
                assert resp.status_code == 200, (await resp.aread()).decode("utf-8", "replace")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = __import__("json").loads(line[6:])
                    if event.get("type") == "content_block_delta":
                        text = (event.get("delta") or {}).get("text") or ""
                        if text:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - t0) * 1000.0
                            content_parts.append(text)

        assert ttft_ms is not None and "".join(content_parts).strip()
        evidence_writer.write_latency_artifact(
            "anthropic",
            {"stage": "llm", "model": model, "ttft_ms": round(ttft_ms, 3)},
        )
        evidence_writer.record(
            provider="anthropic",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=ttft_ms,
            model=model,
            details={"stage": "llm", "reply_chars": len("".join(content_parts))},
        )
    except Exception as exc:
        evidence_writer.record(
            provider="anthropic",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
