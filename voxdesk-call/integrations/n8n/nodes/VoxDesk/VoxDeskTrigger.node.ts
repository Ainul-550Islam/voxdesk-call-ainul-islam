/**
 * n8n Trigger node for VoxDesk (`integrations/n8n/nodes/VoxDesk/VoxDeskTrigger.node.ts`).
 *
 * Subscribes and unsubscribes via `POST /api/webhooks` and `DELETE /api/webhooks/{id}`
 * for events:
 * - `call.started`
 * - `call.ended`
 * - `call.analyzed`
 * - `transfer.initiated`
 * - `tool.invoked`
 */

import { createHmac, timingSafeEqual } from "crypto";

export interface IHookContext {
  getNodeWebhookUrl(name: string): string | undefined;
  getNodeParameter(name: string): unknown;
  getWorkflowStaticData(type: string): Record<string, unknown>;
  getCredentials(type: string): Promise<{
    baseUrl: string;
    apiKey: string;
    webhookSecret?: string;
  }>;
  helpers: {
    httpRequest(options: {
      method: "GET" | "POST" | "DELETE";
      url: string;
      headers?: Record<string, string>;
      body?: Record<string, unknown>;
      json?: boolean;
    }): Promise<Record<string, unknown>>;
  };
}

export interface IWebhookContext {
  getHeaderData(): Record<string, string | undefined>;
  getBodyData(): Record<string, unknown>;
  getCredentials(type: string): Promise<{
    baseUrl: string;
    apiKey: string;
    webhookSecret?: string;
  }>;
  helpers: {
    returnJsonArray(
      items: Array<Record<string, unknown>>
    ): Array<{ json: Record<string, unknown> }>;
  };
}

export class VoxDeskTrigger {
  description = {
    displayName: "VoxDesk Trigger",
    name: "voxDeskTrigger",
    group: ["trigger"],
    version: 1,
    description:
      "Starts a workflow when VoxDesk emits call lifecycle or tool webhook events",
    defaults: {
      name: "VoxDesk Trigger",
    },
    inputs: [],
    outputs: ["main"],
    credentials: [
      {
        name: "voxDeskApi",
        required: true,
      },
    ],
    webhooks: [
      {
        name: "default",
        httpMethod: "POST",
        responseMode: "onReceived",
        path: "voxdesk",
      },
    ],
    properties: [
      {
        displayName: "Events",
        name: "events",
        type: "multiOptions",
        required: true,
        default: ["call.ended"],
        options: [
          { name: "Call Started", value: "call.started" },
          { name: "Call Ended", value: "call.ended" },
          { name: "Call Analyzed", value: "call.analyzed" },
          { name: "Transfer Initiated", value: "transfer.initiated" },
          { name: "Tool Invoked", value: "tool.invoked" },
        ],
      },
    ],
  };

  webhookMethods = {
    default: {
      async checkExists(this: IHookContext): Promise<boolean> {
        const webhookData = this.getWorkflowStaticData("node");
        const webhookId = webhookData.webhookId as string | undefined;
        if (!webhookId) {
          return false;
        }
        const credentials = await this.getCredentials("voxDeskApi");
        const baseUrl = String(
          credentials.baseUrl || "http://localhost:8000"
        ).replace(/\/+$/, "");
        try {
          await this.helpers.httpRequest({
            method: "GET",
            url: `${baseUrl}/api/webhooks/${encodeURIComponent(webhookId)}`,
            headers: {
              Authorization: `Bearer ${credentials.apiKey}`,
            },
            json: true,
          });
          return true;
        } catch {
          delete webhookData.webhookId;
          return false;
        }
      },

      async create(this: IHookContext): Promise<boolean> {
        const webhookUrl = this.getNodeWebhookUrl("default");
        if (!webhookUrl) {
          return false;
        }
        const events = this.getNodeParameter("events") as string[];
        const credentials = await this.getCredentials("voxDeskApi");
        const baseUrl = String(
          credentials.baseUrl || "http://localhost:8000"
        ).replace(/\/+$/, "");

        const response = await this.helpers.httpRequest({
          method: "POST",
          url: `${baseUrl}/api/webhooks`,
          headers: {
            Authorization: `Bearer ${credentials.apiKey}`,
            "Content-Type": "application/json",
          },
          body: {
            url: webhookUrl,
            events,
            ...(credentials.webhookSecret
              ? { secret: credentials.webhookSecret }
              : {}),
          },
          json: true,
        });

        const webhookData = this.getWorkflowStaticData("node");
        webhookData.webhookId = response.id;
        return true;
      },

      async delete(this: IHookContext): Promise<boolean> {
        const webhookData = this.getWorkflowStaticData("node");
        const webhookId = webhookData.webhookId as string | undefined;
        if (!webhookId) {
          return true;
        }
        const credentials = await this.getCredentials("voxDeskApi");
        const baseUrl = String(
          credentials.baseUrl || "http://localhost:8000"
        ).replace(/\/+$/, "");
        try {
          await this.helpers.httpRequest({
            method: "DELETE",
            url: `${baseUrl}/api/webhooks/${encodeURIComponent(webhookId)}`,
            headers: {
              Authorization: `Bearer ${credentials.apiKey}`,
            },
            json: true,
          });
        } finally {
          delete webhookData.webhookId;
        }
        return true;
      },
    },
  };

  async webhook(this: IWebhookContext): Promise<{
    workflowData: Array<Array<{ json: Record<string, unknown> }>>;
  }> {
    const credentials = await this.getCredentials("voxDeskApi");
    const headers = this.getHeaderData();
    const body = this.getBodyData();

    if (credentials.webhookSecret) {
      const signatureHeader =
        headers["x-voxdesk-signature"] || headers["X-VoxDesk-Signature"] || "";
      const normalized = signatureHeader.trim().replace(/^sha256=/i, "");
      const sigBuf = Buffer.from(normalized, "hex");
      const expected = createHmac("sha256", credentials.webhookSecret)
        .update(JSON.stringify(body))
        .digest();
      if (
        sigBuf.length !== expected.length ||
        !timingSafeEqual(sigBuf, expected)
      ) {
        throw new Error("Invalid X-VoxDesk-Signature webhook signature");
      }
    }

    return {
      workflowData: [this.helpers.returnJsonArray([body])],
    };
  }
}
