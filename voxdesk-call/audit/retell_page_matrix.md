# Page-by-page Retell / VoxDesk matrix

**Audit date:** 2026-10-06. The active dashboard entrypoint is `dashboard/src/main.jsx`, which mounts `dashboard/src/app/app.tsx`; its active route table is `dashboard/src/app/router.tsx`. That table has 102 unique route entries and 68 distinct component names; all 68 names map to active switch cases in `app.tsx`. The separate legacy `dashboard/src/App.jsx` is not the active main entrypoint, although several explicit legacy bridge routes delegate to it.

## Score dimensions and interpretation

The score vector in the canonical matrix is ordered as `ROUTE_EXISTS / UI_EXISTS / API_EXISTS / DATABASE_SUPPORT / REAL_DATA / AUTH / RBAC / ERROR_STATE / EMPTY_STATE / RESPONSIVE / E2E / RETELL_PARITY`. Each numeric score is one of 0, 25, 50, 75, or 100. `NA` is used only where the page is static/public and persistence is not an expected product behavior. Scores are conservative evidence grades, not an automated accessibility or visual-quality result.

- `100` means the stated condition is present and supported by source plus a relevant passing route/API/data test where applicable.
- `75` means code and/or a closely related API test supports the behavior, but not every page interaction was driven in a browser.
- `50` means partial implementation or code inspection only; this is the default for error/empty/responsive dimensions without a browser-specific assertion.
- `25` means only limited route or API evidence exists; `0` would mean missing, but no listed canonical route is missing.
- `E2E` means a backend/API/database lifecycle test, not a browser screenshot or a click-by-click frontend journey. The 16 named Prompt 8 E2E tests passed sequentially. Dashboard component tests also passed, but viewport-specific responsive testing was not run.
- `RETELL_PARITY` is a coarse match to the public benchmark, not production readiness. A `VERIFIED` VoxDesk path can still score 50 when Retell’s public surface is broader.
- The authenticated parity page deliberately displays route/integration evidence as implementation evidence only; it does not translate a registered route into a runtime pass.

## Canonical product-order scorecard

