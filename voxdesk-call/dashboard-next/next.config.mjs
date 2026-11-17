import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * Security headers (PART 16, P16-09; CSP nonce added in PART 17A, P17A-04 / R-FE-1).
 *
 * The dashboard is a same-origin application: it talks to the API through
 * relative paths, and the API is served from the same host. That single fact is
 * what makes a strict policy possible, so the policy below encodes it:
 *
 * * `default-src 'self'` — no third-party script, style, font or frame by
 *   default. Next injects inline bootstrap script, so `'unsafe-inline'` is
 *   *not* used for `script-src`: `middleware.ts` mints a per-request nonce and
 *   the policy carries it (`'nonce-…' 'strict-dynamic'`), which is what
 *   `docs/SECURITY_FRONTEND_REVIEW.md` F-3 asked for. `'unsafe-inline'` appears
 *   only for styles, where Next's CSS-in-JS genuinely needs it.
 *
 * The CSP therefore lives in `middleware.ts`, not in `headers()` below: a nonce
 * is per response, and `headers()` can only emit constants. Everything that is
 * constant stays here.
 * * `connect-src 'self'` — the API is same-origin. A wildcard here would let an
 *   injected script exfiltrate a token to anywhere, which is the whole point of
 *   CSP.
 * * `frame-ancestors 'none'` plus `X-Frame-Options: DENY` — the app cannot be
 *   framed, so clickjacking on "delete agent" is not available. Belt and braces
 *   because `frame-ancestors` is ignored by very old browsers.
 * * `Referrer-Policy: strict-origin-when-cross-origin` — full URL on same-origin
 *   requests, origin only elsewhere. Call ids and invite tokens live in paths.
 * * `Strict-Transport-Security` is set for two years with `preload`. It only has
 *   an effect over HTTPS; a plain-HTTP deployment ignores it, and that is fine —
 *   the header is for the deployments that are HTTPS.
 * * `Permissions-Policy` denies the device APIs this product never uses, so an
 *   injected script cannot ask for the microphone and get it.
 *
 * `headers()` applies to every path, including the static assets Next serves, so
 * there is no route that quietly ships without them.
 */
const STATIC_CSP = [
  "default-src 'self'",
  "base-uri 'self'",
  // Overridden per request by middleware.ts, which adds the nonce. Kept here so
  // a request that somehow bypasses the middleware still gets `'self'` and not
  // nothing at all.
  "script-src 'self'",
  // Next's runtime and the design system both emit inline style attributes.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "form-action 'self'",
  "frame-src 'none'",
  "object-src 'none'",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

const SECURITY_HEADERS = [
  // Fallback only: middleware.ts emits the authoritative, nonce-carrying policy.
  { key: "Content-Security-Policy", value: STATIC_CSP },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // PART 17A, P17A-03: ship a Node server, not a static export.
  //
  // The app has dynamic route segments (`/share/[token]`, `/use-cases/[slug]`,
  // `/dashboard/agents/[id]/…`) whose values are leaf ids that do not exist at
  // build time. `output: 'export'` would require `generateStaticParams` for each
  // of them, i.e. enumerating every agent and campaign at build time, which is
  // both impossible and pointless. A nonce-based CSP needs per-request HTML for
  // the same reason. So the shipped artefact is the standalone server: the
  // Dockerfile copies `.next/standalone` and runs `node server.js`, and Caddy
  // reverse-proxies to it.
  output: "standalone",
  // `@voxdesk/web-sdk` is `file:../sdk/web`: a symlink that points outside this
  // project. Next refuses to compile a module whose real path escapes the
  // workspace root it inferred, so the root is declared explicitly. Both bundlers
  // also need transpilePackages because the package ships TypeScript sources.
  turbopack: {
    root: path.resolve(here, ".."),
  },
  outputFileTracingRoot: path.resolve(here, ".."),
  transpilePackages: ["@voxdesk/web-sdk"],
  // The only signal that would tempt anyone to relax the policy above.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
