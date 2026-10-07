import React from 'react';
import type { SaveState } from '../../types/agent-builder';
import { AgentPublishStatus } from './AgentPublishStatus';

export interface AgentBuilderHeaderProps {
  name: string;
  status: string;
  version?: number | null;
  etag?: string;
  environment?: string;
  saveState: SaveState;
  lastSaved?: string;
  onSave: () => void;
  onValidate?: () => void;
  onTest: () => void;
  onPublish: () => void;
  onReload?: () => void;
  onBack: () => void;
}

export function AgentBuilderHeader({
  name,
  status,
  version = null,
  etag = '',
  environment = 'production',
  saveState,
  lastSaved,
  onSave,
  onValidate,
  onTest,
  onPublish,
  onReload,
  onBack,
}: AgentBuilderHeaderProps) {
  const saveLabel: Record<SaveState, string> = {
    SAVED: 'Saved',
    SAVING: 'Saving...',
    UNSAVED: 'Unsaved changes',
    ERROR: 'Save error',
    CONFLICT: 'Conflict (409 ETag mismatch)',
  };

  const saveTone: Record<SaveState, string> = {
    SAVED: 'text-emerald-300',
    SAVING: 'text-blue-300',
    UNSAVED: 'text-amber-300',
    ERROR: 'text-red-300',
    CONFLICT: 'text-red-300 font-semibold',
  };

  return (
    <header className="sticky top-0 z-40 min-h-16 border-b border-white/10 bg-black/85 backdrop-blur flex flex-wrap items-center justify-between gap-3 px-4 sm:px-6 py-2">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="rounded-xl border border-white/10 px-3 py-1.5 text-xs text-white/70 hover:text-white hover:bg-white/5"
        >
          ← Agents
        </button>
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-sm font-semibold text-white">{name}</h1>
            <AgentPublishStatus
              status={status}
              activeVersion={version}
              environment={environment}
              compact
            />
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-white/50">
            <span aria-live="polite" className={saveTone[saveState]}>
              {saveLabel[saveState]}
            </span>
            {lastSaved && (
              <span>• Saved {new Date(lastSaved).toLocaleTimeString()}</span>
            )}
            {etag && (
              <span className="font-mono text-[10px] text-white/35">
                • ETag {etag}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {saveState === 'CONFLICT' && onReload && (
          <button
            type="button"
            onClick={onReload}
            className="rounded-xl border border-amber-500/40 bg-amber-500/15 px-3 py-2 text-xs font-medium text-amber-200 hover:bg-amber-500/25"
          >
            Reload Latest
          </button>
        )}
        {onValidate && (
          <button
            type="button"
            onClick={onValidate}
            className="rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-xs text-white hover:bg-white/10"
          >
            Validate
          </button>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={saveState === 'SAVING'}
          className="rounded-xl bg-white px-4 py-2 text-xs font-medium text-black hover:bg-white/90 disabled:opacity-50"
        >
          {saveState === 'SAVING' ? 'Saving...' : 'Save Draft'}
        </button>
        <button
          type="button"
          onClick={onTest}
          className="rounded-xl border border-white/20 px-4 py-2 text-xs text-white hover:bg-white/10"
        >
          Test
        </button>
        <button
          type="button"
          onClick={onPublish}
          className="rounded-xl bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-500"
        >
          Publish
        </button>
      </div>
    </header>
  );
}

export default AgentBuilderHeader;
