import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.agent_management_routes import router as agent_management_router
from app.api.auth_routes import router as auth_router
from app.api.appointment_routes import (
    calendar_router,
    router as appointment_router,
)
from app.api.analytics_routes import router as analytics_router
from app.api.automation_routes import router as automation_router
from app.api.campaign_routes import router as campaign_router
from app.api.inbox_routes import router as inbox_router
from app.api.qa_routes import router as qa_router
from app.api.lead_routes import router as lead_router
from app.api.lead_activity_routes import router as lead_activity_router
from app.api.lead_import_routes import router as lead_import_router
from app.api.lead_segment_routes import router as lead_segment_router
# Batch 07: durable job platform + transactional outbox operator surfaces.
from app.api.jobs_routes import router as jobs_router
from app.api.outbox_routes import router as outbox_router
from app.api.conversation_routes import router as conversation_router
from app.api.notification_routes import router as notification_router
from app.api.workflow_routes import router as workflow_router
from app.api.billing_routes import router as billing_router
from app.api.calendar_webhook_routes import router as calendar_webhook_router
from app.api.crm_webhook_routes import router as crm_webhook_router
from app.api.integration_routes import router as integration_router
from app.api.knowledge_routes import router as knowledge_router
from app.api_tools.routes import router as api_tools_router
from app.mcp.routes import router as mcp_router
from app.api.public_webhook_routes import router as public_webhook_router
from app.api.connector_routes import router as connector_p3_router
from app.api.security_routes import router as security_p3_router
from app.api.routes import router as api_router
from app.api.organization_routes import router as organization_router
from app.api.tenant_admin_routes import router as tenant_admin_router
from app.api.environment_routes import router as environment_router
from app.api.tenant_security_routes import router as tenant_security_router
from app.api.tenant_usage_routes import router as tenant_usage_router
from app.api.ai_routes import router as ai_router
from app.api.governance_routes import router as governance_router
from app.api.governance_admin_routes import router as governance_admin_router
from app.api.model_registry_routes import router as model_registry_router
from app.api.model_registry_admin_routes import router as model_registry_admin_router
from app.api.evidence_routes import router as evidence_router
from app.api.evidence_admin_routes import router as evidence_admin_router
from app.api.risk_routes import router as risk_router
from app.api.risk_admin_routes import router as risk_admin_router
from app.api.specialized_agent_routes import router as specialized_agent_router
from app.api.legal_routes import router as legal_router
from app.api.translation_routes import router as translation_router
from app.api.anomaly_routes import router as anomaly_router
from app.review.routes import router as review_router
from app.api.insight_routes import router as insight_router
from app.api.forecast_routes import router as forecast_router
from app.api.compliance_routes import router as compliance_router
from app.api.roi_routes import router as roi_router
from app.api.deployment_routes import router as deployment_control_router
from app.api.deployment_runtime_routes import router as deployment_runtime_router
from app.api.health_routes import router as health_router
from app.api.prompt_routes import router as prompt_router
from app.api.eval_routes import router as eval_router
from app.api.phone_numbers_routes import router as phone_numbers_router
from app.api.queue_routes import router as queue_router
from app.api.agent_state_routes import router as agent_state_router
from app.api.routing_routes import router as routing_router
from app.api.supervisor_routes import router as supervisor_router
from app.api.skills_routes import router as skills_router
from app.api.organization_membership_routes import router as organization_membership_router
from app.api.tenant_membership_routes import router as tenant_membership_router
from app.api.environment_access_routes import router as environment_access_router
from app.api.environment_resource_routes import router as environment_resource_router
from app.api.environment_resource_export_routes import router as environment_resource_export_router
from app.api.team_routes import router as team_router
from app.api.gdpr_routes import router as gdpr_router
from app.api.license_routes import router as license_router
from app.api.api_key_routes import router as api_key_router
from app.api.domain_routes import router as domain_router
from app.api.identity_routes import router as identity_router
from app.api.mfa_routes import router as mfa_router
from app.api.password_routes import router as password_router
from app.api.scim_routes import admin_router as scim_admin_router
from app.api.scim_routes import scim_router
from app.api.service_account_routes import router as service_account_router
from app.api.security_session_routes import security_router
from app.api.session_routes import router as session_router
from app.api.sso_routes import admin_router as sso_admin_router
from app.api.sso_routes import public_router as sso_public_router
from app.core.chaos import add_chaos_middleware
from app.core.config import settings
from app.core.errors import install_error_handling
from app.core.logging import log
from app.core.metrics import add_metrics_endpoint, add_metrics_middleware
from app.core.rate_limit import add_rate_limit_middleware
from app.core.security_headers import add_security_headers
from app.core.security_txt import add_security_txt
from app.db.models import Base
# Register additive enterprise governance models on the shared metadata before
# development/test create_all and before Alembic imports its target metadata.
import app.governance  # noqa: F401
import app.db.enterprise_models  # noqa: F401 — P0/P1 missing API models: batch_calls, experiments, pcap, retention, webhooks, salesforce, kb collections, simulation, tool registry, workflow triggers, multichannel, call policies, DNC
from app.db.session import get_engine
from app.channels.messaging import router as channels_router
from app.telephony.twilio_handler import router as telephony_router

