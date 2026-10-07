/**
 * dashboard/src/features/public-widget/PublicWidget.tsx
 * Embeddable & previewable Public Web Widget operating strictly on a scoped
 * PublicWidgetKey (vdpk_*) and short-lived session token (vdws_*).
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  endPublicWidgetSession,
  fetchPublicWidgetBootstrap,
  PublicWidgetApiError,
  sendPublicWidgetMessage,
  startPublicWidgetSession,
} from '../../api/public-widget';
import type {
  PublicWidgetBootstrapConfig,
  PublicWidgetSessionMode,
  PublicWidgetSessionRecord,
  PublicWidgetTurnRecord,
  PublicWidgetUiState,
} from '../../api/types/public-widget';
import { WidgetSession } from './WidgetSession';

export interface PublicWidgetProps {
  publicKey?: string;
  origin?: string;
  defaultOpen?: boolean;
  inline?: boolean;
  visitorId?: string;
  onSessionChange?: (session: PublicWidgetSessionRecord | null) => void;
}

function mapErrorToUiState(err: unknown): {
  uiState: PublicWidgetUiState;
  message: string;
} {
  if (err instanceof PublicWidgetApiError) {
    switch (err.code) {
      case 'FORBIDDEN_ORIGIN':
      case 'MISSING_ORIGIN':
        return { uiState: 'FORBIDDEN_ORIGIN', message: err.message };
      case 'INVALID_PUBLIC_KEY':
      case 'MISSING_PUBLIC_KEY':
      case 'KEY_REVOKED':
      case 'KEY_ROTATED':
      case 'KEY_EXPIRED':
      case 'AGENT_NOT_PUBLISHED':
      case 'AGENT_NOT_FOUND':
        return { uiState: 'INVALID_PUBLIC_KEY', message: err.message };
      case 'RATE_LIMIT_EXCEEDED':
        return { uiState: 'RATE_LIMITED', message: err.message };
      case 'SESSION_EXPIRED':
        return { uiState: 'EXPIRED', message: err.message };
      case 'NOT_CONFIGURED':
        return { uiState: 'NOT_CONFIGURED', message: err.message };
      default:
        return { uiState: 'ERROR', message: err.message };
    }
  }
  const fallback = err instanceof Error ? err.message : 'Unexpected widget error';
  return { uiState: 'ERROR', message: fallback };
}

export function PublicWidget({
  publicKey: initialPublicKey = '',
  origin,
  defaultOpen = true,
  inline = false,
  visitorId,
  onSessionChange,
}: PublicWidgetProps) {
  const [isOpen, setIsOpen] = useState<boolean>(defaultOpen);
  const [activeKey, setActiveKey] = useState<string>(initialPublicKey);
  const [uiState, setUiState] = useState<PublicWidgetUiState>('IDLE');
  const [bootstrap, setBootstrap] = useState<PublicWidgetBootstrapConfig | null>(null);
  const [session, setSession] = useState<PublicWidgetSessionRecord | null>(null);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<PublicWidgetTurnRecord[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sendingMessage, setSendingMessage] = useState<boolean>(false);

  useEffect(() => {
    if (initialPublicKey && initialPublicKey !== activeKey) {
      setActiveKey(initialPublicKey);
    }
  }, [initialPublicKey, activeKey]);

  const validateAndBootstrap = useCallback(
    async (keyToValidate: string) => {
      const trimmed = keyToValidate.trim();
      if (!trimmed) {
        setUiState('INVALID_PUBLIC_KEY');
        setErrorMessage('A valid VoxDesk public key (vdpk_...) is required.');
        return null;
      }
      setUiState('VALIDATING_KEY');
      setErrorMessage(null);
      try {
        const cfg = await fetchPublicWidgetBootstrap({
          publicKey: trimmed,
          origin:
            origin ||
            (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000'),
        });
        setBootstrap(cfg);
        setUiState('READY');
        return cfg;
      } catch (err) {
        const mapped = mapErrorToUiState(err);
        setBootstrap(null);
        setUiState(mapped.uiState);
        setErrorMessage(mapped.message);
        return null;
      }
    },
    [origin],
  );

  useEffect(() => {
    if (isOpen && activeKey.trim() && uiState === 'IDLE') {
      void validateAndBootstrap(activeKey);
    }
  }, [isOpen, activeKey, uiState, validateAndBootstrap]);

  const handleStartSession = async (mode: PublicWidgetSessionMode) => {
    const trimmed = activeKey.trim();
    if (!trimmed) {
      setUiState('INVALID_PUBLIC_KEY');
      setErrorMessage('Public key is required before starting a widget session.');
      return;
    }

    setUiState('CONNECTING');
    setErrorMessage(null);
    try {
      const started = await startPublicWidgetSession({
        publicKey: trimmed,
        mode,
        visitorId,
        origin:
          origin ||
          (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000'),
      });
      setSession(started);
      setSessionToken(started.session_token || null);
      setTranscript(started.transcript || []);
      onSessionChange?.(started);

      if (started.status === 'not_configured') {
        setUiState('NOT_CONFIGURED');
        setErrorMessage(
          started.error_message ||
            'Live WebRTC/SIP voice transport is not configured for this workspace.',
        );
      } else {
        setUiState('CONNECTED');
      }
    } catch (err) {
      const mapped = mapErrorToUiState(err);
      setUiState(mapped.uiState);
      setErrorMessage(mapped.message);
    }
  };

  const handleSendMessage = async (message: string) => {
    if (!session || !sessionToken) return;
    setSendingMessage(true);
    setUiState('SPEAKING');
    setErrorMessage(null);
    try {
      const res = await sendPublicWidgetMessage({
        sessionId: session.session_id,
        sessionToken,
        message,
      });
      setTranscript(res.transcript);
      const updated: PublicWidgetSessionRecord = {
        ...session,
        turns_count: res.turns_count,
        transcript: res.transcript,
      };
      setSession(updated);
      onSessionChange?.(updated);
      setUiState('LISTENING');
    } catch (err) {
      const mapped = mapErrorToUiState(err);
      setUiState(mapped.uiState);
      setErrorMessage(mapped.message);
    } finally {
      setSendingMessage(false);
    }
  };

  const handleEndSession = async () => {
    if (!session || !sessionToken) {
      setUiState('ENDED');
      return;
    }
    try {
      const ended = await endPublicWidgetSession({
        sessionId: session.session_id,
        sessionToken,
        reason: 'visitor_ended',
      });
      setSession(ended);
      setSessionToken(null);
      setUiState('ENDED');
      onSessionChange?.(ended);
    } catch (err) {
      const mapped = mapErrorToUiState(err);
      setUiState(mapped.uiState);
      setErrorMessage(mapped.message);
    }
  };

  const handleReset = () => {
    setSession(null);
    setSessionToken(null);
    setTranscript([]);
    setErrorMessage(null);
    if (bootstrap) {
      setUiState('READY');
    } else if (activeKey.trim()) {
      void validateAndBootstrap(activeKey);
    } else {
      setUiState('IDLE');
    }
  };

  const primaryColor = bootstrap?.appearance?.primary_color || '#2563EB';
  const widgetTitle = bootstrap?.appearance?.title || 'VoxDesk AI Concierge';
  const widgetSubtitle =
    bootstrap?.appearance?.subtitle || 'Pinned published agent · Origin-verified public widget';

  if (!inline && !isOpen) {
    return (
      <button
        type="button"
        data-testid="public-widget-launcher-btn"
        onClick={() => setIsOpen(true)}
        style={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          zIndex: 999,
          padding: '12px 18px',
          borderRadius: 999,
          border: 'none',
          background: primaryColor,
          color: '#FFFFFF',
          fontWeight: 700,
          fontSize: 13.5,
          boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
          cursor: 'pointer',
        }}
      >
        {widgetTitle}
      </button>
    );
  }

  return (
    <div
      data-testid="public-widget-root"
      style={
        inline
          ? {
              width: '100%',
              maxWidth: 460,
              background: '#0A0E17',
              border: '1px solid #1E2D45',
              borderRadius: 16,
              padding: 18,
              color: '#F1F5F9',
              boxShadow: '0 12px 32px rgba(0,0,0,0.35)',
            }
          : {
              position: 'fixed',
              right: 24,
              bottom: 24,
              zIndex: 999,
              width: 390,
              maxWidth: 'calc(100vw - 32px)',
              background: '#0A0E17',
              border: '1px solid #1E2D45',
              borderRadius: 16,
              padding: 18,
              color: '#F1F5F9',
              boxShadow: '0 18px 48px rgba(0,0,0,0.55)',
            }
      }
    >
      {/* Top bar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 12,
          marginBottom: 14,
        }}
      >
        <div>
          <div
            data-testid="public-widget-title"
            style={{ fontSize: 15, fontWeight: 700, color: '#F8FAFC' }}
          >
            {widgetTitle}
          </div>
          <div style={{ fontSize: 12, color: '#94A3B8', marginTop: 2 }}>{widgetSubtitle}</div>
        </div>
        {!inline && (
          <button
            type="button"
            data-testid="public-widget-minimize-btn"
            onClick={() => setIsOpen(false)}
            style={{
              background: 'transparent',
              border: '1px solid #1E2D45',
              color: '#94A3B8',
              borderRadius: 6,
              padding: '3px 8px',
              fontSize: 11,
              cursor: 'pointer',
            }}
          >
            Minimize
          </button>
        )}
      </div>

      {/* Bootstrap metadata bar when key is validated */}
      {bootstrap && (
        <div
          data-testid="public-widget-bootstrap-meta"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            padding: '8px 10px',
            borderRadius: 8,
            background: '#0F1623',
            border: '1px solid #1E2D45',
            marginBottom: 12,
            fontSize: 11,
            color: '#94A3B8',
          }}
        >
          <span>
            Agent: <strong style={{ color: '#E2E8F0' }}>{bootstrap.agent_name}</strong>
          </span>
          <span>·</span>
          <span>
            Version: <strong style={{ color: '#38BDF8' }}>v{bootstrap.published_version_number}</strong>
          </span>
          <span>·</span>
          <span>
            Key: <code>{bootstrap.public_key_prefix}...</code>
          </span>
          <span>·</span>
          <span data-testid="public-widget-voice-transport-status">
            Voice: {bootstrap.voice_transport_configured ? 'WebRTC Ready' : 'NOT_CONFIGURED'}
          </span>
        </div>
      )}

      {/* Mode launcher buttons when READY or before active session */}
      {!session && uiState === 'READY' && bootstrap && (
        <div
          data-testid="public-widget-mode-actions"
          style={{
            display: 'flex',
            gap: 10,
            marginBottom: 12,
          }}
        >
          {bootstrap.appearance.enable_chat && (
            <button
              type="button"
              data-testid="public-widget-start-chat-btn"
              onClick={() => void handleStartSession('chat')}
              style={{
                flex: 1,
                padding: '10px 14px',
                borderRadius: 8,
                border: 'none',
                background: primaryColor,
                color: '#FFFFFF',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Start Chat Session
            </button>
          )}
          {bootstrap.appearance.enable_voice && (
            <button
              type="button"
              data-testid="public-widget-start-voice-btn"
              onClick={() => void handleStartSession('voice')}
              style={{
                flex: 1,
                padding: '10px 14px',
                borderRadius: 8,
                border: '1px solid #1E2D45',
                background: '#141D2E',
                color: '#E2E8F0',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Start Web Call
            </button>
          )}
        </div>
      )}

      {/* Session / state view */}
      <WidgetSession
        uiState={uiState}
        session={session}
        transcript={transcript}
        errorMessage={errorMessage}
        sendingMessage={sendingMessage}
        onSendMessage={handleSendMessage}
        onEndSession={handleEndSession}
        onSwitchMode={handleStartSession}
        onReset={handleReset}
      />
    </div>
  );
}

export default PublicWidget;
