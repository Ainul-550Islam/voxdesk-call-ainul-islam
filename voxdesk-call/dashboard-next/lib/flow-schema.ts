// File: dashboard-next/lib/flow-schema.ts — TypeScript types, Zod schema, client validator, dagre auto-layout, and round-trip fixtures mirroring app/builder/node.py (Part 5 / Gate G6)

import dagre from "dagre";
import { z } from "zod";

export const FLOW_NODE_TYPES = [
  "start",
  "conversation",
  "logic_split",
  "function",
  "transfer",
  "press_digit",
  "send_sms",
  "extract_variables",
  "end",
  "subagent",
] as const;

export type FlowNodeType = (typeof FLOW_NODE_TYPES)[number];

export const EQUATION_OPERATORS = [
  "==",
  "!=",
  ">",
  ">=",
  "<",
  "<=",
  "contains",
  "not_contains",
  "starts_with",
  "ends_with",
  "in",
  "not_in",
  "is_set",
  "is_empty",
] as const;

export type EquationOperator = (typeof EQUATION_OPERATORS)[number];

export const EquationClauseSchema = z.object({
  variable: z.string().min(1),
  operator: z.enum(EQUATION_OPERATORS).default("=="),
  value: z.any().optional().default(null),
});

export type EquationClause = z.infer<typeof EquationClauseSchema>;

export const EdgeConditionKindSchema = z.enum(["always", "equation", "prompt", "else"]);
export type EdgeConditionKind = z.infer<typeof EdgeConditionKindSchema>;

export const EdgeConditionSchema = z.object({
  kind: EdgeConditionKindSchema.default("always"),
  equations: z.array(EquationClauseSchema).default([]),
  match_mode: z.enum(["all", "any"]).default("all"),
  prompt: z.string().nullable().optional().default(null),
  expression: z.string().nullable().optional().default(null),
  label: z.string().optional().default("always"),
});

export type EdgeCondition = z.infer<typeof EdgeConditionSchema>;

export const ModelOverrideSchema = z.object({
  provider: z.string().nullable().optional().default(null),
  model: z.string().nullable().optional().default(null),
  temperature: z.number().min(0).max(2).nullable().optional().default(null),
  max_tokens: z.number().int().positive().nullable().optional().default(null),
});

export type ModelOverride = z.infer<typeof ModelOverrideSchema>;

export const VoiceOverrideSchema = z.object({
  provider: z.string().nullable().optional().default(null),
  voice_id: z.string().nullable().optional().default(null),
  speed: z.number().min(0.5).max(2.0).nullable().optional().default(null),
});

export type VoiceOverride = z.infer<typeof VoiceOverrideSchema>;

export const VariableSpecSchema = z.object({
  name: z.string().min(1),
  type: z.enum(["string", "number", "boolean", "enum"]).default("string"),
  description: z.string().optional().default(""),
  required: z.boolean().optional().default(false),
  default: z.any().optional().default(null),
  pattern: z.string().nullable().optional().default(null),
});

export type VariableSpec = z.infer<typeof VariableSpecSchema>;

export const GlobalNodeConfigSchema = z.object({
  enabled: z.boolean().default(false),
  trigger_condition: EdgeConditionSchema.nullable().optional().default(null),
  return_to_previous: z.boolean().default(false),
});

export type GlobalNodeConfig = z.infer<typeof GlobalNodeConfigSchema>;

export const FlowNodePositionSchema = z.object({
  x: z.number().default(0),
  y: z.number().default(0),
});

export type FlowNodePosition = z.infer<typeof FlowNodePositionSchema>;

export const FlowNodeSchema = z.object({
  id: z.string().min(1),
  type: z.string().min(1),
  label: z.string().default(""),
  params: z.record(z.string(), z.any()).default({}),
  position: FlowNodePositionSchema.default({ x: 0, y: 0 }),
  is_global: z.boolean().default(false),
  global_config: GlobalNodeConfigSchema.nullable().optional().default(null),
  model_override: ModelOverrideSchema.nullable().optional().default(null),
  voice_override: VoiceOverrideSchema.nullable().optional().default(null),
  variables: z.array(VariableSpecSchema).default([]),
  max_retries: z.number().int().min(0).default(1),
  timeout_ms: z.number().int().min(100).default(10000),
});

