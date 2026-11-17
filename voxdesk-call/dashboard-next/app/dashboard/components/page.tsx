// File: dashboard-next/app/dashboard/components/page.tsx — reusable flow components and where they are used (PART 12, P12-20)
"use client";

import { useCallback, useEffect, useState } from "react";
import { request } from "@/lib/api";
import { GlassCard } from "@/components/enterprise/GlassCard";

type ComponentVersion = {
  version: number;
  content_hash: string;
  published: boolean;
  changelog: string;
};

type Component = {
  id: string;
  name: string;
  description: string;
  versions: ComponentVersion[];
};

type Usage = { count: number; bindings: { agent_id: string; version: number; sync_mode: string }[] };

export default function ComponentsPage() {
  const [components, setComponents] = useState<Component[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [usage, setUsage] = useState<Usage | null>(null);
  const [plan, setPlan] = useState<{ plan: { agent_id: string; action: string }[] } | null>(null);
  const [error, setError] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const response = await request<{ components: Component[] }>("/api/flow-components");
      setComponents(response.components ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load components");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const inspect = async (componentId: string) => {
    setSelected(componentId);
    setPlan(null);
    try {
      const [usageResponse, planResponse] = await Promise.all([
        request<Usage>(`/api/flow-components/${componentId}/usage`),
        request<{ plan: { agent_id: string; action: string }[] }>(
          `/api/flow-components/${componentId}/propagation-plan?new_version=1`,
        ),
      ]);
      setUsage(usageResponse);
      setPlan(planResponse);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load component");
    }
  };

  return (
    <main style={{ padding: 24, display: "grid", gap: 16 }}>
      <GlassCard title="Flow components" description="Publishing a component never edits a live agent: dependents get a draft.">
        {error && <p style={{ color: "#fca5a5", fontSize: 13 }}>{error}</p>}
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 8 }}>
          {components.map((component) => {
            const published = component.versions.find((version) => version.published);
            return (
              <li
                key={component.id}
                style={{ border: "1px solid rgba(148,163,184,0.3)", borderRadius: 6, padding: 10 }}
              >
                <button
                  type="button"
                  onClick={() => inspect(component.id)}
                  style={{ background: "none", border: "none", color: "#a5b4fc", cursor: "pointer", fontSize: 14, padding: 0 }}
                >
                  {component.name}
                </button>
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>
                  {component.description || "No description"} ·{" "}
                  {published ? `published v${published.version}` : "not published"}
                </div>
              </li>
            );
          })}
          {components.length === 0 && <li style={{ fontSize: 13 }}>No components yet.</li>}
        </ul>
      </GlassCard>

      {selected && (
        <GlassCard title="Usage" description="Snapshot bindings stay on the version they froze; live-sync bindings receive a draft.">
          {usage ? (
            <>
              <p style={{ fontSize: 13 }} data-testid="usage-count">
                Used by {usage.count} agent(s).
              </p>
              <ul style={{ fontSize: 12, color: "#cbd5e1" }}>
                {usage.bindings.map((binding) => (
                  <li key={binding.agent_id}>
                    {binding.agent_id} — v{binding.version} ({binding.sync_mode})
                  </li>
                ))}
              </ul>
              {plan && (
                <p style={{ fontSize: 12, color: "#94a3b8" }}>
                  Publishing would:{" "}
                  {plan.plan.length === 0
                    ? "change nothing"
                    : plan.plan.map((item) => `${item.action} for ${item.agent_id}`).join(", ")}
                </p>
              )}
            </>
          ) : (
            <p style={{ fontSize: 13 }}>Loading…</p>
          )}
        </GlassCard>
      )}
    </main>
  );
}
