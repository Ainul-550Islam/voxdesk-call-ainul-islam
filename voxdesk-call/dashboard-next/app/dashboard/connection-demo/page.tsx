"use client";

import React, { useEffect, useState } from "react";
import { api, ApiError, BASE_URL } from "@/lib/api";

// Full-Stack Connection Demo — CSS + HTML + TypeScript + Backend API diagnostics.
//
// Every row below is a live HTTP request against a route the FastAPI app
// actually registers. A row is "success" only when the request succeeded; a
// reachable server behind a failed request is reported as a failure, because
// reachability is not a completed operation.
//
// `scripts/verify_frontend_backend_paths.py` re-checks the paths used here
// against `app.openapi()` so this page cannot drift back to routes that do not
// exist. It is a developer diagnostic, so it renders nothing in production.

interface ConnectionTest {
  name: string;
  endpoint: string;
  status: "pending" | "success" | "error";
  latency?: number;
  data?: unknown;
  error?: string;
  type: string;
  run: () => Promise<unknown>;
}

/** Environment-scoped routes need the environment they are scoped to. */
const DEMO_ENVIRONMENT_ID =
  process.env.NEXT_PUBLIC_DEMO_ENVIRONMENT_ID ??
  "00000000-0000-0000-0000-000000000001";

function buildTests(): ConnectionTest[] {
  return [
    {
      name: "Backend Health",
      endpoint: "/health",
      status: "pending",
      type: "BackendHealth",
      run: async () => {
        // Health is unauthenticated, so it goes out raw: `perform` would
        // trigger a token refresh against an open route.
        const res = await fetch(`${BASE_URL}/health`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      },
    },
    {
      name: "Specialized Agents",
      endpoint: "/api/specialized-agents",
      status: "pending",
      type: "SpecializedAgentDefinitionFull[]",
      run: () => api.specializedAgents(),
    },
    {
      name: "Workflows",
      endpoint: "/api/workflows",
      status: "pending",
      type: "WorkflowDefinitionFull[]",
      run: () => api.workflows(),
    },
    {
      name: "Voice Profiles",
      endpoint: "/api/agent/voice-profiles",
      status: "pending",
      type: "{ items: VoiceProfile[]; total: number }",
      run: () => api.voiceProfiles(),
    },
    {
      name: "Compliance Frameworks",
      endpoint: `/api/compliance/frameworks?environment_id=${DEMO_ENVIRONMENT_ID}`,
      status: "pending",
      type: "GovernancePolicyFull[]",
      run: () => api.complianceFrameworks(),
    },
    {
      name: "Clause Library",
      endpoint: "/api/legal/clause-library",
      status: "pending",
      type: "ClauseLibraryEntry[]",
      run: () => api.clauseLibrary(),
    },
    {
      name: "QMS Providers",
      endpoint: "/api/compliance/qms/providers",
      status: "pending",
      type: "QMSProvidersResponse",
      run: () => api.qmsProviders(),
    },
    {
      name: "Translation Jobs",
      endpoint: `/api/translations/jobs?environment_id=${DEMO_ENVIRONMENT_ID}`,
      status: "pending",
      type: "TranslationJobListResponse",
      run: () => api.translationJobs({ environment_id: DEMO_ENVIRONMENT_ID }),
    },
    {
      name: "Connectors",
      endpoint: "/api/connectors",
      status: "pending",
      type: "ConnectorProvider[]",
      run: () => api.connectors(),
    },
  ];
}

const IS_PRODUCTION = process.env.NODE_ENV === "production";

export default function ConnectionDemoPage() {
  const [tests, setTests] = useState<ConnectionTest[]>([]);
  const [apiStatus, setApiStatus] = useState<{
    connected: boolean;
    latency_ms?: number;
    error: string | null;
    last_check: string;
  }>({
    connected: false,
    error: null,
    last_check: new Date(0).toISOString(),
  });
  const [selectedTest, setSelectedTest] = useState<ConnectionTest | null>(null);

  useEffect(() => {
    if (IS_PRODUCTION) return;
    let cancelled = false;

    async function runTests() {
      const pending = buildTests();
      setTests(pending);

      for (let i = 0; i < pending.length; i += 1) {
        if (cancelled) return;
        const test = pending[i];
        const start = performance.now();
        try {
          const data = await test.run();
          if (cancelled) return;
          const latency = Math.round(performance.now() - start);
          const settled: ConnectionTest = { ...test, status: "success", latency, data };
          setTests((prev) => prev.map((row, idx) => (idx === i ? settled : row)));
          if (i === 0) {
            setApiStatus({
              connected: true,
              latency_ms: latency,
              error: null,
              last_check: new Date().toISOString(),
            });
          }
        } catch (err) {
          if (cancelled) return;
          const latency = Math.round(performance.now() - start);
          const errorMsg =
            err instanceof ApiError
              ? `${err.status} ${err.message} (code=${err.code} reason=${err.reason})`
              : err instanceof Error
                ? err.message
                : "Unknown error";
          // A reachable server behind a rejected request is not a success.
          const settled: ConnectionTest = { ...test, status: "error", latency, error: errorMsg };
          setTests((prev) => prev.map((row, idx) => (idx === i ? settled : row)));
          if (i === 0) {
            setApiStatus({
              connected: false,
              error: errorMsg,
              last_check: new Date().toISOString(),
            });
          }
        }
      }
    }

    void runTests();

    return () => {
      cancelled = true;
    };
  }, []);

  if (IS_PRODUCTION) {
    return (
      <section className="card">
        <h1>Connection diagnostics</h1>
        <p className="muted">
          This diagnostic is disabled outside development. It issues live
          requests against tenant-scoped routes and is not a production surface.
        </p>
      </section>
    );
  }

  const successCount = tests.filter((t) => t.status === "success").length;
  const errorCount = tests.filter((t) => t.status === "error").length;
  const pendingCount = tests.filter((t) => t.status === "pending").length;

  return (
    <>
      <header className="page-head">
        <h1>Connection diagnostics</h1>
        <p className="muted">
          Observed API responses from <code>{BASE_URL}</code>. Authentication,
          authorization and validation failures remain failures. Development
          only.
        </p>
      </header>

      <section className="card">
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <h2>Backend API Connection — Live Status</h2>
          <div
            className={`api-status ${apiStatus.connected ? "connected" : "disconnected"}`}
          >
            <span className="dot" />
            <span>
              {apiStatus.connected
                ? `Connected (${apiStatus.latency_ms ?? "?"}ms)`
                : "Disconnected"}
            </span>
          </div>
        </header>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 12,
          }}
        >
          <article className="stat">
            <div
              className="value"
              style={{
                color: apiStatus.connected
                  ? "var(--factory-success)"
                  : "var(--factory-danger)",
              }}
            >
              {apiStatus.connected ? "✓ Connected" : "✗ Disconnected"}
            </div>
            <div className="label">FastAPI Backend — {BASE_URL}</div>
          </article>
          <article className="stat">
            <div className="value">
              {successCount}/{tests.length}
            </div>
            <div className="label">Requests that returned a payload</div>
          </article>
          <article className="stat">
            <div className="value">{pendingCount}</div>
            <div className="label">Still in flight or not yet started</div>
          </article>
          <article className="stat">
            <div
              className="value"
              style={{
                color: errorCount > 0 ? "var(--factory-danger)" : "var(--factory-success)",
              }}
            >
              {errorCount} Errors
            </div>
            <div className="label">Only successful HTTP operations count as success</div>
          </article>
        </div>

        <div style={{ marginTop: 16 }}>
          <h3>Connection Details</h3>
          <dl className="kv">
            <div>
              <dt>Base URL</dt>
              <dd>
                <code>{BASE_URL}</code>
              </dd>
            </div>
            <div>
              <dt>Last Check</dt>
              <dd>
                {apiStatus.last_check === new Date(0).toISOString()
                  ? "—"
                  : new Date(apiStatus.last_check).toLocaleString()}
              </dd>
            </div>
            <div>
              <dt>Auth</dt>
              <dd>Bearer token + HttpOnly refresh cookie + 401 refresh-and-retry</dd>
            </div>
            <div>
              <dt>Error Parsing</dt>
              <dd>
                FastAPI detail string + 422 validation list + identity{" "}
                {`{code, message, reason}`}
              </dd>
            </div>
            <div>
              <dt>CORS</dt>
              <dd>allow_origins from settings.cors_origin_list, allow_credentials true</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="card">
        <header className="section-head">
          <div>
            <h2>Endpoints</h2>
            <p className="muted">
              Each row is a real request through <code>lib/api.ts</code>. Latency
              is measured; errors are what the server returned. No data is
              synthesised.
            </p>
          </div>
          <div className="section-actions">
            <span
              className={`api-status ${errorCount ? "disconnected" : pendingCount ? "checking" : "connected"}`}
            >
              Observed: {successCount} succeeded; {errorCount} failed; {pendingCount}{" "}
              pending
            </span>
          </div>
        </header>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Endpoint</th>
                <th>Response shape</th>
                <th>Status</th>
                <th>Latency</th>
                <th>Data</th>
              </tr>
            </thead>
            <tbody>
              {tests.map((test) => (
                <tr
                  key={test.endpoint}
                  className={selectedTest?.endpoint === test.endpoint ? "selected" : ""}
                >
                  <td>
                    <strong>{test.name}</strong>
                  </td>
                  <td>
                    <code>{test.endpoint}</code>
                  </td>
                  <td>
                    <code>{test.type}</code>
                  </td>
                  <td>
                    <span
                      className={`status-badge ${test.status === "success" ? "published" : test.status === "error" ? "archived" : "draft"}`}
                    >
                      {test.status === "success"
                        ? "✓ Success"
                        : test.status === "error"
                          ? "✗ Error"
                          : "⏳ Pending"}
                    </span>
                  </td>
                  <td>{test.latency ? `${test.latency}ms` : "—"}</td>
                  <td>
                    <button
                      onClick={() => setSelectedTest(test)}
                      className="btn-factory secondary"
                      style={{ padding: "4px 8px", fontSize: 11 }}
                      disabled={test.status !== "success"}
                    >
                      {test.status === "success" ? "View" : "No payload"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {selectedTest && (
        <section className="card">
          <header
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <h2>
              Detail — {selectedTest.name} — {selectedTest.type}
            </h2>
            <button onClick={() => setSelectedTest(null)} className="btn-factory secondary">
              Close
            </button>
          </header>

          <div style={{ marginTop: 12 }}>
            <h3>Response body — as returned</h3>
            <pre className="code-block">
              {JSON.stringify(selectedTest.data, null, 2)}
            </pre>
          </div>

          {selectedTest.error && (
            <div className="error-banner" style={{ marginTop: 16 }}>
              <strong>Error:</strong> {selectedTest.error}
              <div style={{ fontSize: 11, marginTop: 4 }}>
                401 means the backend is up but this session is not authenticated;
                403 that the authenticated identity lacks the permission; 404 that
                the route or resource is absent; 422 that the query was rejected.
              </div>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2>Verification boundaries</h2>
        <p>
          These checks report only the HTTP operations observed in this browser. A
          reachable server, registered provider or rendered page does not prove
          successful external delivery, authentication, build status or production
          readiness.
        </p>
        <p>
          Current capability evidence is maintained in{" "}
          <code>docs/SALES/FEATURE_MATRIX_VERIFIED.md</code>.
        </p>
      </section>
    </>
  );
}
