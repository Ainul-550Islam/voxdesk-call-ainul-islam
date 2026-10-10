/**
 * n8n Action node for VoxDesk (`integrations/n8n/nodes/VoxDesk/VoxDesk.node.ts`).
 *
 * Operations (real API calls only):
 * - `createCall` -> `POST /api/v1/telephony/calls`
 * - `createWebCall` -> `POST /api/web-calls`
 * - `createBatch` -> `POST /api/calls/outbound/bulk`
 * - `getCall` -> `GET /api/calls/{callId}`
 */

export interface IExecuteContext {
  getInputData(): Array<{ json: Record<string, unknown> }>;
  getNodeParameter(name: string, itemIndex: number, fallback?: unknown): unknown;
  getCredentials(type: string): Promise<{
    baseUrl: string;
    apiKey: string;
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

export class VoxDesk {
  description = {
    displayName: "VoxDesk",
    name: "voxDesk",
    group: ["transform"],
    version: 1,
    subtitle: '={{$parameter["operation"]}}',
    description:
      "Create outbound calls, browser web calls, batch calls, and fetch call details via VoxDesk REST API",
    defaults: {
      name: "VoxDesk",
    },
    inputs: ["main"],
    outputs: ["main"],
    credentials: [
      {
        name: "voxDeskApi",
        required: true,
      },
    ],
    properties: [
      {
        displayName: "Operation",
        name: "operation",
        type: "options",
        noDataExpression: true,
        options: [
          {
            name: "Create Call",
            value: "createCall",
            description: "Place an outbound PSTN/SIP voice call",
          },
          {
            name: "Create Web Call",
            value: "createWebCall",
            description: "Create a browser WebRTC / WebSocket voice call session",
          },
          {
            name: "Create Batch",
            value: "createBatch",
            description: "Enqueue a batch of outbound calls",
          },
          {
            name: "Get Call",
            value: "getCall",
            description: "Fetch call details, metrics, and transcript metadata",
          },
        ],
        default: "createWebCall",
      },
      {
        displayName: "Agent ID",
        name: "agentId",
        type: "string",
        default: "",
        required: true,
        displayOptions: {
          show: {
            operation: ["createCall", "createWebCall", "createBatch"],
          },
        },
      },
      {
        displayName: "To Phone Number (E.164)",
        name: "toNumber",
        type: "string",
        default: "",
        required: true,
        displayOptions: {
          show: {
            operation: ["createCall"],
          },
        },
      },
      {
        displayName: "From Phone Number (E.164)",
        name: "fromNumber",
        type: "string",
        default: "",
        required: false,
        displayOptions: {
          show: {
            operation: ["createCall", "createBatch"],
          },
        },
      },
      {
        displayName: "Recipients JSON",
        name: "recipientsJson",
        type: "json",
        default: "[]",
        required: true,
        displayOptions: {
          show: {
            operation: ["createBatch"],
          },
        },
      },
      {
        displayName: "Dynamic Variables JSON",
        name: "dynamicVarsJson",
        type: "json",
        default: "{}",
        required: false,
        displayOptions: {
          show: {
            operation: ["createCall", "createWebCall"],
          },
        },
      },
      {
        displayName: "Metadata JSON",
        name: "metadataJson",
        type: "json",
        default: "{}",
        required: false,
        displayOptions: {
          show: {
            operation: ["createCall", "createWebCall"],
          },
        },
      },
      {
        displayName: "Call ID",
        name: "callId",
        type: "string",
        default: "",
        required: true,
        displayOptions: {
          show: {
            operation: ["getCall"],
          },
        },
      },
    ],
  };

  async execute(
    this: IExecuteContext
  ): Promise<Array<Array<{ json: Record<string, unknown> }>>> {
    const items = this.getInputData();
    const credentials = await this.getCredentials("voxDeskApi");
    const baseUrl = String(credentials.baseUrl || "http://localhost:8000").replace(
      /\/+$/,
      ""
    );
    const headers = {
      Authorization: `Bearer ${credentials.apiKey}`,
      "Content-Type": "application/json",
    };

    const returnData: Array<{ json: Record<string, unknown> }> = [];

    for (let i = 0; i < items.length; i++) {
      const operation = String(this.getNodeParameter("operation", i));

      if (operation === "createCall") {
        const agentId = String(this.getNodeParameter("agentId", i));
        const toNumber = String(this.getNodeParameter("toNumber", i));
        const fromNumber = String(this.getNodeParameter("fromNumber", i, ""));
        const dynamicVars = JSON.parse(
          String(this.getNodeParameter("dynamicVarsJson", i, "{}"))
        ) as Record<string, unknown>;
        const metadata = JSON.parse(
          String(this.getNodeParameter("metadataJson", i, "{}"))
        ) as Record<string, unknown>;

        const response = await this.helpers.httpRequest({
          method: "POST",
          url: `${baseUrl}/api/v1/telephony/calls`,
          headers,
          body: {
            agent_id: agentId,
            to_number: toNumber,
            ...(fromNumber ? { from_number: fromNumber } : {}),
            dynamic_vars: dynamicVars,
            metadata,
          },
          json: true,
        });
        returnData.push({ json: response });
      } else if (operation === "createWebCall") {
        const agentId = String(this.getNodeParameter("agentId", i));
        const dynamicVars = JSON.parse(
          String(this.getNodeParameter("dynamicVarsJson", i, "{}"))
        ) as Record<string, unknown>;
        const metadata = JSON.parse(
          String(this.getNodeParameter("metadataJson", i, "{}"))
        ) as Record<string, unknown>;

        const response = await this.helpers.httpRequest({
          method: "POST",
          url: `${baseUrl}/api/web-calls`,
          headers,
          body: {
            agent_id: agentId,
            dynamic_vars: dynamicVars,
            metadata,
          },
          json: true,
        });
        returnData.push({ json: response });
      } else if (operation === "createBatch") {
        const agentId = String(this.getNodeParameter("agentId", i));
        const fromNumber = String(this.getNodeParameter("fromNumber", i, ""));
        const recipients = JSON.parse(
          String(this.getNodeParameter("recipientsJson", i, "[]"))
        ) as Array<Record<string, unknown>>;

        const response = await this.helpers.httpRequest({
          method: "POST",
          url: `${baseUrl}/api/calls/outbound/bulk`,
          headers,
          body: {
            agent_id: agentId,
            ...(fromNumber ? { from_number: fromNumber } : {}),
            calls: recipients,
          },
          json: true,
        });
        returnData.push({ json: response });
      } else if (operation === "getCall") {
        const callId = String(this.getNodeParameter("callId", i));
        const response = await this.helpers.httpRequest({
          method: "GET",
          url: `${baseUrl}/api/calls/${encodeURIComponent(callId)}`,
          headers,
          json: true,
        });
        returnData.push({ json: response });
      } else {
        throw new Error(`Unsupported VoxDesk operation: ${operation}`);
      }
    }

    return [returnData];
  }
}