| Page | Canonical route / active component | Scores R/UI/API/DB/Data/Auth/RBAC/Error/Empty/Resp/E2E/Parity | Evidence boundary |
|---|---|---|---|
| Home | `/` · `HomePage` | `100/100/75/NA/100/100/100/75/75/50/75/50` | Public home/API boundary and no-synthetic-metrics tests pass; no browser click-through. |
| AI Voice Agent | `/product/voice-agents` · `VoiceAgentsPage` | `100/100/75/NA/100/100/100/75/75/50/75/50` | Public product page and persisted builder/version journey; provider is deployment-specific. |
| Use Cases | `/use-cases` · `UseCasesPage` | `100/100/100/75/100/100/100/75/75/50/100/50` | Public catalog, use-case detail, and master flow use server-backed catalog data. |
| Industry | `/industries` · `IndustriesPage` | `100/100/50/NA/75/100/100/50/50/50/25/50` | Route and component exist; this audit did not find a dedicated industry-data E2E. |
| Integrations | `/integrations` · `IntegrationsPage` | `100/100/100/75/100/100/100/75/75/50/75/50` | Registry/status API exists; public catalogue does not assert a tenant connection. |
| Pricing | `/pricing` · `PricingPage` | `100/100/100/75/100/100/100/75/75/50/100/50` | Public pricing endpoint and seed-catalogue status are exercised; no Retell prices are copied. |
| Login | `/login` · `LoginPage` | `100/100/100/100/100/100/100/75/75/50/75/50` | Protected-route redirect and auth boundary tests pass; complete password-reset lifecycle not covered here. |
| Signup | `/signup` · `SignupPage` | `100/100/100/100/100/100/100/75/75/50/100/50` | Signup creates tenant/environment and authenticated dashboard access in E2E. |
| Dashboard | `/app/overview` · `OperatorConsole` | `100/100/100/100/100/100/100/75/75/50/100/50` | Authenticated, tenant/environment-scoped APIs and master/public-to-dashboard E2E pass. |
| Agent Builder | `/dashboard/agents/:id/builder` · `AgentBuilderPage` | `100/100/100/100/100/100/100/75/75/50/100/75` | Persisted builder draft/publish/version and simulation paths pass; browser-level page editing is not asserted. |
| Testing / Simulation | `/dashboard/simulations` · `SimulationsPage` | `100/100/100/100/100/100/100/75/75/50/100/75` | Version-pinned playground and failed-scenario persistence E2E pass. |
| Calls | `/calls` · `CallLogConsole` | `100/100/100/100/100/100/100/75/75/50/100/75` | Call, transcript, replay, monitor, export authorization, and analytics API paths pass. |
| Analytics | `/dashboard/analytics` · `AnalyticsPage` | `100/75/100/100/100/100/100/75/75/50/100/50` | Tenant-scoped analytics reads pass; route bridges to the established analytics console; custom dashboards are partial. |
| Knowledge Base | `/dashboard/knowledge` · `AgentKnowledgePage` | `100/100/100/100/100/100/100/75/75/50/100/75` | Persisted source ingestion, association, and retrieval pass; connected-drive sync is not verified. |
| Phone Numbers | `/phone-numbers` · `PhoneNumbersPage` | `100/100/100/100/100/100/100/75/75/50/100/50` | Number records, environment binding, and call path pass with explicit simulated/test boundaries. |
| Campaigns | `/campaigns` · `LegacyCampaignsPage` | `100/75/100/100/100/100/100/75/75/50/100/50` | Protected legacy bridge; audience planning and controlled workflow lead action pass; live dialing is not run. |
| CRM | `/dashboard/contacts` · `ContactsPage` | `100/100/100/100/100/100/100/75/75/50/100/50` | Persisted contacts/memory path passes; external CRM remains NOT_CONFIGURED. |
| Workflows | `/workflows` · `WorkflowsPage` | `100/100/100/100/100/100/100/75/75/50/100/75` | Persisted create/publish/execute/read lifecycle passes in backend E2E and dashboard tests. |
| Conductor | `/dashboard/conductor` · `ConductorPage` | `100/100/100/100/100/100/100/75/75/50/100/75` | Diagnosis, reproduction, human review, and immutable apply E2E pass; no auto-publish. |
| Settings | `/settings` · `LegacySettingsPage` | `100/75/100/100/100/100/100/75/75/50/100/50` | Protected legacy bridge; security settings, session/key and tenant policy API tests pass. |
| Billing | `/billing` · `LegacyBillingPage` | `100/75/100/100/100/100/100/75/75/50/100/50` | Protected legacy bridge; plan/usage/invoice state tests pass; no payment settlement is claimed. |
| Final Parity / diagnostics | `/dashboard/final-parity` · `FinalParityPage` | `100/100/100/100/100/100/75/100/75/50/75/50` | Parity page fetches authenticated capability/integration APIs; component test keeps PARTIAL and NOT_CONFIGURED visible. |

## Page-level observations

1. **Public website:** the current router has public home/product/use-case/industry/integration/pricing routes. The public manifest/pricing/use-case/status/contact-sales APIs return server-backed data where intended; marketing copy remains static and does not invent usage metrics or customer outcomes. Public/auth boundary tests pass. No real browser viewport, keyboard, contrast, or full CTA click-chain run is recorded.
2. **Auth:** signup creates a durable tenant and production environment; authenticated APIs use a tenant/environment context. Anonymous users are blocked from protected APIs/routes and unsafe return paths are rejected. Password reset/email verification and full session-restoration coverage are not all established by the Prompt 8 E2E subset.
3. **Operator pages:** agent, simulation, call, analytics, knowledge, phone, campaign, CRM/contact, workflow, Conductor, settings, and billing pages map to active or explicit legacy-bridge route components. Backend E2E paths cover persistence and authorization, but those tests do not constitute a browser click-through of every control.
4. **Parity diagnostics:** `/dashboard/final-parity` calls the authenticated capability and integration APIs. Its component regression verifies live-shaped API response consumption, displays `PARTIAL` and `NOT_CONFIGURED`, and rejects `PRODUCTION_READY` as an unsupported inference. The E2E inspection API is read-only and resolves the exact call-pinned version.
5. **Responsive/accessibility:** responsive breakpoints and reduced-motion rules exist in shared and page stylesheets. This audit did not run actual desktop/tablet/mobile browser viewports, keyboard-only navigation, screen-reader checks, contrast measurements, or a full performance profile. Responsive/accessibility values remain 50, not 100.

