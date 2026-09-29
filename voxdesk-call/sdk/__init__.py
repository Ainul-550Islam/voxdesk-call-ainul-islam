"""First-party VoxDesk SDK.

Only VoxDesk endpoints are supported; provider APIs are intentionally not
exposed through this package.
"""

from .client import (
    ApiToolModel,
    ApiToolResult,
    ConnectorModel,
    KnowledgeSearchModel,
    McpToolModel,
    SearchHitModel,
    UrlIngestModel,
    VoxDeskClient,
    VoxDeskError,
    VoiceAgentClient,
)

VoiceClient = VoxDeskClient
__all__ = [
    "ApiToolModel",
    "ApiToolResult",
    "ConnectorModel",
    "KnowledgeSearchModel",
    "McpToolModel",
    "SearchHitModel",
    "UrlIngestModel",
    "VoxDeskClient",
    "VoxDeskError",
    "VoiceAgentClient",
    "VoiceClient",
]
