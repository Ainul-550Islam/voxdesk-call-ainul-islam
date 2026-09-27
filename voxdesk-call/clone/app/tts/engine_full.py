"""Clone build — full TTS engine contract with provider routing.
Not a model server; defines how synthesis flows to providers.
"""
from __future__ import annotations

class TTSProviderRouter:
    def __init__(self) -> None:
        self.providers = {}
    def register(self, name: str, provider) -> None:
        self.providers[name] = provider
    async def synthesize(self, text: str, voice_id: str, provider: str = "default", **kw) -> bytes:
        p = self.providers.get(provider)
        if p is None:
            raise RuntimeError(f"Provider not registered: {provider}")
        return await p.synthesize(text, voice_id, **kw)