# P0 Missing APIs — Final Backend Gate closure (Prompts 2-10+)
# Outbound, Web Call, Call Control, DTMF
from app.api.outbound_call_routes import router as outbound_call_router
# Transfer initiation + warm-transfer context
from app.api.transfer_control_routes import router as transfer_control_router
# Live monitoring / takeover / human takeover session
from app.api.live_monitoring_routes import router as live_monitoring_router
# Agent delete/archive lifecycle
from app.api.agent_lifecycle_routes import router as agent_lifecycle_router
# Phone-number lifecycle extended
from app.api.phone_number_lifecycle_routes import router as phone_number_lifecycle_router
# Recording management
from app.api.recording_management_routes import router as recording_management_router
# Native batch-call
from app.api.batch_call_routes import router as batch_call_router
# Post-call analysis + custom fields + backfill
from app.api.post_call_analysis_routes import router as post_call_analysis_router
# A/B testing + rollout
from app.api.ab_testing_routes import router as ab_testing_router
# PCAP/debug artifact
from app.api.pcap_routes import router as pcap_router
# Per-agent retention
from app.api.retention_routes import router as retention_router
# Webhook lifecycle + delivery control + event-type subscription
from app.api.webhook_lifecycle_routes import router as webhook_lifecycle_router
# Salesforce CRM adapter
from app.api.salesforce_routes import router as salesforce_router
# CRM outcome write-back
from app.api.crm_writeback_routes import router as crm_writeback_router
# Reusable Knowledge Base entity layer
from app.api.knowledge_base_routes import router as knowledge_base_router
# P1 — Call simulation, version diff, draft/publish env, tool registry, workflow triggers, multichannel, call search/export/policies/DNC
from app.api.call_simulation_routes import router as call_simulation_router
from app.api.agent_version_routes import router as agent_version_router
from app.api.tool_registry_routes import router as tool_registry_router
from app.api.workflow_event_routes import router as workflow_event_router
from app.api.multichannel_routes import router as multichannel_router
from app.api.call_search_export_routes import router as call_search_export_router

