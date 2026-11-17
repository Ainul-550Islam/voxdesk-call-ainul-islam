// File: dashboard-next/app/dashboard/workflows/library/page.tsx — integration library (PART 14, P14-04)
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { request } from "@/lib/api";
import { GlassBadge } from "@/components/enterprise/GlassBadge";
import { GlassCard } from "@/components/enterprise/GlassCard";

type Action = {
  name: string;
  description: string;
  scopes: string[];
  effect: string;
};

type Integration = {
  key: string;
  name: string;
  category: string;
  auth: string;
  available: boolean;
  reason: string | null;
  actions: Action[];
};

type Install = {
  workflow_key: string;
  name: string;
  version: number;
  enabled: boolean;
  allowed_actions: string[];
  review_text?: string;
  permission_review: { reviewed_by?: string; reviewed_at?: string; scopes?: string[] };
  needs_review: boolean;
};

type Review = { scopes: string[]; unknown_actions: string[]; review_text: string };

const AUTH_LABEL: Record<string, string> = {
  oauth2: "OAuth 2.0",
  api_key: "API key",
  webhook: "Webhook",
  none: "None",
};

export default function WorkflowLibraryPage() {
  const [catalog, setCatalog] = useState<Integration[]>([]);
  const [installs, setInstalls] = useState<Install[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [actions, setActions] = useState<string[]>([]);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [catalogue, installed] = await Promise.all([
        request<{ integrations: Integration[] }>("/api/workflow-library/catalog"),
        request<{ installs: Install[] }>("/api/workflow-library/installs"),
      ]);
      setCatalog(catalogue.integrations ?? []);
      setInstalls(installed.installs ?? []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load the library");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const current = useMemo(
    () => catalog.find((entry) => entry.key === selected) ?? null,
    [catalog, selected],
  );

  useEffect(() => {
    setActions([]);
    setReview(null);
  }, [selected]);

  useEffect(() => {
    if (!selected || actions.length === 0) {
      setReview(null);
      return;
    }
    const query = actions.map((name) => `actions=${encodeURIComponent(name)}`).join("&");
    request<Review>(`/api/workflow-library/review/${selected}?${query}`)
      .then(setReview)
      .catch(() => setReview(null));
  }, [actions, selected]);

  const toggle = (name: string) =>
    setActions((prev) => (prev.includes(name) ? prev.filter((item) => item !== name) : [...prev, name]));

  const install = useCallback(async () => {
    if (!selected || actions.length === 0) {
      setMessage("Choose an integration and at least one action.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await request("/api/workflow-library/install", {
        method: "POST",
        body: JSON.stringify({ workflow_key: selected, actions, config: {}, enabled: false }),
      });
      setMessage(`${selected} installed. It stays off until you enable it.`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Install failed");
    } finally {
      setBusy(false);
    }
  }, [actions, load, selected]);

  const setEnabled = useCallback(
    async (key: string, enabled: boolean) => {
      setBusy(true);
      try {
        await request(`/api/workflow-library/${key}/enable`, {
          method: "POST",
          body: JSON.stringify({ enabled }),
        });
        await load();
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not change this install");
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">Integration library</h1>
        <p className="text-sm opacity-70">
          Every integration lists the permissions it needs before anything is installed. An install
          cannot be enabled until somebody has signed off on those permissions.
        </p>
      </header>

      {message ? (
        <div className="rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-sm">{message}</div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        {catalog.map((integration) => (
          <GlassCard
            key={integration.key}
            title={integration.name}
            description={`${integration.category} · ${AUTH_LABEL[integration.auth] ?? integration.auth}`}
          >
            <div className="flex items-center gap-2">
              <GlassBadge variant={integration.available ? "success" : "warning"} dot>
                {integration.available ? "Installable" : "Not configured"}
              </GlassBadge>
              {installs.some((row) => row.workflow_key === integration.key) ? (
                <GlassBadge variant="accent">Installed</GlassBadge>
              ) : null}
            </div>
            {integration.reason ? (
              <p className="mt-2 text-xs opacity-70">{integration.reason}</p>
            ) : null}
            <ul className="mt-3 flex flex-col gap-2">
              {integration.actions.map((action) => (
                <li key={action.name} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    aria-label={action.description}
                    disabled={!integration.available}
                    checked={selected === integration.key && actions.includes(action.name)}
                    onChange={() => {
                      setSelected(integration.key);
                      if (selected === integration.key) {
                        toggle(action.name);
                      } else {
                        setActions([action.name]);
                      }
                    }}
                  />
                  <span>
                    <span className="font-medium">{action.name}</span>
                    <span className="opacity-70"> — {action.description}</span>
                    {action.effect === "write" ? (
                      <GlassBadge variant="warning" size="sm">
                        writes data
                      </GlassBadge>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </GlassCard>
        ))}
      </div>

      {current ? (
        <GlassCard title={`Install ${current.name}`} description="Permissions you are approving">
          {review ? (
            <>
              <p className="text-sm">{review.review_text}</p>
              <ul className="mt-2 flex flex-wrap gap-2">
                {review.scopes.map((scope) => (
                  <li key={scope}>
                    <GlassBadge variant="info">{scope}</GlassBadge>
                  </li>
                ))}
              </ul>
              {review.unknown_actions.length ? (
                <p className="mt-2 text-sm text-amber-400">
                  Unknown action(s): {review.unknown_actions.join(", ")}
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-sm opacity-70">Pick the actions above to see the permission review.</p>
          )}
          <button
            type="button"
            disabled={busy || actions.length === 0}
            onClick={() => void install()}
            className="mt-4 rounded-lg bg-white/10 px-4 py-2 text-sm disabled:opacity-40"
          >
            Install (stays disabled until enabled)
          </button>
        </GlassCard>
      ) : null}

      <GlassCard title="Installed" description="Enable only what you have reviewed">
        {installs.length === 0 ? (
          <p className="text-sm opacity-70">Nothing installed yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left opacity-60">
              <tr>
                <th className="py-1">Integration</th>
                <th className="py-1">Version</th>
                <th className="py-1">Reviewed by</th>
                <th className="py-1">State</th>
                <th className="py-1"> </th>
              </tr>
            </thead>
            <tbody>
              {installs.map((row) => (
                <tr key={row.workflow_key} className="border-t border-white/5">
                  <td className="py-2">{row.name}</td>
                  <td className="py-2">v{row.version}</td>
                  <td className="py-2">{row.permission_review.reviewed_by ?? "—"}</td>
                  <td className="py-2">
                    {row.needs_review ? (
                      <GlassBadge variant="warning">review needed</GlassBadge>
                    ) : row.enabled ? (
                      <GlassBadge variant="success">enabled</GlassBadge>
                    ) : (
                      <GlassBadge variant="neutral">disabled</GlassBadge>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void setEnabled(row.workflow_key, !row.enabled)}
                      className="rounded-lg bg-white/10 px-3 py-1 text-xs disabled:opacity-40"
                    >
                      {row.enabled ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>
    </div>
  );
}
