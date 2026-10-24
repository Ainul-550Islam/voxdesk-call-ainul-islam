import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import IntegrationsV2CatalogPage from "../page";
import { StatusBadge } from "../components/StatusBadge";
import { ConnectButton } from "../components/ConnectButton";
import { MappingTable } from "../components/MappingTable";
import { int2Api } from "../lib/api";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    int2Api: {
      ...actual.int2Api,
      getCatalog: vi.fn(),
      connectProvider: vi.fn(),
    },
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Integrations v2 UI Honesty & Controls", () => {
  it("StatusBadge never shows LIVE for unconfigured or protocol-tested statuses", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <div>
            <StatusBadge status="NOT_CONFIGURED" />
            <StatusBadge status="PROTOCOL_TESTED" />
            <StatusBadge status="CONFIGURED" />
            <StatusBadge status="ERROR" />
          </div>
        );
      });
      expect(host.textContent).toContain("Not configured");
      expect(host.textContent).toContain("PROTOCOL_TESTED");
      expect(host.textContent).toContain("Configured");
      expect(host.textContent).toContain("Error");
      expect(host.textContent).not.toContain("LIVE");
    } finally {
      await act(async () => root.unmount());
    }
  });

  it("Catalog page renders provider cards from API data without hardcoded fake statuses", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.mocked(int2Api.getCatalog).mockResolvedValueOnce({
      providers: [
        {
          slug: "zendesk",
          display_name: "Zendesk Support",
          category: "helpdesk",
          auth_modes: ["oauth2"],
          capabilities: ["tickets", "webhooks"],
          default_scopes: [],
          webhook_signature_header: "X-Zendesk-Webhook-Signature",
          docs_note: "Zendesk Support v2",
          env_configured: false,
          ui_status: "NOT_CONFIGURED",
          connection_id: null,
          last_health_ok: null,
          last_health_at: null,
          last_error: "",
          verification_status: "NOT_CONFIGURED",
        },
        {
          slug: "zoho_crm",
          display_name: "Zoho CRM",
          category: "crm",
          auth_modes: ["oauth2"],
          capabilities: ["contacts", "deals"],
          default_scopes: [],
          webhook_signature_header: "X-Zoho-Webhook-Token",
          docs_note: "Zoho CRM v6",
          env_configured: true,
          ui_status: "CONFIGURED",
          connection_id: "conn-123",
          last_health_ok: true,
          last_health_at: "2026-10-24T00:00:00Z",
          last_error: "",
          verification_status: "PROTOCOL_TESTED",
        },
      ],
      supported_transforms: ["identity", "e164_phone"],
      supported_entities: ["Contact", "Ticket"],
      supported_directions: ["inbound", "outbound", "bidirectional"],
    });

    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(<IntegrationsV2CatalogPage />);
      });
      expect(host.textContent).toContain("Zendesk Support");
      expect(host.textContent).toContain("Not configured");
      expect(host.textContent).toContain("Zoho CRM");
      expect(host.textContent).toContain("Configured");
      expect(host.textContent).toContain("PROTOCOL_TESTED");
      expect(host.textContent).not.toContain("LIVE_VERIFIED");
    } finally {
      await act(async () => root.unmount());
    }
  });

  it("ConnectButton starts OAuth popup flow and surfaces API errors cleanly", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const openSpy = vi.fn(() => ({ closed: false }) as unknown as Window);
    vi.stubGlobal("open", openSpy);

    vi.mocked(int2Api.connectProvider)
      .mockResolvedValueOnce({
        authorize_url: "https://slack.com/oauth/v2/authorize?state=abc",
        expires_at: "2026-10-24T01:00:00Z",
      })
      .mockRejectedValueOnce(new Error("Missing SLACK_CLIENT_ID"));

    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(<ConnectButton provider="slack" />);
      });
      const btn = host.querySelector(
        '[data-testid="connect-oauth-slack"]'
      ) as HTMLButtonElement;
      expect(btn).toBeTruthy();

      await act(async () => {
        btn.click();
      });
      expect(int2Api.connectProvider).toHaveBeenCalledTimes(1);
      expect(openSpy).toHaveBeenCalledWith(
        "https://slack.com/oauth/v2/authorize?state=abc",
        "voxdesk_oauth_slack",
        "width=620,height=720"
      );

      // Simulate OAuth error callback then retry with error
      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            data: {
              type: "voxdesk_int2_oauth_complete",
              provider: "slack",
              error: "access_denied",
            },
          })
        );
      });
      expect(host.textContent).toContain("access_denied");

      await act(async () => {
        btn.click();
      });
      expect(host.textContent).toContain("Missing SLACK_CLIENT_ID");
    } finally {
      await act(async () => root.unmount());
    }
  });

  it("MappingTable validates empty fields and emits updated rules on transform change", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const onChange = vi.fn();
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <MappingTable
            rules={[
              {
                entity_type: "Contact",
                direction: "bidirectional",
                voxdesk_field: "phone",
                external_field: "",
                transform: "identity",
                transform_config: {},
                required: true,
                default_value: null,
              },
            ]}
            transforms={["identity", "e164_phone", "lowercase"]}
            validationErrors={[
              { field: "Phone", message: "Required field 'Phone' is missing" },
            ]}
            onChange={onChange}
          />
        );
      });

      const errList = host.querySelector('[data-testid="mapping-validation-errors"]');
      expect(errList?.textContent).toContain("both VoxDesk field and vendor field are required");
      expect(errList?.textContent).toContain("Required field 'Phone' is missing");

      const select = host.querySelector(
        '[aria-label="transform-0"]'
      ) as HTMLSelectElement;
      await act(async () => {
        select.value = "e164_phone";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange.mock.calls[0][0][0].transform).toBe("e164_phone");
    } finally {
      await act(async () => root.unmount());
    }
  });
});
