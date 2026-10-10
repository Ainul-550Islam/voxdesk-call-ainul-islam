/**
 * Zapier Platform App definition for VoxDesk (`integrations/zapier/index.js`).
 *
 * Exposes REST Hook triggers (`call.started`, `call.ended`, `call.analyzed`,
 * `transfer.initiated`, `tool.invoked` via `/api/webhooks`) and actions
 * (`create_call`, `create_web_call`, `create_batch`, `get_call`).
 */

"use strict";

const subscribeHook = async (z, bundle) => {
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/webhooks`,
    method: "POST",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
      "Content-Type": "application/json",
    },
    body: {
      url: bundle.targetUrl,
      events: bundle.inputData.events || ["call.ended"],
    },
  });
  return response.data;
};

const unsubscribeHook = async (z, bundle) => {
  const webhookId = bundle.subscribeData.id;
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/webhooks/${encodeURIComponent(webhookId)}`,
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
    },
  });
  return response.data;
};

const parseWebhookEvent = (z, bundle) => [bundle.cleanedRequest];

const createWebCall = async (z, bundle) => {
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/web-calls`,
    method: "POST",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
      "Content-Type": "application/json",
    },
    body: {
      agent_id: bundle.inputData.agent_id,
      dynamic_vars: bundle.inputData.dynamic_vars || {},
      metadata: bundle.inputData.metadata || {},
    },
  });
  return response.data;
};

const createCall = async (z, bundle) => {
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/v1/telephony/calls`,
    method: "POST",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
      "Content-Type": "application/json",
    },
    body: {
      agent_id: bundle.inputData.agent_id,
      to_number: bundle.inputData.to_number,
      from_number: bundle.inputData.from_number,
      dynamic_vars: bundle.inputData.dynamic_vars || {},
      metadata: bundle.inputData.metadata || {},
    },
  });
  return response.data;
};

const createBatch = async (z, bundle) => {
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/calls/outbound/bulk`,
    method: "POST",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
      "Content-Type": "application/json",
    },
    body: {
      agent_id: bundle.inputData.agent_id,
      from_number: bundle.inputData.from_number,
      calls: bundle.inputData.calls || [],
    },
  });
  return response.data;
};

const getCall = async (z, bundle) => {
  const response = await z.request({
    url: `${bundle.authData.baseUrl}/api/calls/${encodeURIComponent(bundle.inputData.call_id)}`,
    method: "GET",
    headers: {
      Authorization: `Bearer ${bundle.authData.apiKey}`,
    },
  });
  return response.data;
};

module.exports = {
  version: "0.3.0",
  platformVersion: "15.0.0",
  authentication: {
    type: "custom",
    fields: [
      {
        key: "baseUrl",
        label: "VoxDesk Base URL",
        required: true,
        default: "http://localhost:8000",
      },
      {
        key: "apiKey",
        label: "API Key",
        required: true,
        type: "password",
      },
    ],
    test: {
      url: "{{bundle.authData.baseUrl}}/api/agents",
      method: "GET",
      headers: {
        Authorization: "Bearer {{bundle.authData.apiKey}}",
      },
    },
  },
  triggers: {
    call_event: {
      key: "call_event",
      noun: "Call Event",
      display: {
        label: "New Call or Tool Event",
        description:
          "Triggers when VoxDesk emits call.started, call.ended, call.analyzed, transfer.initiated, or tool.invoked.",
      },
      operation: {
        type: "hook",
        inputFields: [
          {
            key: "events",
            label: "Events",
            choices: [
              "call.started",
              "call.ended",
              "call.analyzed",
              "transfer.initiated",
              "tool.invoked",
            ],
            list: true,
            required: true,
          },
        ],
        performSubscribe: subscribeHook,
        performUnsubscribe: unsubscribeHook,
        perform: parseWebhookEvent,
      },
    },
  },
  creates: {
    create_web_call: {
      key: "create_web_call",
      noun: "Web Call",
      display: {
        label: "Create Web Call",
        description: "Creates a new browser voice call session via POST /api/web-calls.",
      },
      operation: {
        inputFields: [
          { key: "agent_id", required: true, label: "Agent ID" },
          { key: "dynamic_vars", dict: true, label: "Dynamic Variables" },
          { key: "metadata", dict: true, label: "Metadata" },
        ],
        perform: createWebCall,
      },
    },
    create_call: {
      key: "create_call",
      noun: "Outbound Call",
      display: {
        label: "Create Call",
        description: "Places an outbound voice call via POST /api/v1/telephony/calls.",
      },
      operation: {
        inputFields: [
          { key: "agent_id", required: true, label: "Agent ID" },
          { key: "to_number", required: true, label: "To Number (E.164)" },
          { key: "from_number", required: false, label: "From Number (E.164)" },
          { key: "dynamic_vars", dict: true, label: "Dynamic Variables" },
          { key: "metadata", dict: true, label: "Metadata" },
        ],
        perform: createCall,
      },
    },
    create_batch: {
      key: "create_batch",
      noun: "Batch Call",
      display: {
        label: "Create Batch Calls",
        description: "Enqueues a batch of outbound calls via POST /api/calls/outbound/bulk.",
      },
      operation: {
        inputFields: [
          { key: "agent_id", required: true, label: "Agent ID" },
          { key: "from_number", required: false, label: "From Number (E.164)" },
        ],
        perform: createBatch,
      },
    },
  },
  searches: {
    get_call: {
      key: "get_call",
      noun: "Call",
      display: {
        label: "Get Call",
        description: "Fetches a call record by ID via GET /api/calls/{id}.",
      },
      operation: {
        inputFields: [{ key: "call_id", required: true, label: "Call ID" }],
        perform: getCall,
      },
    },
  },
};
