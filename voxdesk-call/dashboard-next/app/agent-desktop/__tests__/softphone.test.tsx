import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DtmfPad } from "../components/DtmfPad";
import { Softphone } from "../Softphone";

describe("softphone desktop components", () => {
  it("renders explicit production registration and presence gates", () => {
    const html = renderToString(<Softphone environmentId="" deviceLabel="Browser" session={null} sdkReady={false} busy={false} onEnvironmentChange={vi.fn()} onDeviceLabelChange={vi.fn()} onRegister={vi.fn()} onPresence={vi.fn()} />);
    expect(html).toContain("Production environment UUID");
    expect(html).toContain("Register softphone");
    expect(html).toContain("Available");
  });

  it("renders a bounded DTMF keypad", () => {
    const html = renderToString(<DtmfPad value="12" onChange={vi.fn()} onSend={vi.fn()} />);
    expect(html).toContain("DTMF sequence");
    expect(html).toContain(">#</button>");
    expect(html).toContain("Send");
  });
});
