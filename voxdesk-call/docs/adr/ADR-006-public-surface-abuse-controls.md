# ADR-006 — Public chat, share links and the four abuse gates

**Status:** accepted · **Part:** 14 (P14-01/02/03) · **Date:** 2026-11-11

## Context

Three public surfaces spend real money:

- the embeddable chat widget (`widget/chat.js` → `POST /api/v1/public/chat/session`);
- share links (`/share/{token}` → `/api/v1/public/share/{token}/redeem`);
- the existing voice widget bootstrap.

Each buys STT/LLM/TTS seconds with no authenticated user behind it. The threats
are ordinary: a scraper creating sessions in a loop, a forwarded link being used
by a hundred people, a captcha-farm solving challenges at 0.3 confidence, and
toll fraud — many calls from one visitor, or destinations concentrated in
high-cost prefixes.

## Decision

**Four independent gates, cheapest first.** They are separate because they fail
differently, and a single combined "risk score" would make an incident review
unable to say which one fired.

1. **Key + origin** — the existing `authenticate_public_key` path (unchanged).
2. **Toll-fraud heuristics** — `toll_fraud_check` flags rapid reuse of one
   fingerprint and a majority of high-cost destinations. Two readable rules, not
   a score.
3. **Captcha** — provider chosen per public key (`recaptcha_v3`, `hcaptcha`,
   `turnstile`) with a per-key score threshold in `public_widget_keys.widget_config`.
   A missing token, a low score, a provider error and an unconfigured verifier
   are *all* refusals: an outage must not open the gate.
4. **Budget** — a per-key daily cap on requests and on billable seconds, counted
   in `public_abuse_budgets` by UTC day. A cap without a counter is a wish.

**Share links are capability URLs and are treated as such.** Only hashes of the
token and the passcode are stored; expiry is mandatory (max 30 days); the usage
cap is enforced with a conditional `UPDATE ... WHERE used_count < max_uses`, so
two concurrent redemptions cannot both take the last use. Redemption mints a
short-lived HMAC-signed session envelope (share id, tenant, agent, expiry), which
the chat/voice bootstrap verifies server-side — the browser's copy of the claims
is never trusted.

**Refusals are specific.** `captcha_missing`, `captcha_low_score`,
`budget_requests_exhausted`, `budget_seconds_exhausted`, `toll_fraud_rapid`,
`expired`, `exhausted`, `bad_passcode`. The widget renders the message; the
support engineer reads the code.

## Consequences

- A tenant can see, per key, today's requests, seconds and refusals.
- Disabling captcha is possible and explicit (`captcha.enabled=false`), and it is
  visible in the key configuration rather than implied by a missing secret.
- Nothing on the public path reveals whether a *different* token exists: the
  errors say the caller's own link is unusable, and no enumeration is possible
  because only a hash is stored and looked up.
- Rate limiting stays where it already was (`app/core/rate_limit.py`); these
  gates sit behind it, not instead of it.

## Alternatives rejected

- **One blended abuse score.** Rejected: it hides which rule fired and makes the
  threshold impossible to tune per customer.
- **Storing the share token to "revoke instantly".** Rejected: a database leak
  would then hand out working links. Revocation works by marking the row revoked,
  which is enough.
- **Letting the browser hold the share claims.** Rejected: an expiry enforced
  client-side is not an expiry.