# Additional 9 enterprise files — 1000+ lines each — for $20-60K sale value expansion, no skip
from app.api.call_analytics_routes import router as call_analytics_router
from app.api.compliance_gdpr_routes import router as compliance_gdpr_router
from app.api.billing_metering_routes import router as billing_metering_router
from app.api.security_audit_routes import router as security_audit_router
from app.api.voice_biometrics_routes import router as voice_biometrics_router
from app.api.campaign_analytics_routes import router as campaign_analytics_router
from app.api.lead_enrichment_routes import router as lead_enrichment_router
from app.api.integration_marketplace_routes import router as integration_marketplace_router
from app.api.realtime_transcription_routes import router as realtime_transcription_router
from app.api.advanced_analytics_routes import router as advanced_analytics_router
from app.api.agent_collaboration_routes import router as agent_collaboration_router
from app.api.agent_evaluation_routes import router as agent_evaluation_router
from app.api.agent_performance_routes import router as agent_performance_router
from app.api.agent_training_routes import router as agent_training_router
from app.api.ai_insights_routes import router as ai_insights_router
from app.api.audit_trail_routes import router as audit_trail_router
from app.api.auto_dialer_routes import router as auto_dialer_router
from app.api.call_coaching_routes import router as call_coaching_router
from app.api.call_disposition_routes import router as call_disposition_router
from app.api.call_escalation_routes import router as call_escalation_router
from app.api.call_feedback_routes import router as call_feedback_router
from app.api.call_intelligence_routes import router as call_intelligence_router
from app.api.call_optimization_routes import router as call_optimization_router
from app.api.call_quality_routes import router as call_quality_router
from app.api.call_routing_advanced_routes import router as call_routing_advanced_router
from app.api.call_scheduling_routes import router as call_scheduling_router
from app.api.call_scoring_routes import router as call_scoring_router
from app.api.call_tagging_routes import router as call_tagging_router
from app.api.call_transfer_advanced_routes import router as call_transfer_advanced_router
from app.api.channel_analytics_routes import router as channel_analytics_router
from app.api.compliance_call_routes import router as compliance_call_router
from app.api.compliance_recording_routes import router as compliance_recording_router
from app.api.contact_enrichment_routes import router as contact_enrichment_router
from app.api.conversation_analytics_routes import router as conversation_analytics_router
from app.api.conversation_intelligence_routes import router as conversation_intelligence_router
from app.api.cost_optimization_routes import router as cost_optimization_router
from app.api.crm_sync_routes import router as crm_sync_router
from app.api.customer_journey_routes import router as customer_journey_router
from app.api.customer_segmentation_routes import router as customer_segmentation_router
from app.api.data_export_routes import router as data_export_router
from app.api.data_import_routes import router as data_import_router
from app.api.dialer_optimization_routes import router as dialer_optimization_router
from app.api.disposition_analytics_routes import router as disposition_analytics_router
from app.api.email_campaign_routes import router as email_campaign_router
from app.api.emotion_detection_routes import router as emotion_detection_router
from app.api.enterprise_billing_routes import router as enterprise_billing_router
from app.api.enterprise_reporting_routes import router as enterprise_reporting_router
from app.api.fraud_detection_routes import router as fraud_detection_router
from app.api.intent_detection_routes import router as intent_detection_router
from app.api.interaction_analytics_routes import router as interaction_analytics_router
from app.api.ivr_analytics_routes import router as ivr_analytics_router
from app.api.knowledge_analytics_routes import router as knowledge_analytics_router
from app.api.lead_qualification_routes import router as lead_qualification_router
from app.api.lead_routing_routes import router as lead_routing_router
from app.api.lead_scoring_advanced_routes import router as lead_scoring_advanced_router
from app.api.live_transcription_routes import router as live_transcription_router
from app.api.marketplace_billing_routes import router as marketplace_billing_router
from app.api.multilingual_support_routes import router as multilingual_support_router
from app.api.notification_advanced_routes import router as notification_advanced_router
from app.api.number_pool_routes import router as number_pool_router
from app.api.omnichannel_analytics_routes import router as omnichannel_analytics_router
from app.api.performance_benchmark_routes import router as performance_benchmark_router
from app.api.predictive_analytics_routes import router as predictive_analytics_router
from app.api.predictive_dialer_routes import router as predictive_dialer_router
from app.api.quality_assurance_routes import router as quality_assurance_router
from app.api.realtime_alerts_routes import router as realtime_alerts_router
from app.api.realtime_dashboard_routes import router as realtime_dashboard_router
from app.api.realtime_monitoring_routes import router as realtime_monitoring_router
from app.api.revenue_analytics_routes import router as revenue_analytics_router
from app.api.risk_assessment_routes import router as risk_assessment_router
from app.api.sales_analytics_routes import router as sales_analytics_router
from app.api.sentiment_advanced_routes import router as sentiment_advanced_router
from app.api.sip_trunk_routes import router as sip_trunk_router
from app.api.speech_analytics_routes import router as speech_analytics_router
from app.api.team_analytics_routes import router as team_analytics_router
from app.api.telephony_advanced_routes import router as telephony_advanced_router
from app.api.transcription_advanced_routes import router as transcription_advanced_router
from app.api.usage_analytics_routes import router as usage_analytics_router
from app.api.voice_analytics_routes import router as voice_analytics_router
from app.api.voice_cloning_routes import router as voice_cloning_router
from app.api.webhook_analytics_routes import router as webhook_analytics_router
from app.api.workflow_analytics_routes import router as workflow_analytics_router
from app.api.workflow_automation_advanced_routes import router as workflow_automation_advanced_router
from app.api.agent_assist_routes import router as agent_assist_router
from app.api.call_compliance_routes import router as call_compliance_router
from app.api.call_redaction_routes import router as call_redaction_router
from app.api.conversation_redaction_routes import router as conversation_redaction_router
from app.api.customer_insights_routes import router as customer_insights_router
from app.api.enterprise_security_routes import router as enterprise_security_router
from app.api.integration_health_routes import router as integration_health_router
from app.api.lead_distribution_routes import router as lead_distribution_router
from app.api.number_porting_routes import router as number_porting_router
from app.api.realtime_coaching_routes import router as realtime_coaching_router
from app.api.revenue_optimization_routes import router as revenue_optimization_router
from app.api.sales_coaching_routes import router as sales_coaching_router
from app.api.speech_to_text_routes import router as speech_to_text_router
from app.api.text_to_speech_routes import router as text_to_speech_router
from app.api.voice_activity_routes import router as voice_activity_router
from app.api.public_home_routes import router as public_home_router
from app.api.agent_builder_routes import router as agent_builder_router
from app.api.agent_test_routes import router as agent_test_router
from app.api.public_use_case_routes import router as public_use_case_router

