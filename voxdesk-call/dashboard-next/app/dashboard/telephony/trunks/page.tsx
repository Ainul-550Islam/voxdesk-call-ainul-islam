// File: dashboard-next/app/dashboard/telephony/trunks/page.tsx — SIP trunks, their test and their health (PART 13, P13-01)
"use client";

import { useCallback, useEffect, useState } from "react";
import { request } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";

type Trunk = {
  id: string;
  name: string;
  host: string;
  port: number;
  transport: string;
  srtp_required: boolean;
  caller_id: string | null;
  egress_address: string | null;
  status: string;
  last_test_at: string | null;
  last_error: string | null;
};

type Health = { total: number; ready: number; degraded: number; ok: boolean };

const BLANK = {
  name: "Primary trunk",
  host: "",
  port: 5061,
  transport: "tls",
  srtp_required: true,
  caller_id: "",
  auth_username: "",
  auth_secret_ref: "",
  egress_address: "",
};

export default function TrunksPage() {
  const [trunks, setTrunks] = useState<Trunk[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [draft, setDraft] = useState({ ...BLANK });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [list, status] = await Promise.all([
        request<{ trunks: Trunk[] }>("/api/telephony/trunks"),
        request<Health>("/api/telephony/trunks/health"),
      ]);
      setTrunks(list.trunks ?? []);
      setHealth(status);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not load trunks");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy(true);
    setMessage("");
    try {
      await request<Trunk>("/api/telephony/trunks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...draft, port: Number(draft.port) }),
      });
      setDraft({ ...BLANK });
      await load();
    } catch (cause) {
      // The API names the offending field, so show that rather than "failed".
      setMessage(cause instanceof Error ? cause.message : "Could not create the trunk");
    } finally {
      setBusy(false);
    }
  };

  const test = async (id: string) => {
    setBusy(true);
    try {
      const result = await request<{ ok: boolean }>(`/api/telephony/trunks/${id}/test`, {
        method: "POST",
      });
      setMessage(result.ok ? "Trunk answered the OPTIONS ping." : "The trunk did not answer.");
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "The test could not run");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <GlassCard
        title="SIP trunks"
        description="Carrier-facing trunks. The password is stored as a secret reference and never returned."
      >
        {health ? (
          <div className="mb-4 flex gap-3">
            <GlassBadge variant={health.ok ? "success" : "warning"}>
              {health.ready}/{health.total} ready
            </GlassBadge>
            {health.degraded > 0 ? (
              <GlassBadge variant="danger">{health.degraded} unreachable</GlassBadge>
            ) : null}
          </div>
        ) : null}

        <table className="w-full text-sm">
          <thead>
            <tr className="text-left opacity-70">
              <th>Name</th>
              <th>Host</th>
              <th>Transport</th>
              <th>SRTP</th>
              <th>Caller ID</th>
              <th>Egress</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {trunks.map((trunk) => (
              <tr key={trunk.id}>
                <td>{trunk.name}</td>
                <td>
                  {trunk.host}:{trunk.port}
                </td>
                <td>{trunk.transport}</td>
                <td>{trunk.srtp_required ? "required" : "plain"}</td>
                <td>{trunk.caller_id ?? "—"}</td>
                <td>{trunk.egress_address ?? "not set"}</td>
                <td>
                  <GlassBadge variant={trunk.status === "READY" ? "success" : "warning"}>
                    {trunk.status}
                  </GlassBadge>
                  {trunk.last_error ? (
                    <span className="ml-2 text-xs opacity-70">{trunk.last_error}</span>
                  ) : null}
                </td>
                <td>
                  <button
                    className="rounded border px-2 py-1 text-xs"
                    disabled={busy}
                    onClick={() => void test(trunk.id)}
                  >
                    Test
                  </button>
                </td>
              </tr>
            ))}
            {trunks.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-3 opacity-70">
                  No trunks yet. Add one to send calls through your own carrier.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </GlassCard>

      <GlassCard title="Add a trunk" description="TLS with SRTP is the default; plain RTP is refused for live dialling.">
        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["name", "Name"],
              ["host", "Host"],
              ["port", "Port"],
              ["transport", "Transport (udp/tcp/tls)"],
              ["caller_id", "Caller ID (E.164)"],
              ["auth_username", "Auth username"],
              ["auth_secret_ref", "Secret reference"],
              ["egress_address", "Egress address"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="text-sm">
              <span className="opacity-70">{label}</span>
              <input
                className="mt-1 w-full rounded border bg-transparent px-2 py-1"
                value={String(draft[key] ?? "")}
                onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
              />
            </label>
          ))}
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.srtp_required}
            onChange={(event) => setDraft({ ...draft, srtp_required: event.target.checked })}
          />
          Require SRTP (unchecking this is refused unless the deployment allows plain RTP)
        </label>
        <button
          className="mt-4 rounded bg-white/10 px-3 py-1.5 text-sm"
          disabled={busy || !draft.host || !draft.auth_secret_ref}
          onClick={() => void create()}
        >
          {busy ? "Saving…" : "Save trunk"}
        </button>
        {message ? <p className="mt-3 text-sm opacity-80">{message}</p> : null}
      </GlassCard>
    </div>
  );
}
