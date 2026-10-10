"""Inbound Caller Noise Reduction Filter wrapping Pipecat `NoisereduceFilter` (Sub-Phase 2G)."""

from __future__ import annotations

import os
from typing import Any

from pipecat.audio.filters.noisereduce_filter import NoisereduceFilter
from pipecat.frames.frames import FilterEnableFrame


def available_denoise_backends() -> dict[str, dict[str, Any]]:
    """Return honest capability matrix of inbound noise-reduction backends.

    Licensed commercial filters (`krisp`, `koala`, `aic`) are never advertised
    unless their license key is present in the runtime environment.
    """
    krisp_licensed = bool(os.getenv("KRISP_MODEL_PATH") and os.getenv("KRISP_LICENSE_KEY"))
    koala_licensed = bool(os.getenv("PICOVOICE_KOALA_ACCESS_KEY"))
    aic_licensed = bool(os.getenv("AICOUSTICS_LICENSE_KEY"))
    return {
        "off": {"available": True, "open_source": True, "licensed": True},
        "noisereduce": {"available": True, "open_source": True, "licensed": True},
        "krisp": {"available": krisp_licensed, "open_source": False, "licensed": krisp_licensed},
        "koala": {"available": koala_licensed, "open_source": False, "licensed": koala_licensed},
        "aic": {"available": aic_licensed, "open_source": False, "licensed": aic_licensed},
    }


def advertised_denoise_modes() -> list[str]:
    """Return only the denoise modes genuinely available in this runtime."""
    return [mode for mode, info in available_denoise_backends().items() if info["available"]]


def build_denoise_enable_frame(enabled: bool) -> FilterEnableFrame:
    """Build a Pipecat `FilterEnableFrame` to dynamically toggle inbound noise reduction."""
    return FilterEnableFrame(enable=bool(enabled))


def build_denoise_filter(
    denoise_enabled: bool | str = True,
) -> NoisereduceFilter | None:
    """Return a Pipecat `NoisereduceFilter` when `denoise_enabled` is active, or `None` when disabled."""
    if isinstance(denoise_enabled, str):
        mode = denoise_enabled.strip().lower()
        if mode in {"", "off", "none", "disabled", "false", "0"}:
            return None
        backends = available_denoise_backends()
        if mode in {"krisp", "koala", "aic"} and not backends[mode]["available"]:
            raise ValueError(
                f"Denoise backend '{mode}' is not licensed in this environment. "
                f"Advertised modes: {advertised_denoise_modes()}"
            )
        if mode not in {"noisereduce", "on", "enabled", "true", "1", "krisp", "koala", "aic"}:
            raise ValueError(f"Unsupported denoise_mode '{denoise_enabled}'")
    elif not denoise_enabled:
        return None
    return NoisereduceFilter()
