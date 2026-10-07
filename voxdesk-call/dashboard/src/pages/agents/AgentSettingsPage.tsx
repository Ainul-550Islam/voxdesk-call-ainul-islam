/**
 * dashboard/src/pages/agents/AgentSettingsPage.tsx
 * Durable Agent Settings page including Metadata, Public Web Widget & Scoped Public Keys, and Danger Zone.
 */

import React, { useState } from 'react';
import { archiveAgent, cloneAgent, deleteAgent } from '../../api/agents';
import { AgentStatusBadge } from '../../components/agents/AgentStatusBadge';
import { Button } from '../../components/ui/Button';
import { GlassCard } from '../../components/ui/GlassCard';
import { WidgetSettings } from '../../features/public-widget/WidgetSettings';
import { useAgentBuilder } from '../../hooks/useAgentBuilder';

export function AgentSettingsPage({ agentId }: { agentId?: string }) {
  const id =
    agentId ||
    (typeof window !== 'undefined'
      ? window.location.pathname.split('/').filter(Boolean).slice(-2)[0]
      : '');
  const { config, localConfig, setLocalConfig, saveState, save } = useAgentBuilder(id);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const handleDuplicate = async () => {
    try {
      const cloned = await cloneAgent(id);
      window.location.href = `/dashboard/agents/${cloned.id}/builder`;
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Duplicate failed');
    }
  };

  const handleArchive = async () => {
    try {
      await archiveAgent(id);
      window.location.href = '/dashboard/agents';
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Archive failed');
    }
  };

  const handleDelete = async () => {
    try {
      await deleteAgent(id);
      window.location.href = '/dashboard/agents';
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="border-b border-white/10 bg-black/50 backdrop-blur">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <button
            onClick={() => {
              window.location.href = `/dashboard/agents/${id}/builder`;
            }}
            className="text-xs text-white/60 hover:text-white"
          >
            ← Back to Builder
          </button>
          <AgentStatusBadge status={(config?.status as 'DRAFT' | 'PUBLISHED' | 'ARCHIVED') || 'DRAFT'} />
        </div>
      </div>

      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <GlassCard>
          <h2 className="text-sm font-medium">Metadata</h2>
          <div className="mt-4 space-y-4">
            <div>
              <label className="text-xs text-white/70">Name</label>
              <input
                value={localConfig?.name || ''}
                onChange={(e) =>
                  setLocalConfig({ ...localConfig!, name: e.target.value })
                }
                className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white"
              />
            </div>
            <div>
              <label className="text-xs text-white/70">Description</label>
              <textarea
                value={localConfig?.description || ''}
                onChange={(e) =>
                  setLocalConfig({ ...localConfig!, description: e.target.value })
                }
                className="mt-1 w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-sm text-white"
              />
            </div>
          </div>
          <div className="mt-6 flex justify-end">
            <Button variant="primary" size="sm" onClick={save}>
              Save {saveState}
            </Button>
          </div>
        </GlassCard>

        {/* Prompt 5: Scoped Public Widget Keys & Live Preview */}
        <WidgetSettings agentId={id || undefined} />

        <GlassCard>
          <h2 className="text-sm font-medium text-red-300">Danger Zone</h2>
          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div>
                <div className="text-sm text-white">Duplicate Agent</div>
                <div className="text-xs text-white/50">
                  Clone via POST /api/agents/:id/clone tenant-scoped
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={handleDuplicate}>
                Duplicate
              </Button>
            </div>
            <div className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div>
                <div className="text-sm text-white">Archive Agent</div>
                <div className="text-xs text-white/50">Archive with confirmation</div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setConfirmArchive(true)}>
                Archive
              </Button>
            </div>
            <div className="flex items-center justify-between rounded-xl border border-red-500/20 bg-red-500/5 p-4">
              <div>
                <div className="text-sm text-red-300">Delete Agent</div>
                <div className="text-xs text-red-300/60">
                  DELETE tenant-scoped, confirmation required
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
                className="text-red-300"
              >
                Delete
              </Button>
            </div>
          </div>
        </GlassCard>

        {confirmArchive && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
            <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0a0a0a] p-6">
              <h3 className="text-sm font-medium text-white">Archive agent?</h3>
              <p className="mt-2 text-xs text-white/60">
                This will archive the agent. You can restore later.
              </p>
              <div className="mt-6 flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmArchive(false)}
                >
                  Cancel
                </Button>
                <Button variant="primary" size="sm" onClick={handleArchive}>
                  Archive
                </Button>
              </div>
            </div>
          </div>
        )}

        {confirmDelete && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
            <div className="w-full max-w-md rounded-2xl border border-red-500/20 bg-[#0a0a0a] p-6">
              <h3 className="text-sm font-medium text-red-300">
                Delete agent permanently?
              </h3>
              <p className="mt-2 text-xs text-white/60">
                This action cannot be undone. Tenant-scoped ownership checked.
              </p>
              <div className="mt-6 flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmDelete(false)}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleDelete}
                  className="bg-red-600 hover:bg-red-500"
                >
                  Delete
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentSettingsPage;