export type FlowNode = z.infer<typeof FlowNodeSchema>;

export const FlowEdgeSchema = z
  .object({
    id: z.string().optional(),
    source: z.string().optional(),
    target: z.string().optional(),
    source_id: z.string().optional(),
    target_id: z.string().optional(),
    condition_label: z.string().optional().default("always"),
    expression: z.string().nullable().optional().default(null),
    priority: z.number().int().optional().default(0),
    condition: EdgeConditionSchema.nullable().optional().default(null),
  })
  .transform((raw) => {
    const sourceId = String(raw.source ?? raw.source_id ?? "");
    const targetId = String(raw.target ?? raw.target_id ?? "");
    const edgeId = raw.id && raw.id.length > 0 ? raw.id : `e_${sourceId}_${targetId}`;
    const condLabel = raw.condition_label || raw.condition?.label || "always";
    const cond: EdgeCondition = raw.condition
      ? {
          ...raw.condition,
          label: raw.condition.label || condLabel,
        }
      : {
          kind: raw.expression ? "equation" : "always",
          equations: [],
          match_mode: "all",
          prompt: null,
          expression: raw.expression ?? null,
          label: condLabel,
        };
    return {
      id: edgeId,
      source: sourceId,
      target: targetId,
      source_id: sourceId,
      target_id: targetId,
      condition_label: condLabel,
      expression: raw.expression ?? cond.expression ?? null,
      priority: raw.priority ?? 0,
      condition: cond,
    };
  });

export type FlowEdge = z.output<typeof FlowEdgeSchema>;

export const FlowGraphSchema = z.object({
  version: z.number().int().default(1),
  nodes: z.array(FlowNodeSchema).default([]),
  edges: z.array(FlowEdgeSchema).default([]),
  initial_variables: z.record(z.string(), z.any()).default({}),
  variables: z.array(VariableSpecSchema).default([]),
  metadata: z.record(z.string(), z.any()).default({}),
});

export type FlowGraph = z.output<typeof FlowGraphSchema>;

export interface FlowValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  node_id: string | null;
  edge_id?: string | null;
  field?: string | null;
}

export interface FlowValidationResult {
  valid: boolean;
  errors: FlowValidationIssue[];
  warnings: FlowValidationIssue[];
}

const BUILTIN_VARIABLES = new Set([
  "caller_number",
  "from_number",
  "to_number",
  "call_id",
  "call_sid",
  "tenant_id",
  "agent_id",
  "last_user_utterance",
  "last_assistant_utterance",
  "last_tool_result",
  "last_tool_status",
  "dtmf_digits",
  "turn_count",
]);

const TERMINAL_NODE_TYPES = new Set<string>(["end", "transfer", "subagent"]);

const TEMPLATE_VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}/g;

function extractTemplateVars(value: unknown): Set<string> {
  const found = new Set<string>();
  if (typeof value === "string") {
    let match: RegExpExecArray | null = null;
    const regex = new RegExp(TEMPLATE_VAR_RE.source, "g");
    while ((match = regex.exec(value)) !== null) {
      found.add(match[1].split(".")[0]);
    }
  } else if (Array.isArray(value)) {
    for (const item of value) {
      for (const v of extractTemplateVars(item)) found.add(v);
    }
  } else if (value && typeof value === "object") {
    for (const item of Object.values(value as Record<string, unknown>)) {
      for (const v of extractTemplateVars(item)) found.add(v);
    }
  }
  return found;
}

/**
 * Client-side flow validator mirroring `app/builder/flow_validation.py::validate_flow`.
 */
