// File: tests/enterprise-ui/metric-card-3d.test.tsx — Tests for MetricCard3D real-data rendering, empty values, trend semantics, accessibility and no-fake-data behavior

import { describe, it, expect } from "vitest";
import React from "react";
import { renderToString } from "react-dom/server";
import { MetricCard3D } from "@/components/enterprise/MetricCard3D";

describe("MetricCard3D", () => {
  it("renders supplied value", () => {
    const html = renderToString(<MetricCard3D label="Total Calls" value={1234} />);
    expect(html).toContain("Total Calls");
    expect(html).toContain("1,234");
  });

  it("renders missing value safely as unavailable symbol", () => {
    const html = renderToString(<MetricCard3D label="Revenue" value={null} />);
    expect(html).toContain("Revenue");
    expect(html).toContain("—");
    expect(html).toContain('aria-label="Value unavailable"');
    // Ensure no fabricated fallback — value should be unavailable, not 0
    expect(html).toContain("unavailable");
    expect(html).not.toContain('>0<');
  });

  it("renders undefined value as unavailable without fabricating zero", () => {
    const html = renderToString(<MetricCard3D label="Conversion" value={undefined} />);
    expect(html).toContain("—");
    expect(html).toContain("value unavailable");
  });

  it("renders string value as provided", () => {
    const html = renderToString(<MetricCard3D label="Status" value="Active" />);
    expect(html).toContain("Active");
  });

  it("renders supplied trend up", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} delta={12} trend="up" />);
    expect(html).toContain("↑");
    expect(html).toContain("+12%");
  });

  it("renders supplied trend down", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} delta={-8} trend="down" />);
    expect(html).toContain("↓");
    expect(html).toContain("-8%");
  });

  it("renders neutral trend for zero delta", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} delta={0} />);
    expect(html).toContain("0%");
  });

  it("does not fabricate delta when null", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} delta={null} />);
    expect(html).not.toContain("↑");
    expect(html).not.toContain("↓");
    // No delta pill should be present — check that no change indicator
    expect(html).not.toContain("enterprise-metric-delta--up");
  });

  it("includes accessible aria-label with label and value", () => {
    const html = renderToString(<MetricCard3D label="Total Calls" value={500} unit="calls" />);
    expect(html).toContain("Total Calls");
    expect(html).toContain("value 500 calls");
  });

  it("renders icon slot when provided", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} icon={<span>📞</span>} />);
    expect(html).toContain("📞");
  });

  it("renders sparkline slot when provided with real data", () => {
    const sparkline = <svg data-testid="sparkline"><polyline points="0,0 10,10" /></svg>;
    const html = renderToString(<MetricCard3D label="Calls" value={100} sparkline={sparkline} />);
    expect(html).toContain("sparkline");
  });

  it("renders disabled state with reduced opacity", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} disabled />);
    expect(html).toContain("opacity:0.6");
  });

  it("does not show fake value for NaN", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={NaN as unknown as number} />);
    expect(html).toContain("—");
    expect(html).not.toContain("NaN");
  });

  it("does not show fake value for Infinity", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={Infinity as unknown as number} />);
    expect(html).toContain("—");
  });

  it("renders with status and ensures status label exists", () => {
    const html = renderToString(<MetricCard3D label="Health" value={99} status="healthy" />);
    expect(html).toContain("Healthy");
  });

  it("keyboard accessibility — renders as button when onClick provided", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={100} onClick={() => {}} />);
    expect(html).toContain('role="button"');
    expect(html).toContain('tabindex="0"');
  });

  it("no fake data behavior — empty string value renders as unavailable", () => {
    const html = renderToString(<MetricCard3D label="Calls" value={"" as unknown as string} />);
    expect(html).toContain("—");
  });
});
