/**
 * dashboard/src/components/telephony/PhoneNumberTable.tsx
 * Interactive E.164 Phone Number table with inbound/outbound agent binding,
 * SIP trunk indicator, status badges, and deletion controls.
 */

import React, { useState } from 'react';
import {
  PhoneNumberRecord,
  PhoneNumberBindAgentPayload,
} from '../../lib/telephonyApi';

export interface AgentSummaryOption {
  id: string;
  name: string;
  status?: string;
}

export interface PhoneNumberTableProps {
  numbers: PhoneNumberRecord[];
  agents: AgentSummaryOption[];
  loading?: boolean;
  onBindAgent: (
    phoneNumberId: string,
    payload: PhoneNumberBindAgentPayload
  ) => Promise<void>;
  onDeleteNumber: (phoneNumberId: string) => Promise<void>;
  onSelectForCall?: (numberRecord: PhoneNumberRecord) => void;
}

function statusBadgeStyle(status: string): React.CSSProperties {
  const upper = (status || '').toUpperCase();
  if (upper === 'READY' || upper === 'ACTIVE') {
    return {
      background: 'rgba(16, 185, 129, 0.14)',
      color: '#34d399',
      border: '1px solid rgba(16, 185, 129, 0.35)',
    };
  }
  if (upper === 'CONFIGURED' || upper === 'PROVISIONING') {
    return {
      background: 'rgba(59, 130, 246, 0.14)',
      color: '#60a5fa',
      border: '1px solid rgba(59, 130, 246, 0.35)',
    };
  }
  if (upper === 'FAILED' || upper === 'SUSPENDED' || upper === 'DISCONNECTED') {
    return {
      background: 'rgba(239, 68, 68, 0.14)',
      color: '#f87171',
      border: '1px solid rgba(239, 68, 68, 0.35)',
    };
  }
  return {
    background: 'rgba(148, 163, 184, 0.14)',
    color: '#94a3b8',
    border: '1px solid rgba(148, 163, 184, 0.3)',
  };
}

