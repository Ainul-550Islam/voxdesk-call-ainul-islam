import { describe, expect, it } from "vitest";
import { realtimeUrl } from "../realtime";

describe("realtime browser URL", () => {
  it("binds the ticket to a same-origin websocket endpoint", () => {
    const url = new URL(realtimeUrl("/ws/cc2/supervisor", "ticket-value"));
    expect(url.origin).toBe(window.location.origin.replace("http:", "ws:").replace("https:", "wss:"));
    expect(url.pathname).toBe("/ws/cc2/supervisor");
    expect(url.searchParams.get("ticket")).toBe("ticket-value");
  });
});
