from __future__ import annotations

import io
import uuid
import wave

import pytest

from app.voice.voice_clone_service import _fingerprint, _validate_audio


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    return output.getvalue()


def test_clone_audio_validation_accepts_pcm_wav():
    fmt, duration = _validate_audio(_wav(), "sample.wav")
    assert fmt == "wav"
    assert duration == 1


def test_clone_fingerprint_is_tenant_and_input_bound():
    tenant = uuid.uuid4()
    first = _fingerprint(tenant_id=tenant, provider="elevenlabs", name="A", object_ref="tenant/x/voice-clones/a.wav")
    second = _fingerprint(tenant_id=uuid.uuid4(), provider="elevenlabs", name="A", object_ref="tenant/x/voice-clones/a.wav")
    assert first != second


def test_clone_audio_rejects_untrusted_format():
    with pytest.raises(ValueError, match="supported WAV or MP3"):
        _validate_audio(b"not audio", "sample.bin")
