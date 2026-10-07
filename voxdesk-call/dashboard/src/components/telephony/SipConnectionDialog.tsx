/**
 * dashboard/src/components/telephony/SipConnectionDialog.tsx
 * SIP Trunk / Connection configuration and OPTIONS connectivity verification component.
 */

import React, { useState } from 'react';
import {
  SipConnectionCreatePayload,
  SipConnectionRecord,
  SipTransportProtocol,
} from '../../lib/telephonyApi';

export interface SipConnectionDialogProps {
  connections: SipConnectionRecord[];
  onCreateConnection: (
    payload: SipConnectionCreatePayload
  ) => Promise<SipConnectionRecord>;
  onTestConnection: (sipConnectionId: string) => Promise<SipConnectionRecord>;
}

export const SipConnectionDialog: React.FC<SipConnectionDialogProps> = ({
  connections,
  onCreateConnection,
  onTestConnection,
}) => {
  const [name, setName] = useState('Primary Enterprise SIP Trunk');
  const [terminationUri, setTerminationUri] = useState('sip:pstn.carrier.example.com:5061');
  const [originationUri, setOriginationUri] = useState('sip:ingress.voxdesk.example.com:5061');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [username, setUsername] = useState('');
  const [passwordSecret, setPasswordSecret] = useState('');
  const [transport, setTransport] = useState<SipTransportProtocol>('TLS');
  const [submitting, setSubmitting] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setFeedback(null);
    try {
      const created = await onCreateConnection({
        name: name.trim() || 'Primary SIP Trunk',
        provider: 'SIP',
        termination_uri: terminationUri.trim(),
        origination_uri: originationUri.trim() || null,
        phone_number: phoneNumber.trim() || null,
        username: username.trim() || null,
        password_secret: passwordSecret || null,
        transport,
      });
      setPasswordSecret('');
      setFeedback(
        `SIP connection "${created.name}" saved with status ${created.status}.`
      );
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to create SIP connection.'
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleTest = async (connId: string) => {
    setTestingId(connId);
    setError(null);
    setFeedback(null);
    try {
      const updated = await onTestConnection(connId);
      setFeedback(
        `SIP connection "${updated.name}" verified: status=${updated.status}.`
      );
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'SIP OPTIONS probe failed.'
      );
    } finally {
      setTestingId(null);
    }
  };

  return (
    <div
      data-testid="sip-connection-dialog"
      style={{
        background: '#0f172a',
        borderRadius: '12px',
        border: '1px solid #1e293b',
        padding: '20px',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '16px',
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: '16px', color: '#f8fafc' }}>
            SIP Trunking & BYOC Connections
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#94a3b8' }}>
            Configure RFC 3261 SIP termination/origination URIs. Credentials are stored as SHA-256 secret references.
          </p>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            marginBottom: '14px',
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'rgba(239, 68, 68, 0.14)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#f87171',
            fontSize: '13px',
          }}
        >
          {error}
        </div>
      )}

      {feedback && (
        <div
          style={{
            marginBottom: '14px',
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'rgba(16, 185, 129, 0.14)',
            border: '1px solid rgba(16, 185, 129, 0.35)',
            color: '#34d399',
            fontSize: '13px',
          }}
        >
          {feedback}
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
          gap: '12px',
          marginBottom: '20px',
        }}
      >
        <div>
          <label
            htmlFor="sip-trunk-name"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Trunk Name
          </label>
          <input
            id="sip-trunk-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div>
          <label
            htmlFor="sip-termination-uri"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Termination SIP URI
          </label>
          <input
            id="sip-termination-uri"
            type="text"
            value={terminationUri}
            onChange={(e) => setTerminationUri(e.target.value)}
            placeholder="sip:pstn.carrier.example.com:5061"
            required
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div>
          <label
            htmlFor="sip-origination-uri"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Origination SIP URI (Optional)
          </label>
          <input
            id="sip-origination-uri"
            type="text"
            value={originationUri}
            onChange={(e) => setOriginationUri(e.target.value)}
            placeholder="sip:ingress.voxdesk.example.com:5061"
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div>
          <label
            htmlFor="sip-transport"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Transport Protocol
          </label>
          <select
            id="sip-transport"
            value={transport}
            onChange={(e) => setTransport(e.target.value as SipTransportProtocol)}
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          >
            <option value="TLS">TLS (Encrypted SIP)</option>
            <option value="TCP">TCP</option>
            <option value="UDP">UDP</option>
            <option value="WSS">WSS (WebSocket Secure)</option>
          </select>
        </div>

        <div>
          <label
            htmlFor="sip-phone-e164"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Associated E.164 Number
          </label>
          <input
            id="sip-phone-e164"
            type="text"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            placeholder="+14155550199"
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div>
          <label
            htmlFor="sip-auth-username"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Digest Auth Username
          </label>
          <input
            id="sip-auth-username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="sip_trunk_user"
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div>
          <label
            htmlFor="sip-auth-secret"
            style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}
          >
            Digest Auth Secret (Hashed)
          </label>
          <input
            id="sip-auth-secret"
            type="password"
            value={passwordSecret}
            onChange={(e) => setPasswordSecret(e.target.value)}
            placeholder="••••••••••••"
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: '6px',
              border: '1px solid #334155',
              background: '#020617',
              color: '#f8fafc',
              fontSize: '13px',
            }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button
            type="submit"
            disabled={submitting}
            style={{
              width: '100%',
              padding: '9px 14px',
              borderRadius: '6px',
              border: 'none',
              background: '#6366f1',
              color: '#ffffff',
              fontSize: '13px',
              fontWeight: 600,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? 'Saving SIP Trunk...' : 'Save SIP Connection'}
          </button>
        </div>
      </form>

      {connections.length > 0 && (
        <div
          style={{
            borderTop: '1px solid #1e293b',
            paddingTop: '14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}
        >
          {connections.map((conn) => (
            <div
              key={conn.id}
              data-testid={`sip-conn-${conn.id}`}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 14px',
                borderRadius: '8px',
                background: '#020617',
                border: '1px solid #1e293b',
                fontSize: '13px',
              }}
            >
              <div>
                <span style={{ fontWeight: 600, color: '#f8fafc' }}>{conn.name}</span>
                <span
                  style={{
                    marginLeft: '10px',
                    fontFamily: 'monospace',
                    color: '#94a3b8',
                    fontSize: '12px',
                  }}
                >
                  {conn.termination_uri} ({conn.transport})
                </span>
                {conn.credential_reference && (
                  <span
                    style={{
                      marginLeft: '8px',
                      fontSize: '11px',
                      color: '#64748b',
                    }}
                  >
                    ref: {conn.credential_reference.slice(0, 18)}...
                  </span>
                )}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span
                  style={{
                    padding: '2px 8px',
                    borderRadius: '999px',
                    fontSize: '11px',
                    fontWeight: 700,
                    background:
                      conn.status === 'READY'
                        ? 'rgba(16, 185, 129, 0.16)'
                        : conn.status === 'FAILED'
                        ? 'rgba(239, 68, 68, 0.16)'
                        : 'rgba(59, 130, 246, 0.16)',
                    color:
                      conn.status === 'READY'
                        ? '#34d399'
                        : conn.status === 'FAILED'
                        ? '#f87171'
                        : '#60a5fa',
                  }}
                >
                  {conn.status}
                </span>
                <button
                  type="button"
                  disabled={testingId === conn.id}
                  onClick={() => handleTest(conn.id)}
                  style={{
                    padding: '5px 10px',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    background: '#1e293b',
                    color: '#e2e8f0',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  {testingId === conn.id ? 'Testing...' : 'Verify SIP'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default SipConnectionDialog;
