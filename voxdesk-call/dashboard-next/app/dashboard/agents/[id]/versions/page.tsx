// File: dashboard-next/app/dashboard/agents/[id]/versions/page.tsx — version history with a three-way merge dialog (PART 12, P12-21)
"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { request } from "@/lib/api";
import { GlassCard } from "@/components/enterprise/GlassCard";

// The merge endpoint takes three snapshots and returns the merged document plus a conflict list.
// The UI never invents a winner: it shows the conflicts and asks which side to keep, which is the
// whole point of a three-way merge over "last write wins".
type MergeConflict = { path: string; base: unknown; ours: unknown; theirs: unknown };
type MergeResponse = {
  merged: Record<string, unknown>;
  conflicts: MergeConflict[];
  clean: boolean;
};

type VersionRow = {
  id: string;
  version_number: number;
  status: string;
  created_at?: string;
};

export default function AgentVersionsPage() {
  const params = useParams<{ id: string }>();
  const agentId = String(params?.id ?? "");

  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [base, setBase] = useState<string>("");
  const [ours, setOurs] = useState<string>("");
  const [theirs, setTheirs] = useState<string>("");
  const [merge, setMerge] = useState<MergeResponse | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, "ours" | "theirs">>({});
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await request<{ versions: VersionRow[] }>(`/api/agents/${agentId}/versions`);
      setVersions(response.versions ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load versions");
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runMerge = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await request<MergeResponse>(`/api/agents/${agentId}/versions/merge`, {
        method: "POST",
        body: JSON.stringify({
          base_version: Number(base),
          ours_version: Number(ours),
          theirs_version: Number(theirs),
          strategy: "manual",
        }),
      });
      setMerge(response);
      setResolutions({});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Merge failed");
    } finally {
      setBusy(false);
    }
  };

  const unresolved = merge ? merge.conflicts.filter((conflict) => !resolutions[conflict.path]) : [];

  return (
    <main style={{ padding: 24, display: "grid", gap: 16 }}>
      <GlassCard title="Version history" description={`Agent ${agentId}`}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              <th align="left">Version</th>
              <th align="left">Status</th>
              <th align="left">Created</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((version) => (
              <tr key={version.id}>
                <td>v{version.version_number}</td>
                <td>{version.status}</td>
                <td>{version.created_at ?? "—"}</td>
              </tr>
            ))}
            {versions.length === 0 && (
              <tr>
                <td colSpan={3}>No versions yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </GlassCard>

      <GlassCard title="Three-way merge" description="Pick a base, ours and theirs. Conflicts are never resolved silently.">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "end" }}>
          {[
            { label: "Base", value: base, set: setBase },
            { label: "Ours", value: ours, set: setOurs },
            { label: "Theirs", value: theirs, set: setTheirs },
          ].map((field) => (
            <label key={field.label} style={{ fontSize: 13 }}>
              {field.label}
              <select
                value={field.value}
                onChange={(event) => field.set(event.target.value)}
                style={{ display: "block", marginTop: 4, padding: 6, borderRadius: 4, border: "1px solid #ccc" }}
              >
                <option value="">Select…</option>
                {versions.map((version) => (
                  <option key={version.id} value={version.version_number}>
                    v{version.version_number}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <button
            type="button"
            onClick={runMerge}
            disabled={busy || !base || !ours || !theirs}
            style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid #6366f1", background: "#4338ca", color: "white" }}
          >
            {busy ? "Merging…" : "Merge"}
          </button>
        </div>

        {error && <p style={{ color: "#fca5a5", fontSize: 13 }}>{error}</p>}

        {merge && (
          <div data-testid="merge-result" style={{ marginTop: 16 }}>
            <p style={{ fontSize: 13 }}>
              {merge.clean
                ? "Merged with no conflicts."
                : `${merge.conflicts.length} conflict(s) need a decision.`}
            </p>
            {merge.conflicts.map((conflict) => (
              <div
                key={conflict.path}
                style={{
                  border: "1px solid rgba(148,163,184,0.35)",
                  borderRadius: 6,
                  padding: 10,
                  marginBottom: 8,
                  fontSize: 13,
                }}
              >
                <strong>{conflict.path}</strong>
                <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
                  <label>
                    <input
                      type="radio"
                      name={`resolve-${conflict.path}`}
                      checked={resolutions[conflict.path] === "ours"}
                      onChange={() => setResolutions({ ...resolutions, [conflict.path]: "ours" })}
                    />{" "}
                    Keep ours: <code>{JSON.stringify(conflict.ours)}</code>
                  </label>
                  <label>
                    <input
                      type="radio"
                      name={`resolve-${conflict.path}`}
                      checked={resolutions[conflict.path] === "theirs"}
                      onChange={() => setResolutions({ ...resolutions, [conflict.path]: "theirs" })}
                    />{" "}
                    Keep theirs: <code>{JSON.stringify(conflict.theirs)}</code>
                  </label>
                  <span style={{ color: "#94a3b8" }}>
                    base: <code>{JSON.stringify(conflict.base)}</code>
                  </span>
                </div>
              </div>
            ))}
            <button
              type="button"
              disabled={unresolved.length > 0}
              style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid #22c55e", background: "#166534", color: "white" }}
            >
              {unresolved.length > 0
                ? `Resolve ${unresolved.length} conflict(s) first`
                : "Save merged draft"}
            </button>
          </div>
        )}
      </GlassCard>
    </main>
  );
}
