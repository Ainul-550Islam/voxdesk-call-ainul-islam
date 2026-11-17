# Migrating from Retell to VoxDesk

This is the operator's guide to moving a live Retell account onto VoxDesk, and
the reference for what the importer will and will not do. It is written for
someone who has a working Retell agent today and needs to know, before they
start, exactly what will come across.

The importer is `scripts/import_retell_agent.py`; the conversion logic is
`app/services/retell_import_service.py`; the guarantees below are asserted in
`tests/services/test_retell_import.py`.

---

## 1. The one rule

**Every field is either converted or reported. Nothing is dropped in silence.**

A migration that quietly loses `post_call_analysis_data` produces an agent that
looks right on a test call and scores nothing for a month. So the importer
emits a *report* alongside the bundle, with three lists:

| list | meaning |
| --- | --- |
| `converted` | `{source_path, target_path, rule}` for everything mapped |
| `unsupported` | fields this importer will not claim to translate, each with a reason **and a suggested action** |
| `lossy` | conversions that are correct but not exact |

`report.clean` is true only when `unsupported` is empty. Any non-empty
`unsupported` list is a decision someone has to make — not a warning to ignore.

```console
$ python scripts/import_retell_agent.py retell/jarvis.json --report-only
jarvis.json: converted 8, unsupported 1, lossy 2
  ✗ response_engine.llm_id (llm_xyz): the prompt for a retell-llm agent is a
      separate object; it is empty here unless the export inlined it.
      → Fetch the llm object and merge its 'prompt' before importing.
```

Exit codes: **0** every field accounted for · **1** unsupported fields present ·
**2** the payload cannot be converted at all (not a Retell object, unreadable
JSON, a flow with no nodes).

---

## 2. What to export from Retell

Two payloads matter, and they are imported differently:

| you have | call | import it as |
| --- | --- | --- |
| a single-prompt agent | `GET /get-agent/{agent_id}` | with its `response_engine.prompt` inlined |
| a conversation flow | `GET /get-conversation-flow/{flow_id}` | the flow JSON itself |
| an agent that *points at* a flow | `GET /get-agent/{id}` | **not this** — fetch the flow and import that |

An agent whose `response_engine.type` is `conversation-flow` only carries a
flow id. The importer will not fetch anything for you (no network calls, no
credential handling); it reports the reference and tells you to export the flow.

A `retell-llm` agent's prompt usually lives in a separate LLM object. If
`response_engine.prompt` is absent, the importer says so rather than inventing
an instruction.

---

## 3. Field mapping — agent level

| Retell | VoxDesk | note |
| --- | --- | --- |
| `agent_name` | agent name | fallback `"Imported agent"`, and said so in the report |
| `language` | `config.language` | default `en-US` |
| `voice_id` | `config.voice` | **kept verbatim, flagged lossy** — VoxDesk voice ids are its own catalog; re-pick a voice |
| `voice_speed` | `config.speech_speed` | clamped to 0.5–2.0 |
| `responsiveness` | `config.responsiveness` | clamped to 0–1 |
| `begin_message_delay_ms` | `config.begin_message_delay_ms` | |
| `max_call_duration_ms` | `config.max_call_seconds` | ms → s |
| `end_call_after_silence_ms` | `config.end_call_after_silence_seconds` | ms → s |
| `webhook_url` | `config.webhook_url` | credential-looking query params replaced with `***` and reported lossy |
| `boosted_keywords` | `config.boosted_keywords` | |
| `post_call_analysis_data` | `config.analysis_schema.fields[]` | reshaped: `type/name/description/examples/required`, enum `choices` → `values` |
| `response_engine.prompt` (`retell-llm`) | one `conversation` node | the whole agent becomes a one-node flow you can split later |

Reported rather than converted: `fallback_voice_ids`, `webhook_events`,
`post_call_analysis_model`, `expressive_emotion_tags`, `guardrail_config`,
`handbook_config`, `pii_config`, `data_storage_setting`,
`pronunciation_dictionary`, `pre_`/`post_session_tools`, `knowledge_base_ids`,
`kb_config`, and the rest of the tenant-level settings listed in
`_convert_agent_fields`. Each carries a concrete next step where one exists —
`knowledge_base_ids`, for example, tells you to upload the corpus and re-attach
it, because the alternative (assuming the ids mean something here) would be a
false claim.

---

## 4. Field mapping — conversation flow nodes