# Observability: Sentry error reporting is optional and off unless a DSN is
# configured. Initialised at import time so it covers startup failures too.
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        # Keep a small trace sample in production; none in dev/test.
        traces_sample_rate=0.1 if settings.is_production else 0.0,
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.config_validation import require_valid_runtime_config
    require_valid_runtime_config(strict=settings.is_production)
    # Refuse to serve traffic with an insecure configuration. In development
    # the same problems are logged as warnings so the app stays runnable.
    problems = settings.validate_security()
    if problems:
        if settings.is_production:
            raise RuntimeError("Insecure configuration, refusing to start: " + "; ".join(problems))
        for problem in problems:
            log.warning("config.insecure", problem=problem)

    engine = get_engine()
    if settings.app_env.lower() in {"development", "test"}:
        # Development/test bootstrap: create any missing tables so the app is
        # usable without running migrations. Production AND staging never do
        # this -- Alembic is the sole schema owner there, and create_all
        # would build schema outside the migration history (and staging must
        # mirror production's schema exactly). See alembic/versions/.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # STEP 7: make sure the plan catalogue exists, and check that every active
    # priced plan has a provider price id.
    #
    # Seeding lives here rather than in a migration because prices are
    # business data that changes: repricing should be an operator action
    # against a running system, not a schema change. `sync_seed_plans` never
    # overwrites a price an operator has edited.
    #
    # The configuration check is requirement 6 -- a plan with no price id
    # fails at boot rather than at checkout, where the failure would be in
    # front of a customer holding a credit card.
    from app.billing.plans import configuration_problems, list_plans, sync_seed_plans
    from app.db.session import get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as session:
        await sync_seed_plans(session)
        plan_problems = configuration_problems(
            await list_plans(session, active_only=True),
            is_production=settings.is_production,
        )
    if plan_problems:
        if settings.is_production and settings.billing_provider == "stripe":
            raise RuntimeError(
                "Billing is misconfigured, refusing to start: " + "; ".join(plan_problems)
            )
        for problem in plan_problems:
            log.warning("billing.plan_misconfigured", problem=problem)

    log.info("voxdesk.started")
    yield
    await engine.dispose()


def _api_docs_config() -> dict[str, str | None]:
    """Swagger / ReDoc / OpenAPI are developer surfaces.

    In production they expose the full API schema and an interactive
    \"try it out\" console, so they are disabled there. Development keeps the
    FastAPI defaults.
    """
    if settings.is_production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title="VoxDesk",
    version="0.4.0",
    lifespan=lifespan,
    **_api_docs_config(),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,  # never \"*\" once cookies are in play
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Host-header validation. Off unless TRUSTED_HOSTS is set, so the single-proxy
# topology (Caddy terminates TLS for exactly the configured domains and binds
# the API to loopback) is unchanged; when set, the app rejects a request whose
# Host header names any other host, closing host-poisoning SSRF and
# cache-poisoning at the application layer too.
if settings.trusted_host_list:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )

