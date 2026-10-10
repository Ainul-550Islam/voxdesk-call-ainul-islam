"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface AgentToolRecord {
  id: string;
  agent_id: string;
  name: string;
  description: string;
  schema: Record<string, unknown>;
  auth_binding: {
    auth_type?: string;
    secret_ref?: string;
    header_name?: string;
  };
  is_enabled: boolean;
  created_at: string | null;
}

interface TestInvokeResponse {
  tool_id: string;
  name: string;
  valid: boolean;
  status?: string;
  http_status?: number;
  response_preview?: unknown;
  validation_errors?: string[];
}

export default function ToolsRegistryPage() {
  const [tools, setTools] = useState<AgentToolRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<TestInvokeResponse | null>(
    null,
  );

  // HTTP Tool Editor state
  const [agentId, setAgentId] = useState("agent-default");
  const [toolName, setToolName] = useState("");
  const [toolDesc, setToolDesc] = useState("");
  const [endpointUrl, setEndpointUrl] = useState(
    "https://api.example.com/v1/appointments/check",
  );
  const [httpMethod, setHttpMethod] = useState("POST");
  const [authType, setAuthType] = useState("bearer");
  const [secretRef, setSecretRef] = useState("vault://secrets/crm_api_token");
  const [headerName, setHeaderName] = useState("Authorization");
  const [schemaJson, setSchemaJson] = useState(
    JSON.stringify(
      {
        type: "object",
        properties: {
          date: { type: "string", description: "Requested date (YYYY-MM-DD)" },
          service_type: { type: "string", description: "Service category" },
        },
        required: ["date"],
      },
      null,
      2,
    ),
  );
  const [testArgsJson, setTestArgsJson] = useState(
    JSON.stringify({ date: "2026-10-15", service_type: "consultation" }, null, 2),
  );

  const loadTools = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await request<{ tools: AgentToolRecord[] }>("/api/agent-tools");
      setTools(res.tools);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load tool registry",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    void loadTools();
  }, [loadTools]);

  async function handleCreateTool(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!toolName.trim()) {
      setError("Tool name is required.");
      return;
    }
    let parsedSchema: Record<string, unknown>;
    try {
      parsedSchema = JSON.parse(schemaJson) as Record<string, unknown>;
    } catch {
      setError("Tool JSON schema is not valid JSON.");
      return;
    }
    try {
      const created = await request<AgentToolRecord>("/api/agent-tools", {
        method: "POST",
        body: JSON.stringify({
          agent_id: agentId.trim() || "agent-default",
          name: toolName.trim(),
          description: toolDesc.trim(),
          schema: {
            ...parsedSchema,
            endpoint_url: endpointUrl.trim(),
            http_method: httpMethod,
          },
          auth_binding: {
            auth_type: authType,
            secret_ref: authType === "none" ? undefined : secretRef.trim(),
            header_name: headerName.trim() || "Authorization",
          },
          is_enabled: true,
        }),
      });
      setToolName("");
      setToolDesc("");
      setTools((prev) => [created, ...prev]);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to register HTTP tool",
      );
    }
  }

  async function handleTestInvoke(toolId: string) {
    setError(null);
    setInvokeResult(null);
    let parsedArgs: Record<string, unknown> = {};
    try {
      parsedArgs = JSON.parse(testArgsJson) as Record<string, unknown>;
    } catch {
      setError("Test invocation arguments must be valid JSON.");
      return;
    }
    try {
      const res = await request<TestInvokeResponse>(
        `/api/agent-tools/${encodeURIComponent(toolId)}/test-invoke`,
        {
          method: "POST",
          body: JSON.stringify({ arguments: parsedArgs }),
        },
      );
      setInvokeResult(res);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Tool test invocation failed",
      );
    }
  }

  async function handleDeleteTool(toolId: string) {
    setError(null);
    try {
      await request(`/api/agent-tools/${encodeURIComponent(toolId)}`, {
        method: "DELETE",
      });
      setTools((prev) => prev.filter((t) => t.id !== toolId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete tool");
    }
  }

  return (
    <div data-testid="tools-registry-page">
      <header className="page-head">
        <div>
          <h1>HTTP Tool Registry &amp; Agent Attachment</h1>
          <p className="muted">
            Define JSON-schema HTTP tools, bind headers via secret references (never raw keys), attach per-agent, and test-invoke.
          </p>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}

      <section className="card" data-testid="http-tool-editor-card">
        <h2>Register HTTP Tool</h2>
        <form onSubmit={handleCreateTool} style={{ display: "grid", gap: "0.75rem" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Attached Agent ID
              <input
                type="text"
                data-testid="tool-agent-id-input"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              />
            </label>
            <label>
              Tool Name (snake_case)
              <input
                type="text"
                data-testid="tool-name-input"
                placeholder="check_calendar_slots"
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
              />
            </label>
            <label>
              HTTP Method
              <select
                value={httpMethod}
                onChange={(e) => setHttpMethod(e.target.value)}
              >
                <option value="POST">POST</option>
                <option value="GET">GET</option>
                <option value="PUT">PUT</option>
              </select>
            </label>
            <label>
              Endpoint URL (HTTPS)
              <input
                type="url"
                data-testid="tool-url-input"
                value={endpointUrl}
                onChange={(e) => setEndpointUrl(e.target.value)}
              />
            </label>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <label>
              Auth Binding Type
              <select
                data-testid="tool-auth-type-select"
                value={authType}
                onChange={(e) => setAuthType(e.target.value)}
              >
                <option value="bearer">Bearer Secret Ref</option>
                <option value="api_key">API Key Header Secret Ref</option>
                <option value="hmac">HMAC Signature Secret Ref</option>
                <option value="none">None</option>
              </select>
            </label>
            <label>
              Secret Reference (no raw secrets)
              <input
                type="text"
                data-testid="tool-secret-ref-input"
                value={secretRef}
                onChange={(e) => setSecretRef(e.target.value)}
              />
            </label>
            <label>
              Header Name
              <input
                type="text"
                value={headerName}
                onChange={(e) => setHeaderName(e.target.value)}
              />
            </label>
          </div>

          <label>
            Description
            <input
              type="text"
              placeholder="Look up available appointment slots in CRM"
              value={toolDesc}
              onChange={(e) => setToolDesc(e.target.value)}
            />
          </label>

          <label>
            Parameters JSON Schema
            <textarea
              rows={5}
              data-testid="tool-schema-textarea"
              value={schemaJson}
              onChange={(e) => setSchemaJson(e.target.value)}
            />
          </label>

          <div>
            <button
              type="submit"
              className="btn primary"
              data-testid="register-tool-submit-btn"
            >
              Save &amp; Attach Tool
            </button>
          </div>
        </form>
      </section>

      <section className="card" data-testid="test-invoke-args-card">
        <h2>Test-Invoke Payload</h2>
        <label>
          Sample Arguments JSON
          <textarea
            rows={3}
            data-testid="test-invoke-args-textarea"
            value={testArgsJson}
            onChange={(e) => setTestArgsJson(e.target.value)}
          />
        </label>
        {invokeResult ? (
          <div style={{ marginTop: "0.75rem" }} data-testid="test-invoke-result">
            <strong>
              Validation:{" "}
              {invokeResult.valid ? "Valid Schema ✓" : "Invalid Schema ✗"}
            </strong>
            <pre>{JSON.stringify(invokeResult, null, 2)}</pre>
          </div>
        ) : null}
      </section>

      <section className="card" data-testid="registered-tools-card">
        <h2>Registered Agent Tools ({tools.length})</h2>
        {loading ? (
          <div className="loading">Loading tools…</div>
        ) : tools.length === 0 ? (
          <div className="empty">No tools registered yet.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Agent</th>
                <th>Auth Secret Binding</th>
                <th>Enabled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr key={t.id}>
                  <td>
                    <strong>{t.name}</strong>
                    <div className="muted">{t.description}</div>
                  </td>
                  <td>{t.agent_id}</td>
                  <td>
                    <code>
                      {t.auth_binding?.auth_type ?? "none"}:{" "}
                      {t.auth_binding?.secret_ref ?? "—"}
                    </code>
                  </td>
                  <td>{t.is_enabled ? "Yes" : "No"}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.35rem" }}>
                      <button
                        type="button"
                        className="btn primary"
                        data-testid={`test-invoke-btn-${t.id}`}
                        onClick={() => handleTestInvoke(t.id)}
                      >
                        Test-Invoke
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => handleDeleteTool(t.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
