"""
app/services/public_use_case_service.py — Server-side orchestration for catalog/query/detail retrieval.
Source of truth: existing backend capability registry + typed static registry when no dynamic content system exists.
No tenant data, no private data, safe serialization.
"""
from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
import re
from app.schemas.public_use_case import (
    UseCaseCategory,
    UseCaseSummary,
    UseCaseDetail,
    UseCaseCapability,
    UseCaseWorkflowStep,
    UseCaseIntegration,
    UseCaseFAQ,
)

# ---------- Static typed registry — only when no dynamic content system exists ----------
# This is the source of truth for public use cases, derived from real product capabilities verified in public_home_routes.py
# Categories must come from backend where category API exists — we provide backend-driven list.

_CATEGORIES: List[UseCaseCategory] = [
    UseCaseCategory(id="receptionists", title="Receptionists & Answering", description="AI receptionists handling inbound calls, routing, and information collection", icon="phone", count=3),
    UseCaseCategory(id="call-centers", title="Call Centers & Dialers", description="Inbound and outbound call center automation with dialers and monitoring", icon="headset", count=2),
    UseCaseCategory(id="industry", title="Industry Voice Agents", description="Vertical-specific voice agents for healthcare, dental, real estate, etc.", icon="building", count=3),
    UseCaseCategory(id="assistants", title="AI Assistants & Agents", description="General AI assistants for support, scheduling, and operations", icon="bot", count=2),
    UseCaseCategory(id="sales", title="Sales & Operations", description="Lead qualification, outbound follow-up, and sales operations", icon="trending", count=2),
]

# Real capabilities mapped from backend-supported features
_REAL_CAPABILITIES: Dict[str, UseCaseCapability] = {
    "voice": UseCaseCapability(id="voice", title="Voice", description="Natural voice interaction with interruption handling", enabled=True, category="core"),
    "knowledge": UseCaseCapability(id="knowledge", title="Knowledge", description="Retrieval-augmented generation from knowledge base", enabled=True, category="core"),
    "tools": UseCaseCapability(id="tools", title="Tool Calling", description="Function calling with CRM and business system integration", enabled=True, category="core"),
    "transfer": UseCaseCapability(id="transfer", title="Human Transfer", description="Warm transfer to human with context", enabled=True, category="core"),
    "calendar": UseCaseCapability(id="calendar", title="Calendar", description="Calendar integration for appointment booking", enabled=True, category="integration"),
    "crm": UseCaseCapability(id="crm", title="CRM Write-back", description="CRM integration with lead and contact management", enabled=True, category="integration"),
    "webhooks": UseCaseCapability(id="webhooks", title="Webhooks", description="Webhook lifecycle with HMAC and DLQ", enabled=True, category="developer"),
}

# Use case summaries — real catalog, no fake metrics
_USE_CASES: List[UseCaseSummary] = [
    UseCaseSummary(slug="ai-receptionist", title="AI Receptionist", category="receptionists", category_title="Receptionists & Answering", description="Answers inbound calls, collects information, and routes requests when needed.", icon="reception", capabilities=["voice", "knowledge", "transfer"], supported=True, featured=True),
    UseCaseSummary(slug="customer-support", title="Customer Support", category="assistants", category_title="AI Assistants & Agents", description="Handle inbound support calls with knowledge base and CRM context. Transfer to human with warm context.", icon="headset", capabilities=["voice", "knowledge", "tools", "transfer"], supported=True, featured=True),
    UseCaseSummary(slug="appointment-booking", title="Appointment Booking", category="receptionists", category_title="Receptionists & Answering", description="Book, reschedule, cancel appointments with calendar integrations and availability checks.", icon="calendar", capabilities=["voice", "calendar", "tools"], supported=True, featured=True),
    UseCaseSummary(slug="lead-qualification", title="Lead Qualification", category="sales", category_title="Sales & Operations", description="Qualify leads with custom fields, scoring, and CRM write-back. Enrichment and routing.", icon="filter", capabilities=["voice", "crm", "tools"], supported=True, featured=False),
    UseCaseSummary(slug="outbound-followup", title="Outbound Follow-up", category="call-centers", category_title="Call Centers & Dialers", description="Follow-up calls with disposition, task creation, and outcome tracking.", icon="outbound", capabilities=["voice", "crm", "webhooks"], supported=True, featured=False),
    UseCaseSummary(slug="call-center-inbound", title="Inbound Call Center", category="call-centers", category_title="Call Centers & Dialers", description="Handle high-volume inbound calls with intelligent routing and monitoring.", icon="headset", capabilities=["voice", "knowledge", "transfer", "tools"], supported=True, featured=False),
    UseCaseSummary(slug="dental-scheduling", title="Dental Scheduling", category="industry", category_title="Industry Voice Agents", description="Dental practice scheduling with patient intake and insurance verification.", icon="tooth", capabilities=["voice", "calendar", "knowledge"], supported=True, featured=False),
    UseCaseSummary(slug="real-estate-qualifier", title="Real Estate Qualifier", category="industry", category_title="Industry Voice Agents", description="Qualify real estate leads with property preferences and budget checks.", icon="home", capabilities=["voice", "crm", "tools"], supported=True, featured=False),
    UseCaseSummary(slug="healthcare-intake", title="Healthcare Intake", category="industry", category_title="Industry Voice Agents", description="Patient intake with HIPAA-aware workflow and appointment coordination.", icon="health", capabilities=["voice", "knowledge", "calendar"], supported=True, featured=False),
    UseCaseSummary(slug="sales-development", title="Sales Development", category="sales", category_title="Sales & Operations", description="Outbound sales development with lead scoring and meeting booking.", icon="trending", capabilities=["voice", "crm", "calendar"], supported=True, featured=False),
    UseCaseSummary(slug="appointment-reminders", title="Appointment Reminders", category="receptionists", category_title="Receptionists & Answering", description="Automated appointment reminders with confirmation and rescheduling.", icon="bell", capabilities=["voice", "calendar"], supported=True, featured=False),
    UseCaseSummary(slug="ai-assistant", title="AI Assistant", category="assistants", category_title="AI Assistants & Agents", description="General AI assistant for operational tasks and information retrieval.", icon="bot", capabilities=["voice", "knowledge", "tools"], supported=True, featured=False),
]

