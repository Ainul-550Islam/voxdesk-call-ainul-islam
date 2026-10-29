"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { getToken } from "@/lib/auth";

/** Mirrors `app/api_tools/routes.py`: the HTTP tool registry is global to the
 *  tenant, not attached to one agent, and an auth binding is an opaque
 *  `secret://` reference rather than a raw key. */
interface AgentToolRecord {
  id: string;
  name: string;
  method: string;
  url_template: string;
  enabled: boolean;
  description?: string;
  auth_ref?: string | null;
}

interface TestInvokeResponse {
  ok: boolean;
  status_code: number;
  data: unknown;
  attempts: number;
  duration_ms: number;
}

export default function ToolsRegistryPage() {
  const [tools, setTools] = useState<AgentToolRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<TestInvokeResponse | null>(
    null,
  );

  // HTTP Tool Editor state
  const [toolName, setToolName] = useState("");
  const [toolDesc, setToolDesc] = useState("");
  const [endpointUrl, setEndpointUrl] = useState(
    "https://api.example.com/v1/appointments/check",
  );
  const [httpMethod, setHttpMethod] = useState("POST");
  const [authType, setAuthType] = useState("bearer");
  const [secretRef, setSecretRef] = useState("vault://secrets/crm_api_token");
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
      const res = await request<AgentToolRecord[]>("/api/api-tools");
      setTools(Array.isArray(res) ? res : []);
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
      // `auth_ref` must be an opaque `secret://` reference: the backend rejects
      // anything else, because a raw key posted here would end up in the API
      // log and in the tool row.
      const authRef =
        authType === "none" ? null : `secret://${authType}/${secretRef.trim()}`;
      const created = await request<AgentToolRecord>("/api/api-tools", {
        method: "POST",
        body: JSON.stringify({
          name: toolName.trim(),
          description: toolDesc.trim(),
          method: httpMethod,
          url_template: endpointUrl.trim(),
          parameter_schema: parsedSchema,
          body_schema: {},
          result_schema: {},
          auth_ref: authRef,
          timeout_seconds: 10,
          retry_max_attempts: 3,
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
        `/api/api-tools/${encodeURIComponent(toolId)}/execute`,
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

  return (
    <div data-testid="tools-registry-page">
      <header className="page-head">
        <div>
          <h1>HTTP Tool Registry</h1>
          <p className="muted">
            Define JSON-schema HTTP tools, bind auth through opaque secret references (never raw keys), and test-invoke them.
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
              Result:{" "}
              {invokeResult.ok
                ? `HTTP ${invokeResult.status_code} ✓`
                : `Failed after ${invokeResult.attempts} attempt(s) ✗`}
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
                <th>Method</th>
                <th>URL Template</th>
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
                  <td>
                    <code>{t.method}</code>
                  </td>
                  <td>
                    <code>{t.url_template}</code>
                  </td>
                  <td>{t.enabled ? "Yes" : "No"}</td>
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
