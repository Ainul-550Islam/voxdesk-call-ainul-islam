"""Environment gates, skip-with-reason helpers, and evidence writer for `tests/real/`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
import wave

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROVIDER_ENV_GATES: dict[str, tuple[str, ...]] = {
    "twilio": (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "TWILIO_TEST_TO_NUMBER",
        "VOXDESK_PUBLIC_BASE_URL",
    ),
    "deepgram": ("DEEPGRAM_API_KEY",),
    "elevenlabs": ("ELEVENLABS_API_KEY",),
    "cartesia": ("CARTESIA_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai_realtime": ("OPENAI_API_KEY",),
    "gemini_live": ("GOOGLE_API_KEY",),
    "telnyx": (
        "TELNYX_API_KEY",
        "TELNYX_CONNECTION_ID",
        "TELNYX_FROM_NUMBER",
        "TELNYX_TO_NUMBER",
        "VOXDESK_PUBLIC_BASE_URL",
    ),
    "salesforce": (
        "SALESFORCE_CLIENT_ID",
        "SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_REFRESH_TOKEN",
        "SALESFORCE_INSTANCE_URL",
    ),
    "hubspot": ("VOXDESK_REAL_HUBSPOT_TOKEN",),
    "webhook_receiver": ("VOXDESK_REAL_WEBHOOK_RECEIVER_URL",),
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Ensure every test under `tests/real/` carries `@pytest.mark.real_provider`."""
    real_dir = Path(__file__).resolve().parent
    for item in items:
        try:
            item_path = Path(str(item.fspath)).resolve()
        except Exception:
            continue
        if real_dir in item_path.parents or item_path.parent == real_dir:
            item.add_marker(pytest.mark.real_provider)


def default_evidence_dir() -> Path:
    override = (os.environ.get("VOXDESK_REAL_EVIDENCE_DIR") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else ROOT / path
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return ROOT / "evidence" / "real-integrations" / today


@dataclass
class RealEvidenceWriter:
    """Thread/process-safe JSON evidence ledger for real-provider integration runs."""

    directory: Path

    @property
    def json_path(self) -> Path:
        return self.directory / "real_integrations.json"

    def _load(self) -> dict[str, Any]:
        if self.json_path.is_file():
            try:
                data = json.loads(self.json_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("providers"), dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "schema": "voxdesk.real_integrations/v1",
            "generated_at": now_iso,
            "run_date": now_iso[:10],
            "provider_fakes": False,
            "providers": {},
        }

    def record(
        self,
        *,
        provider: str,
        status: str,
        test_node: str,
        reason: str = "",
        missing_env: list[str] | None = None,
        latency_ms: float | None = None,
        model: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        doc = self._load()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc["generated_at"] = now_iso
        doc["provider_fakes"] = False
        entry: dict[str, Any] = {
            "provider": provider,
            "status": status,
            "provider_fakes": False,
            "test_node": test_node,
            "recorded_at_utc": now_iso,
            "reason": reason,
            "missing_env": list(missing_env or []),
            "latency_ms": round(float(latency_ms), 3) if latency_ms is not None else None,
            "model": model,
            "details": dict(details or {}),
        }
        doc["providers"][provider] = entry
        self.json_path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        return entry

    def write_latency_artifact(self, provider: str, payload: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        lat_path = self.directory / "provider_latency.json"
        existing: dict[str, Any] = {
            "schema": "voxdesk.provider_stream_latency/v1",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider_fakes": False,
            "providers": {},
        }
        if lat_path.is_file():
            try:
                loaded = json.loads(lat_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("providers"), dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                pass
        existing["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing["provider_fakes"] = False
        existing["providers"][provider] = {**payload, "provider_fakes": False}
        lat_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return lat_path


@pytest.fixture
def evidence_writer() -> RealEvidenceWriter:
    return RealEvidenceWriter(directory=default_evidence_dir())


def require_provider_env(
    provider: str,
    *,
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
    extra_vars: tuple[str, ...] = (),
) -> dict[str, str]:
    """Verify `VOXDESK_REAL_INTEGRATION=1` and all required env vars for `provider`.

    Records an honest `SKIPPED_NO_CREDENTIALS` entry before calling `pytest.skip(...)`.
    """
    required = list(PROVIDER_ENV_GATES.get(provider, ()))
    for var in extra_vars:
        if var not in required:
            required.append(var)

    opt_in = (os.environ.get("VOXDESK_REAL_INTEGRATION") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    if not opt_in:
        missing = ["VOXDESK_REAL_INTEGRATION=1", *missing]

    if missing:
        reason = f"{provider} real integration skipped; missing {', '.join(missing)}"
        evidence_writer.record(
            provider=provider,
            status="SKIPPED_NO_CREDENTIALS",
            test_node=request.node.nodeid,
            reason=reason,
            missing_env=missing,
        )
        pytest.skip(reason)

    return {name: os.environ[name].strip() for name in required}


@pytest.fixture
def sample_speech_wav_bytes() -> bytes:
    """Return a real recorded 16 kHz mono PCM16 WAV from repository speech fixtures."""
    fixtures = ROOT / "tests" / "agent" / "fixtures" / "incomplete_utterances"
    candidates = sorted(fixtures.glob("*.wav")) if fixtures.is_dir() else []
    if candidates:
        return candidates[0].read_bytes()
    # Deterministic fallback 300ms 440Hz tone in valid RIFF WAV container
    import io
    import math
    import struct

    buf = io.BytesIO()
    sr = 16000
    n_samples = int(sr * 0.3)
    frames = bytearray()
    for i in range(n_samples):
        val = int(12000 * math.sin(2.0 * math.pi * 440.0 * (i / sr)))
        frames.extend(struct.pack("<h", val))
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))
    return buf.getvalue()
