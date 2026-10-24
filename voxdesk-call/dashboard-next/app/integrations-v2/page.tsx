"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { int2Api, type CatalogProvider } from "./lib/api";
import { ProviderCard } from "./components/ProviderCard";

const CATEGORY_ORDER: Array<{ key: "helpdesk" | "crm" | "collab"; title: string }> = [
  { key: "helpdesk", title: "Helpdesk & ITSM" },
  { key: "crm", title: "CRM Connectors" },
  { key: "collab", title: "Collaboration & Meetings" },
];

export default function IntegrationsV2CatalogPage() {
  const [providers, setProviders] = useState<CatalogProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    int2Api
      .getCatalog()
      .then((res) => {
        if (!active) return;
        setProviders(res.providers);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Failed to load catalog");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main style={{ maxWidth: 1180, margin: "0 auto", padding: "1.75rem" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1.5rem",
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: "1.6rem" }}>Integrations v2</h1>
          <p style={{ margin: "0.35rem 0 0", color: "#475569", fontSize: "0.9rem" }}>
            Helpdesk, CRM, and Collaboration connectors with live connection state.
          </p>
        </div>

        <nav style={{ display: "flex", gap: "0.85rem", fontSize: "0.88rem" }}>
          <Link href="/integrations-v2/sync-runs">Sync Runs</Link>
          <Link href="/integrations-v2/notifications">Notifications</Link>
          <Link href="/integrations-v2/agent-tools">Agent Tools</Link>
          <Link href="/integrations-v2/matrix">Capability Matrix</Link>
        </nav>
      </header>

      {loading && <p>Loading provider catalog…</p>}
      {error && (
        <div
          role="alert"
          style={{
            padding: "0.85rem 1rem",
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}

      {!loading &&
        !error &&
        CATEGORY_ORDER.map(({ key, title }) => {
          const items = providers.filter((p) => p.category === key);
          if (items.length === 0) return null;
          return (
            <section key={key} style={{ marginBottom: "2rem" }}>
              <h2 style={{ fontSize: "1.15rem", marginBottom: "0.75rem" }}>
                {title}
              </h2>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))",
                  gap: "1rem",
                }}
              >
                {items.map((p) => (
                  <ProviderCard key={p.slug} provider={p} />
                ))}
              </div>
            </section>
          );
        })}
    </main>
  );
}
