import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AgentStateBar } from "../components/AgentStateBar";
import { CustomerContextCard } from "../components/CustomerContextCard";
import { SoftphoneDock } from "../components/SoftphoneDock";
import DialerConsolePage from "../supervisor/dialer/page";
import { AgentGrid } from "../supervisor/wallboard/components/AgentGrid";
import { QueueTile } from "../supervisor/wallboard/components/QueueTile";
import type { SoftphoneCall } from "../types";

const call: SoftphoneCall = {
  id: "call-1", call_id: "canonical-1", session_id: "session-1", origin: "agent_outbound",
  status: "in_progress", agent_joined_at: new Date(0).toISOString(), answered_at: new Date(0).toISOString(),
  ended_at: null, failure_category: "", muted: false, on_hold: false, recording_paused: false,
  created_at: new Date(0).toISOString(), legs: [],
  warm_transfer_briefing: { text_redacted: "Redacted AI handoff", status: "briefing_played", human_briefed: true, ai_detached: false, returned_to_ai: false },
  customer_context: {
    lead: { id: "lead-1", name: "Customer One", company: "Example Co", score: 42, status: "qualified" },
    prior_calls: [{ id: "prior-1", status: "completed", started_at: new Date(0).toISOString(), duration_seconds: 18, summary_redacted: "Prior redacted summary" }],
    crm: { ticket_id: "ticket-1" }, crm_synced: true,
  },
};

describe("required agent desktop components", () => {
  it("renders real softphone controls and verified recording state", () => {
    const html = renderToStaticMarkup(<SoftphoneDock call={call} dtmf="" transferDestination="+15550003000" busy={false} onDtmfChange={vi.fn()} onSendDtmf={vi.fn()} onTransferDestinationChange={vi.fn()} onTransfer={vi.fn()} onFinishTransfer={vi.fn()} onControl={vi.fn()} onResume={vi.fn()} onEnd={vi.fn()} onRecordPause={vi.fn()} onConference={vi.fn()} />);
    expect(html).toContain("Softphone");
    expect(html).toContain("Pause recording");
    expect(html).toContain("Add destination to conference");
    expect(html).toContain("Audio meter unavailable for this transport");
  });

  it("renders linked customer history and the redacted AI briefing", () => {
    const html = renderToStaticMarkup(<CustomerContextCard call={call} />);
    expect(html).toContain("Customer One");
    expect(html).toContain("Redacted AI handoff");
    expect(html).toContain("Prior redacted summary");
    expect(html).toContain("ticket id");
  });

  it("renders state reasons and observed occupancy", () => {
    const html = renderToStaticMarkup(<AgentStateBar state="available" reasonCode="manual" stateChangedAt={new Date(0).toISOString()} occupancy={{ user_id: "agent-1", seconds: { available: 60 }, occupancy: "0.5", occupancy_percent: "50" }} onChange={vi.fn().mockResolvedValue(true)} />);
    expect(html).toContain("available");
    expect(html).toContain("Ready for contacts");
    expect(html).toContain("50.0%");
  });

  it("renders supervisor metrics and the policy-only dialer console", () => {
    const queue = renderToStaticMarkup(<QueueTile queue={{ queue_id: "queue-1", name: "Support", enabled: true, waiting: 2, assigned: 1, oldest_wait_seconds: 42, estimated_wait_seconds: 90, service_level_percent: 95, asa_seconds: 12, aht_seconds: 120, offered: 20, abandoned: 1, abandon_percent: 5 }} />);
    expect(queue).toContain("Support");
    expect(queue).toContain("95%");
    expect(queue).toContain("120s");
    const agents = renderToStaticMarkup(<AgentGrid agents={[{ user_id: "agent-1", full_name: "Agent One", email: "agent@example.test", state: "available", active_count: 0, capacity: 1, softphone_registered: true }]} />);
    expect(agents).toContain("Agent One");
    expect(agents).toContain("Registered");
    const dialer = renderToStaticMarkup(<DialerConsolePage />);
    expect(dialer).toContain("Load policy");
    expect(dialer).toContain("Existing dialer policy UUID");
  });
});
