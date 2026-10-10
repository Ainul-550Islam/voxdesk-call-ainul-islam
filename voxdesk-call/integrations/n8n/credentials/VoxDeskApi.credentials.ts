/**
 * n8n credentials definition for VoxDesk REST API (`integrations/n8n/credentials/VoxDeskApi.credentials.ts`).
 */

export interface INodeProperties {
  displayName: string;
  name: string;
  type: string;
  default: unknown;
  required?: boolean;
  typeOptions?: Record<string, unknown>;
  description?: string;
}

export interface ICredentialType {
  name: string;
  displayName: string;
  documentationUrl?: string;
  properties: INodeProperties[];
  authenticate?: Record<string, unknown>;
}

export class VoxDeskApi implements ICredentialType {
  name = "voxDeskApi";
  displayName = "VoxDesk API";
  documentationUrl = "https://docs.voxdesk.ai/authentication";

  properties: INodeProperties[] = [
    {
      displayName: "Base URL",
      name: "baseUrl",
      type: "string",
      default: "http://localhost:8000",
      required: true,
      description: "Base URL of your VoxDesk API instance",
    },
    {
      displayName: "API Key",
      name: "apiKey",
      type: "string",
      typeOptions: { password: true },
      default: "",
      required: true,
      description: "VoxDesk Server API Key or Bearer Token",
    },
    {
      displayName: "Webhook Signing Secret",
      name: "webhookSecret",
      type: "string",
      typeOptions: { password: true },
      default: "",
      required: false,
      description:
        "Optional HMAC-SHA256 signing secret used to verify incoming VoxDesk webhooks",
    },
  ];

  authenticate = {
    type: "generic",
    properties: {
      headers: {
        Authorization: "=Bearer {{$credentials.apiKey}}",
      },
    },
  };
}
