// File: tests/enterprise-ui/data-state.test.tsx — Tests covering loading, empty, error and unavailable presentation states for DataState component

import { describe, it, expect } from "vitest";
import React from "react";
import { renderToString } from "react-dom/server";
import { DataState } from "@/components/enterprise/DataState";

describe("DataState", () => {
  it("renders loading state", () => {
    const html = renderToString(<DataState variant="loading" />);
    expect(html).toContain("Loading");
    expect(html).toContain("Fetching data");
    expect(html).toContain("status");
  });

  it("renders loading with custom title and description", () => {
    const html = renderToString(<DataState variant="loading" title="Syncing agents" description="Please wait while agents load" />);
    expect(html).toContain("Syncing agents");
    expect(html).toContain("Please wait");
  });

  it("renders empty state", () => {
    const html = renderToString(<DataState variant="empty" />);
    expect(html).toContain("No data");
    expect(html).toContain("no data to display");
  });

  it("renders empty with custom action", () => {
    const html = renderToString(<DataState variant="empty" title="No calls" description="No calls yet" actionLabel="Create call" onAction={() => {}} />);
    expect(html).toContain("No calls");
    expect(html).toContain("Create call");
  });

  it("renders error state with retry", () => {
    const html = renderToString(<DataState variant="error" onRetry={() => {}} />);
    expect(html).toContain("Something went wrong");
    expect(html).toContain("Retry");
    expect(html).toContain("assertive");
  });

  it("renders error with custom retry label", () => {
    const html = renderToString(<DataState variant="error" onRetry={() => {}} retryLabel="Try again" />);
    expect(html).toContain("Try again");
  });

  it("renders unavailable state — no fake data", () => {
    const html = renderToString(<DataState variant="unavailable" />);
    expect(html).toContain("Unavailable");
    expect(html).toContain("currently unavailable");
    expect(html).not.toContain("0 results");
  });

  it("renders forbidden state", () => {
    const html = renderToString(<DataState variant="forbidden" />);
    expect(html).toContain("Access restricted");
    expect(html).toContain("permission");
  });

  it("supports retry callback without assuming callback exists — renders without retry when no callback", () => {
    const html = renderToString(<DataState variant="error" />);
    expect(html).toContain("Something went wrong");
    expect(html).not.toContain("Retry");
  });

  it("supports custom icon", () => {
    const html = renderToString(<DataState variant="empty" icon={<span>📭</span>} />);
    expect(html).toContain("📭");
  });

  it("renders children slot", () => {
    const html = renderToString(<DataState variant="empty"><div>Custom child</div></DataState>);
    expect(html).toContain("Custom child");
  });

  it("does not fabricate fallback values — unavailable does not show fake numbers", () => {
    const html = renderToString(<DataState variant="unavailable" title="Metrics unavailable" description="No metrics configured" />);
    expect(html).toContain("Metrics unavailable");
    expect(html).not.toContain("$0");
    expect(html).not.toContain("100%");
  });

  it("loading state does not show retry by default unless provided", () => {
    const html = renderToString(<DataState variant="loading" />);
    expect(html).not.toContain("Retry");
  });

  it("error state aria-live assertive for accessibility", () => {
    const html = renderToString(<DataState variant="error" />);
    expect(html).toContain("assertive");
  });

  it("empty state aria-live polite", () => {
    const html = renderToString(<DataState variant="empty" />);
    expect(html).toContain("polite");
  });
});
