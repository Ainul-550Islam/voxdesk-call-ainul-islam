"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  TranslationExportDocument,
  TranslationExportFormat,
  TranslationJobRow,
} from "@/lib/api";

// GAP-P0-P1: Translation — side-by-side review, glossary, audit chain, export.
// Backend: app/translation/engine.py, glossary.py, job_service.py,
// persistence.py, quality.py, schemas.py.
//
// Everything on this page is read from `GET /api/translation/jobs` and
// `GET /api/translation/jobs/{execution_id}/export`. Nothing is seeded locally:
// a translation the server did not record is not shown as if it existed.

const EXPORT_FORMATS: TranslationExportFormat[] = ["json", "srt", "txt"];

interface Scope {
  tenantId: string;
  environmentId: string;
}

function describeError(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function TranslationPage() {
  const [scope, setScope] = useState<Scope>({ tenantId: "", environmentId: "" });
  const [scopeDraft, setScopeDraft] = useState<Scope>({
    tenantId: "",
    environmentId: "",
  });
  const [jobs, setJobs] = useState<TranslationJobRow[]>([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [document, setDocument] = useState<TranslationExportDocument | null>(null);
  const [exportText, setExportText] = useState<{ format: string; body: string } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [documentLoading, setDocumentLoading] = useState(false);

  const activeScope: Scope | null =
    scope.tenantId && scope.environmentId ? scope : null;

  const loadJobs = useCallback(async () => {
    if (!activeScope) return;
    setLoading(true);
    setError(null);
    setLoadFailed(false);
    try {
      const page = await api.translationJobs({
        environment_id: activeScope.environmentId,
        ...(statusFilter ? { status: statusFilter } : {}),
        limit: 50,
      });
      setJobs(page.items ?? []);
      setTotal(page.total ?? (page.items ?? []).length);
      // A filter change can drop the job being reviewed out of the list.
      setSelectedJobId((current) =>
        current && (page.items ?? []).some((row) => row.execution_id === current)
          ? current
          : null,
      );
    } catch (err) {
      setJobs([]);
      setTotal(0);
      setLoadFailed(true);
      setError(describeError(err, "Failed to load translation jobs"));
    } finally {
      setLoading(false);
    }
  }, [activeScope, statusFilter]);

  useEffect(() => {
    if (!activeScope) return;
    void loadJobs();
  }, [activeScope, loadJobs]);

  // The document used for review is the json export itself, so what a reviewer
  // approves on screen is byte-for-byte what the export button downloads.
  useEffect(() => {
    if (!activeScope || !selectedJobId) {
      setDocument(null);
      setExportText(null);
      return;
    }
    let cancelled = false;
    setDocumentLoading(true);
    setError(null);
    api
      .translationExportDocument(selectedJobId, activeScope.environmentId)
      .then((doc) => {
        if (cancelled) return;
        setDocument(doc);
        setExportText(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setDocument(null);
        setError(describeError(err, "Failed to load this job's segments"));
      })
      .finally(() => {
        if (!cancelled) setDocumentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeScope, selectedJobId]);

  async function handleExport(format: TranslationExportFormat) {
    if (!activeScope || !selectedJobId) return;
    setError(null);
    try {
      const body = await api.exportTranslationJob(
        selectedJobId,
        activeScope.environmentId,
        format,
      );
      setExportText({ format, body });
    } catch (err) {
      setError(describeError(err, `Failed to export as ${format}`));
    }
  }

  function downloadExport() {
    if (!exportText || !selectedJobId) return;
    const mime =
      exportText.format === "json"
        ? "application/json"
        : exportText.format === "srt"
          ? "application/x-subrip"
          : "text/plain;charset=utf-8";
    const url = URL.createObjectURL(
      new Blob([exportText.body], { type: mime }),
    );
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = `translation-${selectedJobId}.${exportText.format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function applyScope() {
    setLoadFailed(false);
    setScope({
      tenantId: scopeDraft.tenantId.trim(),
      environmentId: scopeDraft.environmentId.trim(),
    });
    setSelectedJobId(null);
  }

  if (!activeScope) {
    return (
      <section className="card" data-testid="translation-scope-form">
        <h1>Translation</h1>
        <p className="muted">
          Translation jobs are recorded per environment. Enter the tenant and
          environment to list the jobs stored for it.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <input
            aria-label="Tenant id"
            data-testid="translation-tenant-input"
            value={scopeDraft.tenantId}
            onChange={(e) =>
              setScopeDraft((prev) => ({ ...prev, tenantId: e.target.value }))
            }
            placeholder="Tenant id"
            style={{ padding: 8, borderRadius: 6, border: "1px solid #ccc" }}
          />
          <input
            aria-label="Environment id"
            data-testid="translation-environment-input"
            value={scopeDraft.environmentId}
            onChange={(e) =>
              setScopeDraft((prev) => ({ ...prev, environmentId: e.target.value }))
            }
            placeholder="Environment id"
            style={{ padding: 8, borderRadius: 6, border: "1px solid #ccc" }}
          />
          <button
            type="button"
            data-testid="translation-scope-apply"
            onClick={applyScope}
            disabled={!scopeDraft.tenantId.trim() || !scopeDraft.environmentId.trim()}
            style={{
              padding: "8px 16px",
              borderRadius: 6,
              border: "none",
              background: "#8b5cf6",
              color: "#fff",
              cursor:
                !scopeDraft.tenantId.trim() || !scopeDraft.environmentId.trim()
                  ? "not-allowed"
                  : "pointer",
            }}
          >
            Load jobs
          </button>
        </div>
      </section>
    );
  }

  return (
    <>
      <header className="page-head">
        <h1>Translation — Review and Export</h1>
        <p className="muted">
          Jobs and segments are read from{" "}
          <code>GET /api/translation/jobs</code> and the job&apos;s export. Model
          translation is not certified output: review the quality flags before
          using a translation.
        </p>
        <p className="muted" data-testid="translation-scope-readout">
          Environment {activeScope.environmentId} — {total} job
          {total === 1 ? "" : "s"}
          {statusFilter ? ` matching “${statusFilter}”` : ""}
        </p>
      </header>

      {error && (
        <div className="error-banner" data-testid="translation-error">
          {error}{" "}
          <button onClick={() => setError(null)} style={{ marginLeft: 8 }}>
            ×
          </button>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 12 }}>
        {/* Left: real job list */}
        <section className="card" style={{ padding: 12 }}>
          <h3>Jobs</h3>
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <input
              aria-label="Filter by status"
              data-testid="translation-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              placeholder="Filter by status"
              style={{
                flex: 1,
                padding: 6,
                borderRadius: 4,
                border: "1px solid #ccc",
                fontSize: 12,
              }}
            />
            <button
              type="button"
              data-testid="translation-refresh"
              onClick={() => void loadJobs()}
              disabled={loading}
              style={{
                padding: "6px 10px",
                borderRadius: 4,
                border: "1px solid #ccc",
                background: "#fff",
                cursor: loading ? "not-allowed" : "pointer",
                fontSize: 12,
              }}
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>

          {loading && jobs.length === 0 ? (
            <p data-testid="translation-loading" style={{ fontSize: 12 }}>
              Loading jobs…
            </p>
          ) : loadFailed ? (
            <p
              data-testid="translation-load-failed"
              style={{ fontSize: 12, color: "#888" }}
            >
              Jobs could not be loaded — see the error above.
            </p>
          ) : jobs.length === 0 ? (
            <p
              data-testid="translation-empty"
              style={{ fontSize: 12, color: "#888" }}
            >
              {statusFilter
                ? `No translation jobs with status “${statusFilter}”.`
                : "No translation jobs recorded in this environment yet."}
            </p>
          ) : (
            <div
              data-testid="translation-job-list"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                maxHeight: 520,
                overflowY: "auto",
              }}
            >
              {jobs.map((job) => (
                <button
                  key={job.execution_id}
                  type="button"
                  data-testid={`translation-job-${job.execution_id}`}
                  onClick={() => setSelectedJobId(job.execution_id)}
                  style={{
                    textAlign: "left",
                    padding: "8px 10px",
                    borderRadius: 6,
                    border:
                      selectedJobId === job.execution_id
                        ? "2px solid #8b5cf6"
                        : "1px solid #e5e7eb",
                    background: "#fff",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 11,
                      fontWeight: 600,
                    }}
                  >
                    <span>{job.source_language} → {job.target_language}</span>
                    <span
                      data-testid={`translation-job-status-${job.execution_id}`}
                    >
                      {job.status}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
                    {job.completed_segments}/{job.total_segments} segments ·{" "}
                    {job.failed_segments} failed
                  </div>
                  <div style={{ fontSize: 10, color: "#888", marginTop: 2 }}>
                    quality {job.quality_status ?? "unknown"} · review{" "}
                    {job.review_state ?? "unknown"}
                    {job.review_required ? " (required)" : ""}
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>

        {/* Right: review + export */}
        <section className="card" style={{ padding: 12 }}>
          {!selectedJobId ? (
            <p data-testid="translation-no-selection" style={{ fontSize: 12, color: "#888" }}>
              Select a job to review its segments and export it.
            </p>
          ) : documentLoading ? (
            <p data-testid="translation-doc-loading" style={{ fontSize: 12 }}>
              Loading segments…
            </p>
          ) : !document ? (
            <p data-testid="translation-doc-empty" style={{ fontSize: 12, color: "#888" }}>
              No exportable segments for this job.
            </p>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <h3 style={{ margin: 0 }}>
                  {document.source_language ?? "?"} →{" "}
                  {document.target_language ?? "?"} —{" "}
                  {document.segment_count} segment
                  {document.segment_count === 1 ? "" : "s"}
                </h3>
                <div style={{ display: "flex", gap: 6 }}>
                  {EXPORT_FORMATS.map((format) => (
                    <button
                      key={format}
                      type="button"
                      data-testid={`translation-export-${format}`}
                      onClick={() => void handleExport(format)}
                      style={{
                        padding: "6px 10px",
                        borderRadius: 4,
                        border: "1px solid #ccc",
                        background: "#fff",
                        cursor: "pointer",
                        fontSize: 12,
                      }}
                    >
                      Export {format.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>

              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 6,
                  fontSize: 11,
                  margin: "10px 0",
                }}
              >
                <div>
                  <dt style={{ color: "#888" }}>Status</dt>
                  <dd style={{ margin: 0 }}>{document.status}</dd>
                </div>
                <div>
                  <dt style={{ color: "#888" }}>Quality</dt>
                  <dd style={{ margin: 0 }}>{document.quality_status ?? "unknown"}</dd>
                </div>
                <div>
                  <dt style={{ color: "#888" }}>Review</dt>
                  <dd style={{ margin: 0 }}>
                    {document.review_state ?? "unknown"}
                    {document.review_required ? " (required)" : ""}
                  </dd>
                </div>
                <div>
                  <dt style={{ color: "#888" }}>Glossary</dt>
                  <dd style={{ margin: 0 }}>{document.glossary_version ?? "—"}</dd>
                </div>
                <div>
                  <dt style={{ color: "#888" }}>Provider</dt>
                  <dd style={{ margin: 0 }}>{document.provider ?? "—"}</dd>
                </div>
                <div>
                  <dt style={{ color: "#888" }}>Model</dt>
                  <dd style={{ margin: 0 }}>{document.model ?? "—"}</dd>
                </div>
              </dl>

              <div
                data-testid="translation-segment-list"
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                  maxHeight: 420,
                  overflowY: "auto",
                }}
              >
                {document.segments.map((segment, index) => (
                  <div
                    key={`${segment.segment_id}-${index}`}
                    data-testid={`translation-segment-${segment.segment_id}`}
                    style={{
                      gridColumn: "span 1",
                      border: "1px solid #e5e7eb",
                      borderRadius: 6,
                      padding: 8,
                      background:
                        segment.quality_flags.length > 0 ? "#fffbeb" : "#fff",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 10,
                        color: "#666",
                      }}
                    >
                      <span>#{segment.segment_id}</span>
                      <span>{segment.status}</span>
                    </div>
                    <div style={{ fontSize: 12, marginTop: 4 }}>
                      {segment.source_text}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        marginTop: 6,
                        paddingTop: 6,
                        borderTop: "1px dashed #e5e7eb",
                      }}
                    >
                      {segment.translated_text}
                    </div>
                    {segment.quality_flags.length > 0 && (
                      <div style={{ fontSize: 10, color: "#b45309", marginTop: 6 }}>
                        ⚠ {segment.quality_flags.join(", ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {exportText && (
                <div style={{ marginTop: 12 }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <h4 style={{ margin: 0 }} data-testid="translation-export-format">
                      Export preview — {exportText.format}
                    </h4>
                    <button
                      type="button"
                      data-testid="translation-export-download"
                      onClick={downloadExport}
                      style={{
                        padding: "6px 12px",
                        borderRadius: 6,
                        border: "none",
                        background: "#10b981",
                        color: "#fff",
                        cursor: "pointer",
                        fontSize: 12,
                      }}
                    >
                      Download
                    </button>
                  </div>
                  <pre
                    className="code-block"
                    data-testid="translation-export-body"
                    style={{ fontSize: 11, maxHeight: 240, overflowY: "auto" }}
                  >
                    {exportText.body}
                  </pre>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