app.include_router(telephony_router)
app.include_router(channels_router)
app.include_router(auth_router)
app.include_router(team_router)
app.include_router(knowledge_router)
app.include_router(api_tools_router)
app.include_router(mcp_router)
app.include_router(public_webhook_router)
app.include_router(connector_p3_router)
app.include_router(security_p3_router)
app.include_router(integration_router)
app.include_router(crm_webhook_router)
app.include_router(appointment_router)
app.include_router(calendar_router)
app.include_router(calendar_webhook_router)
app.include_router(billing_router)
app.include_router(analytics_router)
app.include_router(api_router)
app.include_router(gdpr_router)
app.include_router(license_router)

# Enterprise expansion surface. Batch 01 shipped these route modules with
# registration deliberately out of its file set (\"reported as an integration
# dependency\"); Batch 02 closes that dependency, and adds the three route
# modules whose services had no HTTP surface at all (automation, notification,
# inbox). Registration order is irrelevant to routing — every path here is
# distinct — but it is kept stable so `app.routes` is diffable.
# STEP 18, enterprise identity. Registered here, in the same place and the same
# way as everything above: the human identity surface (identity, MFA, sessions),
# the two public credential-recovery flows plus the login-page capability
# lookup (password_router), machine credentials (api_key_router for API keys,
# service_account_router for machine identities), enterprise
# domains (domain_router), SSO administration and its public login endpoints
# (sso_admin_router / sso_public_router), and SCIM provisioning
# (scim_admin_router for the credentials an IdP uses, scim_router for the
# protocol itself).
app.include_router(identity_router)
app.include_router(mfa_router)
app.include_router(session_router)
app.include_router(security_router)
app.include_router(password_router)
app.include_router(api_key_router)
app.include_router(service_account_router)
app.include_router(domain_router)
app.include_router(sso_admin_router)
app.include_router(sso_public_router)
app.include_router(scim_admin_router)
app.include_router(scim_router)
# Organization → tenant → environment foundation. Same registration site as
# the identity routers. Paths do not overlap the existing ``/api/tenants``
# collection; hierarchy ids in the URL are checked against the principal.
app.include_router(organization_router)
app.include_router(tenant_admin_router)
app.include_router(environment_router)
app.include_router(tenant_security_router)
app.include_router(tenant_usage_router)
app.include_router(ai_router)
app.include_router(governance_router)
app.include_router(governance_admin_router)
app.include_router(model_registry_router)
app.include_router(model_registry_admin_router)
app.include_router(evidence_router)
app.include_router(evidence_admin_router)
app.include_router(risk_router)
app.include_router(risk_admin_router)
app.include_router(specialized_agent_router)
app.include_router(legal_router)
app.include_router(translation_router)
app.include_router(anomaly_router)
app.include_router(review_router)
app.include_router(insight_router)
app.include_router(forecast_router)
app.include_router(compliance_router)
app.include_router(roi_router)
app.include_router(deployment_control_router)
app.include_router(deployment_runtime_router)
app.include_router(health_router)
app.include_router(prompt_router)
app.include_router(eval_router)
app.include_router(phone_numbers_router)
app.include_router(queue_router)
app.include_router(agent_state_router)
app.include_router(routing_router)
app.include_router(supervisor_router)
app.include_router(skills_router)
app.include_router(organization_membership_router)
app.include_router(tenant_membership_router)
app.include_router(environment_access_router)
app.include_router(environment_resource_router)
app.include_router(environment_resource_export_router)

app.include_router(agent_management_router)
app.include_router(workflow_router)
app.include_router(campaign_router)
app.include_router(automation_router)
app.include_router(notification_router)
app.include_router(inbox_router)
app.include_router(qa_router)
app.include_router(conversation_router)
app.include_router(lead_router)
app.include_router(lead_activity_router)
app.include_router(lead_import_router)
app.include_router(lead_segment_router)
# Batch 07: operator APIs over the one durable job table and the outbox.
app.include_router(jobs_router)
app.include_router(outbox_router)