export function validateFlowGraphClient(input: unknown): FlowValidationResult {
  const parsed = FlowGraphSchema.safeParse(input);
  const errors: FlowValidationIssue[] = [];
  const warnings: FlowValidationIssue[] = [];

  if (!parsed.success) {
    for (const issue of parsed.error.issues) {
      errors.push({
        code: "SCHEMA_VALIDATION_ERROR",
        message: issue.message,
        severity: "error",
        node_id: null,
        field: issue.path.join("."),
      });
    }
    return { valid: false, errors, warnings };
  }

  const graph = parsed.data;
  if (graph.nodes.length === 0) {
    errors.push({
      code: "EMPTY_FLOW",
      message: "Flow graph must contain at least one node.",
      severity: "error",
      node_id: null,
    });
    return { valid: false, errors, warnings };
  }

  // 1. Unique node IDs & valid node types
  const seenNodeIds = new Set<string>();
  const nodesById = new Map<string, FlowNode>();
  for (const node of graph.nodes) {
    if (seenNodeIds.has(node.id)) {
      errors.push({
        code: "DUPLICATE_NODE_ID",
        message: `Duplicate node id '${node.id}'.`,
        severity: "error",
        node_id: node.id,
      });
    } else {
      seenNodeIds.add(node.id);
      nodesById.set(node.id, node);
    }
    if (!FLOW_NODE_TYPES.includes(node.type as FlowNodeType)) {
      errors.push({
        code: "UNKNOWN_NODE_TYPE",
        message: `Unsupported node type '${node.type}' on node '${node.id}'.`,
        severity: "error",
        node_id: node.id,
      });
    }
  }

  // 2. Unique edge IDs & endpoint existence
  const seenEdgeIds = new Set<string>();
  const outgoing = new Map<string, FlowEdge[]>();
  for (const nid of nodesById.keys()) outgoing.set(nid, []);

  for (const edge of graph.edges) {
    if (seenEdgeIds.has(edge.id)) {
      errors.push({
        code: "DUPLICATE_EDGE_ID",
        message: `Duplicate edge id '${edge.id}'.`,
        severity: "error",
        node_id: edge.source_id || null,
        edge_id: edge.id,
      });
    } else {
      seenEdgeIds.add(edge.id);
    }

    if (!nodesById.has(edge.source_id)) {
      errors.push({
        code: "DANGLING_EDGE_SOURCE",
        message: `Edge '${edge.id}' references non-existent source node '${edge.source_id}'.`,
        severity: "error",
        node_id: edge.source_id || null,
        edge_id: edge.id,
      });
      continue;
    }
    if (!nodesById.has(edge.target_id)) {
      errors.push({
        code: "DANGLING_EDGE_TARGET",
        message: `Edge '${edge.id}' references non-existent target node '${edge.target_id}'.`,
        severity: "error",
        node_id: edge.source_id,
        edge_id: edge.id,
      });
      continue;
    }
    outgoing.get(edge.source_id)?.push(edge);
  }

  // 3. Start node check
  const startNodes = graph.nodes.filter((n) => n.type === "start");
  let entryNodeId: string | null = null;
  if (startNodes.length === 0) {
    errors.push({
      code: "MISSING_START_NODE",
      message: "Flow must contain exactly one 'start' node.",
      severity: "error",
      node_id: null,
    });
  } else if (startNodes.length > 1) {
    for (const s of startNodes.slice(1)) {
      errors.push({
        code: "MULTIPLE_START_NODES",
        message: `Flow has multiple start nodes; '${s.id}' is redundant.`,
        severity: "error",
        node_id: s.id,
      });
    }
    entryNodeId = startNodes[0].id;
  } else {
    entryNodeId = startNodes[0].id;
  }

  // 4. Global nodes & reachability
  const reachable = new Set<string>();
  const queue: string[] = [];
  if (entryNodeId && nodesById.has(entryNodeId)) {
    reachable.add(entryNodeId);
    queue.push(entryNodeId);
  }

  for (const node of graph.nodes) {
    const isGlobal = Boolean(node.is_global || node.global_config?.enabled);
    if (!isGlobal) continue;
    const trig = node.global_config?.trigger_condition;
    const hasTrigger =
      trig !== null &&
      trig !== undefined &&
      ((trig.kind === "prompt" && Boolean(trig.prompt?.trim())) ||
        (trig.kind === "equation" &&
          (trig.equations.length > 0 || Boolean(trig.expression?.trim()))) ||
        trig.kind === "always");
    if (!hasTrigger) {
      errors.push({
        code: "UNREACHABLE_GLOBAL_NODE",
        message: `Global node '${node.id}' has no trigger_condition and can never be entered.`,
        severity: "error",
        node_id: node.id,
      });
    } else {
      reachable.add(node.id);
      queue.push(node.id);
    }
  }

  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const edge of outgoing.get(cur) ?? []) {
      if (!reachable.has(edge.target_id)) {
        reachable.add(edge.target_id);
        queue.push(edge.target_id);
      }
    }
  }

  if (entryNodeId !== null) {
    for (const node of graph.nodes) {
      const isGlobal = Boolean(node.is_global || node.global_config?.enabled);
      if (isGlobal) continue;
      if (!reachable.has(node.id)) {
        errors.push({
          code: "UNREACHABLE_NODE",
          message: `Node '${node.id}' ('${node.label || node.type}') is not reachable from the start node.`,
          severity: "error",
          node_id: node.id,
        });
      }
    }
  }

  // 5. Dead ends
  for (const node of graph.nodes) {
    const nodeOut = outgoing.get(node.id) ?? [];
    if (TERMINAL_NODE_TYPES.has(node.type)) {
      if (node.type === "end" && nodeOut.length > 0) {
        warnings.push({
          code: "END_NODE_HAS_OUTGOING_EDGES",
          message: `Terminal node '${node.id}' has outgoing edges that will never be traversed.`,
          severity: "warning",
          node_id: node.id,
        });
      }
      continue;
    }
    const returnsToPrev = Boolean(
      (node.is_global || node.global_config?.enabled) && node.global_config?.return_to_previous
    );
    if (nodeOut.length === 0 && !returnsToPrev) {
      errors.push({
        code: "DEAD_END_NODE",
        message: `Non-terminal node '${node.id}' ('${node.type}') has no outgoing edges (dead end).`,
        severity: "error",
        node_id: node.id,
      });
    }
  }

  // 6. Defined vs referenced variables
  const definedVars = new Set<string>(BUILTIN_VARIABLES);
  for (const k of Object.keys(graph.initial_variables)) definedVars.add(k);
  for (const v of graph.variables) if (v.name) definedVars.add(v.name);

  for (const node of graph.nodes) {
    for (const v of node.variables) if (v.name) definedVars.add(v.name);
    const params = node.params ?? {};
    if (node.type === "extract_variables") {
      const rawVars = params.variables;
      if (Array.isArray(rawVars)) {
        for (const item of rawVars) {
          if (typeof item === "string" && item.trim()) definedVars.add(item.trim());
          else if (item && typeof item === "object" && typeof item.name === "string") {
            definedVars.add(item.name);
          }
        }
      }
      if (typeof params.output_variable === "string" && params.output_variable) {
        definedVars.add(params.output_variable);
      }
    }
    if (node.type === "function") {
      const outVar = params.output_variable || params.response_variable;
      if (typeof outVar === "string" && outVar.trim()) definedVars.add(outVar.trim());
      const mapping = params.response_mapping;
      if (mapping && typeof mapping === "object") {
        for (const k of Object.keys(mapping)) definedVars.add(k);
      }
    }
    if (typeof params.set_variables === "object" && params.set_variables !== null) {
      for (const k of Object.keys(params.set_variables)) definedVars.add(k);
    }
  }

  for (const node of graph.nodes) {
    for (const ref of extractTemplateVars(node.params)) {
      if (!definedVars.has(ref)) {
        errors.push({
          code: "UNDEFINED_VARIABLE",
          message: `Node '${node.id}' references undefined variable '{{${ref}}}'.`,
          severity: "error",
          node_id: node.id,
          field: "params",
        });
      }
    }
  }

  for (const edge of graph.edges) {
    for (const eq of edge.condition?.equations ?? []) {
      const varRoot = eq.variable.split(".")[0].trim();
      if (varRoot && !definedVars.has(varRoot)) {
        errors.push({
          code: "UNDEFINED_VARIABLE",
          message: `Edge '${edge.id}' from node '${edge.source_id}' references undefined variable '${varRoot}'.`,
          severity: "error",
          node_id: edge.source_id,
          edge_id: edge.id,
          field: "condition.equations",
        });
      }
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
  };
}