export const PhoneNumberTable: React.FC<PhoneNumberTableProps> = ({
  numbers,
  agents,
  loading = false,
  onBindAgent,
  onDeleteNumber,
  onSelectForCall,
}) => {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleAgentChange = async (
    row: PhoneNumberRecord,
    direction: 'inbound' | 'outbound',
    agentId: string
  ) => {
    setBusyId(row.id);
    setErrorMsg(null);
    try {
      await onBindAgent(row.id, {
        inbound_agent_id:
          direction === 'inbound' ? agentId || null : row.inbound_agent_id,
        outbound_agent_id:
          direction === 'outbound' ? agentId || null : row.outbound_agent_id,
      });
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Failed to update agent binding.'
      );
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string) => {
    setBusyId(id);
    setErrorMsg(null);
    try {
      await onDeleteNumber(id);
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Failed to release phone number.'
      );
    } finally {
      setBusyId(null);
    }
  };

  if (loading && numbers.length === 0) {
    return (
      <div
        data-testid="phone-number-table-loading"
        style={{
          padding: '28px',
          textAlign: 'center',
          color: '#94a3b8',
          background: '#0f172a',
          borderRadius: '12px',
          border: '1px solid #1e293b',
        }}
      >
        Loading E.164 phone numbers...
      </div>
    );
  }

  return (
    <div
      data-testid="phone-number-table"
      style={{
        background: '#0f172a',
        borderRadius: '12px',
        border: '1px solid #1e293b',
        overflow: 'hidden',
      }}
    >
      {errorMsg && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            background: 'rgba(239, 68, 68, 0.12)',
            borderBottom: '1px solid rgba(239, 68, 68, 0.3)',
            color: '#f87171',
            fontSize: '13px',
          }}
        >
          {errorMsg}
        </div>
      )}

      {numbers.length === 0 ? (
        <div
          data-testid="phone-number-empty-state"
          style={{
            padding: '36px 24px',
            textAlign: 'center',
            color: '#94a3b8',
          }}
        >
          <div style={{ fontSize: '15px', fontWeight: 600, color: '#e2e8f0' }}>
            No E.164 phone numbers configured yet
          </div>
          <div style={{ fontSize: '13px', marginTop: '6px' }}>
            Provision or attach a number above and bind an inbound or outbound agent to begin handling calls.
          </div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '13px',
              color: '#e2e8f0',
            }}
          >
            <thead>
              <tr
                style={{
                  background: '#020617',
                  borderBottom: '1px solid #1e293b',
                  textAlign: 'left',
                  color: '#94a3b8',
                  fontSize: '12px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                <th style={{ padding: '12px 16px' }}>E.164 Number</th>
                <th style={{ padding: '12px 16px' }}>Provider / SIP</th>
                <th style={{ padding: '12px 16px' }}>Status</th>
                <th style={{ padding: '12px 16px' }}>Inbound Agent</th>
                <th style={{ padding: '12px 16px' }}>Outbound Agent</th>
                <th style={{ padding: '12px 16px', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {numbers.map((item) => {
                const isBusy = busyId === item.id;
                return (
                  <tr
                    key={item.id}
                    data-testid={`phone-row-${item.e164_number}`}
                    style={{
                      borderBottom: '1px solid #1e293b',
                    }}
                  >
                    <td style={{ padding: '14px 16px', fontWeight: 600, fontFamily: 'monospace', fontSize: '14px' }}>
                      {item.e164_number}
                      <div style={{ fontSize: '11px', color: '#64748b', fontFamily: 'sans-serif', fontWeight: 400 }}>
                        ID: {item.id.slice(0, 8)}
                      </div>
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <span
                        style={{
                          padding: '3px 8px',
                          borderRadius: '6px',
                          background: '#1e293b',
                          color: '#cbd5e1',
                          fontSize: '12px',
                          fontWeight: 600,
                        }}
                      >
                        {item.provider}
                      </span>
                      {item.sip_enabled && (
                        <span
                          style={{
                            marginLeft: '6px',
                            padding: '3px 8px',
                            borderRadius: '6px',
                            background: 'rgba(139, 92, 246, 0.16)',
                            color: '#c4b5fd',
                            fontSize: '11px',
                          }}
                        >
                          SIP Trunk
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <span
                        data-testid={`phone-status-${item.e164_number}`}
                        style={{
                          display: 'inline-block',
                          padding: '3px 10px',
                          borderRadius: '999px',
                          fontSize: '11px',
                          fontWeight: 700,
                          ...statusBadgeStyle(item.status),
                        }}
                      >
                        {item.status}
                      </span>
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <select
                        aria-label={`Inbound Agent for ${item.e164_number}`}
                        disabled={isBusy}
                        value={item.inbound_agent_id || ''}
                        onChange={(e) =>
                          handleAgentChange(item, 'inbound', e.target.value)
                        }
                        style={{
                          background: '#020617',
                          color: '#e2e8f0',
                          border: '1px solid #334155',
                          borderRadius: '6px',
                          padding: '6px 10px',
                          fontSize: '12px',
                          minWidth: '170px',
                        }}
                      >
                        <option value="">-- Unbound --</option>
                        {agents.map((ag) => (
                          <option key={ag.id} value={ag.id}>
                            {ag.name} ({ag.id.slice(0, 8)})
                          </option>
                        ))}
                      </select>
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <select
                        aria-label={`Outbound Agent for ${item.e164_number}`}
                        disabled={isBusy}
                        value={item.outbound_agent_id || ''}
                        onChange={(e) =>
                          handleAgentChange(item, 'outbound', e.target.value)
                        }
                        style={{
                          background: '#020617',
                          color: '#e2e8f0',
                          border: '1px solid #334155',
                          borderRadius: '6px',
                          padding: '6px 10px',
                          fontSize: '12px',
                          minWidth: '170px',
                        }}
                      >
                        <option value="">-- Unbound --</option>
                        {agents.map((ag) => (
                          <option key={ag.id} value={ag.id}>
                            {ag.name} ({ag.id.slice(0, 8)})
                          </option>
                        ))}
                      </select>
                    </td>
                    <td style={{ padding: '14px 16px', textAlign: 'right' }}>
                      <div style={{ display: 'inline-flex', gap: '8px' }}>
                        {onSelectForCall && (
                          <button
                            type="button"
                            onClick={() => onSelectForCall(item)}
                            style={{
                              padding: '6px 12px',
                              borderRadius: '6px',
                              border: '1px solid rgba(59, 130, 246, 0.4)',
                              background: 'rgba(59, 130, 246, 0.14)',
                              color: '#60a5fa',
                              fontSize: '12px',
                              fontWeight: 600,
                              cursor: 'pointer',
                            }}
                          >
                            Dial From
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleDelete(item.id)}
                          style={{
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid rgba(239, 68, 68, 0.35)',
                            background: 'rgba(239, 68, 68, 0.1)',
                            color: '#f87171',
                            fontSize: '12px',
                            fontWeight: 600,
                            cursor: isBusy ? 'not-allowed' : 'pointer',
                          }}
                        >
                          Release
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PhoneNumberTable;