# P0/P1 Missing API closure — Prompts 2-10+ (Final Backend Gate)
# These routers close all gaps listed in add missing.txt (40 gaps)
# Outbound, Web Call, Call Control, DTMF
app.include_router(outbound_call_router)
# Transfer initiation + warm-transfer context
app.include_router(transfer_control_router)
# Live monitoring / takeover / human takeover session lifecycle
app.include_router(live_monitoring_router)
# Agent delete/archive lifecycle
app.include_router(agent_lifecycle_router)
# Phone-number lifecycle extended
app.include_router(phone_number_lifecycle_router)
# Recording management
app.include_router(recording_management_router)
# Native batch-call
app.include_router(batch_call_router)
# Post-call analysis + custom fields + backfill
app.include_router(post_call_analysis_router)
# A/B testing + rollout
app.include_router(ab_testing_router)
# PCAP/debug artifact
app.include_router(pcap_router)
# Per-agent retention + purge status
app.include_router(retention_router)
# Webhook lifecycle + delivery control + event-type subscription + DLQ
app.include_router(webhook_lifecycle_router)
# Salesforce CRM adapter
app.include_router(salesforce_router)
# CRM outcome write-back
app.include_router(crm_writeback_router)
# Reusable Knowledge Base entity layer
app.include_router(knowledge_base_router)
# Call simulation, version diff, draft/publish env, tool registry, workflow triggers, multichannel, call search/export/policies/DNC
app.include_router(call_simulation_router)
app.include_router(agent_version_router)
app.include_router(tool_registry_router)
app.include_router(workflow_event_router)
app.include_router(multichannel_router)
app.include_router(call_search_export_router)

# Additional 9 enterprise files — 1000+ lines each — for $20-60K sale value, no skip
app.include_router(call_analytics_router)
app.include_router(compliance_gdpr_router)
app.include_router(billing_metering_router)
app.include_router(security_audit_router)
app.include_router(voice_biometrics_router)
app.include_router(campaign_analytics_router)
app.include_router(lead_enrichment_router)
app.include_router(integration_marketplace_router)
app.include_router(realtime_transcription_router)

# Additional 87 enterprise files — 1000+ lines each — to reach 200 files total, no skip
app.include_router(advanced_analytics_router)
app.include_router(agent_collaboration_router)
app.include_router(agent_evaluation_router)
app.include_router(agent_performance_router)
app.include_router(agent_training_router)
app.include_router(ai_insights_router)
app.include_router(audit_trail_router)
app.include_router(auto_dialer_router)
app.include_router(call_coaching_router)
app.include_router(call_disposition_router)
app.include_router(call_escalation_router)
app.include_router(call_feedback_router)
app.include_router(call_intelligence_router)
app.include_router(call_optimization_router)
app.include_router(call_quality_router)
app.include_router(call_routing_advanced_router)
app.include_router(call_scheduling_router)
app.include_router(call_scoring_router)
app.include_router(call_tagging_router)
app.include_router(call_transfer_advanced_router)
app.include_router(channel_analytics_router)
app.include_router(compliance_call_router)
app.include_router(compliance_recording_router)
app.include_router(contact_enrichment_router)
app.include_router(conversation_analytics_router)
app.include_router(conversation_intelligence_router)
app.include_router(cost_optimization_router)
app.include_router(crm_sync_router)
app.include_router(customer_journey_router)
app.include_router(customer_segmentation_router)
app.include_router(data_export_router)
app.include_router(data_import_router)
app.include_router(dialer_optimization_router)
app.include_router(disposition_analytics_router)
app.include_router(email_campaign_router)
app.include_router(emotion_detection_router)
app.include_router(enterprise_billing_router)
app.include_router(enterprise_reporting_router)
app.include_router(fraud_detection_router)
app.include_router(intent_detection_router)
app.include_router(interaction_analytics_router)
app.include_router(ivr_analytics_router)
app.include_router(knowledge_analytics_router)
app.include_router(lead_qualification_router)
app.include_router(lead_routing_router)
app.include_router(lead_scoring_advanced_router)
app.include_router(live_transcription_router)
app.include_router(marketplace_billing_router)
app.include_router(multilingual_support_router)
app.include_router(notification_advanced_router)
app.include_router(number_pool_router)
app.include_router(omnichannel_analytics_router)
app.include_router(performance_benchmark_router)
app.include_router(predictive_analytics_router)
app.include_router(predictive_dialer_router)
app.include_router(quality_assurance_router)
app.include_router(realtime_alerts_router)
app.include_router(realtime_dashboard_router)
app.include_router(realtime_monitoring_router)
app.include_router(revenue_analytics_router)
app.include_router(risk_assessment_router)
app.include_router(sales_analytics_router)
app.include_router(sentiment_advanced_router)
app.include_router(sip_trunk_router)
app.include_router(speech_analytics_router)
app.include_router(team_analytics_router)
app.include_router(telephony_advanced_router)
app.include_router(transcription_advanced_router)
app.include_router(usage_analytics_router)
app.include_router(voice_analytics_router)
app.include_router(voice_cloning_router)
app.include_router(webhook_analytics_router)
app.include_router(workflow_analytics_router)
app.include_router(workflow_automation_advanced_router)
app.include_router(agent_assist_router)
app.include_router(call_compliance_router)
app.include_router(call_redaction_router)
app.include_router(conversation_redaction_router)
app.include_router(customer_insights_router)
app.include_router(enterprise_security_router)
app.include_router(integration_health_router)
app.include_router(lead_distribution_router)
app.include_router(number_porting_router)
app.include_router(realtime_coaching_router)
app.include_router(revenue_optimization_router)
app.include_router(sales_coaching_router)
app.include_router(speech_to_text_router)
app.include_router(text_to_speech_router)
app.include_router(voice_activity_router)
app.include_router(public_home_router)
app.include_router(agent_builder_router)
app.include_router(agent_test_router)
app.include_router(public_use_case_router)

