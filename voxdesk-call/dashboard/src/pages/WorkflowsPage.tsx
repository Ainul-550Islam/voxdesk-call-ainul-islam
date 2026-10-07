import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '../api/client';

interface WorkflowNodeSummary {
  id: string;
  type: string;
  next: string;
  delay_seconds: number;
  retry_limit: number;
}

interface WorkflowRecord {
  id: string;
  tenant_id: string;
  name: string;
  version: number;
  status: string;
  trigger: string;
  entry_node: string;
  description: string;
  nodes: WorkflowNodeSummary[];
}

interface WorkflowStepRecord {
  node_id: string;
  status: string;
  detail: string;
  attempt: number;
  at: string;
}

interface WorkflowExecutionRecord {
  id: string;
  workflow_id: string;
  tenant_id: string;
  idempotency_key: string;
  status: string;
  current_node: string;
  steps: WorkflowStepRecord[];
  started_at: string;
  finished_at: string;
  error: string;
}

interface PendingExecutionKey {
  leadId: string;
  key: string;
}

function readableError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'The workflow request failed without a readable error message.';
}

function makeIdempotencyKey(): string {
  const randomId = globalThis.crypto?.randomUUID?.();
  return `workflow-ui-${randomId || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

function statusColor(status: string): string {
  if (status === 'active' || status === 'completed') return '#16794a';
  if (status === 'failed' || status === 'timed_out') return '#a42d3b';
  if (status === 'paused' || status === 'waiting_approval') return '#8a5a00';
  return '#475569';
}

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState('');
  const [leadId, setLeadId] = useState('');
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [executions, setExecutions] = useState<Record<string, WorkflowExecutionRecord[]>>({});
  const [executionErrors, setExecutionErrors] = useState<Record<string, string>>({});
  const pendingExecutionKeys = useRef<Record<string, PendingExecutionKey>>({});

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await apiClient.get<WorkflowRecord[]>('/workflows');
      setWorkflows(rows);
    } catch (error) {
      setLoadError(readableError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  const createWorkflow = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = workflowName.trim();
    if (!name) return;

    setPendingAction('create');
    setActionError(null);
    setNotice(null);
    try {
      const created = await apiClient.post<WorkflowRecord>('/workflows', {
        name,
        description: 'Manual lead qualification workflow using the built-in update_lead_status action.',
        trigger: 'manual',
        entry_node: 'qualify_lead',
        nodes: [
          {
            id: 'qualify_lead',
            type: 'action',
            action_name: 'update_lead_status',
            action_params: { status: 'qualified' },
            next: 'complete',
          },
          { id: 'complete', type: 'terminal' },
        ],
      });
      setWorkflowName('');
      setNotice(`Created persisted draft “${created.name}” at version ${created.version}.`);
      await loadWorkflows();
    } catch (error) {
      setActionError(readableError(error));
    } finally {
      setPendingAction(null);
    }
  };

  const publishWorkflow = async (workflow: WorkflowRecord) => {
    setPendingAction(`publish:${workflow.id}`);
    setActionError(null);
    setNotice(null);
    try {
      const published = await apiClient.post<WorkflowRecord>(
        `/workflows/${encodeURIComponent(workflow.id)}/publish`,
      );
      setNotice(`Published “${published.name}” version ${published.version}.`);
      await loadWorkflows();
    } catch (error) {
      setActionError(readableError(error));
    } finally {
      setPendingAction(null);
    }
  };

  const loadExecutions = async (workflowId: string) => {
    setExecutionErrors((current) => ({ ...current, [workflowId]: '' }));
    try {
      const rows = await apiClient.get<WorkflowExecutionRecord[]>(
        `/workflows/${encodeURIComponent(workflowId)}/executions`,
      );
      setExecutions((current) => ({ ...current, [workflowId]: rows }));
    } catch (error) {
      setExecutionErrors((current) => ({ ...current, [workflowId]: readableError(error) }));
    }
  };

  const executeWorkflow = async (workflow: WorkflowRecord) => {
    const normalizedLeadId = leadId.trim();
    if (!normalizedLeadId) {
      setActionError('Enter an existing lead UUID before executing this workflow.');
      return;
    }

    const previousKey = pendingExecutionKeys.current[workflow.id];
    const idempotencyKey = previousKey?.leadId === normalizedLeadId
      ? previousKey.key
      : makeIdempotencyKey();
    pendingExecutionKeys.current[workflow.id] = {
      leadId: normalizedLeadId,
      key: idempotencyKey,
    };

    setPendingAction(`execute:${workflow.id}`);
    setActionError(null);
    setNotice(null);
    setExecutionErrors((current) => ({ ...current, [workflow.id]: '' }));
    try {
      const execution = await apiClient.post<WorkflowExecutionRecord>(
        `/workflows/${encodeURIComponent(workflow.id)}/execute`,
        { payload: { lead_id: normalizedLeadId } },
        { headers: { 'Idempotency-Key': idempotencyKey } },
      );
      delete pendingExecutionKeys.current[workflow.id];
      setNotice(`Execution ${execution.id} completed with status “${execution.status}”.`);
      await loadExecutions(workflow.id);
    } catch (error) {
      // Retain this key for a retry of the same lead. The server can then
      // replay the same durable operation if the response was interrupted.
      setActionError(readableError(error));
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <main
      aria-labelledby="workflows-title"
      style={{
        minHeight: '100vh',
        boxSizing: 'border-box',
        padding: 'clamp(16px, 3vw, 32px)',
        background: '#080c12',
        color: '#e9eef5',
        fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
      }}
    >
      <div style={{ maxWidth: 1200, margin: '0 auto', display: 'grid', gap: 20 }}>
        <header>
          <p style={{ margin: '0 0 7px', color: '#97b8d7', fontSize: 12, fontWeight: 700, letterSpacing: '.12em', textTransform: 'uppercase' }}>
            Tenant-scoped workflow API
          </p>
          <h1 id="workflows-title" style={{ margin: 0, fontSize: 'clamp(25px, 4vw, 36px)' }}>
            Workflows
          </h1>
          <p style={{ maxWidth: 820, color: '#b8c3d0', lineHeight: 1.55 }}>
            Review persisted workflow definitions, publish drafts, and execute a controlled lead action against an existing lead. Execution is a real backend mutation and is permission-checked by the server.
          </p>
        </header>

        <section aria-labelledby="create-workflow-title" style={{ display: 'grid', gap: 12, padding: 16, background: '#111923', border: '1px solid #293544', borderRadius: 12 }}>
          <div>
            <h2 id="create-workflow-title" style={{ margin: 0, fontSize: 19 }}>Create a workflow draft</h2>
            <p style={{ color: '#a8b3c1', fontSize: 13, lineHeight: 1.5, margin: '7px 0 0' }}>
              This creates a durable manual workflow with the built-in “update lead status” action. It does not create a lead or run the workflow.
            </p>
          </div>
          <form onSubmit={createWorkflow} style={{ display: 'grid', gridTemplateColumns: 'minmax(200px, 1fr) auto', gap: 10, alignItems: 'end' }}>
            <label htmlFor="workflow-name" style={{ display: 'grid', gap: 6, fontSize: 13 }}>
              Workflow name
              <input
                id="workflow-name"
                required
                maxLength={200}
                value={workflowName}
                onChange={(event) => setWorkflowName(event.target.value)}
                autoComplete="off"
                style={{ minWidth: 0, padding: 10, borderRadius: 8, border: '1px solid #415064', background: '#0d141d', color: '#f2f5f8' }}
              />
            </label>
            <button
              type="submit"
              disabled={pendingAction === 'create' || !workflowName.trim()}
              style={{ border: '1px solid #42617f', borderRadius: 9, background: '#142438', color: '#e9eef5', padding: '11px 14px', cursor: pendingAction === 'create' ? 'wait' : 'pointer' }}
            >
              {pendingAction === 'create' ? 'Creating…' : 'Create draft'}
            </button>
          </form>
          <p style={{ color: '#a8b3c1', fontSize: 12, margin: 0 }}>
            Creating/publishing requires tenant update permission. Running requires campaign run permission. The server remains the authority for both.
          </p>
        </section>

        {notice && <div role="status" style={{ border: '1px solid #286142', background: '#10291d', padding: 12, borderRadius: 9 }}>{notice}</div>}
        {actionError && <div role="alert" style={{ border: '1px solid #824452', background: '#351921', padding: 12, borderRadius: 9 }}>{actionError}</div>}

        <section aria-labelledby="workflow-list-title" style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
            <div>
              <h2 id="workflow-list-title" style={{ margin: 0, fontSize: 20 }}>Workspace definitions</h2>
              <p style={{ color: '#98a6b6', fontSize: 12, margin: '6px 0 0' }}>Loaded from the authenticated tenant-scoped workflow API.</p>
            </div>
            <button
              type="button"
              onClick={() => void loadWorkflows()}
              disabled={loading}
              style={{ border: '1px solid #42617f', borderRadius: 9, background: '#142438', color: '#e9eef5', padding: '9px 12px', cursor: loading ? 'wait' : 'pointer' }}
            >
              {loading ? 'Refreshing…' : 'Refresh workflows'}
            </button>
          </div>

          {loading && workflows.length === 0 && <p role="status">Loading workflow definitions…</p>}
          {loadError && (
            <div role="alert" style={{ border: '1px solid #824452', background: '#351921', padding: 12, borderRadius: 9 }}>
              Workflow definitions are unavailable: {loadError}
              <button type="button" onClick={() => void loadWorkflows()} style={{ marginLeft: 12 }}>Retry</button>
            </div>
          )}
          {!loading && !loadError && workflows.length === 0 && (
            <div style={{ padding: 16, border: '1px dashed #415064', borderRadius: 10, color: '#b8c3d0' }}>
              No workflows are configured in this workspace.
            </div>
          )}

          <div style={{ display: 'grid', gap: 12 }}>
            {workflows.map((workflow) => {
              const isPublishing = pendingAction === `publish:${workflow.id}`;
              const isExecuting = pendingAction === `execute:${workflow.id}`;
              const runRows = executions[workflow.id];
              return (
                <article key={workflow.id} style={{ display: 'grid', gap: 12, padding: 16, background: '#111923', border: '1px solid #293544', borderRadius: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
                    <div style={{ minWidth: 0 }}>
                      <h3 style={{ margin: 0, fontSize: 17 }}>{workflow.name}</h3>
                      <p style={{ color: '#a8b3c1', fontSize: 12, lineHeight: 1.5, margin: '6px 0' }}>{workflow.description || 'No description is stored.'}</p>
                      <code style={{ overflowWrap: 'anywhere', color: '#9fb2c8', fontSize: 11 }}>{workflow.id}</code>
                    </div>
                    <span style={{ color: '#fff', background: statusColor(workflow.status), borderRadius: 999, padding: '5px 10px', fontSize: 11, fontWeight: 700 }}>
                      {workflow.status.toUpperCase()} · v{workflow.version}
                    </span>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, color: '#b8c3d0', fontSize: 12 }}>
                    <div>Trigger: <strong>{workflow.trigger || 'manual'}</strong></div>
                    <div>Entry node: <strong>{workflow.entry_node}</strong></div>
                    <div>Nodes: <strong>{workflow.nodes.length}</strong></div>
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 20, color: '#a8b3c1', fontSize: 12 }}>
                    {workflow.nodes.map((node) => <li key={node.id}><code>{node.id}</code> · {node.type}</li>)}
                  </ul>

                  <div style={{ display: 'flex', alignItems: 'end', flexWrap: 'wrap', gap: 10 }}>
                    {workflow.status === 'draft' && (
                      <button
                        type="button"
                        disabled={isPublishing || pendingAction !== null}
                        onClick={() => void publishWorkflow(workflow)}
                        style={{ border: '1px solid #42617f', borderRadius: 9, background: '#142438', color: '#e9eef5', padding: '9px 12px', cursor: isPublishing ? 'wait' : 'pointer' }}
                      >
                        {isPublishing ? 'Publishing…' : 'Publish workflow'}
                      </button>
                    )}
                    <label htmlFor={`lead-id-${workflow.id}`} style={{ display: 'grid', gap: 5, fontSize: 12 }}>
                      Existing lead UUID for execution
                      <input
                        id={`lead-id-${workflow.id}`}
                        value={leadId}
                        onChange={(event) => setLeadId(event.target.value)}
                        autoComplete="off"
                        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                        style={{ width: 'min(100%, 390px)', boxSizing: 'border-box', padding: 9, borderRadius: 8, border: '1px solid #415064', background: '#0d141d', color: '#f2f5f8' }}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={workflow.status !== 'active' || isExecuting || pendingAction !== null || !leadId.trim()}
                      onClick={() => void executeWorkflow(workflow)}
                      title={workflow.status !== 'active' ? 'Publish this workflow before executing it.' : undefined}
                      style={{ border: '1px solid #42617f', borderRadius: 9, background: workflow.status === 'active' ? '#142438' : '#202833', color: '#e9eef5', padding: '9px 12px', cursor: isExecuting ? 'wait' : 'pointer' }}
                    >
                      {isExecuting ? 'Executing…' : 'Execute for lead'}
                    </button>
                    <button
                      type="button"
                      disabled={pendingAction !== null}
                      onClick={() => void loadExecutions(workflow.id)}
                      style={{ border: '1px solid #415064', borderRadius: 9, background: '#101720', color: '#e9eef5', padding: '9px 12px' }}
                    >
                      Refresh executions
                    </button>
                  </div>

                  {executionErrors[workflow.id] && (
                    <div role="alert" style={{ color: '#ffb5bd', fontSize: 12 }}>
                      Execution history is unavailable: {executionErrors[workflow.id]}
                    </div>
                  )}
                  {runRows && (
                    <div style={{ borderTop: '1px solid #293544', paddingTop: 10 }}>
                      <h4 style={{ margin: '0 0 8px', fontSize: 14 }}>Persisted executions ({runRows.length})</h4>
                      {runRows.length === 0 ? (
                        <p style={{ color: '#a8b3c1', fontSize: 12, margin: 0 }}>No executions are persisted for this workflow.</p>
                      ) : (
                        <ul style={{ display: 'grid', gap: 8, margin: 0, paddingLeft: 20 }}>
                          {runRows.map((execution) => (
                            <li key={execution.id} style={{ color: '#b8c3d0', fontSize: 12 }}>
                              <strong>{execution.status.toUpperCase()}</strong> · {execution.id} · current node {execution.current_node || '—'}
                              {execution.error && <div role="alert" style={{ color: '#ffb5bd' }}>{execution.error}</div>}
                              {execution.steps.map((step, index) => (
                                <div key={`${execution.id}:${step.node_id}:${index}`} style={{ color: '#98a6b6', marginTop: 3 }}>
                                  {step.node_id}: {step.status} — {step.detail}
                                </div>
                              ))}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>

        <footer style={{ borderTop: '1px solid #293544', paddingTop: 14, color: '#95a2b1', fontSize: 12 }}>
          <a href="/dashboard/agents" style={{ color: '#9fcfff' }}>Agent Studio</a>
          {' · '}
          <a href="/calls" style={{ color: '#9fcfff' }}>Calls</a>
          {' · '}
          <a href="/settings" style={{ color: '#9fcfff' }}>Settings</a>
          {' · '}
          <a href="/billing" style={{ color: '#9fcfff' }}>Billing</a>
        </footer>
      </div>
    </main>
  );
}

export default WorkflowsPage;
