import type { CallStatus, SoftphoneCall } from "./types";

export const ACTIVE_STATUSES: ReadonlySet<CallStatus> = new Set([
  "pending",
  "dialing",
  "waiting_for_agent",
  "in_progress",
  "held",
]);

export function mergeCalls(current: SoftphoneCall[], incoming: SoftphoneCall[]): SoftphoneCall[] {
  const byId = new Map(current.map((call) => [call.id, call]));
  for (const call of incoming) {
    byId.set(call.id, call);
  }
  return [...byId.values()].sort((left, right) => right.created_at.localeCompare(left.created_at));
}

export function canJoin(call: SoftphoneCall, sessionId: string | null): boolean {
  return Boolean(
    sessionId
    && call.session_id === sessionId
    && !call.agent_joined_at
    && ["pending", "dialing", "waiting_for_agent"].includes(call.status),
  );
}

export function canControl(call: SoftphoneCall): boolean {
  return ACTIVE_STATUSES.has(call.status) && Boolean(call.agent_joined_at);
}

export function statusLabel(call: SoftphoneCall): string {
  if (call.status === "in_progress" && call.agent_joined_at) return "Live with agent";
  if (call.status === "held") return "Customer on hold";
  return call.status.replaceAll("_", " ");
}

export function validE164(value: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(value);
}

export function validDtmf(value: string): boolean {
  return /^[0-9*#]{1,32}$/.test(value);
}

export function citationLabel(citation: Record<string, unknown>, index: number): string {
  const title = citation.title ?? citation.document_title ?? citation.document_id;
  const section = citation.section ?? citation.page ?? citation.chunk_id;
  const base = title ? String(title) : `Evidence ${index + 1}`;
  return section === undefined ? base : `${base} · ${String(section)}`;
}
