# ADR-002: Conversation-Flow Execution Engine (`FlowRunner` vs `pipecat-ai-flows`)

- **Status**: Accepted
- **Date**: 2026-10-08
- **Phase**: PART 5 — Conversation-Flow Builder (Gate G6)

---

## Context

VoxDesk requires a visual, multi-node Conversation-Flow Builder (Retell parity row `#8`) that lets operators author directed graphs of conversation states (`start`, `conversation`, `logic_split`, `function`, `transfer`, `press_digit`, `send_sms`, `extract_variables`, `subagent`, `end`), global interrupt nodes, deterministic equation edges, LLM-judged prompt-condition edges, dynamic variable extraction, and per-node model/voice overrides.

The voice runtime is pinned to **`pipecat-ai==0.0.94`** (Python 3.13, `FastAPI` + `SQLAlchemy 2.0`). In the existing repository:
- `app/builder/node.py` already defines `FlowNode`, `FlowEdge`, and `FlowGraph`.
- `app/builder/workflow_executor.py` coordinates non-voice asynchronous workflow automations over the durable job queue and is not in the real-time voice path.
- `pipecat-ai-flows` is **not installed** in `requirements.txt`.

We must decide between:
- **Option A (Recommended)**: A custom, deterministic `FlowRunner` (`app/builder/flow_runner.py`) paired with a thin Pipecat `FrameProcessor` adapter (`app/agent/flow_processor.py`) built directly on top of the extended `app/builder/node.py` contract and Pipecat `0.0.94` native frames (`LLMMessagesUpdateFrame`, `LLMSetToolsFrame`, `LLMUpdateSettingsFrame`, `LLMRunFrame`, `TTSTextFrame`).
- **Option B**: Adding the external `pipecat-ai-flows` package.

---

## Evaluated Options & Trade-Offs

| Dimension | Option A: Custom `FlowRunner` + `FlowProcessor` (`app/builder/`) | Option B: External `pipecat-ai-flows` |
| :--- | :--- | :--- |
| **Pinned Compatibility (`pipecat-ai==0.0.94`)** | Uses only verified `pipecat.frames.frames` primitives (`LLMMessagesUpdateFrame`, `LLMSetToolsFrame`, `LLMUpdateSettingsFrame`, `LLMRunFrame`, `TranscriptionFrame`) shipped in `0.0.94`. Zero dependency conflicts. | `pipecat-ai-flows` releases coupled to newer `pipecat-ai` (`>=0.0.96+`) introduce breaking adapter imports and extra transitive dependencies. |
| **Deterministic Equation Evaluation & Hybrid Routing** | Evaluates deterministic equations (`==`, `!=`, `>`, `>=`, `<`, `<=`, `contains`, `not_contains`, `exists`, `regex`) in `<0.1 ms` before invoking an injected async judge callable with strict timeout for natural-language prompt conditions. | Primarily relies on LLM function-calling transitions for every edge, adding extra LLM round-trip latency and token cost on deterministic branches. |
| **Offline & Text Simulation (`FlowRunner`)** | Pure-Python state machine decouples graph traversal from WebSocket media transport. Supports deterministic unit testing and `/api/agents/{id}/flow/simulate` text-only scripted turns without requiring a live LLM or audio stream. | Tightly coupled to live Pipecat `PipelineTask` and LLM service instances; difficult to run in fast, hermetic CI simulations. |
| **Unwired Table Closure (`W-12`)** | Directly resolves `AgentTool` (`function` nodes), `KnowledgeCollection` (scoped RAG per agent/node), and `WorkflowTrigger` (post-flow/event triggers) within the tenant-scoped runtime. | Has no native awareness of VoxDesk's multi-tenant PostgreSQL schema (`AgentVersion`, `AgentTool`, `KnowledgeCollection`, `WorkflowTrigger`). |
| **JSON Schema Parity with React Flow (`@xyflow/react`)** | Exports a single canonical JSON Schema from `app/builder/node.py` mirrored 1:1 by `dashboard-next/lib/flow-schema.ts` (`zod`), enabling round-trip serialization between backend validation (`flow_validation.py`) and the Next.js canvas. | Uses its own internal Python dict format that does not preserve React Flow viewport/position/inspector metadata or VoxDesk node types (`press_digit`, `send_sms`, `subagent`). |

---

## Decision

