"use client";

import { useEffect, useState } from "react";
import type { SoftphoneCall } from "../types";
import styles from "../agent-desktop.module.css";
import { DtmfPad } from "./DtmfPad";
import { TransferControls } from "./TransferControls";

type Props = {
  call: SoftphoneCall;
  dtmf: string;
  transferDestination: string;
  busy: boolean;
  canAnswer?: boolean;
  audioLevel?: number | null;
  onDtmfChange: (value: string) => void;
  onSendDtmf: () => void;
  onTransferDestinationChange: (value: string) => void;
  onTransfer: (mode: "blind" | "warm") => void;
  onFinishTransfer: (action: "complete" | "cancel") => void;
  onControl: (action: "hold" | "mute", enabled: boolean) => void;
  onResume: () => void;
  onEnd: () => void;
  onAnswer?: () => void;
  onRecordPause: (paused: boolean) => void;
  onConference: () => void;
};

export function SoftphoneDock(props: Props) {
  const { call, busy } = props;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [call.id]);
  const elapsed = call.answered_at ? Math.max(0, Math.floor((now - new Date(call.answered_at).getTime()) / 1000)) : null;
  const timer = elapsed === null ? "Not answered" : `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  return <article className={`${styles.card} ${styles.dock}`}>
    <div className={styles.rowBetween}><h2>Softphone · {call.id.slice(0, 8)}</h2><span role="timer">{timer}</span></div>
    <p className={styles.muted}>{call.status} · {call.origin} · {props.audioLevel == null ? "Audio meter unavailable for this transport" : `Audio level ${Math.round(props.audioLevel * 100)}%`}</p>
    <div className={styles.buttonRow}>
      {props.canAnswer && <button className={styles.button} disabled={busy} onClick={props.onAnswer}>Answer</button>}
      {props.canAnswer && <button className={styles.buttonDanger} disabled={busy} onClick={props.onEnd}>Decline</button>}
      {call.on_hold
        ? <button className={styles.buttonSecondary} disabled={busy} onClick={props.onResume}>Resume</button>
        : <button className={styles.buttonSecondary} disabled={busy || !call.agent_joined_at} onClick={() => props.onControl("hold", true)}>Hold</button>}
      <button className={styles.buttonSecondary} disabled={busy || !call.agent_joined_at} onClick={() => props.onControl("mute", !call.muted)}>{call.muted ? "Unmute" : "Mute"}</button>
      {!props.canAnswer && <button className={styles.buttonDanger} disabled={busy} onClick={props.onEnd}>End call</button>}
      {call.recording_paused == null
        ? <button className={styles.buttonSecondary} disabled title="The provider has not reported a verifiable recording state">Recording state unavailable</button>
        : <button className={styles.buttonSecondary} disabled={busy} onClick={() => props.onRecordPause(!call.recording_paused)}>{call.recording_paused ? "Resume recording" : "Pause recording"}</button>}
    </div>
    <DtmfPad value={props.dtmf} onChange={props.onDtmfChange} onSend={props.onSendDtmf} disabled={busy || !call.agent_joined_at} />
    <TransferControls call={call} destination={props.transferDestination} busy={busy} onDestinationChange={props.onTransferDestinationChange} onTransfer={props.onTransfer} onFinish={props.onFinishTransfer} />
    <button className={styles.buttonSecondary} disabled={busy || !call.agent_joined_at || !props.transferDestination.trim()} onClick={props.onConference}>Add destination to conference</button>
  </article>;
}
