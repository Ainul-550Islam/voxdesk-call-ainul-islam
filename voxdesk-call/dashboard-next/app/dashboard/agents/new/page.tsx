"use client";

/**
 * Agent template gallery (PART 18, P18-01 UI half).
 *
 * The seven starter templates are the first thing a new tenant should see,
 * because an empty builder is the reason trials die. This page is the gallery,
 * the preview and the create flow in one screen:
 *
 *  * the list comes from `GET /api/agent-templates` — one request, no fan-out;
 *  * clicking a card loads `GET /api/agent-templates/{id}` for the full flow,
 *    tools, variables, simulation cases and compliance notes, so the operator
 *    sees what they are about to get *before* creating anything;
 *  * creating posts `{name}` and lands the operator in the builder.
 *
 * Everything created here is a **draft**. The API enforces that; so does the
 * copy on this page, because a template that went straight to live would be a
 * production agent nobody reviewed.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { request } from "@/lib/api";

const BASE = "/api/agent-templates";

/**
 * What `GET /api/agent-templates` actually returns. The gallery omits the flow
 * (it is large and the list is a menu) but does include the tool list, the
 * variable specs, the analysis schema and the simulation cases, so the counts
 * below are read from real arrays rather than a second endpoint.
 */
type TemplateSummary = {
  id: string;
  name: string;
  vertical: string;
  summary: string;
  config: Record<string, unknown>;
  tools: Record<string, unknown>[];
  dynamic_variables: { name: string; type: string; purpose?: string }[];
  analysis_schema: { fields?: { name: string; type: string }[] };
  simulation_cases: { name: string; turns: string[]; expect_node: string }[];
  kb_placeholders: string[];
  compliance_notes: string[];
};

type TemplateDetail = TemplateSummary & {
  config: Record<string, unknown>;
  flow: { nodes?: { id: string; node_type: string }[]; edges?: unknown[] };
  tools: Record<string, unknown>[];
  dynamic_variables: { name: string; type: string; purpose?: string }[];
  analysis_schema: { fields?: { name: string; type: string }[] };
  simulation_cases: { name: string; turns: string[]; expect_node: string }[];
  kb_placeholders: string[];
  compliance_notes: string[];
};

export default function NewAgentFromTemplatePage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TemplateDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    request<{ templates?: TemplateSummary[] } | TemplateSummary[]>(BASE)
      .then((payload) => {
        const list = Array.isArray(payload) ? payload : payload.templates ?? [];
        setTemplates(list);
        if (list.length > 0) setSelectedId(list[0].id);
      })
      .catch((exc: unknown) =>
        setError(exc instanceof Error ? exc.message : "The template catalog could not be loaded."),
      );
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setDetail(null);
    setDetailError(null);
    request<TemplateDetail>(`${BASE}/${encodeURIComponent(selectedId)}`)
      .then((payload) => {
        setDetail(payload);
        setName((current) => current || payload.name);
      })
      .catch((exc: unknown) =>
        setDetailError(
          exc instanceof Error ? exc.message : "This template could not be loaded.",
        ),
      );
  }, [selectedId]);

  const create = useCallback(async () => {
    if (!selectedId) return;
    setCreating(true);
    setCreateError(null);
    try {
      const agent = await request<{ id: string }>(
        `${BASE}/${encodeURIComponent(selectedId)}/instantiate`,
        {
          method: "POST",
          body: JSON.stringify({ name: name.trim() || undefined }),
        },
      );
      router.push(`/dashboard/agents/${agent.id}`);
    } catch (exc) {
      setCreateError(exc instanceof Error ? exc.message : "The agent could not be created.");
    } finally {
      setCreating(false);
    }
  }, [name, router, selectedId]);

  const nodeTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of detail?.flow?.nodes ?? []) {
      counts.set(node.node_type, (counts.get(node.node_type) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [detail]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!templates) return <div className="loading">Loading templates…</div>;

  return (
    <>
      <header className="page-head">
        <h1>Start from a template</h1>
        <p className="muted">
          Seven working agents — a real flow, real tools, a post-call analysis
          schema and the compliance notes that vertical needs. Everything is
          created as a <strong>draft</strong>: nothing talks to a customer until
          you review it and switch it live.
        </p>
      </header>

      <div className="template-grid">
        {templates.map((template) => (
          <button
            key={template.id}
            type="button"
            className="template-card"
            aria-pressed={template.id === selectedId}
            onClick={() => setSelectedId(template.id)}
          >
            <span className="vertical">{template.vertical}</span>
            <h2>{template.name}</h2>
            <p className="muted">{template.summary}</p>
            <ul className="facts">
              <li>{template.dynamic_variables?.length ?? 0} variables</li>
              <li>{template.tools?.length ?? 0} tools</li>
              <li>{template.simulation_cases?.length ?? 0} test cases</li>
              <li>{template.kb_placeholders?.length ?? 0} KB documents needed</li>
            </ul>
          </button>
        ))}
      </div>

      {detailError && <div className="error-banner">{detailError}</div>}

      {detail && (
        <section className="card template-preview" aria-live="polite">
          <h2>{detail.name}</h2>
          <p className="muted">{detail.summary}</p>

          <h3>The flow</h3>
          <ul className="facts">
            {nodeTypes.map(([type, count]) => (
              <li key={type}>
                {count} × <code>{type}</code>
              </li>
            ))}
            <li>{detail.flow?.edges?.length ?? 0} edges</li>
          </ul>

          <h3>Variables it collects</h3>
          <ul className="facts">
            {(detail.dynamic_variables ?? []).map((variable) => (
              <li key={variable.name}>
                <code>{variable.name}</code> <span className="muted">({variable.type})</span>
                {variable.purpose ? <> — {variable.purpose}</> : null}
              </li>
            ))}
          </ul>

          <h3>After every call it records</h3>
          <ul className="facts">
            {(detail.analysis_schema?.fields ?? []).map((field) => (
              <li key={field.name}>
                <code>{field.name}</code> <span className="muted">({field.type})</span>
              </li>
            ))}
          </ul>

          <h3>Proven by {detail.simulation_cases?.length ?? 0} scripted calls</h3>
          <ul className="facts">
            {(detail.simulation_cases ?? []).map((testCase) => (
              <li key={testCase.name}>
                {testCase.name} — {testCase.turns.length} turns, ends at{" "}
                <code>{testCase.expect_node}</code>
              </li>
            ))}
          </ul>

          {(detail.kb_placeholders ?? []).length > 0 && (
            <>
              <h3>You will need to supply</h3>
              <ul className="facts">
                {detail.kb_placeholders.map((placeholder) => (
                  <li key={placeholder}>{placeholder}</li>
                ))}
              </ul>
            </>
          )}

          <h3>Before you switch it live</h3>
          <ul className="facts compliance">
            {(detail.compliance_notes ?? []).map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>

          <div className="create-row">
            <label htmlFor="agent-name">Agent name</label>
            <input
              id="agent-name"
              value={name}
              maxLength={120}
              onChange={(event) => setName(event.target.value)}
            />
            <button
              type="button"
              className="primary"
              onClick={create}
              disabled={creating || !name.trim()}
            >
              {creating ? "Creating…" : "Create draft agent"}
            </button>
          </div>
          {createError && (
            <div className="error-banner" role="alert">
              {createError}
            </div>
          )}

          <p className="muted small">
            Not sure which one fits?{" "}
            <Link href="/demo">Have the agent call you</Link> and hear it first.
          </p>
        </section>
      )}
    </>
  );
}
