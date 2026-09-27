"""Batch-07 Extension / Part 2 — TTS engine interface (gap fill, not production proof).

Defines the contract for text-to-speech synthesis that would connect to
existing agent/tts.py and to external providers (voice.ai-style). No model
weights or inference server is included — this is the interface layer only.
"""
from __future__ import annotations
from app.agent import tts as agent_tts

class TTSService:
    """Stabilized interface for synthesis. Real engines (ElevenLabs, OpenAI,
    or an on-prem model) plug in by implementing synth()."""
    def __init__(self, provider: str = "default") -> None:
        self.provider = provider
    async def synth(self, text: str, voice_id: str, *, language: str = "en", speed: float = 1.0) -> bytes:
        """Return audio bytes (WAV/MP3). Raises TTSUnavailable for outages."""
        # Placeholder: calls existing agent/tts.py if available, else raises
        try:
            return await agent_tts.synth(text, voice_id)  # type: ignore
        except Exception:
            raise TTSUnavailable(provider=self.provider)

class TTSUnavailable(Exception):
    def __init__(self, provider: str) -> None:
        super().__init__(f"TTS unavailable: {provider}")
