// File: dashboard-next/app/dashboard/telephony/concurrency/page.tsx — limits, reserved, burst and the 24 h series (PART 13, P13-05)
"use client";

import { useCallback, useEffect, useState } from "react";
import { request } from "@/lib/api";
import { GlassCard } from "@/components/enterprise/GlassCard";

type Snapshot = {
  base: number;
  reserved_inbound: number;
  burst_ceiling: number;
  burst_billing: boolean;
  in_use: { total: number; inbound: number; outbound: number };
  remaining: number;
  over_base: number;
};

type Point = { bucket_start: string; inbound: number; outbound: number; total: number; peak: number };
type Series = { points: Point[]; source: string; hours: number };

export default function ConcurrencyPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [draft, setDraft] = useState({ base: 10, reserved_inbound: 2, burst_billing: true });
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      // `GET /api/concurrency` and the series are two views of one state; they
      // are fetched together so the header cannot disagree with the chart.
      const [current, points] = await Promise.all([
        request<Snapshot>("/api/concurrency"),
        request<Series>("/api/concurrency/series?hours=24"),
      ]);
      setSnapshot(current);
      setSeries(points);
      setDraft({
        base: current.base,
        reserved_inbound: current.reserved_inbound,
        burst_billing: current.burst_billing,
      });
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not load concurrency");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    try {
      await request<Snapshot>("/api/concurrency/limits", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base: Number(draft.base),
          reserved_inbound: Number(draft.reserved_inbound),
          burst_billing: draft.burst_billing,
          provider_cps: {},
        }),
      });
      setMessage("Saved. The burst ceiling is derived, so it followed automatically.");
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not save the limit");
    }
  };

  const points = series?.points ?? [];
  const peak = Math.max(1, ...points.map((point) => point.total));
  const barWidth = points.length ? Math.max(1, Math.floor(600 / points.length)) : 1;

  return (
    <div className="space-y-6">
      <GlassCard
        title="Concurrency"
        description="Base is what you pay for; the inbound reserve can never be taken by outbound; burst is billed."
      >
        {snapshot ? (
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-5">
            <div>
              <div className="opacity-70">Base</div>
              <div className="text-xl">{snapshot.base}</div>
            </div>
            <div>
              <div className="opacity-70">Reserved inbound</div>
              <div className="text-xl">{snapshot.reserved_inbound}</div>
            </div>
            <div>
              <div className="opacity-70">Burst ceiling</div>
              <div className="text-xl">{snapshot.burst_ceiling}</div>
            </div>
            <div>
              <div className="opacity-70">In use</div>
              <div className="text-xl">
                {snapshot.in_use.total} <span className="text-xs opacity-70">
                  ({snapshot.in_use.inbound} in / {snapshot.in_use.outbound} out)
                </span>
              </div>
            </div>
            <div>
              <div className="opacity-70">Over base</div>
              <div className="text-xl">{snapshot.over_base}</div>
            </div>
          </div>
        ) : (
          <p className="text-sm opacity-70">Loading…</p>
        )}
      </GlassCard>

      <GlassCard title="Last 24 hours" description="What the sampler stored. Empty means it has not run yet — not zero.">
        {points.length === 0 ? (
          <p className="text-sm opacity-70">No samples yet ({series?.source ?? "empty"}).</p>
        ) : (
          <svg viewBox={`0 0 ${points.length * barWidth} 120`} className="h-32 w-full" role="img" aria-label="Concurrency over the last 24 hours">
            {points.map((point, index) => {
              const height = Math.round((point.total / peak) * 110);
              const inbound = Math.round((point.inbound / peak) * 110);
              return (
                <g key={point.bucket_start}>
                  <rect x={index * barWidth} y={110 - height} width={barWidth} height={height} fill="rgba(120,180,255,0.55)" />
                  <rect x={index * barWidth} y={110 - inbound} width={barWidth} height={inbound} fill="rgba(120,255,180,0.75)" />
                </g>
              );
            })}
          </svg>
        )}
        <p className="mt-2 text-xs opacity-60">Green is inbound (inside the reserve), blue is outbound.</p>
      </GlassCard>

      <GlassCard title="Entitlement" description="Raising base raises the burst ceiling to min(3×base, base+300).">
        <div className="flex flex-wrap items-end gap-4 text-sm">
          <label>
            <span className="opacity-70">Base</span>
            <input
              type="number"
              min={1}
              className="mt-1 w-24 rounded border bg-transparent px-2 py-1"
              value={draft.base}
              onChange={(event) => setDraft({ ...draft, base: Number(event.target.value) })}
            />
          </label>
          <label>
            <span className="opacity-70">Reserved inbound</span>
            <input
              type="number"
              min={0}
              className="mt-1 w-24 rounded border bg-transparent px-2 py-1"
              value={draft.reserved_inbound}
              onChange={(event) =>
                setDraft({ ...draft, reserved_inbound: Number(event.target.value) })
              }
            />
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.burst_billing}
              onChange={(event) => setDraft({ ...draft, burst_billing: event.target.checked })}
            />
            Bill calls above base
          </label>
          <button className="rounded bg-white/10 px-3 py-1.5" onClick={() => void save()}>
            Save
          </button>
        </div>
        {message ? <p className="mt-3 text-sm opacity-80">{message}</p> : null}
      </GlassCard>
    </div>
  );
}
