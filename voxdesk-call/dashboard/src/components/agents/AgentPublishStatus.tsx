import React from 'react';
import { GlassCard } from '../ui/GlassCard';
import type { SaveState } from '../../types/agent-builder';

export interface AgentPublishStatusProps {
  status?: string;
  activeVersion?: number | null;
  publishedAt?: string | null;
  publishedBy?: string | null;
  environment?: string;
  validationStatus?: 'valid' | 'invalid' | 'unvalidated' | string;
  saveState?: SaveState;
  draftEtag?: string;
  compact?: boolean;
  onValidate?: () => void;
  onPublish?: () => void;
}

export function AgentPublishStatus({
  status = 'DRAFT',
  activeVersion = null,
  publishedAt = null,
  publishedBy = null,
  environment = 'production',
  validationStatus = 'unvalidated',
  saveState = 'SAVED',
  draftEtag = '',
  compact = false,
  onValidate,
  onPublish,
}: AgentPublishStatusProps) {
  const normalizedStatus = String(status || 'DRAFT').toUpperCase();
  const isPublished = normalizedStatus === 'PUBLISHED';
  const isArchived = normalizedStatus === 'ARCHIVED' || normalizedStatus === 'RETIRED';

  const statusBadgeClass = isPublished
    ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
    : isArchived
    ? 'border-zinc-500/30 bg-zinc-500/15 text-zinc-300'
    : 'border-amber-500/30 bg-amber-500/15 text-amber-300';

  const validationBadgeClass =
    validationStatus === 'valid'
      ? 'text-emerald-300 border-emerald-500/20 bg-emerald-500/10'
      : validationStatus === 'invalid'
      ? 'text-red-300 border-red-500/20 bg-red-500/10'
      : 'text-white/60 border-white/10 bg-white/[0.04]';

  if (compact) {
    return (
      <div className="inline-flex items-center gap-2 text-xs" data-testid="agent-publish-status-compact">
        <span className={`rounded-full border px-2.5 py-0.5 font-medium ${statusBadgeClass}`}>
          {normalizedStatus}
        </span>
        {activeVersion !== null && activeVersion > 0 && (
          <span className="rounded-full border border-blue-500/25 bg-blue-500/10 px-2 py-0.5 text-blue-300">
            v{activeVersion}
          </span>
        )}
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-white/60">
          {environment}
        </span>
      </div>
    );
  }

  return (
    <GlassCard className="p-4" data-testid="agent-publish-status">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusBadgeClass}`}
            >
              {normalizedStatus}
            </span>
            <span className="inline-flex items-center rounded-full border border-blue-500/25 bg-blue-500/10 px-2.5 py-0.5 text-xs font-medium text-blue-300">
              {activeVersion && activeVersion > 0 ? `Live v${activeVersion}` : 'Unpublished Draft'}
            </span>
            <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-0.5 text-xs text-white/70">
              Env: {environment}
            </span>
            <span
              className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${validationBadgeClass}`}
            >
              Validation: {validationStatus}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-white/50">
            <span>Save state: {saveState}</span>
            {publishedAt && (
              <span>• Published: {new Date(publishedAt).toLocaleString()}</span>
            )}
            {publishedBy && <span>• By: {publishedBy}</span>}
            {draftEtag && (
              <span className="font-mono text-[11px] text-white/40">
                • ETag: {draftEtag}
              </span>
            )}
          </div>
        </div>

        {(onValidate || onPublish) && (
          <div className="flex items-center gap-2">
            {onValidate && (
              <button
                type="button"
                onClick={onValidate}
                className="rounded-xl border border-white/15 bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white hover:bg-white/10"
              >
                Validate Config
              </button>
            )}
            {onPublish && (
              <button
                type="button"
                onClick={onPublish}
                disabled={isArchived}
                className="rounded-xl bg-blue-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                Publish Version
              </button>
            )}
          </div>
        )}
      </div>
    </GlassCard>
  );
}

export default AgentPublishStatus;
