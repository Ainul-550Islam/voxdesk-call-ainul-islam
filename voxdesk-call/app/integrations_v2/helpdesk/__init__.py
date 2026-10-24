"""Enterprise Helpdesk v2 providers (Zendesk, ServiceNow, Freshdesk, Zoho Desk)."""

from app.integrations_v2.helpdesk.freshdesk import FreshdeskProvider
from app.integrations_v2.helpdesk.models import (
    HelpdeskProviderV2,
    HelpdeskTicket,
    HelpdeskTicketComment,
    HelpdeskUser,
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)
from app.integrations_v2.helpdesk.servicenow import ServiceNowProvider
from app.integrations_v2.helpdesk.zendesk import ZendeskProvider
from app.integrations_v2.helpdesk.zoho_desk import ZohoDeskProvider

__all__ = [
    "FreshdeskProvider",
    "HelpdeskProviderV2",
    "HelpdeskTicket",
    "HelpdeskTicketComment",
    "HelpdeskUser",
    "ServiceNowProvider",
    "TicketCreateInput",
    "TicketPriority",
    "TicketStatus",
    "TicketUpdateInput",
    "ZendeskProvider",
    "ZohoDeskProvider",
]
