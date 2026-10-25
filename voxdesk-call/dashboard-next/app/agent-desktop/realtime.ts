"use client";

export interface StreamTicket {
  ticket: string;
  expires_in: number;
  websocket_url: string;
}

export function realtimeUrl(path: string, ticket: string): string {
  const url = new URL(path, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
