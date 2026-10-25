import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SupervisorWallboard } from "../../../components/supervisor/SupervisorWallboard";

describe("supervisor wallboard", () => {
  it("renders observed queue and agent values without simulated evidence", () => {
    const html = renderToStaticMarkup(<SupervisorWallboard loading={false} snapshot={{ generated_at: new Date(0).toISOString(), queues: [{ queue_id: "queue-1", name: "Support", enabled: true, waiting: 2, assigned: 1, oldest_wait_seconds: 42, estimated_wait_seconds: 90 }], agents: [{ user_id: "user-1", full_name: "Agent One", email: "agent@example.test", state: "available", active_count: 0, capacity: 1, softphone_registered: true }], callbacks: { scheduled: 1, running: 0, completed: 2, failed: 0, cancelled: 0 }, assist: { active_sessions: 1, open_alerts: 0 } }} />);
    expect(html).toContain("Live wallboard");
    expect(html).toContain("Support");
    expect(html).toContain("42s");
    expect(html).toContain("Agent One");
    expect(html).toContain("Registered");
  });
});
