"""Speech-to-speech (realtime) runtime for VOXDESK.

This package owns the *live* speech-to-speech path: the provider clients
(OpenAI Realtime, Gemini Live), the audio conversion layer, the Pipecat
processor that drives a session, the tool bridge that lets the realtime
model call the tools the cascaded pipeline already exposes, the failover
into the cascaded pipeline and the cost meter.

Nothing here is enabled by default. ``ENABLE_S2S`` plus a provider
credential is required; see ``app/carriers_v2/settings.py``.
"""

from __future__ import annotations

from app.agent.s2s.s2s_audio import (
    GEMINI_LIVE_SAMPLE_RATE,
    OPENAI_REALTIME_SAMPLE_RATE,
    from_pipeline_pcm,
    to_pipeline_pcm,
)
from app.agent.s2s.s2s_cost import (
    S2SCostMeter,
    S2SUsage,
    build_cost_meter,
    persist_token_usage,
)
from app.agent.s2s.s2s_failover import (
    S2S_FAILOVER_TOTAL,
    CascadedFallback,
    failover_reason,
    failover_to_cascaded,
)
from app.agent.s2s.s2s_pipeline import S2SPipelineProcessor
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SBudgetExceeded,
    S2SError,
    S2SEvent,
    S2SEventType,
    S2SProviderError,
    S2SSession,
    S2SSessionTimeout,
)
from app.agent.s2s.s2s_tools_bridge import S2SToolsBridge

__all__ = [
    "CascadedFallback",
    "GEMINI_LIVE_SAMPLE_RATE",
    "OPENAI_REALTIME_SAMPLE_RATE",
    "ReconnectPolicy",
    "S2SBudgetExceeded",
    "S2SCostMeter",
    "S2SError",
    "S2SEvent",
    "S2SEventType",
    "S2SProviderError",
    "S2SSession",
    "S2SSessionTimeout",
    "S2S_FAILOVER_TOTAL",
    "S2SPipelineProcessor",
    "S2SToolsBridge",
    "S2SUsage",
    "build_cost_meter",
    "failover_reason",
    "failover_to_cascaded",
    "from_pipeline_pcm",
    "persist_token_usage",
    "to_pipeline_pcm",
]
