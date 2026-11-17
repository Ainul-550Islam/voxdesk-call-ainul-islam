// File: dashboard-next/app/share/[token]/share-link-view.tsx — public share link page (PART 14, P14-03)
// The client half. `page.tsx` resolves the route param (Next 16 hands it over as a
// promise) and renders this component with a plain `token` prop, so no React 19
// `use()` is needed on a React 18 runtime.
"use client";

import { useCallback, useEffect, useState } from "react";

type LinkInfo = {
  agent_id: string;
  label: string;
  requires_passcode: boolean;
  max_seconds: number;
  remaining_uses: number;
  expires_at: string;
};

type Session = {
  session_token: string;
  agent_id: string;
  max_seconds: number;
  expires_at_unix: number;
};

const COPY = {
  title: "Talk to our assistant",
  loading: "Checking this link…",
  passcode: "This link is protected. Enter the passcode you were given.",
  start: "Start",
  expired: "This link has expired or has already been used.",
  limit: "You can talk for up to",
  seconds: "seconds",
  ready: "You are connected. Keep this tab open.",
};

function apiBase(): string {
  // Relative URLs keep the page on whatever host served it: no localhost, no
  // hard-coded domain that breaks in a self-hosted deployment.
  return "";
}

export default function ShareLinkView({ token }: { token: string }) {
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [passcode, setPasscode] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "blocked" | "error">("loading");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase()}/api/v1/public/share/${encodeURIComponent(token)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        setState("blocked");
        setMessage(COPY.expired);
        return;
      }
      const payload = (await response.json()) as LinkInfo;
      setInfo(payload);
      setState("ready");
    } catch {
      setState("error");
      setMessage("We could not check this link. Please try again.");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const redeem = useCallback(async () => {
    try {
      const response = await fetch(
        `${apiBase()}/api/v1/public/share/${encodeURIComponent(token)}/redeem`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(passcode ? { passcode } : {}),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = payload && payload.detail;
        const code = detail && typeof detail === "object" ? detail.code : "";
        setMessage(
          code === "bad_passcode" || code === "passcode_required"
            ? "That passcode was not accepted."
            : COPY.expired,
        );
        return;
      }
      setSession(payload as Session);
      setMessage("");
    } catch {
      setMessage("We could not start the session. Please try again.");
    }
  }, [token, passcode]);

  if (state === "loading") {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-6">
        <p className="text-sm opacity-70">{COPY.loading}</p>
      </main>
    );
  }

  if (state !== "ready" || !info) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-6 text-center">
        <h1 className="text-xl font-semibold">{COPY.title}</h1>
        <p className="text-sm opacity-70">{message || COPY.expired}</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-5 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{COPY.title}</h1>
        <p className="text-sm opacity-70">
          {info.label || info.agent_id} · {COPY.limit} {info.max_seconds} {COPY.seconds}
        </p>
      </header>

      {session ? (
        <section className="rounded-xl border border-white/10 bg-white/5 p-4">
          <p className="text-sm">{COPY.ready}</p>
          <p className="mt-2 text-xs opacity-60">
            Session ends at {new Date(session.expires_at_unix * 1000).toLocaleTimeString()}
          </p>
          {/* The token is held in memory only: writing it to localStorage would
              let any script on the page reuse the remaining seconds. */}
          <p className="mt-1 font-mono text-[10px] break-all opacity-40">{session.session_token.slice(0, 12)}…</p>
        </section>
      ) : (
        <section className="flex flex-col gap-3">
          {info.requires_passcode ? (
            <>
              <p className="text-sm opacity-70">{COPY.passcode}</p>
              <input
                type="password"
                value={passcode}
                autoComplete="one-time-code"
                onChange={(event) => setPasscode(event.target.value)}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm"
                aria-label="Passcode"
              />
            </>
          ) : null}
          <button
            type="button"
            onClick={() => void redeem()}
            className="rounded-lg bg-white/10 px-4 py-2 text-sm"
          >
            {COPY.start}
          </button>
          <p className="text-xs opacity-60">
            {info.remaining_uses} use{info.remaining_uses === 1 ? "" : "s"} left · expires{" "}
            {new Date(info.expires_at).toLocaleString()}
          </p>
        </section>
      )}

      {message ? <p className="text-sm text-amber-400">{message}</p> : null}
    </main>
  );
}
