"""Provider cost from the existing price table.

This is not a tenant charge. ``app.billing.cost`` remains the price source.
A missing price stays unknown. It is never rewritten as zero.
"""

from __future__ import annotations

from app.billing.cost import cost_line


def for_runtime(
    *,
    provider: str,
    tokens: int | None,
    tts_characters: int | None = None,
    voice_minutes: float | None = None,
) -> dict:
    """Same price table. Unknown stays unknown and is never rewritten as zero."""
    return estimate(
        provider=provider,
        tokens=tokens,
        tts_characters=tts_characters,
        voice_minutes=voice_minutes,
    )


def estimate(
    *,
    provider: str,
    tokens: int | None,
    tts_characters: int | None = None,
    voice_minutes: float | None = None,
) -> dict:
    llm = None
    if tokens is not None:
        if tokens < 0:
            raise ValueError("token count cannot be negative")
        llm = cost_line("llm_token", tokens, provider=provider)
    tts = None
    if tts_characters is not None:
        if tts_characters < 0:
            raise ValueError("character count cannot be negative")
        tts = cost_line("tts_character", tts_characters)
    voice = None
    if voice_minutes is not None:
        if voice_minutes < 0:
            raise ValueError("voice minutes cannot be negative")
        voice = cost_line("voice_minute", voice_minutes)
    known_parts = [line for line in (llm, tts, voice) if line is not None]
    if not known_parts:
        return {
            "provider_cost_usd": None,
            "known": False,
            "reason": "no_usage",
            "tenant_charge_usd": None,
            "source": "billing.cost",
        }
    if any(not line.known for line in known_parts):
        return {
            "provider_cost_usd": None,
            "known": False,
            "reason": "price_unknown",
            "tenant_charge_usd": None,
            "source": "billing.cost",
            "tokens": tokens,
            "provider": provider,
        }
    total = sum(line.cost_usd or 0.0 for line in known_parts)
    return {
        "provider_cost_usd": round(total, 6),
        "known": True,
        "reason": "configured_price",
        "tenant_charge_usd": None,
        "source": "billing.cost",
        "tokens": tokens,
        "provider": provider,
    }
