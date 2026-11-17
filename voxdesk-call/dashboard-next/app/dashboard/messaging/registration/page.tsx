// File: dashboard-next/app/dashboard/messaging/registration/page.tsx — A2P 10DLC / toll-free wizard (PART 13, P13-08)
"use client";

import { useCallback, useEffect, useState } from "react";
import { request } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";

type Registration = {
  id: string;
  kind: string;
  state: string;
  rejection_reason: string | null;
  next_step: string;
};

type Status = {
  states: Record<string, string>;
  sms: { allowed: boolean; reason: string };
  toll_free_sms: { allowed: boolean; reason: string };
  next_steps: Record<string, string>;
};

const KINDS = [
  { key: "a2p_brand", label: "1. Brand (10DLC)" },
  { key: "a2p_campaign", label: "2. Campaign (10DLC)" },
  { key: "toll_free_verification", label: "Toll-free verification" },
];

export default function RegistrationPage() {
  const [registrations, setRegistrations] = useState<Registration[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [kind, setKind] = useState("a2p_brand");
  const [payload, setPayload] = useState('{"legal_business_name": ""}');
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [list, current] = await Promise.all([
        request<{ registrations: Registration[] }>("/api/messaging/registrations"),
        request<Status>("/api/messaging/status"),
      ]);
      setRegistrations(list.registrations ?? []);
      setStatus(current);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not load registrations");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const createDraft = async () => {
    setMessage("");
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      setMessage("The payload must be valid JSON.");
      return;
    }
    try {
      await request<Registration>("/api/messaging/registrations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, payload: parsed }),
      });
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not create the draft");
    }
  };

  const submit = async (id: string) => {
    setMessage("");
    try {
      await request<Registration>(`/api/messaging/registrations/${id}/submit`, { method: "POST" });
      setMessage("Submitted to the carrier.");
      await load();
    } catch (cause) {
      // The API returns the blockers, which is exactly what the operator needs.
      setMessage(cause instanceof Error ? cause.message : "The carrier refused the submission");
    }
  };

  return (
    <div className="space-y-6">
      <GlassCard
        title="Messaging registration"
        description="Carriers fine the sender for traffic on an unregistered brand, so outbound SMS stays blocked until a campaign is approved."
      >
        {status ? (
          <div className="flex flex-wrap gap-3 text-sm">
            <GlassBadge variant={status.sms.allowed ? "success" : "warning"}>
              SMS {status.sms.allowed ? "enabled" : "blocked"}
            </GlassBadge>
            <span className="opacity-70">{status.sms.reason}</span>
          </div>
        ) : null}

        <table className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left opacity-70">
              <th>Kind</th>
              <th>State</th>
              <th>What happens next</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {registrations.map((row) => (
              <tr key={row.id}>
                <td>{row.kind}</td>
                <td>
                  <GlassBadge
                    variant={
                      row.state === "approved"
                        ? "success"
                        : row.state === "rejected"
                          ? "danger"
                          : "warning"
                    }
                  >
                    {row.state}
                  </GlassBadge>
                  {row.rejection_reason ? (
                    <span className="ml-2 text-xs opacity-70">{row.rejection_reason}</span>
                  ) : null}
                </td>
                <td className="opacity-80">{row.next_step}</td>
                <td>
                  {row.state === "draft" ? (
                    <button className="rounded border px-2 py-1 text-xs" onClick={() => void submit(row.id)}>
                      Submit
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
            {registrations.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-3 opacity-70">
                  Nothing filed yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </GlassCard>

      <GlassCard title="New registration" description="Start with the brand; a campaign cannot be submitted before its brand is approved.">
        <div className="space-y-3 text-sm">
          <label className="block">
            <span className="opacity-70">Kind</span>
            <select
              className="mt-1 rounded border bg-transparent px-2 py-1"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              {KINDS.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.label}
                </option>
              ))}
            </select>
            {status?.next_steps?.[kind] ? (
              <span className="ml-3 opacity-70">{status.next_steps[kind]}</span>
            ) : null}
          </label>
          <label className="block">
            <span className="opacity-70">Payload (JSON)</span>
            <textarea
              className="mt-1 h-32 w-full rounded border bg-transparent px-2 py-1 font-mono text-xs"
              value={payload}
              onChange={(event) => setPayload(event.target.value)}
            />
          </label>
          <button className="rounded bg-white/10 px-3 py-1.5" onClick={() => void createDraft()}>
            Save draft
          </button>
        </div>
        {message ? <p className="mt-3 text-sm opacity-80">{message}</p> : null}
      </GlassCard>
    </div>
  );
}
