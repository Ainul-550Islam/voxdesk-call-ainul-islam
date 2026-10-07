import React, { useCallback, useEffect, useState } from 'react';
import {
  getVersions,
  getVersionSnapshot,
  rollbackVersion,
} from '../../api/agent-builder';
import type { Version } from '../../types/agent-builder';

export function AgentVersionPanel({ agentId }: { agentId: string }) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [selectedSnapshot, setSelectedSnapshot] = useState<Version | null>(
    null
  );
  const [loading, setLoading] = useState(true);
  const [busyVersion, setBusyVersion] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadVersions = useCallback(async () => {
    if (!agentId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const v = await getVersions(agentId);
      setVersions(v);
    } catch {
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadVersions();
  }, [loadVersions]);

  const handleInspect = async (ver: number) => {
    try {
      const snap = await getVersionSnapshot(agentId, ver);
      setSelectedSnapshot(snap);
    } catch {
      const local = versions.find((v) => v.version === ver) || null;
      setSelectedSnapshot(local);
    }
  };

  const handleRollback = async (ver: number) => {
    setBusyVersion(ver);
    setNotice(null);
    try {
      const minted = await rollbackVersion(
        agentId,
        ver,
        `Rolled back to v${ver} from Builder Version Panel`
      );
      setNotice(`Rolled back to v${ver} (active version is now v${minted.version}).`);
      await loadVersions();
    } catch (e: any) {
      setNotice(e?.message || 'Rollback failed');
    } finally {
      setBusyVersion(null);
    }
  };

  if (loading) {
    return <div className="h-32 animate-pulse rounded-xl bg-white/5" />;
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium text-white">
          Immutable Version History ({versions.length})
        </h2>
        <p className="text-xs text-white/50">
          PostgreSQL append-only version snapshots. Inspect any snapshot or
          roll back to mint a new active release.
        </p>
      </div>

      {notice && (
        <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-xs text-blue-200">
          {notice}
        </div>
      )}

      <div className="space-y-2">
        {versions.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-6 text-center text-xs text-white/40">
            No published versions yet — publish this draft to mint v1.
          </div>
        ) : (
          versions.map((v) => (
            <div
              key={v.id || `v-${v.version}`}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3.5"
            >
              <div className="space-y-0.5">
                <div className="flex items-center gap-2 text-sm text-white font-medium">
                  <span>v{v.version}</span>
                  {v.is_current && (
                    <span className="rounded-full bg-emerald-500/20 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-300">
                      Active
                    </span>
                  )}
                  {v.is_rollback && (
                    <span className="rounded-full bg-amber-500/20 border border-amber-500/30 px-2 py-0.5 text-[10px] text-amber-300">
                      Rollback
                    </span>
                  )}
                  {v.published_environment && (
                    <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/70">
                      {v.published_environment}
                    </span>
                  )}
                </div>
                <div className="text-xs text-white/40">
                  {new Date(v.created_at).toLocaleString()}
                  {v.author ? ` • ${v.author}` : ''}
                </div>
                <div className="text-xs text-white/60">
                  {v.changes || 'Published configuration snapshot'}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleInspect(v.version)}
                  className="rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1 text-xs text-white/80 hover:text-white hover:bg-white/10"
                >
                  Inspect
                </button>
                <button
                  type="button"
                  onClick={() => {
                    window.location.href = `/dashboard/agents/${encodeURIComponent(
                      agentId
                    )}/versions/${v.version}`;
                  }}
                  className="rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1 text-xs text-white/80 hover:text-white hover:bg-white/10"
                >
                  Full View
                </button>
                {!v.is_current && (
                  <button
                    type="button"
                    disabled={busyVersion === v.version}
                    onClick={() => handleRollback(v.version)}
                    className="rounded-lg border border-blue-500/30 bg-blue-500/15 px-2.5 py-1 text-xs text-blue-200 hover:bg-blue-500/25 disabled:opacity-50"
                  >
                    {busyVersion === v.version ? 'Restoring...' : 'Rollback'}
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {selectedSnapshot && (
        <div className="rounded-xl border border-white/10 bg-black/70 p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-white">
              Snapshot v{selectedSnapshot.version} (`config_snapshot`)
            </span>
            <button
              type="button"
              onClick={() => setSelectedSnapshot(null)}
              className="text-xs text-white/50 hover:text-white"
            >
              Close
            </button>
          </div>
          <pre className="max-h-64 overflow-auto rounded-lg bg-black p-3 text-[11px] text-emerald-200 font-mono">
            {JSON.stringify(selectedSnapshot.config_snapshot || {}, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export default AgentVersionPanel;
