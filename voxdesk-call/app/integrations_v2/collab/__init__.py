"""Enterprise Collaboration v2 providers (Microsoft Teams, Zoom, Slack)."""

from app.integrations_v2.collab.slack import SlackProvider
from app.integrations_v2.collab.teams import TeamsProvider
from app.integrations_v2.collab.zoom import ZoomProvider

__all__ = ["SlackProvider", "TeamsProvider", "ZoomProvider"]
