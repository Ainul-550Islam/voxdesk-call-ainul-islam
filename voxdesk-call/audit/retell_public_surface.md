# Retell public-surface benchmark

**Observed:** 2026-10-06 (Asia/Dhaka)  
**Purpose:** Publicly observable Retell pages, product concepts, API documentation, and changelog items used as the external comparison baseline for VoxDesk Prompt 8. This is a vendor-surface inventory, not evidence about Retell's private architecture and not evidence that VoxDesk implements the same capability.

## Evidence rules

- Only Retell's public website, public documentation, API references, and public changelog are used below.
- Marketing language is treated as a statement about Retell's advertised product, not as an independent performance, compliance, or deployment certification.
- Historical changelog entries are date-stamped. The changelog is used to identify surfaced features, not to infer undocumented implementation details.
- No Retell price is copied into VoxDesk. Retell pricing is a moving public catalogue; VoxDesk is audited against architecture and feature concepts, not identical price points.
- A VoxDesk route, UI component, or integration record is not treated as a test pass or a connected provider. Repository evidence and runtime evidence are recorded separately in `retell_feature_matrix.md` and `retell_gap_register.md`.

## Public website and documentation surfaces sampled

| Public surface | What it establishes for this benchmark |
|---|---|
| [Retell home](https://www.retellai.com/) | Public product positioning and discoverable product surface. |
| [Retell pricing](https://www.retellai.com/pricing) | Public pricing architecture. The catalogue emphasizes usage-based voice pricing, concurrency, knowledge bases, and enterprise options; individual prices are deliberately not copied. |
| [Documentation introduction](https://docs.retellai.com/general/introduction) | Public documentation map and how Retell frames its voice-agent platform. |
| [API overview](https://docs.retellai.com/api-references/overview) | Public API domains, base URL, bearer-key authentication, and SDK entry points. |
| [Create Voice Agent API](https://docs.retellai.com/api-references/create-agent) | A public API path for creating voice agents. |
| [Update Chat Agent API](https://docs.retellai.com/api-references/update-chat-agent) | A public API path for chat-agent configuration. |
| [Create Phone Call API](https://docs.retellai.com/api-references/create-phone-call) | A public API path for initiating phone calls. |
| [Outbound call guide](https://docs.retellai.com/deploy/outbound-call) | Outbound-call deployment concepts and prerequisites. |
| [Dynamic variables](https://docs.retellai.com/build/dynamic-variables) | Agent variables can be supplied and used at runtime; this is distinct from a static prompt editor. |
| [Session history](https://docs.retellai.com/features/session-history) | Public session/call history surface. |

## Capability surfaces in public documentation

### Agent construction and runtime

Retell publicly documents voice agents, chat agents, editable agent settings, agent versions, prompt and model configuration, dynamic variables, tools, and public APIs for agent creation/update. These are the comparison surfaces for VoxDesk's agent builder and version lifecycle. The existence of a vendor API does not establish that a VoxDesk provider is configured or reachable.

### Testing and release decisions

The [testing overview](https://docs.retellai.com/test/test-overview) separates Playground use, graded simulation, web-call audio testing, and real phone-call testing. The public documentation distinguishes a simulation result that is graded from an ordinary test interaction. This matters for VoxDesk reporting: a deterministic simulation must not be presented as a carrier-connected production call.

The [A/B testing guide](https://docs.retellai.com/deploy/ab-testing) describes percentage-based splits for inbound/outbound calls and chats, analytics comparisons across agent versions, and dynamic per-call selection as a distinct webhook/API path. Version history alone is therefore not equivalent to percentage-based traffic allocation.

### Phone, SIP, inbound, and outbound

The [custom telephony guide](https://docs.retellai.com/deploy/custom-telephony) distinguishes elastic SIP from dial-to-SIP. Its documented connection test sends SIP OPTIONS without placing a call; a real inbound or outbound call remains necessary to establish call-path operation. The [outbound call guide](https://docs.retellai.com/deploy/outbound-call) and Create Phone Call API are the public outbound references.

The [inbound call/SMS webhook guide](https://docs.retellai.com/features/inbound-call-webhook) describes configuration per phone number and webhook choices including rejecting a request, overriding the agent/version/settings, and supplying dynamic variables. It documents a 10-second timeout and up to two retries. Those are vendor-documented behaviors; they are not assumed to be VoxDesk behavior.

The [webhook overview](https://docs.retellai.com/features/webhook-overview) describes event types, account- or agent-level registration, signature verification, and retry behavior.

### Live monitoring and human intervention

The [live monitoring guide](https://docs.retellai.com/features/live-monitoring) publicly describes active-call monitoring, streaming transcripts, listen-in, whisper, takeover, and call-ending controls, with permission and privacy restrictions. A control-plane record or button alone does not establish a working live audio path.

### Analytics, CRM, knowledge, and workflows

The [analytics dashboard guide](https://docs.retellai.com/features/analytics-dashboard) documents custom call/chat dashboards, metrics, filters, breakdowns, and agent-version comparisons.

The [CRM integration overview](https://docs.retellai.com/integrations/crm-overview) names Salesforce, HubSpot, Dynamics 365, GoHighLevel, and Zoho, and describes contact synchronization, mapping, and activity logging. An integration name in a catalogue is not proof of a connected tenant account.

The [knowledge-base guide](https://docs.retellai.com/build/knowledge-base) describes URL, document, and text sources, connected-drive sources, retrieval, refresh, and public limits. A source record is not the same as a successful retrieval during a call.

The [Conductor overview](https://docs.retellai.com/conductor/overview) describes an assistant for building and investigating agents with proposed changes for review. The comparison point is the human-reviewed change lifecycle, not an assumption of automatic production mutation.

### Privacy and data controls

[Data Storage Settings](https://docs.retellai.com/accounts/privacy-disable) documents storage modes, retention, and PII scrubbing across transcripts, recordings, logs, variables, metadata, analysis, tool data, DTMF, and SMS. This is broader than a single transcript-redaction check; VoxDesk is scored only for the specific controls and tests present in its repository.

## Changelog observations

- The [24 August 2026 changelog entry](https://www.retellai.com/changelog/retell-workflows-brex-top-25-and-more) announces Retell Workflows and includes other product updates. It is used as evidence that Workflows were publicly surfaced by that date.
- The [19 June 2026 changelog entry](https://www.retellai.com/changelog/live-call-monitoring-custom-dashboards-built-in-crm-colloquial-model-expressive-mode) describes live monitoring, custom dashboards, and built-in CRM. The entry also describes real-time transcripts and listen/whisper/takeover actions, dashboard filtering, and CRM syncing.
- The public [Retell changelog](https://www.retellai.com/changelog) is the index used to date these observations. No private Retell implementation detail is inferred.

## Benchmark summary

The public Retell surface spans voice and chat agents, simulation and phone-call testing, version experiments, phone/SIP connectivity, inbound webhooks, live monitoring and takeover, analytics dashboards, built-in CRM and integrations, knowledge sources, Workflows, Conductor, data retention/PII controls, and API/SDK surfaces. The feature-by-feature comparison intentionally marks limited, credential-dependent, simulated, or untested VoxDesk paths as `PARTIAL` or `NOT_CONFIGURED` rather than translating route presence into parity.
