"use client";

import { useEffect, useState } from "react";
import { BASE_URL, request } from "@/lib/api";
import type { ApiConnectionStatus, BackendHealth } from "@/lib/agent-factory-types";

// Backend API Connection Status Component
// Shows real-time connection to FastAPI backend
// Backend: app/main.py lifespan, health_routes.py, 84 routers

export default function ApiStatus() {
  const [status, setStatus] = useState<ApiConnectionStatus>({
    connected: false,
    base_url: BASE_URL,
    last_check: new Date().toISOString(),
    error: null,
  });
  const [health, setHealth] = useState<BackendHealth | null>(null);

  useEffect(() => {
    let cancelled = false;
    
    async function checkConnection() {
      const start = performance.now();
      try {
        // Try health endpoint — real backend API
        const healthData = await request<BackendHealth>("/health", {}, { retryOn401: false });
        if (cancelled) return;
        const latency = Math.round(performance.now() - start);
        setStatus({
          connected: true,
          latency_ms: latency,
          base_url: BASE_URL,
          last_check: new Date().toISOString(),
          error: null,
        });
        setHealth(healthData);
      } catch (err) {
        if (cancelled) return;
        // Try alternative — /api/analytics/overview requires auth, so we check if we get 401 (means backend is up)
        try {
          await request("/api/analytics/overview", {}, { retryOn401: false });
        } catch (e: any) {
          const isBackendUp = e.status === 401 || e.status === 422 || e.status === 403;
          if (isBackendUp) {
            const latency = Math.round(performance.now() - start);
            setStatus({
              connected: true,
              latency_ms: latency,
              base_url: BASE_URL,
              last_check: new Date().toISOString(),
              error: null,
            });
            return;
          }
        }
        setStatus({
          connected: false,
          base_url: BASE_URL,
          last_check: new Date().toISOString(),
          error: err instanceof Error ? err.message : "Backend not reachable",
        });
      }
    }

    checkConnection();
    const interval = setInterval(checkConnection, 30000); // Check every 30s

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className={`api-status ${status.connected ? "connected" : "disconnected"}`} title={`Backend: ${status.base_url} | Last check: ${status.last_check} | Latency: ${status.latency_ms || "?"}ms`}>
      <span className="dot" />
      <span>
        {status.connected ? `API Connected (${status.latency_ms || "?"}ms)` : "API Disconnected"}
      </span>
      {health && (
        <span style={{ fontSize: 10, opacity: 0.7 }}>
          v{health.version} | {health.environment} | DB {health.database}
        </span>
      )}
      {status.error && !status.connected && (
        <span style={{ fontSize: 10, color: "#991b1b" }} title={status.error}>
          {status.error.slice(0, 50)}
        </span>
      )}
    </div>
  );
}
