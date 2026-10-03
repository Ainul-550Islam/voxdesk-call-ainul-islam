"""Notification delivery across channels.

``delivery`` owns the outbound send and its result type. Routing, templating,
priority ordering and deduplication come from ``app.domain.notification_models``
and are persisted by the notification service, so a redelivery after a crash
cannot fan out twice.
"""

__all__ = ["delivery"]