We choose **Option A**: implement a custom **`FlowRunner`** (`app/builder/flow_runner.py`), **`flow_validation`** (`app/builder/flow_validation.py`), and **`FlowProcessor`** (`app/agent/flow_processor.py`) reusing and extending the `app/builder/node.py` contract, while retaining `app/builder/workflow_executor.py` strictly for non-voice background workflow automations.

### Architectural Highlights
1. **Extended Node & Edge Contract (`app/builder/node.py`)**:
   - First-class support for all 10 conversation-flow node types (`start`, `conversation`, `logic_split`, `function`, `transfer`, `press_digit`, `send_sms`, `extract_variables`, `subagent`, `end`) while preserving backward compatibility with legacy workflow node types (`prompt`, `llm_response`, `function_call`, `condition`, `transfer_agent`, `transfer_human`, `extract_variable`, `webhook`, `wait_for_input`).
   - Structured `EdgeCondition` (`always`, `equation`, `prompt`) with deterministic operator evaluation (`==`, `!=`, `>`, `>=`, `<`, `<=`, `contains`, `not_contains`, `exists`, `in`, `matches`).
   - Support for `is_global` nodes (`global_condition`) that can preempt active nodes from anywhere in the graph, plus per-node `model_override` and `voice_override`.
   - Canonical JSON Schema export (`export_flow_json_schema()`) shared with the frontend Zod schema.
2. **Static Graph Validation (`app/builder/flow_validation.py`)**:
   - Enforces single `start` node, unique node/edge IDs, valid edge endpoints, BFS reachability from `start`, dead-end detection on non-terminal nodes, undefined variable reference checks (`{{var}}` and equation variables against initial/extracted/function variables), and unreachable global node checks.
   - Returns structured `FlowValidationIssue` objects (`code`, `message`, `node_id`, `edge_id`, `severity`) consumed by both the React Flow `ValidationPanel` and the `AgentVersion` publish gate.
3. **Runtime Engine (`app/builder/flow_runner.py`) & Pipecat Adapter (`app/agent/flow_processor.py`)**:
   - `FlowRunner` tracks `current_node_id`, `variables`, `history` (node transition timeline), and `executed_actions`.
   - After each user turn (or auto-advancing action node), `FlowRunner` evaluates global nodes first, then outgoing edges (equations first, then prompt conditions via an injected async judge with timeout), extracts variables, executes `function` / `transfer` / `press_digit` / `send_sms` / `subagent` side effects via injectable handlers, and emits `NodeTransitionEvent` records.
   - `FlowProcessor(FrameProcessor)` intercepts `TranscriptionFrame` in the Pipecat `0.0.94` pipeline, steps `FlowRunner`, and emits `LLMMessagesUpdateFrame`, `LLMSetToolsFrame`, and `LLMUpdateSettingsFrame` whenever a node transition swaps the active prompt, tool list, or model settings.

---

## Test Plan

1. **Graph Validation (`tests/builder/test_flow_validation.py`)**:
   - Valid multi-node flow passes with zero errors.
   - Missing/multiple `start` nodes, duplicate node IDs, orphan/unreachable nodes, non-terminal dead ends, undefined variable references in equations or `{{templates}}`, and unreachable global nodes all produce structured errors carrying the offending `node_id`.
2. **FlowRunner Execution (`tests/builder/test_flow_runner.py`)**:
   - Deterministic equation branching (`logic_split`), async fake judge prompt conditions with timeout fallback, dynamic variable extraction (`extract_variables`), `function` tool execution (`AgentTool` / HTTP callback), `transfer` (cold/warm), `press_digit` (DTMF), `send_sms`, `subagent` handoff, and global node preemption.
3. **Pipecat Pipeline Adapter (`tests/agent/test_flow_processor.py`)**:
   - `FlowProcessor` wired into a fake Pipecat pipeline swaps system prompt and tool schemas via `LLMMessagesUpdateFrame` and `LLMSetToolsFrame` across ≥3 nodes (`conversation` -> `function` -> `transfer`) and records node-transition events in the call timeline.
4. **Frontend Canvas & Schema Round-Trip (`dashboard-next/tests/flow-editor.test.tsx`)**:
   - Add, connect, and delete nodes; edit edge conditions and node inspector properties; display structured validation errors; verify lossless JSON Schema / Zod round-trip against backend fixtures.
