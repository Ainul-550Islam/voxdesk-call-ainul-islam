import React, { useCallback, useEffect, useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';
import { AgentPublishStatus } from '../../components/agents/AgentPublishStatus';
import { ConductorPanel } from '../../features/conductor/ConductorPanel';
import {
  getAgent,
  validateAgentConfig,
  publishAgentConfig,
  fetchAgentVersionHistory,
  rollbackAgentToVersion,
  testAgent,
  archiveAgent,
  restoreAgent,
} from '../../api/agents';
import type {
  DurableAgentRecord,
  AgentValidationResult,
  AgentTestResult,
} from '../../api/types/agent';
import type {
  AgentVersionSnapshot,
  AgentPublishEnvironment,
} from '../../api/types/agent-version';

export interface AgentDetailPageProps {
  agentId?: string;
}

export function AgentDetailPage({ agentId: propAgentId }: AgentDetailPageProps) {
  const resolvedId =
    propAgentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).pop() || ''
      : '');

  const [agent, setAgent] = useState<DurableAgentRecord | null>(null);
  const [versions, setVersions] = useState<AgentVersionSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [validation, setValidation] = useState<AgentValidationResult | null>(null);
  const [testResult, setTestResult] = useState<AgentTestResult | null>(null);
  const [changelog, setChangelog] = useState('');
  const [publishEnv, setPublishEnv] = useState<AgentPublishEnvironment>('production');
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!resolvedId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [agentRow, versionRows] = await Promise.all([
        getAgent(resolvedId),
        fetchAgentVersionHistory(resolvedId),
      ]);
      setAgent(agentRow);
      setVersions(versionRows);
    } catch (e: any) {
      setError(e?.message || 'Failed to load agent details');
    } finally {
      setLoading(false);
    }
  }, [resolvedId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleValidate = async () => {
    if (!resolvedId) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      const res = await validateAgentConfig(resolvedId);
      setValidation(res);
      setActionMessage(
        res.valid
          ? 'Validation passed with 0 blocking errors.'
          : `Validation found ${res.errors.length} error(s).`
      );
      await loadData();
    } catch (e: any) {
      setError(e?.message || 'Validation failed');
    } finally {
      setActionBusy(false);
    }
  };

  const handlePublish = async () => {
    if (!resolvedId) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      const ver = await publishAgentConfig(resolvedId, {
        changelog: changelog.trim() || 'Published from Agent Detail',
        environment: publishEnv,
      });
      setChangelog('');
      setActionMessage(`Published immutable version v${ver.version} to ${publishEnv}.`);
      await loadData();
    } catch (e: any) {
      setError(e?.message || 'Publish failed');
    } finally {
      setActionBusy(false);
    }
  };

  const handleRollback = async (targetVersion: number) => {
    if (!resolvedId) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      const ver = await rollbackAgentToVersion(resolvedId, {
        target_version: targetVersion,
        reason: `Rolled back to v${targetVersion}`,
      });
      setActionMessage(
        `Rolled back to v${targetVersion} (minted immutable version v${ver.version}).`
      );
      await loadData();
    } catch (e: any) {
      setError(e?.message || 'Rollback failed');
    } finally {
      setActionBusy(false);
    }
  };

  const handleTest = async () => {
    if (!resolvedId) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      const res = await testAgent(resolvedId);
      setTestResult(res);
      setActionMessage(
        res.ok ? 'Offline readiness check passed.' : 'Readiness check reported issues.'
      );
    } catch (e: any) {
      setError(e?.message || 'Test check failed');
    } finally {
      setActionBusy(false);
    }
  };

  const handleArchiveToggle = async () => {
    if (!resolvedId || !agent) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      if (agent.status === 'ARCHIVED') {
        await restoreAgent(resolvedId);
        setActionMessage('Agent restored.');
      } else {
        await archiveAgent(resolvedId, 'Archived from detail view');
        setActionMessage('Agent archived.');
      }
      await loadData();
    } catch (e: any) {
      setError(e?.message || 'Lifecycle update failed');
    } finally {
      setActionBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="mx-auto max-w-6xl space-y-6">
          <div className="h-24 rounded-2xl bg-white/5 animate-pulse" />
          <div className="h-64 rounded-2xl bg-white/5 animate-pulse" />
        </div>
      </div>
    );
  }

  if (error && !agent) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="mx-auto max-w-3xl">
          <GlassCard className="p-8 text-center">
            <h1 className="text-base font-semibold text-red-300">Unable to load agent</h1>
            <p className="mt-2 text-xs text-white/60">{error}</p>
            <div className="mt-6 flex justify-center gap-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  window.location.href = '/dashboard/agents';
                }}
              >
                ← Back to Agents
              </Button>
              <Button variant="primary" size="sm" onClick={loadData}>
                Retry
              </Button>
            </div>
          </GlassCard>
        </div>
      </div>
    );
  }

  const latestVersion = versions[0] || null;

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/70 backdrop-blur">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => {
                window.location.href = '/dashboard/agents';
              }}
              className="rounded-xl border border-white/10 px-3 py-1.5 text-xs text-white/70 hover:text-white"
            >
              ← Agents
            </button>
            <div>
              <h1 className="text-base font-semibold text-white">
                {agent?.name || 'Agent Detail'}
              </h1>
              <p className="text-xs text-white/50">
                ID: {agent?.id} {agent?.external_key ? `• Key: ${agent.external_key}` : ''}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={handleTest} disabled={actionBusy}>
              Test Readiness
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleArchiveToggle}
              disabled={actionBusy}
            >
              {agent?.status === 'ARCHIVED' ? 'Restore' : 'Archive'}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.location.href = `/dashboard/agents/${encodeURIComponent(
                  resolvedId
                )}/builder`;
              }}
            >
              Open Builder
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {actionMessage && (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
            {actionMessage}
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs text-red-200">
            {error}
          </div>
        )}

        <AgentPublishStatus
          status={agent?.status || 'DRAFT'}
          activeVersion={
            agent?.published_version_number ??
            agent?.active_version ??
            latestVersion?.version ??
            null
          }
          publishedAt={latestVersion?.published_at || agent?.published_at || null}
          publishedBy={latestVersion?.published_by || null}
          environment={latestVersion?.published_environment || 'production'}
          validationStatus={agent?.validation_status || 'unvalidated'}
          draftEtag={agent?.draft_etag || agent?.etag || ''}
          onValidate={handleValidate}
          onPublish={handlePublish}
        />

        <div className="grid gap-6 lg:grid-cols-3">
          <GlassCard className="lg:col-span-2 space-y-4">
            <h2 className="text-sm font-semibold text-white">
              Runtime &amp; Voice Configuration
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 text-xs">
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">Greeting</div>
                <div className="mt-1 text-white">
                  {agent?.greeting || 'Thanks for calling. How can I help you today?'}
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">Persona</div>
                <div className="mt-1 text-white">
                  {agent?.persona || 'professional, concise, empathetic'}
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">LLM Provider &amp; Model</div>
                <div className="mt-1 text-white">
                  {agent?.llm_provider || 'anthropic'} /{' '}
                  {agent?.llm_model || 'claude-haiku-4-5-20251001'}
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">Voice &amp; Language</div>
                <div className="mt-1 text-white">
                  {agent?.voice_id || 'default'} • {agent?.primary_language || agent?.language || 'en-US'} (
                  {agent?.speech_speed ?? 1.0}x)
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">Handoff Mode</div>
                <div className="mt-1 text-white">
                  {agent?.handoff_mode || 'none'}{' '}
                  {agent?.handoff_destination ? `→ ${agent.handoff_destination}` : ''}
                </div>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="text-white/50">Operating Window</div>
                <div className="mt-1 text-white">
                  {agent?.operating_window || '09:00-17:00'} (
                  {agent?.operating_timezone || 'America/New_York'})
                </div>
              </div>
            </div>

            {validation && (
              <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 space-y-2">
                <div className="text-xs font-medium text-white">
                  Validation Report ({new Date(validation.checked_at).toLocaleTimeString()})
                </div>
                {validation.errors.length === 0 ? (
                  <div className="text-xs text-emerald-300">
                    ✓ Configuration is valid and ready to publish.
                  </div>
                ) : (
                  validation.errors.map((err, i) => (
                    <div key={i} className="text-xs text-red-300">
                      • [{err.field}] {err.message}
                    </div>
                  ))
                )}
              </div>
            )}

            {testResult && (
              <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 space-y-2">
                <div className="text-xs font-medium text-white">
                  Offline Readiness Check (Hash: {testResult.config_hash.slice(0, 12)})
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
                  {Object.entries(testResult.checks).map(([k, v]) => (
                    <div
                      key={k}
                      className="rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5"
                    >
                      <span className="text-white/50">{k}: </span>
                      <span className="text-white font-medium">{String(v)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </GlassCard>

          <GlassCard className="space-y-4">
            <h2 className="text-sm font-semibold text-white">Publish New Version</h2>
            <p className="text-xs text-white/50">
              Mints an immutable AgentVersion snapshot in PostgreSQL and projects runtime
              fields onto the Tenant voice loop.
            </p>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-white/70 mb-1">
                  Target Environment
                </label>
                <select
                  value={publishEnv}
                  onChange={(e) =>
                    setPublishEnv(e.target.value as AgentPublishEnvironment)
                  }
                  className="w-full rounded-xl border border-white/10 bg-black/60 px-3 py-2 text-xs text-white"
                >
                  <option value="production">Production</option>
                  <option value="staging">Staging</option>
                  <option value="development">Development</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-white/70 mb-1">
                  Release Notes / Changelog
                </label>
                <textarea
                  value={changelog}
                  onChange={(e) => setChangelog(e.target.value)}
                  placeholder="Describe what changed in this version..."
                  rows={3}
                  className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-xs text-white placeholder-white/35"
                />
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={handlePublish}
                disabled={actionBusy || agent?.status === 'ARCHIVED'}
                className="w-full"
              >
                {actionBusy ? 'Publishing...' : 'Validate & Publish Version'}
              </Button>
            </div>
          </GlassCard>
        </div>

        <GlassCard className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">
                Immutable Version History ({versions.length})
              </h2>
              <p className="text-xs text-white/50">
                Append-only snapshots ordered newest first. Rollback mints a new version
                referencing the target snapshot.
              </p>
            </div>
          </div>

          {versions.length === 0 ? (
            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-8 text-center text-xs text-white/50">
              No published versions yet. Publish the current draft to mint v1.
            </div>
          ) : (
            <div className="space-y-2.5">
              {versions.map((ver, index) => {
                const isActive =
                  ver.is_active !== undefined ? ver.is_active : index === 0;
                return (
                  <div
                    key={ver.id || `v-${ver.version}`}
                    className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4"
                  >
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-white">
                          v{ver.version}
                        </span>
                        {isActive && (
                          <span className="rounded-full bg-emerald-500/20 border border-emerald-500/30 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                            Active
                          </span>
                        )}
                        {ver.is_rollback && (
                          <span className="rounded-full bg-amber-500/20 border border-amber-500/30 px-2 py-0.5 text-[10px] text-amber-300">
                            Rollback
                          </span>
                        )}
                        <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10px] text-white/70">
                          {ver.published_environment || 'production'}
                        </span>
                        <span className="font-mono text-[10px] text-white/40">
                          #{String(ver.config_hash || '').slice(0, 10)}
                        </span>
                      </div>
                      <div className="text-xs text-white/70">
                        {ver.changelog || ver.release_notes || 'Published configuration snapshot'}
                      </div>
                      <div className="text-[11px] text-white/40">
                        Published {new Date(ver.published_at).toLocaleString()} by{' '}
                        {ver.published_by || 'system'}
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          window.location.href = `/dashboard/agents/${encodeURIComponent(
                            resolvedId
                          )}/versions/${ver.version}`;
                        }}
                        className="rounded-xl border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs text-white hover:bg-white/10"
                      >
                        Inspect Snapshot
                      </button>
                      {!isActive && (
                        <button
                          type="button"
                          onClick={() => handleRollback(ver.version)}
                          disabled={actionBusy}
                          className="rounded-xl border border-blue-500/30 bg-blue-500/15 px-3 py-1.5 text-xs text-blue-200 hover:bg-blue-500/25 disabled:opacity-50"
                        >
                          Rollback to v{ver.version}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </GlassCard>

        {resolvedId && (
          <ConductorPanel
            agentId={resolvedId}
            agentKind="voice"
            baseVersionNumber={agent?.active_version_number ?? undefined}
            originSurface="agent_detail"
          />
        )}
      </div>
    </div>
  );
}

export default AgentDetailPage;
