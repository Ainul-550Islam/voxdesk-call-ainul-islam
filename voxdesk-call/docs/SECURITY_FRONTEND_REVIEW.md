# Frontend security review — `dashboard-next`

Reviewed as part of PART 16 (P16-09). The console is same-origin with the API,
which is unusual and useful: it is the reason a strict Content-Security-Policy is
possible at all. Each finding below is either fixed in this PART or has a reason
and an owner.

## Findings

| # | Area | Finding | State |
|---|---|---|---|
| F-1 | CSP | No `Content-Security-Policy` was set. | **Fixed** — `dashboard-next/next.config.mjs` sets `default-src 'self'`, `connect-src 'self'`, `frame-src 'none'`, `object-src 'none'`, `frame-ancestors 'none'`, `upgrade-insecure-requests`. |
| F-2 | Clickjacking | No `X-Frame-Options`, no `frame-ancestors`. | **Fixed** — both are set (`DENY` + `frame-ancestors 'none'`). Belt and braces because the directive is newer than some browsers a phone-clinic deployment may still serve. |
| F-3 | Inline script | Next injects an inline bootstrap script; `script-src 'self'` therefore needs a nonce to be *correct* under a policy that does not also allow `'unsafe-inline'`. | **Open, ticketed (R-FE-1)** — today the app ships `script-src 'self'` (inline is blocked) and where Next needs inline, a nonce is the fix. This is PART 17A work because it touches the root layout and every route. Not allowing `'unsafe-inline'` is a deliberate choice: the alternative is a policy that reads well and protects little. |
| F-4 | Styles | `style-src 'unsafe-inline'` is required by Next's CSS-in-JS and the design system. | **Accepted** — style injection is a far weaker primitive than script injection; it cannot exfiltrate a token on its own, and `connect-src 'self'` blocks the exfiltration step. |
| F-5 | Cookies | The refresh token is an HttpOnly cookie sent with `credentials: "include"` (`lib/api.ts`). | **Verified** — the access token lives in memory only; nothing long-lived is readable by script. `SameSite`/`Secure` are set server-side (`app/api/auth_routes.py`); `Secure` must be on in any HTTPS deployment and the deployment validator enforces it. |
| F-6 | Referrer | Call ids and invitation tokens appear in paths. | **Fixed** — `Referrer-Policy: strict-origin-when-cross-origin`. |
| F-7 | Transport | No HSTS. | **Fixed** — `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`. Ignored over plain HTTP by definition. |
| F-8 | Third-party requests | No CDN script, font, or analytics in the console. | **Verified** — `connect-src`/`script-src`/`font-src` allow `'self'` (and `data:` for fonts) only, so a future CDN addition would fail closed in a browser instead of silently shipping. |
| F-9 | XSS sinks | `dangerouslySetInnerHTML` and `innerHTML` usage. | **Verified** — none in `app/`; transcript text is rendered as text nodes. A grep gate would be better than a human check; noted for 17A. |
| F-10 | Error surfaces | Pages that render an empty table on a failed request. | **Fixed for PART 15/16 pages** — alerts, conductor and the new roles page distinguish `loadError` from empty data and are tested for it (`app/dashboard/team/roles/__tests__/roles_page.test.tsx`). Older pages are audited in 17A. |
| F-11 | Permissions in the UI | The roles editor could hide what the caller cannot grant. | **Fixed** — the catalogue returns `grantable`; the UI shows every permission but disables the ungrantable ones with a stated reason, and the server still refuses. |
| F-12 | Dependency audit | `npm audit` runs in CI. | **Verified** — `.github/workflows/security-scan.yml`. `overrides` pins `postcss` and `source-map-js` to patched versions. |
| F-13 | Device APIs | Microphone/camera could be requested by injected script. | **Fixed** — `Permissions-Policy` denies camera, microphone, geolocation, payment and USB. |

## What this review is not

A manual authenticated test of every screen. `docs/COMPLIANCE/PENTEST_SCOPE_AND_TEMPLATE.md`
defines that engagement, and it has **not** been performed. The automated
baseline in `reports/dast/` covers the unauthenticated API surface only — the
console was not scanned by ZAP in this PART because it is served separately and
builds with `next build` in the release pipeline.

## Verification

```bash
cd dashboard-next
npx tsc --noEmit -p tsconfig.json     # types
npx vitest run                        # 25 files / 191 tests
curl -sSD - -o /dev/null http://127.0.0.1:3000/dashboard | grep -iE 'content-security|x-frame|referrer|strict-transport'
```
