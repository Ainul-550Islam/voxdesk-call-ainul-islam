"use client";

import Link from "next/link";
import Script from "next/script";
import { useState } from "react";
import { AssistPanel } from "./AssistPanel";
import { AgentStateBar } from "./components/AgentStateBar";
import { CallbackQueue } from "./components/CallbackQueue";
import { CallList } from "./components/CallList";
import { CustomerContextCard } from "./components/CustomerContextCard";
import { OutboundDialer } from "./components/OutboundDialer";
import { SoftphoneDock } from "./components/SoftphoneDock";
import { WrapupForm } from "./components/WrapupForm";
import { Softphone } from "./Softphone";
import { useAssistStream } from "./hooks/useAssistStream";
import { useAgentState } from "./hooks/useAgentState";
import { useSoftphone } from "./hooks/useSoftphone";
import { canJoin, validE164 } from "./lib";
import styles from "./agent-desktop.module.css";
import type { SoftphoneCall } from "./types";

const VOICE_SDK_URL = "https://cdn.jsdelivr.net/npm/@twilio/voice-sdk@2.18.5/dist/twilio.min.js";
const VOICE_SDK_INTEGRITY = "sha384-LUG7DmzDPWNKRYoqyGpV4d/IByMLiya/oUhMSc/4/6hi0wbzO/8PffM4lzeFEYS8";

export default function AgentDesktopPage() {
  const [sdkReady, setSdkReady] = useState(false);
  const [environmentId, setEnvironmentId] = useState("");
  const [deviceLabel, setDeviceLabel] = useState("VoxDesk agent desktop");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [destination, setDestination] = useState("");
  const [callerId, setCallerId] = useState("");
  const [transferDestination, setTransferDestination] = useState("");
  const [dtmf, setDtmf] = useState("");
  const softphone = useSoftphone();
  const { session, calls, busy, error, notice, setError, setNotice } = softphone;
  const assist = useAssistStream(setError);
  const agentState = useAgentState(setError, setNotice);
  const selected = calls.find((call) => call.id === selectedId) ?? null;

  function register() {
    if (!environmentId.trim()) {
      setError("Enter the active production environment UUID");
      return;
    }
    void softphone.register(environmentId.trim(), deviceLabel.trim());
  }

  async function dial() {
    if (!validE164(destination)) {
      setError("Enter the destination in E.164 format, for example +15551234567");
      return;
    }
    if (callerId && !validE164(callerId)) {
      setError("Caller ID must be E.164 and belong to this tenant");
      return;
    }
    const call = await softphone.dial(destination, callerId);
    if (call) setSelectedId(call.id);
  }

  function join(call: SoftphoneCall) { void softphone.join(call); }
  function control(call: SoftphoneCall, action: "mute" | "hold", enabled: boolean) { void softphone.control(call, action, enabled); }
  function end(call: SoftphoneCall) { void softphone.end(call); }

  function transfer(mode: "blind" | "warm") {
    if (!selected || !validE164(transferDestination)) {
      setError("Select a call and enter an E.164 transfer destination");
      return;
    }
    void softphone.transfer(selected, transferDestination, mode);
  }

  function conference() {
    if (!selected || !validE164(transferDestination)) {
      setError("Select a call and enter an E.164 conference destination");
      return;
    }
    void softphone.conference(selected, transferDestination);
  }

  function finishTransfer(action: "complete" | "cancel") {
    if (selected) void softphone.finishTransfer(selected, action);
  }

  function sendDtmf() {
    if (selected && softphone.sendDtmf(selected, dtmf)) setDtmf("");
  }

  function startAssist() {
    if (!selected?.call_id) {
      setError("Select a call with a canonical call record");
      return;
    }
    void assist.start(selected.call_id).then((started) => { if (started) setNotice("Agent assist started for redacted final transcript turns"); });
  }

  function stopAssist() { void assist.stop().then((stopped) => { if (stopped) setNotice("Agent assist stopped"); }); }

  function createWrapup() {
    if (!selected?.call_id) return;
    void assist.createWrapup(selected.call_id).then((draft) => {
      if (draft) {
        setNotice("Redacted wrap-up draft created; submit a disposition before Available");
        void agentState.loadWrapups();
      }
    });
  }

  return <main className={styles.page}>
    <Script src={VOICE_SDK_URL} strategy="afterInteractive" integrity={VOICE_SDK_INTEGRITY} crossOrigin="anonymous" onLoad={() => setSdkReady(true)} onError={() => setError("The pinned Twilio Voice SDK could not be loaded; check network and CSP policy")} />
    <header className={styles.header}>
      <div><p className={styles.eyebrow}>CONTACT CENTER · HUMAN WEBRTC</p><h1>Agent desktop</h1><p>Carrier-backed softphone, transfers, DTMF, and evidence-grounded live assist.</p><nav><Link href="/agent-desktop/supervisor/wallboard">Supervisor wallboard</Link> · <Link href="/agent-desktop/supervisor/dialer">Dialer console</Link></nav></div>
      <span className={session ? styles.online : styles.offline}>{session ? "Registered" : sdkReady ? "SDK ready" : "SDK loading"}</span>
    </header>
    {(error || assist.error) && <div className={styles.error} role="alert">{error || assist.error}</div>}
    {notice && <div className={styles.notice} role="status">{notice}</div>}
    <AgentStateBar state={agentState.state} reasonCode={agentState.reasonCode} stateChangedAt={agentState.stateChangedAt} occupancy={agentState.occupancy} busy={busy} onChange={agentState.changeState} />
    <section className={styles.grid}>
      <Softphone environmentId={environmentId} deviceLabel={deviceLabel} session={session} sdkReady={sdkReady} busy={busy} onEnvironmentChange={setEnvironmentId} onDeviceLabelChange={setDeviceLabel} onRegister={register} onPresence={(state) => void agentState.changeState(state)} />
      <OutboundDialer destination={destination} callerId={callerId} session={session} busy={busy} onDestinationChange={setDestination} onCallerIdChange={setCallerId} onDial={() => void dial()} />
    </section>
    <CallList calls={calls} selectedId={selectedId} sessionId={session?.id ?? null} busy={busy} onSelect={setSelectedId} onRefresh={() => void softphone.refreshCalls()} onJoin={join} onControl={control} onEnd={end} />
    <section className={styles.grid}><CustomerContextCard call={selected} /><CallbackQueue onError={setError} /></section>
    <WrapupForm wrapups={agentState.wrapups} busy={busy} onSubmit={agentState.submitWrapup} />
    {selected && <section className={styles.grid}>
      <SoftphoneDock call={selected} dtmf={dtmf} transferDestination={transferDestination} busy={busy} canAnswer={canJoin(selected, session?.id ?? null)} onDtmfChange={setDtmf} onSendDtmf={sendDtmf} onTransferDestinationChange={setTransferDestination} onTransfer={transfer} onFinishTransfer={finishTransfer} onControl={(action, enabled) => control(selected, action, enabled)} onResume={() => void softphone.resume(selected)} onEnd={() => end(selected)} onAnswer={() => join(selected)} onRecordPause={(paused) => void softphone.recordPause(selected, paused)} onConference={conference} />
      <AssistPanel call={selected} assistCallId={assist.assistCallId} transcript={assist.transcript} suggestions={assist.suggestions} alerts={assist.alerts} scripts={assist.scripts} adherence={assist.adherence} transport={assist.transport} busy={busy} onStart={startAssist} onStop={stopAssist} onFeedback={assist.feedback} onWrapup={createWrapup} />
    </section>}
  </main>;
}