# Cross-cutting middleware and handlers. Order is deliberate: exception
# handlers + request-id first, then security headers, then rate limiting, then
# (test-only) failure injection, then metrics — which observes whatever the
# inner stack produces, injected failures and latency included.
install_error_handling(app)
add_security_headers(app)
add_rate_limit_middleware(app)
add_chaos_middleware(app)
add_metrics_middleware(app)
add_metrics_endpoint(app)
add_security_txt(app)



def _mount_dashboard_if_built(app: FastAPI, dist_dir: str | None = None) -> None:
    """Serve the built dashboard when it is present in the image.

    CANONICAL PRODUCTION FRONTEND (P0-01 audit):
    - dashboard/ (Vite + React) is the sole production frontend shipped by Dockerfile
    - dashboard-next/ (Next.js) is roadmap/shadow-parity, CI-tested in polyglot.yml
      but NOT served by this function nor built in production Dockerfile.
    - See docs/CURRENT-ARCHITECTURE.md, docs/DASHBOARD.md, dashboard-next/README.md

    The React build is a separate stage in the Dockerfile. When it exists
    (production image) its static assets are mounted and any non-API path falls
    back to index.html so client-side routes (e.g. /agent) survive a refresh.
    In dev/test the dist directory does not exist and the app stays API-only.

    Future promotion path for Next.js:
    - When dashboard-next achieves full parity per its migration checklist,
      Dockerfile will switch to build dashboard-next and this function will
      be updated to mount .next/standalone or export output.
    - Until then, this function explicitly logs which frontend is canonical.
    """
    if dist_dir is None:
        dist_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
        )
    index_file = os.path.join(dist_dir, "index.html")
    
    # Detect shadow frontend presence for observability (not serving it)
    shadow_next_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dashboard-next", ".next")
    )
    shadow_next_exists = os.path.isdir(shadow_next_dir)
    
    if not os.path.isfile(index_file):
        log.info(
            "dashboard.not_built",
            canonical="dashboard (Vite)",
            shadow_next_present=shadow_next_exists,
            dist_dir=dist_dir,
        )
        return

    log.info(
        "dashboard.mounted",
        canonical="dashboard (Vite)",
        dist_dir=dist_dir,
        shadow_next_present=shadow_next_exists,
        note="dashboard-next is roadmap/shadow, not production served",
    )

    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # Never let the SPA shell swallow unknown API/telephony/auth paths -- a
        # typo'd client call must 404 (JSON), not receive an HTML 200.
        if full_path.startswith(("api/", "auth/", "telephony/", "channels/", "health")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        # API/telephony/auth paths are handled by the routers above; anything
        # else maps to a real file when one exists, otherwise the SPA shell.
        candidate = os.path.normpath(os.path.join(dist_dir, full_path))
        if (
            full_path
            and os.path.isfile(candidate)
            and os.path.abspath(candidate).startswith(os.path.abspath(dist_dir))
        ):
            return FileResponse(candidate)
        return FileResponse(index_file)


_mount_dashboard_if_built(app)
