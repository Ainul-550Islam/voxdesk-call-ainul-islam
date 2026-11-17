"use client";

/**
 * Public request-a-call demo (PART 18, P18-03).
 *
 * Three steps, and the page refuses to skip any of them: verify the number you
 * control, tick the consent, solve the captcha. The server re-checks all three —
 * this file only makes the refusal readable.
 *
 * Design notes that are not cosmetic:
 *  * The consent wording comes from `GET /demo`, not from this file. If it were
 *    hard-coded here a stale tab would show today's text while sending an old
 *    version, which is exactly the case the server refuses.
 *  * Every refusal is rendered from the server's `code`, so a new gate added
 *    server-side appears here without a UI change.
 *  * The daily budget is shown up front. A demo that silently stops working at
 *    call 25 looks broken; one that says "3 calls left today" does not.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

const API = "/api/v1/public/demo";

type CaptchaInfo = {
  provider: string;
  site_key: string;
  enabled: boolean;
};

type DemoStatus = {
  enabled: boolean;
  calls_today: number;
  calls_remaining: number;
  seconds_used: number;
  seconds_remaining: number;
  max_call_seconds: number;
  captcha: CaptchaInfo;
  consent_text: string;
  consent_version: string;
};

type Step = "verify" | "confirm" | "call" | "done";

type ApiError = { code?: string; message?: string; detail?: unknown };

function messageFor(error: ApiError): string {
  switch (error.code) {
    case "destination_not_e164":
      return "Enter the number in international format, starting with the country code (+).";
    case "destination_not_verified":
      return "That number is not verified yet. Verify it first — we only call numbers you control.";
    case "consent_required":
      return "Tick the consent box. We do not place a demo call without it.";
    case "consent_text_changed":
      return "The consent wording changed while this page was open. Reload and read it again.";
    case "captcha_missing":
    case "captcha_failed":
    case "captcha_low_score":
      return "The captcha check did not pass. Try again.";
    case "verification_code_wrong":
      return "That code is not correct.";
    case "verification_expired":
      return "That code has expired. Request a new one.";
    case "verification_attempts_exhausted":
      return "Too many wrong codes for this number. Try again in 15 minutes.";
    case "rate_limited_ip":
      return "This connection has requested several demos recently. Try again later.";
    case "rate_limited_number":
      return "This number has already had its demo calls for today.";
    case "demo_daily_cap_reached":
    case "demo_seconds_cap_reached":
      return "The public demo has used its calls for today. Book a walkthrough instead — it is a better demo anyway.";
    case "demo_disabled":
      return "The public demo is closed right now.";
    case "dial_failed":
      return "The demo call could not be placed. Nothing was charged to you.";
    default:
      return error.message || "Something went wrong. Try again.";
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload?.detail ?? {}) as ApiError;
    throw Object.assign(new Error(messageFor(detail)), { code: detail.code });
  }
  return payload as T;
}

export default function DemoPage() {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [step, setStep] = useState<Step>("verify");
  const [number, setNumber] = useState("");
  const [code, setCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [captchaToken, setCaptchaToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [masked, setMasked] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    fetch(API, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setStatus)
      .catch(() => setError("The demo status could not be loaded."));
  }, []);

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }, []);

  const onRequestCode = () =>
    run(async () => {
      const response = await post<{ destination: string }>("/verification", { number });
      setMasked(response.destination);
      setStep("confirm");
    });

  const onConfirmCode = () =>
    run(async () => {
      await post("/verification/confirm", { number, code });
      setStep("call");
    });

  const onCall = () =>
    run(async () => {
      const response = await post<Record<string, unknown>>("/call", {
        number,
        consent,
        consent_version: status?.consent_version ?? "",
        captcha_token: captchaToken || null,
      });
      setResult(response);
      setStep("done");
    });

  const budget = useMemo(() => {
    if (!status) return "";
    return `${status.calls_remaining} of ${status.calls_today + status.calls_remaining} demo calls left today`;
  }, [status]);

  if (error && !status) {
    return (
      <main className="demo-shell">
        <div className="error-banner">{error}</div>
      </main>
    );
  }

  if (!status) {
    return (
      <main className="demo-shell">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <main className="demo-shell">
      <header className="demo-head">
        <h1>Have the agent call you</h1>
        <p className="lede">
          One real phone call from the product, not a recording. Verify your
          number, tick the consent, and it calls within a few seconds.
        </p>
        <p className="muted budget" aria-live="polite">
          {budget} · calls are capped at {status.max_call_seconds} seconds
        </p>
      </header>

      {!status.enabled && (
        <div className="notice notice-closed">
          The public demo is closed right now. Book a walkthrough and we will
          call you at a time that suits you.
        </div>
      )}

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      <ol className="steps" aria-label="Demo steps">
        <li aria-current={step === "verify" ? "step" : undefined}>1. Your number</li>
        <li aria-current={step === "confirm" ? "step" : undefined}>2. The code</li>
        <li aria-current={step === "call" || step === "done" ? "step" : undefined}>3. The call</li>
      </ol>

      {step === "verify" && (
        <section className="card">
          <h2>Where should it call?</h2>
          <label htmlFor="demo-number">
            Phone number <span className="muted">(international format)</span>
          </label>
          <input
            id="demo-number"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            placeholder="+15550100111"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
            aria-describedby="demo-number-help"
          />
          <p id="demo-number-help" className="muted small">
            We send one SMS with a code. That is how we know the number is yours —
            and why nobody can point this demo at someone else&rsquo;s phone.
          </p>
          <button className="primary" onClick={onRequestCode} disabled={busy || !number}>
            {busy ? "Sending…" : "Send me a code"}
          </button>
        </section>
      )}

      {step === "confirm" && (
        <section className="card">
          <h2>Enter the code</h2>
          <p className="muted">
            Sent to {masked ?? number}. It expires in 15 minutes and works once.
          </p>
          <label htmlFor="demo-code">6-digit code</label>
          <input
            id="demo-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={8}
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <button className="primary" onClick={onConfirmCode} disabled={busy || code.length < 4}>
            {busy ? "Checking…" : "Confirm"}
          </button>
        </section>
      )}

      {step === "call" && (
        <section className="card">
          <h2>One call, {status.max_call_seconds} seconds</h2>

          <div className="consent">
            <input
              id="demo-consent"
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            <label htmlFor="demo-consent">{status.consent_text}</label>
          </div>

          {status.captcha.enabled && (
            <div className="captcha">
              <label htmlFor="demo-captcha">
                Human check ({status.captcha.provider})
              </label>
              <input
                id="demo-captcha"
                placeholder="captcha token"
                value={captchaToken}
                onChange={(event) => setCaptchaToken(event.target.value)}
                aria-describedby="demo-captcha-help"
              />
              <p id="demo-captcha-help" className="muted small">
                In a full build this is rendered by the provider&rsquo;s widget against
                site key <code>{status.captcha.site_key || "(not configured)"}</code>.
              </p>
            </div>
          )}

          <button className="primary" onClick={onCall} disabled={busy || !consent}>
            {busy ? "Dialling…" : "Call me now"}
          </button>
          <p className="muted small">
            Your handset will ring once. If it does not, that is a fault on our
            side and nothing was charged to you.
          </p>
        </section>
      )}

      {step === "done" && (
        <section className="card success" role="status">
          <h2>Calling you now</h2>
          <p className="muted">
            {String((result as { destination?: string })?.destination ?? "")} — the
            call is live for up to {String((result as { max_seconds?: number })?.max_seconds ?? status.max_call_seconds)} seconds.
          </p>
          <p className="muted small">
            The transcript appears in the workspace the moment the call ends. Ask
            for it in the walkthrough and we will walk through it line by line.
          </p>
        </section>
      )}
    </main>
  );
}
