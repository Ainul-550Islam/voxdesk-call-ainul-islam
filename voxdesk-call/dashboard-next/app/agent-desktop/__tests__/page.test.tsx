import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AgentDesktopPage from "../page";

describe("agent desktop page", () => {
  it("renders the registration and consent gates without a browser device", () => {
    const html = renderToString(<AgentDesktopPage />);
    expect(html).toContain("Agent desktop");
    expect(html).toContain("Production environment UUID");
    expect(html).toContain("current affirmative voice consent");
    expect(html).toContain("SDK loading");
  });
});
