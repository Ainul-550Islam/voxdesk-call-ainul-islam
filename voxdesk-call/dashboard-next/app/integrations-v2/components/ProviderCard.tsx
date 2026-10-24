"use client";

import React from "react";
import Link from "next/link";
import type { CatalogProvider } from "../lib/api";
import { StatusBadge } from "./StatusBadge";

export interface ProviderCardProps {
  provider: CatalogProvider;
}

export function ProviderCard({ provider }: ProviderCardProps) {
  return (
    <div
      data-testid={`provider-card-${provider.slug}`}
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 10,
        padding: "1rem",
        background: "#ffffff",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        gap: "0.75rem",
      }}
    >
      <div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "0.5rem",
          }}
        >
          <strong style={{ fontSize: "1rem", color: "#0f172a" }}>
            {provider.display_name}
          </strong>
          <StatusBadge status={provider.ui_status} />
        </div>

        <p
          style={{
            margin: "0.4rem 0",
            fontSize: "0.8rem",
            color: "#64748b",
          }}
        >
          {provider.docs_note || `Category: ${provider.category}`}
        </p>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.3rem",
            marginTop: "0.5rem",
          }}
        >
          {provider.capabilities.map((cap) => (
            <span
              key={cap}
              style={{
                fontSize: "0.7rem",
                padding: "0.12rem 0.45rem",
                background: "#f8fafc",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                color: "#334155",
              }}
            >
              {cap}
            </span>
          ))}
        </div>

        {provider.last_error && (
          <div
            style={{
              marginTop: "0.5rem",
              fontSize: "0.75rem",
              color: "#991b1b",
              background: "#fef2f2",
              padding: "0.35rem 0.5rem",
              borderRadius: 6,
            }}
          >
            {provider.last_error}
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          paddingTop: "0.5rem",
          borderTop: "1px solid #f1f5f9",
        }}
      >
        <StatusBadge status={provider.verification_status} />
        <Link
          href={`/integrations-v2/${provider.slug}`}
          style={{
            fontSize: "0.82rem",
            fontWeight: 600,
            color: "#2563eb",
            textDecoration: "none",
          }}
        >
          Configure →
        </Link>
      </div>
    </div>
  );
}
