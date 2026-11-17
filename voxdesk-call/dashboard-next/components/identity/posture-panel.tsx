"use client";

import { Dash } from "@/components/identity/badges";
import { InlineFacts, Section } from "@/components/identity/fields";
import { useLoader } from "@/components/identity/use-identity";
import { api } from "@/lib/api";
import type { MeResponse } from "@/lib/types";
import { identityApi } from "@/lib/identity";
import type { SecurityPosture, WorkspaceSettings } from "@/lib/identity-types";

/**
 * Workspace posture and settings, read-only.
 *
 * This panel exists because of a retirement, and says so: the Vite console had a
 * `/security-settings` page that fetched the tenant settings allowlist and the
 * server-resolved security posture (`GET /api/tenants/{id}/settings`,
 * `GET /api/tenants/{id}/security/posture`). The Next console covered policy,
 * sessions and API keys but not those two reads, so mapping the route as "done"
 * would have been a lie — a small one, but exactly the kind `docs/UI_PARITY.md`
 * exists to prevent. Porting the reads was cheaper than defending the gap.
 *
 * Two deliberate properties:
 *
 * * **Read-only.** The one writable field on that Vite page was the workspace
 *   name; everything else was rendered as text because the API's projection
 *   refuses the rest. A form here would offer edits the backend rejects, so the
 *   panel states facts and links to the pages that own the writable surfaces.
 * * **No certification language.** The posture object says where a request
 *   resolved (organization, tenant, environment, auth method). It is not a
 *   compliance attestation and the panel never implies one: a boolean from a
 *   config server is not a certificate, and "compliant" is not a word this page
 *   is allowed to print.
 */
export function PosturePanel() {
  // `/api/me` is the only call that names the caller's tenant. Everything else on
  // this panel is scoped by it, and nothing is requested until it is known — a
  // tenant segment built from `undefined` would be a 404 dressed up as data.
  const me = useLoader<MeResponse>(() => api.me());
  const tenantId = me.data?.user?.tenant_id ?? null;
  const posture = useLoader<SecurityPosture>(
    () => identityApi.posture(tenantId as string),
    { deps: [tenantId], enabled: Boolean(tenantId) },
  );
  const settings = useLoader<WorkspaceSettings>(
    () => identityApi.workspaceSettings(tenantId as string),
    { deps: [tenantId], enabled: Boolean(tenantId) },
  );

  if (me.error) return <p className="error-banner">{me.error}</p>;
  if (!tenantId) {
    return (
      <Section
        title="Workspace posture"
        description="Where this request resolved, according to the server."
      >
        <p className="muted">
          Loading the caller&apos;s workspace… if this stays here, the session has
          no tenant and the posture reads cannot be scoped.
        </p>
      </Section>
    );
  }

  return (
    <>
      <Section
        title="Workspace posture"
        description="Server-resolved hierarchy for this session. This is a lookup, not a compliance certification — no attestation is claimed here or implied."
      >
        {posture.error ? <p className="error-banner">{posture.error}</p> : null}
        {posture.loading ? <p className="muted">Loading…</p> : null}
        {posture.data ? (
          <InlineFacts
            facts={[
              { label: "Organization", value: posture.data.organization_id },
              { label: "Workspace", value: posture.data.tenant_id },
              {
                label: "Environment",
                value: posture.data.environment_id || <Dash />,
              },
              {
                label: "Default environment",
                value: posture.data.default_environment_id || <Dash />,
              },
              { label: "Authentication method", value: posture.data.auth_method },
              {
                label: "Machine credential",
                value: posture.data.is_machine ? "yes" : "no",
              },
              {
                label: "Tenant override from the client",
                value: posture.data.client_tenant_override ? "accepted" : "ignored",
              },
            ]}
          />
        ) : null}
      </Section>

      <Section
        title="Workspace settings"
        description="The allowlist the API returns. Secret and routing fields are withheld by the server, so they are absent here too — this page cannot leak what it cannot see."
      >
        {settings.error ? <p className="error-banner">{settings.error}</p> : null}
        {settings.loading ? <p className="muted">Loading…</p> : null}
        {settings.data ? (
          <InlineFacts
            facts={[
              { label: "Name", value: settings.data.name },
              { label: "Plan", value: settings.data.plan || <Dash /> },
              { label: "Lifecycle", value: settings.data.lifecycle_status },
              { label: "Industry", value: settings.data.industry || <Dash /> },
              { label: "Time zone", value: settings.data.timezone || <Dash /> },
              { label: "Language", value: settings.data.language || <Dash /> },
              { label: "SMS", value: settings.data.sms_enabled ? "enabled" : "off" },
              {
                label: "WhatsApp",
                value: settings.data.whatsapp_enabled ? "enabled" : "off",
              },
              {
                label: "Outbound calling",
                value: settings.data.outbound_enabled ? "enabled" : "off",
              },
              {
                label: "Call recording",
                value: settings.data.record_calls ? "enabled" : "off",
              },
              { label: "IVR", value: settings.data.ivr_enabled ? "enabled" : "off" },
              {
                label: "Human handoff",
                value: settings.data.humanize ? "enabled" : "off",
              },
            ]}
          />
        ) : null}
      </Section>
    </>
  );
}