# Detailed data per slug
_DETAILS: Dict[str, Dict[str, Any]] = {
    "ai-receptionist": {
        "long_description": "AI Receptionist handles inbound calls 24/7, collects caller information, and routes to appropriate team members. Built on real voice infrastructure with knowledge base and human handoff.",
        "problem": "Businesses miss calls after hours, receptionists are overwhelmed, and call routing is manual and error-prone.",
        "solution": "Deploy an AI receptionist that answers instantly, collects intent, queries knowledge, and transfers with context when needed.",
        "workflow": [
            {"order": 1, "title": "Incoming call", "description": "Caller dials business number, provisioned via phone number API", "capabilities": ["voice"]},
            {"order": 2, "title": "Identify intent", "description": "LLM identifies caller intent from speech-to-text", "capabilities": ["voice", "knowledge"]},
            {"order": 3, "title": "Gather information", "description": "Collect name, reason, urgency via conversational flow", "capabilities": ["voice"]},
            {"order": 4, "title": "Query knowledge", "description": "Retrieve business info from knowledge base", "capabilities": ["knowledge"]},
            {"order": 5, "title": "Route or transfer", "description": "Transfer to human with warm context or handle directly", "capabilities": ["transfer"]},
        ],
        "integrations": [
            {"id": "phone", "name": "Phone Numbers", "description": "Provision and assign numbers to agent", "verified": True, "category": "core"},
            {"id": "knowledge", "name": "Knowledge Base", "description": "Business information retrieval", "verified": True, "category": "core"},
        ],
        "faq": [
            {"question": "Does it work after hours?", "answer": "Yes, AI receptionist handles calls 24/7 with configurable business hours and voicemail behavior."},
            {"question": "Can it transfer to human?", "answer": "Yes, warm transfer with context is supported via call handling configuration."},
        ],
        "example_conversation": [
            {"role": "user", "content": "Hi, I'd like to speak with support."},
            {"role": "agent", "content": "Absolutely, I can help. Could you share your name and what you need help with?"},
            {"role": "user", "content": "I'm John, my order hasn't arrived."},
            {"role": "agent", "content": "Thanks John, let me check your order status and connect you to support."},
        ],
    },
    "appointment-booking": {
        "long_description": "Appointment Booking agent handles scheduling with real calendar integrations, availability checks, and confirmation.",
        "problem": "Manual appointment booking is time-consuming, leads to double bookings, and misses after-hours requests.",
        "solution": "AI agent checks calendar availability, books, reschedules, and sends confirmations automatically.",
        "workflow": [
            {"order": 1, "title": "Incoming call", "description": "Caller requests appointment", "capabilities": ["voice"]},
            {"order": 2, "title": "Check availability", "description": "Query calendar integration for free slots", "capabilities": ["calendar"]},
            {"order": 3, "title": "Confirm details", "description": "Collect date, time, service type", "capabilities": ["voice"]},
            {"order": 4, "title": "Book appointment", "description": "Execute calendar tool to create event", "capabilities": ["tools", "calendar"]},
            {"order": 5, "title": "Send confirmation", "description": "Confirm via voice and optional webhook", "capabilities": ["webhooks"]},
        ],
        "integrations": [
            {"id": "calendar", "name": "Calendar", "description": "Google Calendar, Outlook integration", "verified": True, "category": "integration"},
        ],
        "faq": [
            {"question": "Which calendars are supported?", "answer": "Calendar integrations are backend-driven. Check /integrations for verified providers."},
        ],
        "example_conversation": [
            {"role": "user", "content": "I'd like to schedule an appointment."},
            {"role": "agent", "content": "Absolutely. Let me check the available times. What day works for you?"},
            {"role": "user", "content": "Tomorrow afternoon."},
            {"role": "agent", "content": "I have 2 PM and 4 PM available tomorrow. Which would you prefer?"},
        ],
    },
}