/**
 * Round-trip parse + serialize helper verifying schema symmetry between UI and backend.
 */
export function roundTripFlowGraph(input: unknown): FlowGraph {
  const parsed = FlowGraphSchema.parse(input);
  return JSON.parse(JSON.stringify(parsed)) as FlowGraph;
}

/**
 * Auto-layout nodes in a FlowGraph using Dagre (`LR` or `TB`).
 */
export function autoLayoutFlowGraph(
  graph: FlowGraph,
  direction: "LR" | "TB" = "LR"
): FlowGraph {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 70, ranksep: 120, marginx: 40, marginy: 40 });

  const nodeWidth = 240;
  const nodeHeight = 110;

  for (const node of graph.nodes) {
    g.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  }
  for (const edge of graph.edges) {
    g.setEdge(edge.source_id, edge.target_id);
  }

  dagre.layout(g);

  const layoutNodes = graph.nodes.map((node) => {
    const pos = g.node(node.id);
    if (!pos) return node;
    return {
      ...node,
      position: {
        x: Math.round(pos.x - nodeWidth / 2),
        y: Math.round(pos.y - nodeHeight / 2),
      },
    };
  });

  return {
    ...graph,
    nodes: layoutNodes,
  };
}

/**
 * Shared round-trip fixture representing a real 4-node flow:
 * `start -> conversation -> function -> transfer`
 */
