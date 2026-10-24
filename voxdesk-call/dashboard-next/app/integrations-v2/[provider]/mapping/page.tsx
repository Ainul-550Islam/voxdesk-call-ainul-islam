"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  int2Api,
  type ConnectionMasked,
  type FieldMappingRuleRow,
} from "../../lib/api";
import { MappingTable, type MappingValidationError } from "../../components/MappingTable";

export default function ProviderFieldMappingPage() {
  const params = useParams<{ provider: string }>();
  const slug = String(params?.provider || "");

  const [connection, setConnection] = useState<ConnectionMasked | null>(null);
  const [entityType, setEntityType] = useState<"Contact" | "Ticket">("Contact");
  const [transforms, setTransforms] = useState<string[]>(["identity"]);
  const [rules, setRules] = useState<FieldMappingRuleRow[]>([]);
  const [samplePayload, setSamplePayload] = useState<Record<string, unknown> | null>(
    null
  );
  const [previewOutput, setPreviewOutput] = useState<Record<string, unknown> | null>(
    null
  );
  const [validationErrors, setValidationErrors] = useState<MappingValidationError[]>(
    []
  );
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([int2Api.getCatalog(), int2Api.listConnections()])
      .then(async ([cat, conns]) => {
        if (!active) return;
        setTransforms(cat.supported_transforms);
        const conn = conns.connections.find((c) => c.provider === slug) || null;
        setConnection(conn);
        const defaultEntity = conn?.category === "helpdesk" ? "Ticket" : "Contact";
        setEntityType(defaultEntity);
        if (conn) {
          const loaded = await int2Api.listMappings(conn.id, defaultEntity);
          if (active) setRules(loaded.rules);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load mappings");
        }
      });
    return () => {
      active = false;
    };
  }, [slug]);

  const handleFetchSample = async () => {
    if (!connection) return;
    setError("");
    setStatusMessage("");
    try {
      const res = await int2Api.fetchLiveSamplePayload(connection.id, entityType);
      setSamplePayload(res.sample);
      if (!res.sample) {
        setStatusMessage("Connected provider returned zero records for sample fetch.");
      } else {
        const preview = await int2Api.previewMappings({
          direction: "inbound",
          rules,
          sample_payload: res.sample,
        });
        setPreviewOutput(preview.output);
        setValidationErrors(preview.errors);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sample payload fetch failed");
    }
  };

  const handleValidateCurrent = async () => {
    if (!samplePayload) {
      setError("Fetch a live sample payload from the provider before previewing.");
      return;
    }
    setError("");
    try {
      const preview = await int2Api.previewMappings({
        direction: "inbound",
        rules,
        sample_payload: samplePayload,
      });
      setPreviewOutput(preview.output);
      setValidationErrors(preview.errors);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    }
  };

  const handleSave = async () => {
    if (!connection) return;
    setError("");
    setStatusMessage("");
    try {
      const res = await int2Api.saveMappings(connection.id, entityType, rules);
      setStatusMessage(`Saved ${res.saved_count} field mapping rule(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save field mappings");
    }
  };

  return (
    <main style={{ maxWidth: 1020, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href={`/integrations-v2/${slug}`}>← Back to {slug}</Link>
      </div>

      <h1 style={{ marginTop: 0 }}>Field Mapping — {slug}</h1>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}
      {statusMessage && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "#ecfdf5",
            color: "#065f46",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {statusMessage}
        </div>
      )}

      {!connection ? (
        <p style={{ color: "#64748b" }}>
          Connect {slug} first before editing field mappings or pulling live sample payloads.
        </p>
      ) : (
        <>
          <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem" }}>
            <label style={{ fontSize: "0.85rem" }}>
              Entity:{" "}
              <select
                value={entityType}
                onChange={async (e) => {
                  const next = e.target.value as "Contact" | "Ticket";
                  setEntityType(next);
                  const loaded = await int2Api.listMappings(connection.id, next);
                  setRules(loaded.rules);
                }}
              >
                <option value="Contact">Contact</option>
                <option value="Ticket">Ticket</option>
              </select>
            </label>

            <button type="button" onClick={handleFetchSample}>
              Fetch Live Sample from Provider
            </button>
            <button type="button" onClick={handleValidateCurrent}>
              Validate Against Sample
            </button>
            <button
              type="button"
              onClick={handleSave}
              style={{
                marginLeft: "auto",
                background: "#2563eb",
                color: "#ffffff",
                border: "none",
                borderRadius: 6,
                padding: "0.4rem 0.9rem",
                fontWeight: 600,
              }}
            >
              Save Mapping Profile
            </button>
          </div>

          <MappingTable
            rules={rules}
            transforms={transforms}
            validationErrors={validationErrors}
            onChange={setRules}
          />

          {samplePayload && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "1rem",
                marginTop: "1.25rem",
              }}
            >
              <div>
                <h3 style={{ fontSize: "0.95rem" }}>Live Provider Sample</h3>
                <pre
                  style={{
                    background: "#0f172a",
                    color: "#f8fafc",
                    padding: "0.75rem",
                    borderRadius: 8,
                    overflowX: "auto",
                    fontSize: "0.78rem",
                  }}
                >
                  {JSON.stringify(samplePayload, null, 2)}
                </pre>
              </div>
              <div>
                <h3 style={{ fontSize: "0.95rem" }}>Mapped VoxDesk Output</h3>
                <pre
                  style={{
                    background: "#0f172a",
                    color: "#f8fafc",
                    padding: "0.75rem",
                    borderRadius: 8,
                    overflowX: "auto",
                    fontSize: "0.78rem",
                  }}
                >
                  {JSON.stringify(previewOutput ?? {}, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </>
      )}
    </main>
  );
}
