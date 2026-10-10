# PART 5 REPORT — Conversation-Flow Builder (Gate G6)

**Date:** 2026-10-08  
**Gate:** G6 (Conversation-Flow Builder)  
**Unwired-Table Rows Closed:** `W-12` (`AgentTool`, `WorkflowTrigger`, `KnowledgeCollection`)  
**Retell Parity Row Closed:** `#8` (`Visual conversation-flow builder`)

---

## 1. Architecture & Scope Summary

### 1.1 ADR-002 Decision (`docs/adr/ADR-002-flow-engine.md`)
- Evaluated **Option A** (custom `FlowRunner` + `FlowProcessor` reusing and extending `app/builder/node.py` on `pipecat-ai==0.0.94`) vs **Option B** (`pipecat-ai-flows`).
- Selected **Option A**:
  - Preserves backward compatibility with `FlowNode`, `FlowEdge`, `FlowGraph`, and `app/builder/workflow_executor.py`.
  - Evaluates deterministic `equation` conditions in `<1ms` before invoking an injected async LLM `judge` with strict timeout (`judge_timeout_seconds`) for `prompt` edges.
  - Emits native `pipecat-ai==0.0.94` frames (`LLMMessagesUpdateFrame`, `LLMSetToolsFrame`, `LLMUpdateSettingsFrame`, `TTSTextFrame`) from `FlowProcessor`.

### 1.2 `/dashboard/agents/page.tsx` vs `/dashboard/agent/page.tsx` Decision
- Checked `dashboard-next/app/dashboard/agent/page.tsx` before creating `dashboard-next/app/dashboard/agents/page.tsx`.
- `dashboard-next/app/dashboard/agent/page.tsx` is a read-only single-tenant legacy configuration view (`api.agentConfig(tenantId)`).
- Created `dashboard-next/app/dashboard/agents/page.tsx` as the multi-agent workspace directory supporting create, duplicate, archive, and direct navigation to `/dashboard/agents/[id]` and `/dashboard/agents/[id]/flow`.

### 1.3 Closure of `W-12` (`AgentTool`, `KnowledgeCollection`, `WorkflowTrigger`)
- `app/builder/flow_runner.py::load_agent_flow_bindings` loads enabled `AgentTool`, active `KnowledgeCollection`, and enabled `WorkflowTrigger` rows from `app/db/enterprise_models.py` for the tenant and agent.
- `FlowRunner` scopes `AgentTool` schemas per conversation node, injects `KnowledgeCollection` summaries into node prompts, invokes `AgentTool` endpoints on `function` nodes, and fires `WorkflowTrigger` rules on terminal/escalation transitions.
- `dashboard-next/app/dashboard/agents/[id]/page.tsx` exposes dedicated `Tools` and `Knowledge` tabs to create and inspect `AgentTool`, `WorkflowTrigger`, and `KnowledgeCollection` bindings.

---

## 2. Files Created & Modified

