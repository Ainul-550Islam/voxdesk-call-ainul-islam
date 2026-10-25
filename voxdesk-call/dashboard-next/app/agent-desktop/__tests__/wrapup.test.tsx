import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { WrapupForm } from "../components/WrapupForm";

describe("wrap-up approval", () => {
  it("requires explicit human approval and explains CRM delivery", () => {
    const html = renderToStaticMarkup(<WrapupForm busy={false} onSubmit={vi.fn()} wrapups={[{
      id: "wrapup-1", call_id: "call-1", status: "draft", summary_redacted: "Redacted summary",
      disposition: "follow_up", tags: [], deadline_at: new Date(0).toISOString(), crm_status: "not_requested",
    }]} />);
    expect(html).toContain("role=\"dialog\"");
    expect(html).toContain("I approve this redacted wrap-up");
    expect(html).toContain("Approve &amp; submit wrap-up");
    expect(html).toContain("disabled=\"\"");
  });
});
