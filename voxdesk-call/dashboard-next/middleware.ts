// Per-request Content-Security-Policy nonce (PART 17A, P17A-04 — closes R-FE-1).
//
// The shipped policy is `script-src 'self' 'nonce-<random>' 'strict-dynamic'`,
// which is the only way to keep Next's inline bootstrap scripts working without
// `'unsafe-inline'`. A nonce is unique per response, so it cannot be a constant
// in `next.config.mjs`; it is minted here, handed to the renderer through the
// request's own `content-security-policy` header (Next parses the nonce out of
// it and stamps its inline scripts), and returned to the browser on the
// response.
//
// Consequence, stated rather than hidden: an HTML document carrying a nonce
// cannot be prerendered at build time, because the nonce would be baked in and
// shared by every visitor. `app/layout.tsx` therefore renders dynamically and
// `next build` reports every page as server-rendered. Static *assets*
// (`/_next/static/…`) are unaffected — they are content-addressed and Caddy caches
// them immutably — so the cost is one server render of the HTML shell per
// navigation, not per asset.
//
// The matcher skips the asset paths: they are not documents, they never contain
// inline script, and minting a nonce for each of them would be pure overhead.

import { NextResponse, type NextRequest } from "next/server";

/** Paths that never need a document policy: hashed assets, images, icons, fonts. */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png).*)"],
};

function buildPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    "base-uri 'self'",
    // `strict-dynamic` makes the nonce authoritative: a script loaded by an
    // already-trusted script is trusted, and `'self'`/host allowlists stop
    // mattering to CSP3 browsers. `'self'` stays for older browsers, which ignore
    // `strict-dynamic` and fall back to the host source.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    // Next's CSS-in-JS emits inline style attributes; there is no nonce for
    // style attributes in CSP2, so this one directive keeps `'unsafe-inline'`.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    // Same-origin API, same-origin WebSocket edge. No wildcard, ever: this is the
    // directive an injected script would need to exfiltrate a token.
    "connect-src 'self'",
    "form-action 'self'",
    "frame-src 'none'",
    "object-src 'none'",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
  ].join("; ");
}

export function middleware(request: NextRequest): NextResponse {
  const nonce = crypto.randomUUID().replace(/-/g, "");
  const policy = buildPolicy(nonce);

  // The request header is how the renderer learns the nonce. Next reads it from
  // `content-security-policy` specifically, so the name is not ours to choose.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("content-security-policy", policy);
  requestHeaders.set("x-nonce", nonce);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}
