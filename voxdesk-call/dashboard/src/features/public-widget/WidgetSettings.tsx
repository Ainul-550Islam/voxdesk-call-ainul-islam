/**
 * dashboard/src/features/public-widget/WidgetSettings.tsx
 * Authenticated management surface for scoped PublicWidgetKeys (CREATE -> ACTIVE -> ROTATE -> REVOKE),
 * origin allowlist enforcement, embed snippet generation, and live widget preview.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { listAgents, type DurableAgentRecord } from '../../api/agents';
import {
  createPublicWidgetKey,
  listPublicWidgetKeys,
  revokePublicWidgetKey,
  rotatePublicWidgetKey,
  updatePublicWidgetKey,
} from '../../api/public-keys';
import type { PublicWidgetKeyRecord } from '../../api/types/public-widget';
import { PublicWidget } from './PublicWidget';

export interface WidgetSettingsProps {
  agentId?: string;
}

export function WidgetSettings({ agentId: initialAgentId }: WidgetSettingsProps) {
  const [agents, setAgents] = useState<DurableAgentRecord[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>(initialAgentId || '');
  const [keys, setKeys] = useState<PublicWidgetKeyRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [keyName, setKeyName] = useState<string>('Production Website Widget');
  const [originsText, setOriginsText] = useState<string>(
    typeof window !== 'undefined' && window.location?.origin
      ? `${window.location.origin}, https://example.com`
      : 'http://localhost:3000, https://example.com',
  );
  const [rateLimit, setRateLimit] = useState<number>(30);
  const [sessionTtl, setSessionTtl] = useState<number>(900);
  const [widgetTitle, setWidgetTitle] = useState<string>('Talk with our AI Concierge');
  const [widgetGreeting, setWidgetGreeting] = useState<string>(
    'Hello! How can I help you today?',
  );
  const [creating, setCreating] = useState<boolean>(false);

  // One-time revealed raw key (`vdpk_...`)
  const [revealedRawKey, setRevealedRawKey] = useState<string | null>(null);
  const [revealedKeyRecord, setRevealedKeyRecord] = useState<PublicWidgetKeyRecord | null>(
    null,
  );
  const [previewPublicKey, setPreviewPublicKey] = useState<string>('');

  // Inline origin editor for existing key
  const [editingKeyId, setEditingKeyId] = useState<string | null>(null);
  const [editingOriginsText, setEditingOriginsText] = useState<string>('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentsRes, keysRes] = await Promise.all([
        listAgents(),
        listPublicWidgetKeys(
          initialAgentId ? { agentId: initialAgentId } : undefined,
        ),
      ]);
      const agentList = agentsRes.agents || [];
      setAgents(agentList);
      if (!selectedAgentId && agentList.length > 0) {
        setSelectedAgentId(initialAgentId || agentList[0].id);
      }
      setKeys(keysRes.keys || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load public widget keys');
    } finally {
      setLoading(false);
    }
  }, [initialAgentId, selectedAgentId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedAgentId) {
      setError('Please select a published agent first.');
      return;
    }
    const parsedOrigins = originsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (parsedOrigins.length === 0) {
      setError('At least one explicit allowed origin is required.');
      return;
    }

    setCreating(true);
    setError(null);
    try {
      const created = await createPublicWidgetKey({
        agent_id: selectedAgentId,
        name: keyName.trim() || 'Public Web Widget Key',
        allowed_origins: parsedOrigins,
        rate_limit_per_minute: Number(rateLimit) || 30,
        session_ttl_seconds: Number(sessionTtl) || 900,
        require_published_agent: true,
        widget_config: {
          title: widgetTitle.trim() || 'Talk with our AI Concierge',
          greeting: widgetGreeting.trim() || 'Hello! How can I help you today?',
          enable_chat: true,
          enable_voice: true,
        },
      });
      setRevealedRawKey(created.public_key);
      setRevealedKeyRecord(created.key);
      setPreviewPublicKey(created.public_key);
      setKeys((prev) => [created.key, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create public key');
    } finally {
      setCreating(false);
    }
  };

  const handleRotateKey = async (keyId: string) => {
    setError(null);
    try {
      const rotated = await rotatePublicWidgetKey(keyId, 'Rotated from Widget Settings UI');
      setRevealedRawKey(rotated.public_key);
      setRevealedKeyRecord(rotated.key);
      setPreviewPublicKey(rotated.public_key);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rotate public key');
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    setError(null);
    try {
      await revokePublicWidgetKey(keyId, 'Revoked from Widget Settings UI');
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke public key');
    }
  };

  const handleSaveOrigins = async (keyId: string) => {
    const parsedOrigins = editingOriginsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (parsedOrigins.length === 0) {
      setError('At least one explicit origin is required.');
      return;
    }
    setError(null);
    try {
      const updated = await updatePublicWidgetKey(keyId, {
        allowed_origins: parsedOrigins,
      });
      setKeys((prev) => prev.map((k) => (k.id === keyId ? updated : k)));
      setEditingKeyId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update allowed origins');
    }
  };

  return (
    <div
      data-testid="widget-settings-root"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 20,
        color: '#F1F5F9',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: 20,
          borderRadius: 12,
          background: '#0F1623',
          border: '1px solid #1E2D45',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>
          Public Web Widget & Scoped Public Keys
        </h2>
        <p style={{ margin: '6px 0 0', fontSize: 13, color: '#94A3B8', lineHeight: 1.5 }}>
          Issue tenant- and agent-scoped public keys (<code>vdpk_...</code>) for external
          website embeds. Public keys store only a SHA-256 digest at rest, enforce strict
          server-side origin allowlists, and never grant access to private workspace APIs.
        </p>
      </div>

      {error && (
        <div
          data-testid="widget-settings-error"
          style={{
            padding: '12px 16px',
            borderRadius: 8,
            background: 'rgba(239,68,68,0.12)',
            border: '1px solid rgba(239,68,68,0.35)',
            color: '#FCA5A5',
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {/* One-time revealed raw public key banner */}
      {revealedRawKey && revealedKeyRecord && (
        <div
          data-testid="widget-revealed-key-banner"
          style={{
            padding: 16,
            borderRadius: 12,
            background: 'rgba(16,185,129,0.1)',
            border: '1px solid rgba(16,185,129,0.4)',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 700, color: '#34D399', marginBottom: 6 }}>
            Save Your Scoped Public Key Now (Shown Once)
          </div>
          <div style={{ fontSize: 12, color: '#A7F3D0', marginBottom: 10 }}>
            Only the SHA-256 hash is persisted in PostgreSQL. Copy this public key for your
            website widget embed:
          </div>
          <div
            data-testid="widget-revealed-raw-key"
            style={{
              padding: '10px 12px',
              borderRadius: 8,
              background: '#0A0E17',
              border: '1px solid #1E2D45',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: 12.5,
              color: '#F8FAFC',
              wordBreak: 'break-all',
              marginBottom: 10,
            }}
          >
            {revealedRawKey}
          </div>
          <div style={{ fontSize: 11.5, color: '#94A3B8', marginBottom: 4 }}>
            HTML Embed Snippet:
          </div>
          <pre
            data-testid="widget-embed-snippet"
            style={{
              margin: 0,
              padding: 10,
              borderRadius: 8,
              background: '#0A0E17',
              border: '1px solid #1E2D45',
              fontSize: 11.5,
              color: '#93C5FD',
              overflowX: 'auto',
            }}
          >
            {revealedKeyRecord.embed_snippet}
          </pre>
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
          gap: 20,
          alignItems: 'start',
        }}
      >
        {/* Create Scoped Public Key Form */}
        <form
          onSubmit={(e) => void handleCreateKey(e)}
          data-testid="create-public-key-form"
          style={{
            padding: 20,
            borderRadius: 12,
            background: '#0F1623',
            border: '1px solid #1E2D45',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          <div style={{ fontSize: 15, fontWeight: 700 }}>Create Scoped Public Key</div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
              Target Published Agent
            </label>
            <select
              data-testid="public-key-agent-select"
              value={selectedAgentId}
              onChange={(e) => setSelectedAgentId(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            >
              <option value="">Select an agent...</option>
              {agents.map((ag) => (
                <option key={ag.id} value={ag.id}>
                  {ag.name} ({ag.status}
                  {ag.published_version_number ? ` · v${ag.published_version_number}` : ''})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
              Key Label
            </label>
            <input
              type="text"
              data-testid="public-key-name-input"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
              Allowed Origins (comma-separated, e.g. https://app.example.com)
            </label>
            <input
              type="text"
              data-testid="public-key-origins-input"
              value={originsText}
              onChange={(e) => setOriginsText(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
                Rate Limit (req / min)
              </label>
              <input
                type="number"
                min={1}
                max={600}
                value={rateLimit}
                onChange={(e) => setRateLimit(Number(e.target.value))}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 8,
                  border: '1px solid #1E2D45',
                  background: '#0A0E17',
                  color: '#F1F5F9',
                  fontSize: 13,
                }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
                Session TTL (seconds)
              </label>
              <input
                type="number"
                min={60}
                max={3600}
                value={sessionTtl}
                onChange={(e) => setSessionTtl(Number(e.target.value))}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 8,
                  border: '1px solid #1E2D45',
                  background: '#0A0E17',
                  color: '#F1F5F9',
                  fontSize: 13,
                }}
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
              Widget Header Title
            </label>
            <input
              type="text"
              value={widgetTitle}
              onChange={(e) => setWidgetTitle(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginBottom: 5 }}>
              Initial Greeting Message
            </label>
            <input
              type="text"
              value={widgetGreeting}
              onChange={(e) => setWidgetGreeting(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#0A0E17',
                color: '#F1F5F9',
                fontSize: 13,
              }}
            />
          </div>

          <button
            type="submit"
            data-testid="create-public-key-submit-btn"
            disabled={creating}
            style={{
              padding: '10px 16px',
              borderRadius: 8,
              border: 'none',
              background: '#2563EB',
              color: '#FFFFFF',
              fontSize: 13,
              fontWeight: 600,
              cursor: creating ? 'wait' : 'pointer',
            }}
          >
            {creating ? 'Generating Scoped Public Key...' : 'Generate Scoped Public Key'}
          </button>
        </form>

        {/* Live Interactive Widget Sandbox */}
        <div
          style={{
            padding: 20,
            borderRadius: 12,
            background: '#0F1623',
            border: '1px solid #1E2D45',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <div style={{ fontSize: 15, fontWeight: 700 }}>Live Public Widget Preview</div>
          <div style={{ fontSize: 12, color: '#94A3B8' }}>
            Paste or use your newly generated <code>vdpk_...</code> key below to verify origin
            validation and pinned session behavior:
          </div>
          <input
            type="text"
            data-testid="widget-preview-key-input"
            value={previewPublicKey}
            onChange={(e) => setPreviewPublicKey(e.target.value)}
            placeholder="Paste vdpk_... public key to preview widget"
            style={{
              width: '100%',
              padding: '9px 12px',
              borderRadius: 8,
              border: '1px solid #1E2D45',
              background: '#0A0E17',
              color: '#F1F5F9',
              fontSize: 12.5,
              fontFamily: 'var(--font-mono, monospace)',
            }}
          />
          {previewPublicKey.trim() ? (
            <PublicWidget publicKey={previewPublicKey.trim()} inline defaultOpen />
          ) : (
            <div
              style={{
                padding: 24,
                borderRadius: 10,
                background: '#0A0E17',
                border: '1px dashed #1E2D45',
                fontSize: 12.5,
                color: '#64748B',
                textAlign: 'center',
              }}
            >
              Generate a public key on the left or paste a <code>vdpk_...</code> key above to
              launch the live widget preview.
            </div>
          )}
        </div>
      </div>

      {/* Existing Keys Table */}
      <div
        style={{
          padding: 20,
          borderRadius: 12,
          background: '#0F1623',
          border: '1px solid #1E2D45',
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>
          Issued Public Widget Keys ({keys.length})
        </div>

        {loading ? (
          <div style={{ fontSize: 13, color: '#94A3B8' }}>Loading public widget keys...</div>
        ) : keys.length === 0 ? (
          <div style={{ fontSize: 13, color: '#64748B' }}>
            No public widget keys have been created yet.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {keys.map((k) => (
              <div
                key={k.id}
                data-testid={`public-key-row-${k.id}`}
                style={{
                  padding: 14,
                  borderRadius: 10,
                  background: '#0A0E17',
                  border: '1px solid #1E2D45',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontWeight: 700, fontSize: 13.5 }}>{k.name}</span>
                    <code
                      style={{
                        fontSize: 11.5,
                        padding: '2px 7px',
                        borderRadius: 6,
                        background: '#141D2E',
                        color: '#93C5FD',
                      }}
                    >
                      {k.key_prefix}...
                    </code>
                    <span
                      data-testid={`public-key-status-${k.id}`}
                      style={{
                        fontSize: 11,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        padding: '2px 8px',
                        borderRadius: 999,
                        background:
                          k.status === 'active'
                            ? 'rgba(16,185,129,0.15)'
                            : 'rgba(239,68,68,0.15)',
                        color: k.status === 'active' ? '#34D399' : '#F87171',
                      }}
                    >
                      {k.status}
                    </span>
                  </div>

                  {k.status === 'active' && (
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        type="button"
                        data-testid={`edit-origins-btn-${k.id}`}
                        onClick={() => {
                          setEditingKeyId(k.id);
                          setEditingOriginsText(k.allowed_origins.join(', '));
                        }}
                        style={{
                          padding: '5px 10px',
                          borderRadius: 6,
                          border: '1px solid #334155',
                          background: '#1E293B',
                          color: '#E2E8F0',
                          fontSize: 11.5,
                          cursor: 'pointer',
                        }}
                      >
                        Edit Origins
                      </button>
                      <button
                        type="button"
                        data-testid={`rotate-key-btn-${k.id}`}
                        onClick={() => void handleRotateKey(k.id)}
                        style={{
                          padding: '5px 10px',
                          borderRadius: 6,
                          border: '1px solid rgba(59,130,246,0.4)',
                          background: 'rgba(59,130,246,0.14)',
                          color: '#60A5FA',
                          fontSize: 11.5,
                          fontWeight: 600,
                          cursor: 'pointer',
                        }}
                      >
                        Rotate Key
                      </button>
                      <button
                        type="button"
                        data-testid={`revoke-key-btn-${k.id}`}
                        onClick={() => void handleRevokeKey(k.id)}
                        style={{
                          padding: '5px 10px',
                          borderRadius: 6,
                          border: '1px solid rgba(239,68,68,0.4)',
                          background: 'rgba(239,68,68,0.14)',
                          color: '#F87171',
                          fontSize: 11.5,
                          fontWeight: 600,
                          cursor: 'pointer',
                        }}
                      >
                        Revoke
                      </button>
                    </div>
                  )}
                </div>

                <div style={{ fontSize: 12, color: '#94A3B8' }}>
                  Allowed Origins:{' '}
                  <strong style={{ color: '#E2E8F0' }}>{k.allowed_origins.join(', ')}</strong> ·
                  Rate Limit: {k.rate_limit_per_minute}/min · Session TTL: {k.session_ttl_seconds}s
                </div>

                {editingKeyId === k.id && (
                  <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                    <input
                      type="text"
                      value={editingOriginsText}
                      onChange={(e) => setEditingOriginsText(e.target.value)}
                      style={{
                        flex: 1,
                        padding: '6px 10px',
                        borderRadius: 6,
                        border: '1px solid #1E2D45',
                        background: '#0F1623',
                        color: '#F1F5F9',
                        fontSize: 12,
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => void handleSaveOrigins(k.id)}
                      style={{
                        padding: '6px 12px',
                        borderRadius: 6,
                        border: 'none',
                        background: '#2563EB',
                        color: '#FFFFFF',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingKeyId(null)}
                      style={{
                        padding: '6px 10px',
                        borderRadius: 6,
                        border: '1px solid #334155',
                        background: 'transparent',
                        color: '#94A3B8',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default WidgetSettings;