| Retell node type | VoxDesk node type | what is carried |
| --- | --- | --- |
| `conversation` | `conversation` | `instruction.text` → `params.prompt` |
| `subagent` | `conversation` | imported as a conversation node, **reported lossy**: tool choice is a node capability here, attach the tools after import |
| `extract_dynamic_variables` | `extract_variables` | `variables[]` reshaped to VoxDesk `VariableSpec` |
| `function` | `function` | `tool_id` / `tool_name` / `url` / `method` kept |
| `transfer_call` | `transfer` | `destination` (required — a transfer with no destination is reported, not guessed), `transfer_mode` cold/warm |
| `press_digit` | `press_digit` | `digits` |
| `sms` | `send_sms` | `message` |
| `branch` / `logic_split` | `logic_split` | conditions move to the outgoing edges' `condition.equations` |
| `end` | `end` | |
| `code` | — | **reported**: VoxDesk code nodes run in a different sandbox; a verbatim copy is a silent behaviour change |
| `agent_swap` | — | reported: import the target agent and wire it as a subagent |
| `mcp` | — | reported: MCP is configured per tenant, not per node |
| `component` | — | reported unless the component's nodes are inlined in the same export |
| `bridge_transfer`, `cancel_transfer` | — | reported: no equivalent |
| `wait_for_input` | — | reported: a VoxDesk conversation node already waits; folding it in changes timing |

Anything not in this table is reported with `unknown Retell node type '…'`.
NODE_TYPE_MAP is a whitelist; the importer never guesses a mapping.

### Edges and conditions

- Retell `edges[]` → VoxDesk `edges[]`, with **re-minted ids** (`01_…`,
  `e01`) so two imports of the same payload produce byte-identical bundles and
  cannot collide with an existing flow.
- `transition_condition.type == "equation"` → carried across as
  `condition.equations` — a deterministic test in both products, so nothing is
  lost.
- `transition_condition.type == "prompt"` → **lossy**. Retell asks the model
  whether the condition holds. VoxDesk edges evaluate equations. The prompt is
  kept as the edge **label** so a human can see the intent, and the report says
  it is not re-evaluated, with the suggestion to re-implement it as an equation
  where the decision must be deterministic.
- A bare edge (no `transition_condition`) is an unconditional transition; it
  converts with no loss and no report entry beyond the conversion itself.

### Two things the importer adds so the flow runs

1. **A `start` node.** Retell has no such node — its entry point is
   `start_node_id`. VoxDesk requires exactly one `start` node and rejects the
   flow without it, so the importer materialises `00_start` and an edge to
   Retell's entry node (or the first surviving node if the entry node was
   dropped for being unsupported).
2. **Declared variables.** Retell lets a prompt interpolate `{{anything}}` that
   was never declared. VoxDesk rejects an undeclared reference
   (`UNDEFINED_VARIABLE`). The importer scans every node prompt, extract
   variable and edge equation and seeds `flow.initial_variables` with `""` for
   anything used but never declared — each one recorded in the report as an
   added declaration, so the operator knows to fill it in.

---

## 5. What cannot be migrated, and what to do

These are product gaps, not importer bugs. Each is on the
`DEAL_ROOM_CHECKLIST` owner-action list where it needs a decision.

| Retell capability | status in VoxDesk | what the operator does |
| --- | --- | --- |
| Knowledge bases (`knowledge_base_ids`) | VoxDesk has its own KB | re-upload the documents, attach to the agent |
| Per-agent model choice (`post_call_analysis_model`) | model routing is tenant/platform level | accept the platform model, or raise it |
| Guardrails, PII redaction, data-retention | tenant-level policies, not per agent | configure once per tenant |
| Prompt-based branching | equations only | re-express deterministic branches as equations |
| JS `code` nodes | different sandbox runtime | port to a VoxDesk code node or an external function |
| Flow-level `global_prompt` | no flow-wide prompt slot | fold into the agent description or each node |
| `component` references | no by-reference inclusion | inline the component's nodes in the export |
| MCP servers, `agent_swap` | tenant config / subagents | reconfigure after import |

---

## 6. End-to-end runbook

```console
# 1. dry run first — read the ✗ lines
$ python scripts/import_retell_agent.py export/ --report-only

# 2. write bundles and a machine-readable report per payload
$ python scripts/import_retell_agent.py export/ --out-dir build/imported \
      --report-json build/import-report.json

# 3. import into a VoxDesk tenant
$ curl -sS -X POST "$VOXDESK/api/agents/import" \
      -H "Authorization: Bearer $TOKEN" \
      -F file=@build/imported/jarvis.bundle.json
```

Bundles are `voxdesk.agent/1` — the same format
`app/services/agent_portability_service.py` exports, so an imported agent is
indistinguishable downstream from a native one, can be re-exported, and can be
moved between VoxDesk tenants later.

### Before you switch traffic

1. Re-pick the voice (the Retell voice id is only a reference).
2. Fill in every variable the importer declared empty — they are listed in the
   report as `implicit reference` conversions.
3. Re-attach tools to `subagent`-derived nodes, and re-declare flow-level tools.
4. Run `validate` on the imported flow; a clean import validates, an incomplete
   one tells you which node.
5. Place one test call per branch, then compare post-call analysis output
   field-by-field with the Retell side — this is where a migration silently
   fails if `post_call_analysis_data` was not clean.
