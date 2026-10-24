"use client";

import React, { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { int2Api } from "../../lib/api";

function OAuthCallbackHandler() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState("Completing OAuth 2.0 exchange…");
  const [error, setError] = useState("");

  useEffect(() => {
    const provider = searchParams?.get("provider") || "";
    const code = searchParams?.get("code") || "";
    const state = searchParams?.get("state") || "";
    const oauthErr = searchParams?.get("error_description") || searchParams?.get("error") || "";

    if (oauthErr) {
      setError(oauthErr);
      window.opener?.postMessage(
        { type: "voxdesk_int2_oauth_complete", provider, error: oauthErr },
        window.location.origin
      );
      return;
    }

    if (!provider || !code || !state) {
      setError("Missing provider, code, or state parameters in callback URL.");
      return;
    }

    int2Api
      .completeOAuthCallback({ provider, code, state })
      .then((res) => {
        setStatus(`Connected ${res.connection.display_name}.`);
        if (window.opener) {
          window.opener.postMessage(
            {
              type: "voxdesk_int2_oauth_complete",
              provider,
              connection: res.connection,
            },
            window.location.origin
          );
          window.close();
        }
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "OAuth exchange failed";
        setError(msg);
        window.opener?.postMessage(
          { type: "voxdesk_int2_oauth_complete", provider, error: msg },
          window.location.origin
        );
      });
  }, [searchParams]);

  return (
    <div style={{ maxWidth: 560, margin: "3rem auto", padding: "1.5rem" }}>
      <h1 style={{ fontSize: "1.25rem" }}>OAuth 2.0 Callback</h1>
      {error ? (
        <div role="alert" style={{ color: "#991b1b", marginBottom: "1rem" }}>
          {error}
        </div>
      ) : (
        <p>{status}</p>
      )}
      <Link href="/integrations-v2">Return to Integrations</Link>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense fallback={<p style={{ padding: "2rem" }}>Loading callback…</p>}>
      <OAuthCallbackHandler />
    </Suspense>
  );
}
