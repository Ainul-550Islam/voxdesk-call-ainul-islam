"use client";

/**
 * Keeps the brand on the document after a client-side navigation.
 *
 * The server renders `:root` once per request. A dashboard that navigates
 * between routes without a full reload keeps that stylesheet, which is what we
 * want — but if an administrator publishes a new theme in another tab, this
 * listener picks it up without forcing everyone else to reload.
 *
 * It reads the CSS variables the server already set rather than fetching the
 * theme again: the values are already correct, and a second request per
 * navigation would be a round trip for no new information.
 */
import { useEffect } from "react";

export function ThemeScript() {
  useEffect(() => {
    const root = document.documentElement;
    if (!root.style.getPropertyValue("--brand-primary")) {
      const computed = getComputedStyle(root);
      const primary = computed.getPropertyValue("--brand-primary").trim();
      if (primary) root.style.setProperty("--brand-primary", primary);
    }
  }, []);
  return null;
}