# Fill details for all use cases with defaults if not specified
for uc in _USE_CASES:
    if uc.slug not in _DETAILS:
        _DETAILS[uc.slug] = {
            "long_description": f"{uc.title} — {uc.description} Built on real voice infrastructure.",
            "problem": f"Operational challenge for {uc.title.lower()} that requires manual handling and leads to inefficiency.",
            "solution": f"Deploy a voice AI agent for {uc.title.lower()} using backend-supported capabilities: {', '.join(uc.capabilities)}.",
            "workflow": [
                {"order": 1, "title": "Incoming call", "description": "Caller initiates interaction", "capabilities": ["voice"]},
                {"order": 2, "title": "Identify intent", "description": "LLM identifies intent and required information", "capabilities": ["voice", "knowledge"]},
                {"order": 3, "title": "Gather information", "description": "Collect necessary details via conversation", "capabilities": ["voice"]},
                {"order": 4, "title": "Execute action", "description": "Query knowledge, call tools, update systems", "capabilities": uc.capabilities},
                {"order": 5, "title": "Confirm and close", "description": "Confirm result and end or transfer", "capabilities": ["transfer"]},
            ],
            "integrations": [
                {"id": "knowledge", "name": "Knowledge Base", "description": "Retrieve business information", "verified": True, "category": "core"},
            ],
            "faq": [
                {"question": f"What does {uc.title} do?", "answer": uc.description},
            ],
            "example_conversation": [
                {"role": "user", "content": f"Hi, I need help with {uc.title.lower()}."},
                {"role": "agent", "content": f"Absolutely, I can help with {uc.title.lower()}. Let me assist you."},
            ],
        }

def _get_categories() -> List[UseCaseCategory]:
    return _CATEGORIES

def _filter_use_cases(q: Optional[str], category: Optional[str]) -> List[UseCaseSummary]:
    results = _USE_CASES
    if category:
        results = [uc for uc in results if uc.category == category]
    if q:
        q_lower = q.lower()
        # Search in title, description, category, capabilities
        filtered = []
        for uc in results:
            if (q_lower in uc.title.lower() or
                q_lower in uc.description.lower() or
                q_lower in uc.category.lower() or
                any(q_lower in cap.lower() for cap in uc.capabilities)):
                filtered.append(uc)
        results = filtered
    return results

def get_use_cases(q: Optional[str] = None, category: Optional[str] = None, page: int = 1, page_size: int = 24) -> Tuple[List[UseCaseSummary], int]:
    """
    Returns paginated use cases and total count.
    Safe pagination, no tenant data.
    """
    filtered = _filter_use_cases(q, category)
    total = len(filtered)
    # Pagination
    start = (page - 1) * page_size
    end = start + page_size
    paginated = filtered[start:end]
    return paginated, total

def get_categories() -> List[UseCaseCategory]:
    # Update counts based on real data
    counts: Dict[str, int] = {}
    for uc in _USE_CASES:
        counts[uc.category] = counts.get(uc.category, 0) + 1
    result = []
    for cat in _CATEGORIES:
        result.append(UseCaseCategory(id=cat.id, title=cat.title, description=cat.description, icon=cat.icon, count=counts.get(cat.id, 0)))
    return result

def get_use_case_by_slug(slug: str) -> Optional[UseCaseDetail]:
    # Validate slug
    if not re.match(r"^[a-z0-9-]+$", slug):
        return None
    summary = next((uc for uc in _USE_CASES if uc.slug == slug), None)
    if not summary:
        return None
    detail_raw = _DETAILS.get(slug, {})
    # Build capabilities from ids
    caps = []
    for cap_id in summary.capabilities:
        if cap_id in _REAL_CAPABILITIES:
            caps.append(_REAL_CAPABILITIES[cap_id])
        else:
            caps.append(UseCaseCapability(id=cap_id, title=cap_id.title(), enabled=True))
    workflow = [UseCaseWorkflowStep(**w) for w in detail_raw.get("workflow", [])]
    integrations = [UseCaseIntegration(**i) for i in detail_raw.get("integrations", [])]
    faq = [UseCaseFAQ(**f) for f in detail_raw.get("faq", [])]
    return UseCaseDetail(
        slug=summary.slug,
        title=summary.title,
        category=summary.category,
        category_title=summary.category_title,
        description=summary.description,
        long_description=detail_raw.get("long_description"),
        icon=summary.icon,
        problem=detail_raw.get("problem"),
        solution=detail_raw.get("solution"),
        capabilities=caps,
        workflow=workflow,
        integrations=integrations,
        security=[
            {"id": "auth", "title": "Authentication", "verified": True},
            {"id": "audit", "title": "Auditability", "verified": True},
        ],
        faq=faq,
        example_conversation=detail_raw.get("example_conversation", []),
        supported=summary.supported,
        meta={"featured": summary.featured},
    )

def search_use_cases(query: str, category: Optional[str] = None) -> List[UseCaseSummary]:
    # Safe search with length limit
    if len(query) > 200:
        query = query[:200]
    return _filter_use_cases(query, category)
