/**
 * dashboard/src/components/calls/CallControlBar.tsx
 *
 * Operator controls for one live call: hold/resume, DTMF, transfer cancel and
 * a whisper to the human leg.
 *
 * The component is deliberately pessimistic. Every button stays disabled until
 * the server answers, because a control that flips to "paused" before the
 * carrier confirms it is a lie an operator will act on: they will start
 * discussing the customer, believing the customer cannot hear them.
 *
 * Capability flags come from the server on every response. When the active
 * provider cannot perform an action, the button is disabled and the reason is
 * shown inline (`UNSUPPORTED_CAPABILITY`) rather than hidden behind a click
 * that is guaranteed to fail.
 */

import React, { useMemo, useState } from 'react';
import { useCallControls } from '../../hooks/useCallControls';
import type { CallControlCapabilities } from '../../api/calls';

export const LIVE_CALL_STATUSES = ['in_progress', 'ringing'] as const;
export const IN_FLIGHT_TRANSFER_STATES = [
  'requested',
  'dialing',
  'connected',
] as const;

const DTMF_ALLOWED = /^[0-9#*wW]+$/;
const MAX_DTMF_DIGITS = 32;
const MAX_DTMF_ITEMS = 25;
const MAX_WHISPER_LENGTH = 500;

export interface CallControlBarProps {
  callId: string;
  /** The call's current status. Controls are disabled unless the call is live. */
  status: string;
  /** The call's current transfer state, used to gate cancel and whisper. */
  transferState?: string | null;
  /** Server-reported capabilities. Omitted until the first response arrives. */
  capabilities?: CallControlCapabilities | null;
  /** Bumped by the caller when the call row changes, to clear stale input. */
  readOnly?: boolean;
}

interface InlineNotice {
  tone: 'error' | 'info';
  text: string;
}

const EMPTY_CAPABILITIES: CallControlCapabilities = {
  pause: false,
  resume: false,
  dtmf: false,
  transfer_cancel: false,
  whisper: false,
  bulk_transfer: false,
};

function isLive(status: string): boolean {
  return (LIVE_CALL_STATUSES as readonly string[]).includes(
    (status || '').toLowerCase()
  );
}

function isTransferInFlight(transferState?: string | null): boolean {
  return (IN_FLIGHT_TRANSFER_STATES as readonly string[]).includes(
    (transferState || '').toLowerCase()
  );
}

export function CallControlBar({
  callId,
  status,
  transferState = null,
  capabilities = null,
  readOnly = false,
}: CallControlBarProps) {
  const controls = useCallControls({ callId });
  const [dtmfInput, setDtmfInput] = useState('');
  const [whisperInput, setWhisperInput] = useState('');

  const live = isLive(status);
  const transferInFlight = isTransferInFlight(transferState);
  const caps = capabilities ?? EMPTY_CAPABILITIES;

  // Every response from the server carries the capability set, so the newest
  // answer wins. Until one arrives the flags stay false: an unknown control is
  // treated as unavailable rather than available.
  const serverCaps = useMemo<CallControlCapabilities>(() => {
    const latest = [
      controls.pause.data?.capabilities,
      controls.resume.data?.capabilities,
      controls.sendDtmf.data?.capabilities,
      controls.cancelTransfer.data?.capabilities,
      controls.whisper.data?.capabilities,
    ].find((value) => Boolean(value));
    return latest ?? caps;
  }, [
    caps,
    controls.pause.data,
    controls.resume.data,
    controls.sendDtmf.data,
    controls.cancelTransfer.data,
    controls.whisper.data,
  ]);

  const paused = (status || '').toLowerCase() === 'paused';

  // The input holds several runs separated by spaces or commas, so validity is
  // decided per run, not on the whole string: "1 2" is two valid runs even
  // though the space between them is not a digit.
  const dtmfRuns = useMemo(
    () => dtmfInput.trim().split(/[,\s]+/).filter(Boolean),
    [dtmfInput]
  );
  const digitsValid =
    dtmfRuns.length > 0 &&
    dtmfRuns.length <= MAX_DTMF_ITEMS &&
    dtmfRuns.every(
      (run) => run.length > 0 && run.length <= MAX_DTMF_DIGITS && DTMF_ALLOWED.test(run)
    );
  const dtmfItems = useMemo(
    () => (digitsValid ? dtmfRuns.map((value) => ({ digits: value })) : []),
    [dtmfRuns, digitsValid]
  );

  const whisperText = whisperInput.trim();
  const whisperValid =
    whisperText.length > 0 && whisperText.length <= MAX_WHISPER_LENGTH;

  const notice: InlineNotice | null = controls.lastError
    ? {
        tone: 'error',
        text: `${controls.lastError.code}: ${controls.lastError.message}`,
      }
    : !live
      ? { tone: 'info', text: 'Controls are available while the call is live.' }
      : null;

  const unsupported = (label: string) =>
    live
      ? {
          disabled: true,
          title: `The active provider has no verified ${label} operation (UNSUPPORTED_CAPABILITY).`,
        }
      : { disabled: true, title: 'Controls are available while the call is live.' };

  return (
    <section
      data-testid="call-control-bar"
      aria-label="Live call controls"
      style={{
        padding: 16,
        borderRadius: 12,
        background: '#0F1623',
        border: '1px solid #1E2D45',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>
          Live call controls
        </h2>
        <span style={{ fontSize: 12, color: '#94A3B8' }}>
          status: <code>{status}</code>
          {transferState ? (
            <>
              {' · transfer: '}
              <code>{transferState}</code>
            </>
          ) : null}
        </span>
      </header>

      {notice ? (
        <div
          data-testid="call-control-notice"
          role={notice.tone === 'error' ? 'alert' : 'status'}
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            fontSize: 12,
            background: notice.tone === 'error' ? '#3F1D1D' : '#132033',
            border: `1px solid ${notice.tone === 'error' ? '#7F1D1D' : '#1E2D45'}`,
            color: notice.tone === 'error' ? '#FCA5A5' : '#94A3B8',
          }}
        >
          {notice.text}
        </div>
      ) : null}

      <div
        style={{
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <button
          type="button"
          data-testid="call-control-pause"
          aria-disabled={!live || paused || !serverCaps.pause || controls.isBusy}
          disabled={!live || paused || !serverCaps.pause || controls.isBusy}
          {...(!serverCaps.pause ? unsupported('pause') : {})}
          onClick={() => controls.pause.mutate({ reason: 'operator_hold' })}
          style={buttonStyle}
        >
          {controls.pause.isPending ? 'Pausing…' : 'Pause (hold)'}
        </button>

        <button
          type="button"
          data-testid="call-control-resume"
          aria-disabled={!live || !paused || !serverCaps.resume || controls.isBusy}
          disabled={!live || !paused || !serverCaps.resume || controls.isBusy}
          {...(!serverCaps.resume ? unsupported('resume') : {})}
          onClick={() => controls.resume.mutate({ reason: 'operator_resume' })}
          style={buttonStyle}
        >
          {controls.resume.isPending ? 'Resuming…' : 'Resume'}
        </button>

        <button
          type="button"
          data-testid="call-control-transfer-cancel"
          aria-disabled={
            !live || !transferInFlight || !serverCaps.transfer_cancel || controls.isBusy
          }
          disabled={
            !live || !transferInFlight || !serverCaps.transfer_cancel || controls.isBusy
          }
          {...(!serverCaps.transfer_cancel ? unsupported('transfer cancel') : {})}
          onClick={() =>
            controls.cancelTransfer.mutate({ reason: 'operator_cancelled' })
          }
          style={buttonStyle}
        >
          {controls.cancelTransfer.isPending ? 'Cancelling…' : 'Cancel transfer'}
        </button>
      </div>

      {readOnly ? (
        <p style={{ margin: 0, fontSize: 12, color: '#94A3B8' }}>
          This call is read-only for you.
        </p>
      ) : null}

      <form
        data-testid="call-control-dtmf-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!digitsValid || dtmfItems.length === 0 || !serverCaps.dtmf) return;
          controls.sendDtmf.mutate({ items: dtmfItems });
          setDtmfInput('');
        }}
        style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}
      >
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
          <span style={{ fontSize: 12, color: '#94A3B8' }}>
            DTMF digits (0-9 # * w; separate runs with a space)
          </span>
          <input
            type="text"
            data-testid="call-control-dtmf-input"
            value={dtmfInput}
            onChange={(event) => setDtmfInput(event.target.value)}
            placeholder="1 w 2#"
            maxLength={MAX_DTMF_DIGITS * 2}
            disabled={!live || controls.isBusy}
            style={inputStyle}
          />
        </label>
        <button
          type="submit"
          data-testid="call-control-dtmf-send"
          aria-disabled={
            !live || !digitsValid || !serverCaps.dtmf || controls.isBusy
          }
          disabled={!live || !digitsValid || !serverCaps.dtmf || controls.isBusy}
          {...(!serverCaps.dtmf ? unsupported('DTMF') : {})}
          style={buttonStyle}
        >
          {controls.sendDtmf.isPending ? 'Sending…' : 'Send digits'}
        </button>
      </form>

      {controls.sendDtmf.data ? (
        <p
          data-testid="call-control-dtmf-result"
          style={{ margin: 0, fontSize: 12, color: '#94A3B8' }}
        >
          Sent {controls.sendDtmf.data.sent} of{' '}
          {controls.sendDtmf.data.items.length} digit run(s);
          {controls.sendDtmf.data.rejected} rejected.
        </p>
      ) : null}

      <form
        data-testid="call-control-whisper-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!whisperValid || !serverCaps.whisper) return;
          controls.whisper.mutate({ message: whisperText });
          setWhisperInput('');
        }}
        style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}
      >
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
          <span style={{ fontSize: 12, color: '#94A3B8' }}>
            Whisper to the human agent (never spoken to the caller)
          </span>
          <input
            type="text"
            data-testid="call-control-whisper-input"
            value={whisperInput}
            onChange={(event) => setWhisperInput(event.target.value)}
            placeholder="Customer is on a callback; keep it short."
            maxLength={MAX_WHISPER_LENGTH}
            disabled={!live || !transferInFlight || controls.isBusy}
            style={inputStyle}
          />
        </label>
        <button
          type="submit"
          data-testid="call-control-whisper-send"
          aria-disabled={
            !live || !transferInFlight || !whisperValid || controls.isBusy
          }
          disabled={
            !live || !transferInFlight || !whisperValid || controls.isBusy
          }
          style={buttonStyle}
        >
          {controls.whisper.isPending ? 'Whispering…' : 'Whisper'}
        </button>
      </form>

      {controls.whisper.data ? (
        <p
          data-testid="call-control-whisper-result"
          style={{ margin: 0, fontSize: 12, color: '#94A3B8' }}
        >
          Delivered: “{controls.whisper.data.message}”
        </p>
      ) : null}
    </section>
  );
}

const buttonStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderRadius: 8,
  border: '1px solid #1E2D45',
  background: '#132033',
  color: '#E2E8F0',
  fontSize: 13,
  cursor: 'pointer',
};

const inputStyle: React.CSSProperties = {
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid #1E2D45',
  background: '#0B1220',
  color: '#E2E8F0',
  fontSize: 13,
  minWidth: 220,
};

export default CallControlBar;
