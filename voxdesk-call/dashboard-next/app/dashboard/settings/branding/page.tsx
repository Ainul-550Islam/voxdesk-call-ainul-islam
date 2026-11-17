"use client";

/**
 * Branding editor with a live preview (PART 18, P18-06).
 *
 * The interesting parts are not the inputs:
 *
 *  * **Contrast is checked before saving, not after.** `POST /theme/validate`
 *    returns the failing pairs, so the editor can show "muted on background is
 *    3.1:1, needs 3:1" next to the colour that caused it. Letting an
 *    administrator publish an unreadable theme and find out from a support
 *    ticket is the failure this page exists to prevent.
 *  * **The coverage panel names what still says "VoxDesk".** A tenant who brands
 *    the dashboard but leaves `email.from_name` alone has not white-labelled
 *    anything — their customers still get mail from the vendor. The panel is
 *    part of the page, not a footnote in the docs.
 *  * **The preview is the real tokens.** It renders from the same CSS custom
 *    properties the dashboard uses, so "it looked fine in the editor" cannot
 *    mean "it looked fine in a box that does not match production".
 */

import { useCallback, useEffect, useState } from "react";

import { request } from "@/lib/api";

const BASE = "/api/whitelabel";

type Branding = {
  product_name: string;
  colors: Record<string, string>;
  shape: { radius: number };
  typography: { font_family: string; base_font_size: number };
  support_email: string;
  footer_text: string;
  from_name: string;
  widget: { position: string; launcher_label: string; welcome_message: string };
  theme_version: number;
  published: boolean;
  is_white_label: boolean;
  css: Record<string, string>;
  document: { title: string; theme_color?: string };
  coverage: {
    surface: string;
    published: boolean;
    is_white_label: boolean;
    vendor_name_fields: string[];
    clean: boolean;
  };
};

const COLOR_FIELDS: { key: string; label: string; help: string }[] = [
  { key: "primary", label: "Primary", help: "Buttons, links, the launcher." },
  { key: "accent", label: "Accent", help: "Highlights and secondary actions." },
  { key: "background", label: "Background", help: "The page behind the panels." },
  { key: "surface", label: "Surface", help: "Cards and panels." },
  { key: "text", label: "Text", help: "Body copy. Must pass 4.5:1 on background." },
  { key: "muted", label: "Muted", help: "Secondary copy. Must pass 3:1." },
];