| # | Path | Status | Purpose |
|---|---|---|---|
| 1 | `docs/adr/ADR-002-flow-engine.md` | `[NEW]` | ADR comparing custom `FlowRunner` vs `pipecat-ai-flows` |
| 2 | `app/builder/node.py` | `[MODIFY]` | 10 conversation-flow node types, `EquationClause`, `EdgeCondition`, `ModelOverride`, `VoiceOverride`, `VariableSpec`, `GlobalNodeConfig`, `FlowGraph`, `export_flow_json_schema()` |
| 3 | `app/builder/workflow_executor.py` | `[MODIFY]` | Clarified docstring separating non-voice workflow automations from `FlowRunner` |
| 4 | `app/builder/flow_validation.py` | `[NEW]` | Static validator for reachability, dead ends, unique IDs, undefined variables, unreachable globals |
| 5 | `app/builder/flow_runner.py` | `[NEW]` | `FlowRunner` runtime engine + `load_agent_flow_bindings` (`W-12`) |
| 6 | `app/domain/agent_models.py` | `[MODIFY]` | `AgentConfig` gains `mode: "single_prompt" | "flow"` + `flow` and `validate_flow()` publish gate |
| 7 | `app/agent/flow_processor.py` | `[NEW]` | Pipecat `FrameProcessor` adapter emitting `LLMMessagesUpdateFrame`, `LLMSetToolsFrame`, `LLMUpdateSettingsFrame`, `TTSTextFrame` |
| 8 | `app/agent/pipeline.py` | `[MODIFY]` | Wires `build_flow_processor_for_runtime` into the live voice pipeline |
| 9 | `app/api/agent_flow_routes.py` | `[NEW]` | `GET/PUT /api/agents/{id}/flow`, `/validate`, `/simulate`, `/publish`, `/flow/schema` |
| 10 | `app/main.py` | `[MODIFY]` | Registers `agent_flow_router` |
| 11 | `dashboard-next/package.json` | `[MODIFY]` | Adds `@xyflow/react`, `dagre`, `zod`, `@types/dagre` |
| 12 | `dashboard-next/lib/flow-schema.ts` | `[NEW]` | TypeScript types, Zod schema, client validator, Dagre auto-layout, round-trip fixture |
| 13 | `dashboard-next/lib/api.ts` | `[MODIFY]` | Typed client methods for agents, versions, flows, tools, knowledge, triggers |
| 14 | `dashboard-next/components/flow/Palette.tsx` | `[NEW]` | Draggable & click-to-add 10-node palette |
| 15 | `dashboard-next/components/flow/NodeCard.tsx` | `[NEW]` | Custom node card renderer with global/error/override badges |
| 16 | `dashboard-next/components/flow/EdgeCondition.tsx` | `[NEW]` | Equation builder & LLM prompt condition editor |
| 17 | `dashboard-next/components/flow/Inspector.tsx` | `[NEW]` | Right-hand node inspector for prompt, tools, variables, overrides, edges |
| 18 | `dashboard-next/components/flow/ValidationPanel.tsx` | `[NEW]` | Live `flow_validation` error/warning list with click-to-focus node |
| 19 | `dashboard-next/app/dashboard/agents/page.tsx` | `[NEW]` | Multi-agent list (create, duplicate, archive, open flow) |
| 20 | `dashboard-next/app/dashboard/agents/[id]/page.tsx` | `[NEW]` | Agent detail tabs: `Config | Flow | Versions | Tools | Knowledge | Test` |
| 21 | `dashboard-next/app/dashboard/agents/[id]/flow/page.tsx` | `[NEW]` | React Flow canvas with palette, inspector, validation panel, undo/redo, autosave, publish |
| 22 | `dashboard-next/tests/flow-editor.test.tsx` | `[NEW]` | Vitest suite for schema round-trip, add/connect/delete nodes, validation display, undo/redo |
| 23 | `tests/builder/__init__.py` | `[NEW]` | Package marker |
| 24 | `tests/builder/test_flow_validation.py` | `[NEW]` | Pytest suite for valid/invalid graphs and structured errors |
| 25 | `tests/builder/test_flow_runner.py` | `[NEW]` | Pytest suite for `FlowRunner`, fake judge, action nodes, `W-12` bindings, API routes, and E2E call timeline |
| 26 | `tests/agent/test_flow_processor.py` | `[NEW]` | Pytest suite for prompt/tool swapping in a fake Pipecat pipeline |

---

## 3. Acceptance Verification Results

```text
$ make verify-truth
61 passed in 26.19s

$ python -c "from app.main import app; print('routes:', len(app.routes))"
routes: 1164

$ pytest -q tests/builder/test_flow_validation.py
7 passed

$ pytest -q tests/builder/test_flow_runner.py
3 passed

$ pytest -q tests/agent/test_flow_processor.py
1 passed

$ (cd dashboard-next && npx vitest run tests/flow-editor.test.tsx)
Test Files  1 passed (1)
Tests       4 passed (4)

$ (cd dashboard-next && npx tsc --noEmit && npx vitest run)
0 TypeScript errors
Test Files  7 passed (7)
Tests       85 passed (85)
```
