"use client";

import React, { useEffect, useState } from "react";
import { int2Api, type ConnectionMasked } from "../lib/api";

export interface ConnectButtonProps {
  provider: string;
  instanceUrl?: string;
  region?: string;
  extraParams?: Record<string, unknown>;
  onConnected?: (connection: ConnectionMasked) => void;
}

export function ConnectButton({
  provider,
  instanceUrl = "",
  region = "us",
  extraParams = {},
  onConnected,
}: ConnectButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const handleMessage = (ev: MessageEvent) => {
      if (!ev.data || typeof ev.data !== "object") return;
      if (ev.data.type === "voxdesk_int2_oauth_complete" && ev.data.provider === provider) {
        setBusy(false);
        if (ev.data.error) {
          setError(String(ev.data.error));
        } else if (ev.data.connection && onConnected) {
          onConnected(ev.data.connection as ConnectionMasked);
        }
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [provider, onConnected]);

  const handleClick = async () => {
    setBusy(true);
    setError("");
    try {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      const redirectUri = origin
        ? `${origin}/integrations-v2/oauth/callback?provider=${encodeURIComponent(provider)}`
        : "";
      const res = await int2Api.connectProvider(provider, {
        instance_url: instanceUrl,
        region,
        redirect_uri: redirectUri,
        extra_params: extraParams,
      });
      if (res.connection) {
        setBusy(false);
        onConnected?.(res.connection);
        return;
      }
      if (res.authorize_url) {
        const popup = window.open(
          res.authorize_url,
          `voxdesk_oauth_${provider}`,
          "width=620,height=720"
        );
        if (!popup) {
          window.location.assign(res.authorize_url);
        }
      } else {
        setBusy(false);
        setError("Authorization URL was not returned by server.");
      }
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Failed to start OAuth flow");
    }
  };

  return (
    <div>
      <button
        type="button"
        data-testid={`connect-oauth-${provider}`}
        disabled={busy}
        onClick={handleClick}
        style={{
          padding: "0.45rem 0.9rem",
          borderRadius: 6,
          border: "none",
          background: "#2563eb",
          color: "#ffffff",
          fontWeight: 600,
          cursor: busy ? "wait" : "pointer",
        }}
      >
        {busy ? "Connecting…" : "Connect via OAuth 2.0"}
      </button>
      {error && (
        <div
          role="alert"
          style={{
            marginTop: "0.45rem",
            color: "#991b1b",
            fontSize: "0.82rem",
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
