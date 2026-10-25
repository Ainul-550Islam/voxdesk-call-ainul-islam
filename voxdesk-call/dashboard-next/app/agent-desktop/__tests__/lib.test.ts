import { describe, expect, it } from "vitest";
import { canJoin, canControl, citationLabel, mergeCalls, statusLabel, validDtmf, validE164 } from "../lib";
import type { SoftphoneCall } from "../types";

const call: SoftphoneCall = {
  id: "call-1",
  call_id: "canonical-1",
  session_id: "session-1",
  origin: "agent_outbound",
  status: "waiting_for_agent",
  agent_joined_at: null,
  answered_at: "2026-01-01T00:00:00Z",
  ended_at: null,
  failure_category: "",
  muted: false,
  on_hold: false,
  created_at: "2026-01-01T00:00:00Z",
  legs: [],
};

describe("agent desktop call state", () => {
  it("joins only the current registration before an agent leg exists", () => {
    expect(canJoin(call, "session-1")).toBe(true);
    expect(canJoin(call, "session-2")).toBe(false);
    expect(canJoin({ ...call, status: "in_progress", agent_joined_at: "2026-01-01T00:00:01Z" }, "session-1")).toBe(false);
    expect(canControl({ ...call, status: "in_progress", agent_joined_at: "2026-01-01T00:00:01Z" })).toBe(true);
    expect(statusLabel({ ...call, status: "in_progress", agent_joined_at: "2026-01-01T00:00:01Z" })).toBe("Live with agent");
  });

  it("updates by call id rather than duplicating a polled call", () => {
    const merged = mergeCalls([call], [{ ...call, status: "in_progress" }]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.status).toBe("in_progress");
  });

  it("validates dial and DTMF input at the browser boundary", () => {
    expect(validE164("+15551234567")).toBe(true);
    expect(validE164("5551234567")).toBe(false);
    expect(validDtmf("12*#")).toBe(true);
    expect(validDtmf("12;DROP")).toBe(false);
  });

  it("uses citation metadata without inventing a source", () => {
    expect(citationLabel({ title: "Refund policy", section: "4.2" }, 0)).toBe("Refund policy · 4.2");
    expect(citationLabel({}, 1)).toBe("Evidence 2");
  });
});