export const SAMPLE_APPOINTMENT_FLOW_FIXTURE: FlowGraph = roundTripFlowGraph({
  version: 1,
  initial_variables: {
    department: "sales",
  },
  variables: [
    {
      name: "department",
      type: "string",
      description: "Target department requested by caller",
      required: true,
      default: "sales",
      pattern: null,
    },
    {
      name: "crm_lookup_status",
      type: "string",
      description: "Status returned by CRM lookup function node",
      required: false,
      default: "pending",
      pattern: null,
    },
  ],
  nodes: [
    {
      id: "node_start",
      type: "start",
      label: "Inbound Greeting",
      params: {
        greeting: "Welcome to VoxDesk Enterprise! How can I direct your call?",
      },
      position: { x: 60, y: 180 },
      is_global: false,
    },
    {
      id: "node_intake",
      type: "conversation",
      label: "Gather Intent",
      params: {
        prompt: "Ask whether the caller needs Sales or Support for {{department}}.",
        tools: ["lookup_customer"],
      },
      position: { x: 340, y: 180 },
      is_global: false,
      model_override: {
        provider: "groq",
        model: "llama-3.3-70b-versatile",
        temperature: 0.2,
        max_tokens: 256,
      },
    },
    {
      id: "node_crm_fn",
      type: "function",
      label: "CRM Account Lookup",
      params: {
        tool_name: "lookup_customer",
        arguments: { phone: "{{caller_number}}", dept: "{{department}}" },
        output_variable: "crm_lookup_status",
      },
      position: { x: 640, y: 180 },
      is_global: false,
    },
    {
      id: "node_transfer_sales",
      type: "transfer",
      label: "Warm Transfer to Sales",
      params: {
        transfer_mode: "warm",
        destination: "+14155550199",
        whisper_text: "Caller {{caller_number}} verified via CRM ({{crm_lookup_status}}).",
      },
      position: { x: 940, y: 180 },
      is_global: false,
    },
  ],
  edges: [
    {
      id: "e_start_intake",
      source: "node_start",
      target: "node_intake",
      condition_label: "always",
      priority: 0,
      condition: {
        kind: "always",
        equations: [],
        match_mode: "all",
        prompt: null,
        expression: null,
        label: "always",
      },
    },
    {
      id: "e_intake_crm",
      source: "node_intake",
      target: "node_crm_fn",
      condition_label: "Caller asks for sales or pricing",
      priority: 10,
      condition: {
        kind: "prompt",
        equations: [],
        match_mode: "all",
        prompt: "Caller asks for sales, pricing, or speaking to an account executive",
        expression: null,
        label: "Caller asks for sales or pricing",
      },
    },
    {
      id: "e_crm_transfer",
      source: "node_crm_fn",
      target: "node_transfer_sales",
      condition_label: "CRM lookup succeeded",
      priority: 10,
      condition: {
        kind: "equation",
        equations: [
          {
            variable: "crm_lookup_status",
            operator: "is_set",
            value: null,
          },
        ],
        match_mode: "all",
        prompt: null,
        expression: null,
        label: "CRM lookup succeeded",
      },
    },
  ],
  metadata: {
    template: "sales_intake_and_transfer",
  },
});
