// File: dashboard-next/app/dashboard/telephony/countries/page.tsx — allow/deny per country with a dry-run (PART 13, P13-06)
"use client";

import { useCallback, useEffect, useState } from "react";
import { request } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";

type Rule = { id?: string; scope: string; scope_id: string | null; country: string; action: string; reason: string | null };
type Decision = {
  allowed: boolean;
  country: string | null;
  action: string;
  matched_scope: string;
  matched_id: string | null;
  reason: string | null;
  code: string;
};

export default function CountriesPage() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [draft, setDraft] = useState<Rule>({ scope: "tenant", scope_id: null, country: "", action: "deny", reason: null });
  const [probe, setProbe] = useState("+8801712345678");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await request<{ rules: Rule[] }>("/api/telephony/countries");
      setRules(response.rules ?? []);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not load the policy");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const persist = async (next: Rule[]) => {
    try {
      await request<{ rules: Rule[] }>("/api/telephony/countries", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          default_action: "allow",
          rules: next.map((rule) => ({
            scope: rule.scope,
            scope_id: rule.scope_id,
            country: rule.country,
            action: rule.action,
            reason: rule.reason,
          })),
        }),
      });
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not save the policy");
    }
  };

  const add = async () => {
    if (!draft.country.trim()) {
      setMessage("A country code is required (ISO-3166 alpha-2, or * for every country).");
      return;
    }
    await persist([...rules, { ...draft, country: draft.country.toUpperCase() }]);
    setDraft({ ...draft, country: "" });
    setMessage("");
  };

  const dryRun = async () => {
    try {
      const response = await request<{ decision: Decision }>("/api/telephony/countries/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number: probe, stage: "dry_run" }),
      });
      setDecision(response.decision);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "The dry-run failed");
    }
  };

  return (
    <div className="space-y-6">
      <GlassCard
        title="Country policy"
        description="Allow-lists turn into deny-by-default automatically: if you list allowed countries, everything else is refused."
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left opacity-70">
              <th>Scope</th>
              <th>Target</th>
              <th>Country</th>
              <th>Action</th>
              <th>Note</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((rule, index) => (
              <tr key={`${rule.scope}-${rule.scope_id}-${rule.country}-${index}`}>
                <td>{rule.scope}</td>
                <td>{rule.scope_id ?? "—"}</td>
                <td>{rule.country}</td>
                <td>
                  <GlassBadge variant={rule.action === "allow" ? "success" : "danger"}>{rule.action}</GlassBadge>
                </td>
                <td className="opacity-70">{rule.reason ?? ""}</td>
                <td>
                  <button
                    className="rounded border px-2 py-1 text-xs"
                    onClick={() => void persist(rules.filter((_, at) => at !== index))}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
            {rules.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-3 opacity-70">
                  No rules yet — every country is currently reachable.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </GlassCard>

      <GlassCard title="Add a rule" description="Agent and number rules beat the tenant rule.">
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <label>
            <span className="opacity-70">Scope</span>
            <select
              className="mt-1 rounded border bg-transparent px-2 py-1"
              value={draft.scope}
              onChange={(event) => setDraft({ ...draft, scope: event.target.value })}
            >
              <option value="tenant">tenant</option>
              <option value="number">number</option>
              <option value="agent">agent</option>
            </select>
          </label>
          {draft.scope !== "tenant" ? (
            <label>
              <span className="opacity-70">Target id</span>
              <input
                className="mt-1 rounded border bg-transparent px-2 py-1"
                value={draft.scope_id ?? ""}
                onChange={(event) => setDraft({ ...draft, scope_id: event.target.value })}
              />
            </label>
          ) : null}
          <label>
            <span className="opacity-70">Country</span>
            <input
              className="mt-1 w-20 rounded border bg-transparent px-2 py-1 uppercase"
              maxLength={2}
              value={draft.country}
              onChange={(event) => setDraft({ ...draft, country: event.target.value })}
            />
          </label>
          <label>
            <span className="opacity-70">Action</span>
            <select
              className="mt-1 rounded border bg-transparent px-2 py-1"
              value={draft.action}
              onChange={(event) => setDraft({ ...draft, action: event.target.value })}
            >
              <option value="deny">deny</option>
              <option value="allow">allow</option>
            </select>
          </label>
          <button className="rounded bg-white/10 px-3 py-1.5" onClick={() => void add()}>
            Add
          </button>
        </div>
      </GlassCard>

      <GlassCard title="Dry-run" description="Ask before publishing: which rule would decide this number?">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input aria-label="Phone number to check"
            className="rounded border bg-transparent px-2 py-1"
            value={probe}
            onChange={(event) => setProbe(event.target.value)}
          />
          <button className="rounded bg-white/10 px-3 py-1.5" onClick={() => void dryRun()}>
            Check
          </button>
        </div>
        {decision ? (
          <div className="mt-3 text-sm">
            <GlassBadge variant={decision.allowed ? "success" : "danger"}>
              {decision.allowed ? "allowed" : "blocked"}
            </GlassBadge>{" "}
            <span className="opacity-80">
              {decision.country ?? "unknown country"} · decided by {decision.matched_scope}
              {decision.matched_id ? ` (${decision.matched_id})` : ""} · code {decision.code}
            </span>
          </div>
        ) : null}
        {message ? <p className="mt-3 text-sm opacity-80">{message}</p> : null}
      </GlassCard>
    </div>
  );
}
