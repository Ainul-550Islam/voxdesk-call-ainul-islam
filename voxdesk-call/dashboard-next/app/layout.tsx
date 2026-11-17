import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { ThemeScript } from "@/components/theme-script";

// PART 17A, P17A-04: every document renders per request.
//
// `middleware.ts` puts a fresh CSP nonce in each response, and Next stamps it on
// its inline bootstrap scripts. A prerendered document would carry one build-time
// nonce shared by all visitors, which is the same as no nonce at all, so the
// shell is rendered dynamically. Static assets keep their immutable caching.
export const dynamic = "force-dynamic";

// PART 18, P18-06: the document title is NOT a constant.
//
// A white-labelled deployment that ships `title: "VoxDesk"` in its HTML has not
// been white-labelled — the browser tab, the bookmark and the search result all
// say the vendor's name. So the title comes from the tenant's brand theme,
// resolved from the request host by `/api/whitelabel/branding`. The fallback is
// the platform's own name, which is correct for the vendor's own deployment and
// is exactly what an unbranded tenant should see.
export async function generateMetadata(): Promise<Metadata> {
  const brand = await loadBrand();
  const title = brand?.document?.title ?? "VoxDesk";
  return {
    title,
    applicationName: title,
    description: `${title} workspace`,
    // `themeColor` and `icons` belong to `viewport` in Next 15; putting them in
    // Metadata is deprecated and silently ignored, which would ship an unbranded
    // favicon on a branded deployment.
    icons: brand?.document?.favicon ? { icon: brand.document.favicon } : undefined,
  };
}

export async function generateViewport(): Promise<Viewport> {
  const brand = await loadBrand();
  return {
    themeColor: brand?.document?.theme_color ?? undefined,
  };
}

type BrandDocument = {
  title?: string;
  theme_color?: string;
  favicon?: string | null;
};

type BrandResponse = {
  document?: BrandDocument;
  css?: Record<string, string>;
};

/**
 * The tenant's brand for this host, or null.
 *
 * Fetched server-side against the API with no credentials: `/api/whitelabel/
 * branding` requires a session, so this call is expected to 401 on the public
 * marketing paths and 200 inside the dashboard. A failure is never fatal — the
 * platform name is a correct answer, and a page that refuses to render because
 * branding is unavailable would be worse than a default title.
 */
async function loadBrand(): Promise<BrandResponse | null> {
  const base = process.env.API_INTERNAL_BASE_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}/api/whitelabel/branding?surface=document`, {
      cache: "no-store",
      signal: AbortSignal.timeout(800),
      headers: { accept: "application/json" },
    });
    if (!response.ok) return null;
    return (await response.json()) as BrandResponse;
  } catch {
    return null;
  }
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const brand = await loadBrand();
  return (
    <html lang="en">
      <head>
        {/* Server-rendered so the first paint already carries the brand colours;
            a client effect would flash the platform blue on every load. */}
        {brand?.css && (
          <style
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: cssText(brand.css) }}
          />
        )}
      </head>
      <body>
        <ThemeScript />
        {children}
      </body>
    </html>
  );
}

function cssText(vars: Record<string, string>): string {
  const body = Object.entries(vars)
    .map(([key, value]) => `${key}:${value}`)
    .join(";");
  return `:root{${body}}`;
}
