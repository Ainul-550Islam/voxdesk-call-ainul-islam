// File: dashboard-next/tests/flow-editor.test.tsx — Vitest tests for Conversation-Flow Builder: add/connect/delete nodes, validation display, undo/redo, and schema round-trip (Part 5 / Gate G6)

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FLOW_NODE_TYPES,
  FlowGraph,
  SAMPLE_APPOINTMENT_FLOW_FIXTURE,
  autoLayoutFlowGraph,
  roundTripFlowGraph,
  validateFlowGraphClient,
} from "@/lib/flow-schema";
import FlowEditorWorkspace from "@/app/dashboard/agents/[id]/flow/page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "agent-flow-test" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Conversation-Flow Schema & Client Validation", () => {
  it("round-trips the shared 4-node appointment flow fixture losslessly", () => {
    expect(FLOW_NODE_TYPES).toHaveLength(10);
    const roundTripped = roundTripFlowGraph(SAMPLE_APPOINTMENT_FLOW_FIXTURE);
    expect(roundTripped).toEqual(SAMPLE_APPOINTMENT_FLOW_FIXTURE);

    const validation = validateFlowGraphClient(roundTripped);
    expect(validation.valid).toBe(true);
    expect(validation.errors).toHaveLength(0);
  });

  it("detects unreachable nodes, dead ends, undefined variables, and unreachable globals", () => {
    const broken: FlowGraph = roundTripFlowGraph({
      ...SAMPLE_APPOINTMENT_FLOW_FIXTURE,
      nodes: [
        ...SAMPLE_APPOINTMENT_FLOW_FIXTURE.nodes,
        {
          id: "node_orphan",
          type: "conversation",
          label: "Orphan Conversation",
          params: { prompt: "Hello {{unknown_var_xyz}}" },
          position: { x: 400, y: 400 },
        },
        {
          id: "node_bad_global",
          type: "end",
          label: "Global Without Trigger",
          is_global: true,
          global_config: { enabled: true, trigger_condition: null },
        },
      ],
    });

    const res = validateFlowGraphClient(broken);
    expect(res.valid).toBe(false);
    const codes = res.errors.map((e) => e.code);
    expect(codes).toContain("UNREACHABLE_NODE");
    expect(codes).toContain("DEAD_END_NODE");
    expect(codes).toContain("UNDEFINED_VARIABLE");
    expect(codes).toContain("UNREACHABLE_GLOBAL_NODE");
  });

  it("computes non-overlapping dagre auto-layout coordinates", () => {
    const laidOut = autoLayoutFlowGraph(SAMPLE_APPOINTMENT_FLOW_FIXTURE, "LR");
    expect(laidOut.nodes).toHaveLength(4);
    const xs = laidOut.nodes.map((n) => n.position.x);
    expect(xs[0]).toBeLessThan(xs[1]);
    expect(xs[1]).toBeLessThan(xs[2]);
    expect(xs[2]).toBeLessThan(xs[3]);
  });
});

describe("FlowEditorWorkspace UI: add, connect, delete, validation display, undo/redo", () => {
  it("adds a node, displays validation errors, connects edges to clear errors, and deletes/undoes", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);

    const starterGraph: FlowGraph = roundTripFlowGraph({
      version: 1,
      nodes: [
        {
          id: "n_start",
          type: "start",
          label: "Start",
          params: { greeting: "Hi!" },
          position: { x: 50, y: 100 },
        },
        {
          id: "n_end",
          type: "end",
          label: "End",
          params: { speak_text: "Bye!" },
          position: { x: 400, y: 100 },
        },
      ],
      edges: [
        {
          id: "e_start_end",
          source: "n_start",
          target: "n_end",
          condition_label: "always",
        },
      ],
    });

    try {
      await act(async () => {
        root.render(
          <FlowEditorWorkspace
            agentId="agent-test-1"
            initialGraph={starterGraph}
            disableInitialFetch
          />,
        );
      });

      // Initially valid 2-node flow
      const statusBadge = host.querySelector(
        '[data-testid="flow-validation-status"]',
      );
      expect(statusBadge?.textContent).toBe("VALID FLOW");

      // 1. Click palette button to add a 'conversation' node
      const addConvoBtn = host.querySelector(
        '[data-testid="palette-add-conversation"]',
      ) as HTMLButtonElement;
      expect(addConvoBtn).not.toBeNull();

      await act(async () => {
        addConvoBtn.click();
      });

      // Adding an unconnected conversation node causes UNREACHABLE_NODE + DEAD_END_NODE
      expect(statusBadge?.textContent).toContain("ERROR(S)");
      expect(host.textContent).toContain("UNREACHABLE_NODE");
      expect(host.textContent).toContain("DEAD_END_NODE");

      // Find the newly added conversation node ID
      const nodeCards = [
        ...host.querySelectorAll('[data-node-type="conversation"]'),
      ] as HTMLElement[];
      expect(nodeCards).toHaveLength(1);
      const newConvoTestId = nodeCards[0].getAttribute("data-testid") ?? "";
      const newConvoId = newConvoTestId.replace("flow-node-card-", "");
      expect(newConvoId).toContain("node_conversation_");

      // 2. Connect n_start -> newConvoId and newConvoId -> n_end using the Quick Connect controls
      const sourceSelect = host.querySelector(
        '[data-testid="quick-connect-source"]',
      ) as HTMLSelectElement;
      const targetSelect = host.querySelector(
        '[data-testid="quick-connect-target"]',
      ) as HTMLSelectElement;
      const connectBtn = host.querySelector(
        '[data-testid="quick-connect-btn"]',
      ) as HTMLButtonElement;

      await act(async () => {
        sourceSelect.value = "n_start";
        sourceSelect.dispatchEvent(new Event("change", { bubbles: true }));
        targetSelect.value = newConvoId;
        targetSelect.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await act(async () => {
        connectBtn.click();
      });

      await act(async () => {
        sourceSelect.value = newConvoId;
        sourceSelect.dispatchEvent(new Event("change", { bubbles: true }));
        targetSelect.value = "n_end";
        targetSelect.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await act(async () => {
        connectBtn.click();
      });

      // Now all 3 nodes are reachable and non-dead-end -> VALID FLOW!
      expect(statusBadge?.textContent).toBe("VALID FLOW");

      // 3. Delete the conversation node via Inspector and verify Undo restores it
      const deleteBtn = host.querySelector(
        '[data-testid="inspector-delete-node"]',
      ) as HTMLButtonElement;
      expect(deleteBtn).not.toBeNull();

      await act(async () => {
        deleteBtn.click();
      });
      expect(
        host.querySelectorAll('[data-node-type="conversation"]'),
      ).toHaveLength(0);

      const undoBtn = host.querySelector(
        '[data-testid="flow-undo-btn"]',
      ) as HTMLButtonElement;
      await act(async () => {
        undoBtn.click();
      });
      expect(
        host.querySelectorAll('[data-node-type="conversation"]'),
      ).toHaveLength(1);
    } finally {
      await act(async () => {
        root.unmount();
      });
      host.remove();
    }
  });
});