## Complete active route inventory (102 unique paths)

Every path below is read from `dashboard/src/app/router.tsx`; route aliases that share a component share the component-level observations above. `AUTH` routes are login/signup; `PROTECTED` routes are behind the authenticated dashboard boundary; all other listed categories are public. A `legacyPath` indicates an explicit bridge to the older page implementation, not a separate active main entrypoint.

| # | Path | Component | Category | Boundary | Legacy target |
|---:|---|---|---|---|---|
| 1 | `/` | `HomePage` | `public` | PUBLIC | — |
| 2 | `/home` | `HomePage` | `public` | PUBLIC | — |
| 3 | `/product` | `VoiceAgentsPage` | `product` | PUBLIC | — |
| 4 | `/product/voice-agents` | `VoiceAgentsPage` | `product` | PUBLIC | — |
| 5 | `/product/customer-service` | `CustomerServicePage` | `product` | PUBLIC | — |
| 6 | `/product/answering-service` | `AnsweringServicePage` | `product` | PUBLIC | — |
| 7 | `/product/appointment-setter` | `AppointmentSetterPage` | `product` | PUBLIC | — |
| 8 | `/product/telemarketing` | `TelemarketingPage` | `product` | PUBLIC | — |
| 9 | `/product/outbound` | `OutboundPage` | `product` | PUBLIC | — |
| 10 | `/product/inbound` | `InboundPage` | `product` | PUBLIC | — |
| 11 | `/product/analytics` | `AnalyticsPage` | `product` | PUBLIC | — |
| 12 | `/product/voice-cloning` | `VoiceCloningPage` | `product` | PUBLIC | — |
| 13 | `/solutions` | `SolutionsPage` | `solutions` | PUBLIC | — |
| 14 | `/solutions/support` | `CustomerServicePage` | `solutions` | PUBLIC | — |
| 15 | `/solutions/appointments` | `AppointmentSetterPage` | `solutions` | PUBLIC | — |
| 16 | `/solutions/lead-qualification` | `TelemarketingPage` | `solutions` | PUBLIC | — |
| 17 | `/solutions/outbound` | `OutboundPage` | `solutions` | PUBLIC | — |
| 18 | `/use-cases` | `UseCasesPage` | `solutions` | PUBLIC | — |
| 19 | `/use-cases/:slug` | `UseCasesDetailPage` | `solutions` | PUBLIC | — |
| 20 | `/industries` | `IndustriesPage` | `solutions` | PUBLIC | — |
| 21 | `/industries/:slug` | `IndustryDetailPage` | `solutions` | PUBLIC | — |
| 22 | `/integrations` | `IntegrationsPage` | `product` | PUBLIC | — |
| 23 | `/integrations/:slug` | `IntegrationDetailPage` | `product` | PUBLIC | — |
| 24 | `/pricing` | `PricingPage` | `public` | PUBLIC | — |
| 25 | `/developers` | `DevelopersPage` | `developers` | PUBLIC | — |
| 26 | `/docs` | `DocsPage` | `developers` | PUBLIC | — |
| 27 | `/docs/:slug` | `DocsPage` | `developers` | PUBLIC | — |
| 28 | `/security` | `SecurityPage` | `company` | PUBLIC | — |
| 29 | `/trust` | `TrustPage` | `company` | PUBLIC | — |
| 30 | `/compliance` | `CompliancePage` | `company` | PUBLIC | — |
| 31 | `/status` | `StatusPage` | `company` | PUBLIC | — |
| 32 | `/resources` | `ResourcesPage` | `public` | PUBLIC | — |
| 33 | `/blog` | `BlogPage` | `public` | PUBLIC | — |
| 34 | `/blog/:slug` | `BlogPost` | `public` | PUBLIC | — |
| 35 | `/about` | `AboutPage` | `company` | PUBLIC | — |
| 36 | `/careers` | `CareersPage` | `company` | PUBLIC | — |
| 37 | `/team` | `TeamPage` | `company` | PUBLIC | — |
| 38 | `/contact` | `ContactSalesPage` | `company` | PUBLIC | — |
| 39 | `/contact-sales` | `ContactSalesPage` | `company` | PUBLIC | — |
| 40 | `/book-demo` | `BookDemoPage` | `company` | PUBLIC | — |
| 41 | `/login` | `LoginPage` | `auth` | AUTH | — |
| 42 | `/signup` | `SignupPage` | `auth` | AUTH | — |
| 43 | `/privacy` | `PrivacyPage` | `legal` | PUBLIC | — |
| 44 | `/terms` | `TermsPage` | `legal` | PUBLIC | — |
| 45 | `/dpa` | `DPA` | `legal` | PUBLIC | — |
| 46 | `/sla` | `SLA` | `legal` | PUBLIC | — |
| 47 | `/app` | `OperatorConsole` | `dashboard` | PROTECTED | — |
| 48 | `/app/overview` | `OperatorConsole` | `dashboard` | PROTECTED | — |
| 49 | `/app/agents` | `AgentsPage` | `dashboard` | PROTECTED | — |
| 50 | `/app/public-keys` | `WidgetSettings` | `dashboard` | PROTECTED | — |
| 51 | `/dashboard` | `AgentsPage` | `dashboard` | PROTECTED | — |
| 52 | `/dashboard/agents` | `AgentsPage` | `dashboard` | PROTECTED | — |
| 53 | `/dashboard/agents/new` | `CreateAgentPage` | `dashboard` | PROTECTED | — |
| 54 | `/dashboard/agents/:id` | `AgentDetailPage` | `dashboard` | PROTECTED | — |
| 55 | `/dashboard/agents/:id/builder` | `AgentBuilderPage` | `dashboard` | PROTECTED | — |
| 56 | `/dashboard/agents/:id/versions/:version` | `AgentVersionDetailPage` | `dashboard` | PROTECTED | — |
| 57 | `/dashboard/agents/:id/settings` | `AgentSettingsPage` | `dashboard` | PROTECTED | — |
| 58 | `/dashboard/agents/list` | `AgentListPage` | `dashboard` | PROTECTED | — |
| 59 | `/app/agents/list` | `AgentListPage` | `dashboard` | PROTECTED | — |
| 60 | `/dashboard/knowledge` | `AgentKnowledgePage` | `dashboard` | PROTECTED | — |
| 61 | `/app/knowledge` | `AgentKnowledgePage` | `dashboard` | PROTECTED | — |
| 62 | `/dashboard/agents/archive` | `AgentArchivePage` | `dashboard` | PROTECTED | — |
| 63 | `/app/agents/archive` | `AgentArchivePage` | `dashboard` | PROTECTED | — |
| 64 | `/dashboard/agents/:id/voice` | `AgentVoicePage` | `dashboard` | PROTECTED | — |
| 65 | `/dashboard/agents/:id/model` | `AgentModelPage` | `dashboard` | PROTECTED | — |
| 66 | `/dashboard/agents/:id/tools` | `AgentToolsPage` | `dashboard` | PROTECTED | — |
| 67 | `/dashboard/agents/:id/test-history` | `AgentTestHistoryPage` | `dashboard` | PROTECTED | — |
| 68 | `/dashboard/agents/:id/duplicate` | `AgentDuplicatePage` | `dashboard` | PROTECTED | — |
| 69 | `/dashboard/public-keys` | `WidgetSettings` | `dashboard` | PROTECTED | — |
| 70 | `/dashboard/chat-agents` | `ChatAgentsPage` | `dashboard` | PROTECTED | — |
| 71 | `/dashboard/contacts` | `ContactsPage` | `dashboard` | PROTECTED | — |
| 72 | `/dashboard/playground` | `AgentPlaygroundPage` | `dashboard` | PROTECTED | — |
| 73 | `/dashboard/simulations` | `SimulationsPage` | `dashboard` | PROTECTED | — |
| 74 | `/dashboard/qa-scorecards` | `QAScorecardsPage` | `dashboard` | PROTECTED | — |
| 75 | `/dashboard/conductor` | `ConductorPage` | `dashboard` | PROTECTED | — |
| 76 | `/dashboard/phone-numbers` | `PhoneNumbersPage` | `dashboard` | PROTECTED | — |
| 77 | `/dashboard/call-runtime` | `CallRuntimePage` | `dashboard` | PROTECTED | — |
| 78 | `/app/phone-numbers` | `PhoneNumbersPage` | `dashboard` | PROTECTED | — |
| 79 | `/app/call-runtime` | `CallRuntimePage` | `dashboard` | PROTECTED | — |
| 80 | `/studio/conductor` | `ConductorPage` | `dashboard` | PROTECTED | — |
| 81 | `/product/playground` | `AgentPlaygroundPage` | `product` | PUBLIC | — |
| 82 | `/product/simulations` | `SimulationsPage` | `product` | PUBLIC | — |
| 83 | `/product/qa-scorecards` | `QAScorecardsPage` | `product` | PUBLIC | — |
| 84 | `/product/conductor` | `ConductorPage` | `product` | PUBLIC | — |
| 85 | `/dashboard/analytics` | `AnalyticsPage` | `dashboard` | PROTECTED | `/analytics` |
| 86 | `/dashboard/final-parity` | `FinalParityPage` | `dashboard` | PROTECTED | — |
| 87 | `/workflows` | `WorkflowsPage` | `dashboard` | PROTECTED | — |
| 88 | `/dashboard/workflows` | `WorkflowsPage` | `dashboard` | PROTECTED | — |
| 89 | `/campaigns` | `LegacyCampaignsPage` | `dashboard` | PROTECTED | `/campaigns` |
| 90 | `/dashboard/campaigns` | `LegacyCampaignsPage` | `dashboard` | PROTECTED | `/campaigns` |
| 91 | `/settings` | `LegacySettingsPage` | `dashboard` | PROTECTED | `/security-settings` |
| 92 | `/dashboard/settings` | `LegacySettingsPage` | `dashboard` | PROTECTED | `/security-settings` |
| 93 | `/security-settings` | `LegacySettingsPage` | `dashboard` | PROTECTED | `/security-settings` |
| 94 | `/billing` | `LegacyBillingPage` | `dashboard` | PROTECTED | `/billing` |
| 95 | `/dashboard/billing` | `LegacyBillingPage` | `dashboard` | PROTECTED | `/billing` |
| 96 | `/workspace` | `OperatorConsole` | `dashboard` | PROTECTED | — |
| 97 | `/calls/:id` | `CallLogConsole` | `dashboard` | PROTECTED | — |
| 98 | `/calls` | `CallLogConsole` | `dashboard` | PROTECTED | — |
| 99 | `/dashboard/calls` | `CallLogConsole` | `dashboard` | PROTECTED | — |
| 100 | `/dashboard/calls/:id` | `CallLogConsole` | `dashboard` | PROTECTED | — |
| 101 | `/agent` | `AgentsPage` | `dashboard` | PROTECTED | — |
| 102 | `/phone-numbers` | `PhoneNumbersPage` | `dashboard` | PROTECTED | — |

**Inventory result:** 102 route entries, 102 distinct path patterns, no route inventory omissions. This is route-table evidence; it does not mean all 102 pages were individually opened in a browser or subjected to the same 12-dimensional runtime test.
