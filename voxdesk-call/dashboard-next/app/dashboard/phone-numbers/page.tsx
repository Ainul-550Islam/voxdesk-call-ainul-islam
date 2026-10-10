"use client";

import React, { useCallback, useEffect, useState } from "react";

interface AgentSummary {
  id: string;
  name: string;
  status: string;
  published_version_number?: number | null;
}

interface PhoneNumberItem {
  id: string;
  phone_number: string;
  friendly_name: string;
  provider: string;
  status: string;
  country_code: string;
  inbound_agent_id: string | null;
  inbound_agent_version: number | null;
  outbound_agent_id: string | null;
}

export default function PhoneNumbersPage() {
  const [numbers, setNumbers] = useState<PhoneNumberItem[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [numRes, agentRes] = await Promise.all([
        fetch("/api/v1/telephony/phone-numbers", { credentials: "include" }),
        fetch("/api/v1/agents", { credentials: "include" }),
      ]);
      if (numRes.ok) {
        const numData = await numRes.json();
        setNumbers(Array.isArray(numData.items) ? numData.items : []);
      }
      if (agentRes.ok) {
        const agentData = await agentRes.json();
        const rawAgents = Array.isArray(agentData.items)
          ? agentData.items
          : Array.isArray(agentData)
          ? agentData
          : [];
        setAgents(rawAgents);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load phone numbers");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleUpdateBinding = async (
    numberId: string,
    inboundAgentId: string | null,
    inboundAgentVersion: number | null,
    outboundAgentId: string | null
  ) => {
    setSavingId(numberId);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`/api/v1/telephony/phone-numbers/${numberId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          inbound_agent_id: inboundAgentId || null,
          inbound_agent_version: inboundAgentVersion,
          outbound_agent_id: outboundAgentId || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Failed with status ${res.status}`);
      }
      const updated = await res.json();
      setNumbers((prev) =>
        prev.map((item) =>
          item.id === numberId
            ? {
                ...item,
                inbound_agent_id: updated.inbound_agent_id ?? null,
                inbound_agent_version: updated.inbound_agent_version ?? null,
                outbound_agent_id: updated.outbound_agent_id ?? null,
              }
            : item
        )
      );
      setNotice("Phone number agent binding saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update binding");
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Phone Numbers &amp; Agent Routing
          </h1>
          <p className="text-sm text-slate-600">
            Bind each provisioned E.164 phone number to an inbound and outbound AI agent (and optional pinned version).
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadData()}
          className="px-3 py-1.5 text-sm font-medium rounded-md border border-slate-300 bg-white hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md bg-emerald-50 border border-emerald-200 p-3 text-sm text-emerald-700">
          {notice}
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Number</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Provider</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Inbound Agent</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Pinned Version</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Outbound Agent</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Loading phone numbers...
                </td>
              </tr>
            ) : numbers.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  No phone numbers provisioned yet.
                </td>
              </tr>
            ) : (
              numbers.map((num) => (
                <PhoneNumberRow
                  key={num.id}
                  item={num}
                  agents={agents}
                  saving={savingId === num.id}
                  onSave={handleUpdateBinding}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PhoneNumberRow({
  item,
  agents,
  saving,
  onSave,
}: {
  item: PhoneNumberItem;
  agents: AgentSummary[];
  saving: boolean;
  onSave: (
    numberId: string,
    inboundAgentId: string | null,
    inboundAgentVersion: number | null,
    outboundAgentId: string | null
  ) => Promise<void>;
}) {
  const [inboundAgentId, setInboundAgentId] = useState<string>(item.inbound_agent_id || "");
  const [inboundVersion, setInboundVersion] = useState<string>(
    item.inbound_agent_version != null ? String(item.inbound_agent_version) : ""
  );
  const [outboundAgentId, setOutboundAgentId] = useState<string>(item.outbound_agent_id || "");

  return (
    <tr>
      <td className="px-4 py-3">
        <div className="font-mono font-medium text-slate-900">{item.phone_number}</div>
        <div className="text-xs text-slate-500">{item.friendly_name}</div>
      </td>
      <td className="px-4 py-3 uppercase text-xs font-semibold text-slate-600">
        {item.provider}
      </td>
      <td className="px-4 py-3">
        <select
          aria-label={`Inbound agent for ${item.phone_number}`}
          value={inboundAgentId}
          onChange={(e) => setInboundAgentId(e.target.value)}
          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">Tenant Default</option>
          {agents.map((ag) => (
            <option key={ag.id} value={ag.id}>
              {ag.name}
            </option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3">
        <input
          type="number"
          min={1}
          placeholder="Latest published"
          aria-label={`Pinned version for ${item.phone_number}`}
          value={inboundVersion}
          onChange={(e) => setInboundVersion(e.target.value)}
          className="w-32 rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </td>
      <td className="px-4 py-3">
        <select
          aria-label={`Outbound agent for ${item.phone_number}`}
          value={outboundAgentId}
          onChange={(e) => setOutboundAgentId(e.target.value)}
          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">Tenant Default</option>
          {agents.map((ag) => (
            <option key={ag.id} value={ag.id}>
              {ag.name}
            </option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3 text-right">
        <button
          type="button"
          disabled={saving}
          onClick={() =>
            void onSave(
              item.id,
              inboundAgentId || null,
              inboundVersion.trim() ? Number(inboundVersion) : null,
              outboundAgentId || null
            )
          }
          className="px-3 py-1 rounded bg-slate-900 text-white text-xs font-medium hover:bg-slate-800 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save"}
        </button>
      </td>
    </tr>
  );
}