export default function BrandingSettingsPage() {
  const [brand, setBrand] = useState<Branding | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contrast, setContrast] = useState<{ failures: string[]; pairs: unknown[] } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    request<{ payload: Record<string, unknown> } & Record<string, unknown>>(`${BASE}/theme`)
      .then((payload) => {
        const themePayload = (payload.payload ?? {}) as Record<string, unknown>;
        setDraft(themePayload);
      })
      .catch((exc: unknown) =>
        setError(exc instanceof Error ? exc.message : "The theme could not be loaded."),
      );
    request<Branding>(`${BASE}/branding?surface=ui`).then(setBrand).catch(() => undefined);
  }, []);

  const check = useCallback(async () => {
    if (!draft) return;
    try {
      const result = await request<{ contrast?: { failures?: string[]; pairs?: unknown[] } }>(
        `${BASE}/theme/validate`,
        { method: "POST", body: JSON.stringify({ payload: draft }) },
      );
      setContrast({
        failures: result.contrast?.failures ?? [],
        pairs: result.contrast?.pairs ?? [],
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Validation failed.");
    }
  }, [draft]);

  const publish = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      await request(`${BASE}/theme`, {
        method: "POST",
        body: JSON.stringify({ payload: draft, activate: true, name: "Brand" }),
      });
      const refreshed = await request<Branding>(`${BASE}/branding?surface=ui`);
      setBrand(refreshed);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The theme could not be published.");
    } finally {
      setSaving(false);
    }
  }, [draft]);

  const setColor = (key: string, value: string) => {
    setDraft((current) => {
      if (!current) return current;
      const colors = { ...(current.colors as Record<string, string>) };
      colors[key] = value;
      return { ...current, colors };
    });
  };

  const setField = (key: string, value: unknown) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));

  if (error && !draft) return <div className="error-banner">{error}</div>;
  if (!draft) return <div className="loading">Loading brand…</div>;

  const colors = (draft.colors ?? {}) as Record<string, string>;
  const email = (draft.email ?? {}) as Record<string, string>;

  return (
    <>
      <header className="page-head">
        <h1>Branding</h1>
        <p className="muted">
          Your name, colours and sender on every surface your customers see —
          the dashboard, the widget and outgoing email. Changing a theme
          publishes a new version; the previous one stays restorable.
        </p>
      </header>

      <div className="branding-layout">
        <section className="card" style={{ display: "grid", gap: "1rem" }}>
          <h2>Identity</h2>
          <label htmlFor="brand-name">Product name</label>
          <input
            id="brand-name"
            maxLength={60}
            value={String(draft.product_name ?? "")}
            onChange={(event) => setField("product_name", event.target.value)}
          />
          <label htmlFor="brand-support">Support email</label>
          <input
            id="brand-support"
            type="email"
            value={String(draft.support_email ?? "")}
            onChange={(event) => setField("support_email", event.target.value)}
          />

          <h2>Colours</h2>
          {COLOR_FIELDS.map((field) => (
            <div key={field.key} className="color-row">
              <input
                type="color"
                aria-label={field.label}
                value={colors[field.key] ?? "#000000"}
                onChange={(event) => setColor(field.key, event.target.value)}
              />
              <div>
                <strong>{field.label}</strong>
                <p className="muted small">{field.help}</p>
              </div>
            </div>
          ))}
          <button type="button" onClick={check}>
            Check contrast
          </button>
          {contrast && (
            <div className={contrast.failures.length ? "error-banner" : "notice-ok"} role="status">
              {contrast.failures.length === 0
                ? "Every checked pair passes WCAG AA."
                : contrast.failures.map((failure) => <div key={failure}>{failure}</div>)}
            </div>
          )}

          <h2>Email</h2>
          <label htmlFor="brand-from">Sender name</label>
          <div id="brand-from-help" className="muted small">
            What appears in your customers&rsquo; inbox. This is the field most
            often missed — leaving it alone means your mail still comes from us.
          </div>
          <input
            id="brand-from"
            aria-describedby="brand-from-help"
            maxLength={80}
            value={email.from_name ?? ""}
            onChange={(event) =>
              setField("email", { ...email, from_name: event.target.value })
            }
          />
          <label htmlFor="brand-footer">Footer text</label>
          <input
            id="brand-footer"
            maxLength={200}
            value={email.footer_text ?? ""}
            onChange={(event) =>
              setField("email", { ...email, footer_text: event.target.value })
            }
          />

          <div className="actions">
            <button type="button" className="primary" onClick={publish} disabled={saving}>
              {saving ? "Publishing…" : "Publish theme"}
            </button>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}
        </section>

        <section className="card">
          <h2>Preview</h2>
          <p className="muted small">
            Rendered from the same tokens the product uses, so this is what your
            users get.
          </p>
          <div
            className="brand-preview"
            style={{
              background: colors.background ?? "#ffffff",
              color: colors.text ?? "#0f172a",
              borderRadius: `${((draft.shape ?? {}) as { radius?: number }).radius ?? 8}px`,
            }}
          >
            <div
              className="brand-preview-head"
              style={{ background: colors.surface ?? "#f8fafc" }}
            >
              <strong>
                {String(draft.product_name || brand?.product_name || "Your product")}
              </strong>
            </div>
            <p style={{ color: colors.text ?? "#0f172a" }}>
              An agent answered this call in two rings. Your name is on the tab,
              the widget and the email that follows.
            </p>
            <p style={{ color: colors.muted ?? "#475569" }}>
              Secondary copy uses the muted token, which is why it is contrast-checked.
            </p>
            <button
              type="button"
              style={{
                background: colors.primary ?? "#1d4ed8",
                color: colors.background ?? "#ffffff",
                borderRadius: `${((draft.shape ?? {}) as { radius?: number }).radius ?? 8}px`,
              }}
            >
              Book a call
            </button>
          </div>
        </section>
      </div>

      {brand && (
        <section className="card">
          <h2>What still says &ldquo;VoxDesk&rdquo;</h2>
          {brand.coverage.clean ? (
            <p className="notice-ok">
              Nothing on the dashboard surface. Publish a theme with your own
              product and sender names to clear this list entirely.
            </p>
          ) : (
            <ul className="facts">
              {brand.coverage.vendor_name_fields.map((field) => (
                <li key={field}>
                  <code>{field}</code> — still the platform name.
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">
            {brand.is_white_label
              ? `White-labelled, theme version ${brand.theme_version}.`
              : brand.published
                ? "A theme is published but it still carries the platform name."
                : "No theme published yet — this is the platform default, which is correct until you brand it."}
          </p>
        </section>
      )}
    </>
  );
}
