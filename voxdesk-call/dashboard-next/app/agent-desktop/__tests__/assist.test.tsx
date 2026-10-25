import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AssistPanel } from "../AssistPanel";
import type { SoftphoneCall } from "../types";

const call: SoftphoneCall = {
  id: "call-1", call_id: "canonical-1", session_id: "session-1", origin: "agent_outbound",
  status: "in_progress", agent_joined_at: null, answered_at: null, ended_at: null,
  failure_category: "", muted: false, on_hold: false, created_at: new Date(0).toISOString(), legs: [],
};

describe("agent assist panel", () => {
  it("shows only redacted evidence and explicit feedback actions", () => {
    const html = renderToString(<AssistPanel call={call} assistCallId="canonical-1" transport="realtime" transcript={[{ id: "turn-1", seq: 1, speaker: "caller", text_redacted: "Email [EMAIL]", processing_status: "completed", created_at: new Date(0).toISOString() }]} alerts={[{ id: "alert-1", kind: "negative_sentiment", severity: "warning", message: "Review caller sentiment", evidence: {}, status: "open", created_at: new Date(0).toISOString() }]} suggestions={[{ id: "suggestion-1", kind: "knowledge", text: "Use the retrieved refund policy", citations: [{ title: "Policy" }], generation_method: "governed_llm", created_at: new Date(0).toISOString() }]} busy={false} onStart={vi.fn()} onStop={vi.fn()} onFeedback={vi.fn()} onWrapup={vi.fn()} />);
    expect(html).toContain("Live agent assist");
    expect(html).toContain("Use the retrieved refund policy");
    expect(html).toContain("Accept");
    expect(html).toContain("Ignore");
    expect(html).toContain("Final turns are redacted");
    expect(html).toContain("Email [EMAIL]");
    expect(html).toContain("Realtime stream connected");
  });
});
