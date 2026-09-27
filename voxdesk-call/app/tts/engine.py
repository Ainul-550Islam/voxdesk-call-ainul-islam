"""Batch-07 Extension — TTS Engine contract with provider adapter.
Not a production synthesis server; defines the interface that connects
existing agent/tts.py to external providers (ElevenLabs, OpenAI TTS, etc.).
"""
from __future__ import annotations

class TTSProvider:
    """Pluggable provider — real implementations require API keys + network."""
    def __init__(self, name: str, endpoint: str, api_key: str | None = None) -> None:
        self.name = name
        self.endpoint = endpoint
        self.api_key = api_key
    async def synthesize(self, text: str, voice_id: str, *, language: str = "en", speed: float = 1.0) -> bytes:
        raise NotImplementedError("provider not implemented — skeleton")

class TTSService:
    def __init__(self, provider: TTSProvider | None = None) -> None:
        self.provider = provider or TTSProvider("default", "")
    async def synth(self, text: str, voice_id: str, *, language: str = "en", speed: float = 1.0) -> bytes:
        return await self.provider.synthesize(text, voice_id, language=language, speed=speed)
