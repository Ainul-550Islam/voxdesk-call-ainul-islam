import React, { useCallback, useEffect, useState } from 'react';
import { GlassCard } from '../../components/ui/GlassCard';
import { Button } from '../../components/ui/Button';
import {
  fetchAgentVersion,
  diffAgentVersions,
  rollbackToAgentVersion,
} from '../../api/agent-versions';
import type {
  AgentVersionSnapshot,
  AgentVersionDiffResponse,
} from '../../api/types/agent-version';

export interface AgentVersionDetailPageProps {
  agentId?: string;
  versionNumber?: number;
}

export function AgentVersionDetailPage({
  agentId: propAgentId,
  versionNumber: propVersion,
}: AgentVersionDetailPageProps) {
  const pathParts =
    typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean)
      : [];
  const versionsIdx = pathParts.indexOf('versions');
  const resolvedAgentId =
    propAgentId || (versionsIdx > 0 ? pathParts[versionsIdx - 1] : '');
  const resolvedVersion =
    propVersion ??
    (versionsIdx >= 0 && pathParts[versionsIdx + 1]
      ? Number(pathParts[versionsIdx + 1])
      : 1);

  const [snapshot, setSnapshot] = useState<AgentVersionSnapshot | null>(null);
  const [diff, setDiff] = useState<AgentVersionDiffResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackNotice, setRollbackNotice] = useState<string | null>(null);

  const loadSnapshot = useCallback(async () => {
    if (!resolvedAgentId || !resolvedVersion) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const ver = await fetchAgentVersion(resolvedAgentId, resolvedVersion);
      setSnapshot(ver);
      if (resolvedVersion > 1) {
        try {
          const d = await diffAgentVersions(
            resolvedAgentId,
            resolvedVersion - 1,
            resolvedVersion
          );
          setDiff(d);
        } catch {
          setDiff(null);
        }
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to load version snapshot');
    } finally {
      setLoading(false);
    }
  }, [resolvedAgentId, resolvedVersion]);

  useEffect(() => {
    loadSnapshot();
  }, [loadSnapshot]);

  const handleRollback = async () => {
    if (!resolvedAgentId || !snapshot) return;
    setRollingBack(true);
    setRollbackNotice(null);
    try {
      const minted = await rollbackToAgentVersion(resolvedAgentId, {
        target_version: snapshot.version,
        reason: `Rolled back from version detail view to v${snapshot.version}`,
      });
      setRollbackNotice(
        `Rolled back to v${snapshot.version}: minted active version v${minted.version}.`
      );
      await loadSnapshot();
    } catch (e: any) {
      setError(e?.message || 'Rollback failed');
    } finally {
      setRollingBack(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="h-20 rounded-2xl bg-white/5 animate-pulse" />
          <div className="h-64 rounded-2xl bg-white/5 animate-pulse" />
        </div>
      </div>
    );
  }

  if (error && !snapshot) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="mx-auto max-w-3xl">
          <GlassCard className="p-8 text-center">
            <h1 className="text-base font-semibold text-red-300">
              Version Snapshot Not Found
            </h1>
            <p className="mt-2 text-xs text-white/60">{error}</p>
            <div className="mt-6 flex justify-center gap-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  window.location.href = resolvedAgentId
                    ? `/dashboard/agents/${encodeURIComponent(resolvedAgentId)}`
                    : '/dashboard/agents';
                }}
              >
                ← Back to Agent
              </Button>
              <Button variant="primary" size="sm" onClick={loadSnapshot}>
                Retry
              </Button>
            </div>
          </GlassCard>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/70 backdrop-blur">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => {
                window.location.href = `/dashboard/agents/${encodeURIComponent(
                  resolvedAgentId
                )}`;
              }}
              className="rounded-xl border border-white/10 px-3 py-1.5 text-xs text-white/70 hover:text-white"
            >
              ← Agent History
            </button>
            <div>
              <h1 className="text-base font-semibold text-white">
                Immutable Version v{snapshot?.version ?? resolvedVersion}
              </h1>
              <p className="text-xs text-white/50">
                Agent: {snapshot?.agent_id || resolvedAgentId} • Hash:{' '}
                <span className="font-mono">{snapshot?.config_hash}</span>
              </p>
            </div>
          </div>

          <Button
            variant="primary"
            size="sm"
            onClick={handleRollback}
            disabled={rollingBack}
          >
            {rollingBack
              ? 'Rolling back...'
              : `Rollback to v${snapshot?.version ?? resolvedVersion}`}
          </Button>
        </div>
      </div>

      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {rollbackNotice && (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
            {rollbackNotice}
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs text-red-200">
            {error}
          </div>
        )}

        <GlassCard className="space-y-3">
          <h2 className="text-sm font-semibold text-white">Snapshot Metadata</h2>
          <div className="grid gap-3 sm:grid-cols-3 text-xs">
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <div className="text-white/50">Status &amp; Environment</div>
              <div className="mt-1 text-white font-medium">
                {snapshot?.status || 'published'} •{' '}
                {snapshot?.published_environment || 'production'}
              </div>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <div className="text-white/50">Published At &amp; Actor</div>
              <div className="mt-1 text-white">
                {snapshot?.published_at
                  ? new Date(snapshot.published_at).toLocaleString()
                  : '—'}{' '}
                ({snapshot?.published_by || 'system'})
              </div>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <div className="text-white/50">Changelog</div>
              <div className="mt-1 text-white">
                {snapshot?.changelog ||
                  snapshot?.release_notes ||
                  'Initial published snapshot'}
              </div>
            </div>
          </div>
        </GlassCard>

        {diff && diff.diffs.length > 0 && (
          <GlassCard className="space-y-3">
            <h2 className="text-sm font-semibold text-white">
              Changes vs v{diff.from_version} ({diff.total_changes})
            </h2>
            <div className="space-y-2 text-xs">
              {diff.diffs.map((item, idx) => (
                <div
                  key={idx}
                  className="rounded-xl border border-white/10 bg-white/[0.02] p-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2"
                >
                  <div>
                    <span className="font-mono text-blue-300">{item.field_path}</span>
                    <span className="ml-2 rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase text-white/70">
                      {item.change_type}
                    </span>
                  </div>
                  <div className="font-mono text-[11px] text-white/60">
                    {JSON.stringify(item.old_value)} → {JSON.stringify(item.new_value)}
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>
        )}

        <GlassCard className="space-y-3">
          <h2 className="text-sm font-semibold text-white">
            Immutable Configuration Snapshot (`config_snapshot`)
          </h2>
          <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/80 p-4 text-xs text-emerald-200 font-mono">
            {JSON.stringify(snapshot?.config_snapshot || {}, null, 2)}
          </pre>
        </GlassCard>
      </div>
    </div>
  );
}

export default AgentVersionDetailPage;
