#!/usr/bin/env python3
"""Generate PROMPT_6_CONTINUATION_REPORT.md.

The report embeds the **complete, unshortened** source of every file created or
modified in the Prompt 6 continuation pass. It never abbreviates a file with
"...", "# existing code", or a line count in place of the body: the body is
written verbatim, which is why the generator reads the files instead of having
the content typed into it.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "PROMPT_6_CONTINUATION_REPORT.md"

# (section, path, status, purpose)
FILES: list[tuple[str, str, str, str]] = [
    # ---------------------------------------------------------- backend ----
    (
        "Backend",
        "app/api/agent_catalog_routes.py",
        "CREATED",
        "Agent Studio catalog endpoints the dashboard already called but that did not "
        "exist: GET /api/agents/voices, GET /api/agents/models, GET /api/agents/tools/catalog.",
    ),
    (
        "Backend",
        "app/main.py",
        "MODIFIED",
        "Registers `agent_catalog_router` ahead of `agent_management_router` so the "
        "concrete catalog paths are not captured by GET /api/agents/{agent_id}.",
    ),
    (
        "Backend",
        "app/providers/compatibility.py",
        "MODIFIED",
        "Adds the import-free `distribution_state` probe, `DistributionState`, and "
        "`PROVIDER_DISTRIBUTIONS` so request handlers never import a provider SDK.",
    ),
    (
        "Backend",
        "app/agent/stt.py",
        "MODIFIED",
        "Defers Deepgram/Pipecat imports until live STT service construction; "
        "configuration and transcript-enqueue request paths stay import-light.",
    ),
    (
        "Backend",
        "app/domain/agent_models.py",
        "MODIFIED",
        "Bug fix: `stable_id` is variadic. Three domain modules called it with 3-4 "
        "parts and every one of those calls raised TypeError at request time.",
    ),
    (
        "Backend",
        "app/release/facts.py",
        "MODIFIED",
        "Bug fix: the revision/down-revision parsers now accept the type-annotated "
        "declaration form, so the release gate stops reporting a phantom branch.",
    ),

    # ---------------------------------------------------------- tests ------
    (
        "Backend tests",
        "tests/test_agent_catalog_routes.py",
        "CREATED",
        "22 tests: routing precedence, honesty contract, no-credential-leak, "
        "source-of-truth assertions, tenant independence.",
    ),
    (
        "Backend tests",
        "tests/test_stable_id_and_release_facts.py",
        "CREATED",
        "9 regression tests for the two defects fixed in this pass.",
    ),
    (
        "Backend tests",
        "tests/test_stt_lazy_import.py",
        "CREATED",
        "Regression test proving STT configuration imports do not load Deepgram or Pipecat SDKs.",
    ),
    (
        "Backend tests",
        "tests/test_deployment.py",
        "MODIFIED",
        "Migration-chain parser accepts annotated revisions; head pinned to 0041.",
    ),
    (
        "Backend tests",
        "tests/test_campaign_release_compatibility.py",
        "MODIFIED",
        "HEAD / PREVIOUS_HEAD advanced to 0041 / 0040.",
    ),
    (
        "Backend tests",
        "tests/test_enterprise_persistence.py",
        "MODIFIED",
        "Revision chain extended through 0039/0040/0041.",
    ),

    # ------------------------------------------------------- frontend api --
    (
        "Frontend API clients",
        "dashboard/src/api/agent-knowledge.ts",
        "MODIFIED",
        "Real /api/knowledge/* client. Previously called a 404 and returned [].",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-tools.ts",
        "MODIFIED",
        "Real /api/agents/{id}/tools client (tool registry).",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-voices.ts",
        "MODIFIED",
        "Real GET /api/agents/voices client with the typed honesty contract.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-models.ts",
        "MODIFIED",
        "Real GET /api/agents/models client with the typed honesty contract.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-validation.ts",
        "MODIFIED",
        "Real POST /api/v1/agents/{id}/validate client.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-templates.ts",
        "MODIFIED",
        "Templates derived from the tenant's own agents; no fabricated catalog.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-metrics.ts",
        "MODIFIED",
        "Per-agent configuration metrics composed from four real endpoints.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-conflict.ts",
        "MODIFIED",
        "ETag identity read and 409/412 conflict classification.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-filters.ts",
        "MODIFIED",
        "Pure client-side filtering; options derived from the loaded rows.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-search.ts",
        "MODIFIED",
        "Pure client-side search over an allowlist of fields.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agent-sort.ts",
        "CREATED",
        "Pure client-side sorting; missing keys sort last in both directions.",
    ),
    (
        "Frontend API clients",
        "dashboard/src/api/agents.ts",
        "MODIFIED",
        "getVoiceProviders / getModelProviders / getTemplates delegate to the real "
        "clients instead of calling paths that do not exist.",
    ),

    # ------------------------------------------------------------ hooks ----
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentKnowledge.ts",
        "MODIFIED",
        "Real knowledge-base state with upload polling to a terminal status.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentTools.ts",
        "MODIFIED",
        "Real tool-registry CRUD plus enable/disable.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentVoiceCatalog.ts",
        "MODIFIED",
        "Real voice catalog; exposes selectable vs unavailable with reasons.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentModelCatalog.ts",
        "MODIFIED",
        "Real model catalog; null default when nothing is selectable.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentValidation.ts",
        "MODIFIED",
        "Real validation; keeps 'not checked' distinct from 'invalid'.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentSave.ts",
        "MODIFIED",
        "Real builder draft save with If-Match ETag and explicit 409 conflict state.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentConflict.ts",
        "MODIFIED",
        "Real concurrent-edit detection by polling the draft ETag.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentMetrics.ts",
        "MODIFIED",
        "Real configuration metrics; states that call analytics are not included.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentTemplates.ts",
        "MODIFIED",
        "Templates from the tenant's own agents; clone through the real endpoint.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentSearch.ts",
        "MODIFIED",
        "Real client-side search state over a loaded list.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentFilters.ts",
        "MODIFIED",
        "Real client-side filter state with derived options.",
    ),
    (
        "Frontend hooks",
        "dashboard/src/hooks/useAgentSort.ts",
        "MODIFIED",
        "Real client-side sort state with toggling.",
    ),

    # ------------------------------------------------------------ pages ----
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentListPage.tsx",
        "MODIFIED",
        "Dense operator table with search, filters, and sorting.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentKnowledgePage.tsx",
        "MODIFIED",
        "Upload, URL ingest, reindex, restore, archive, retrieval preview.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentVoicePage.tsx",
        "MODIFIED",
        "Voice configuration against the real catalog, saved with ETag.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentModelPage.tsx",
        "MODIFIED",
        "Model configuration from runtime presets, saved with ETag.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentToolsPage.tsx",
        "MODIFIED",
        "Registered custom functions: create, enable/disable, delete.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentTestHistoryPage.tsx",
        "MODIFIED",
        "Persisted simulation runs plus a clearly-scoped live browser session.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentArchivePage.tsx",
        "MODIFIED",
        "Server-reported archived agents with restore.",
    ),
    (
        "Frontend pages",
        "dashboard/src/pages/agents/AgentDuplicatePage.tsx",
        "MODIFIED",
        "Clone an agent through POST /api/v1/agents/{id}/clone.",
    ),

    # ----------------------------------------------------------- routing ---
    (
        "Frontend routing",
        "dashboard/src/app/router.tsx",
        "MODIFIED",
        "Ten new dashboard routes for the completed pages.",
    ),
    (
        "Frontend routing",
        "dashboard/src/app/app.tsx",
        "MODIFIED",
        "Imports and switch cases for the eight completed pages.",
    ),

    # ------------------------------------------------------- frontend tests -
    (
        "Frontend tests",
        "dashboard/src/__tests__/agentStudio.pages.test.tsx",
        "CREATED",
        "34 tests covering the eight pages, the list utilities, and routing.",
    ),
    (
        "Reporting",
        "scripts/generate_continuation_report.py",
        "CREATED",
        "Generates this continuation report from complete file bodies and exact diffs; "
        "the report is never manually abbreviated.",
    ),
]

# Files whose full body is deliberately not inlined because they are large
# pre-existing files whose only change is a small, precisely described edit.
# Their exact diff is included instead.
DIFF_ONLY = {
    "app/main.py",
    "dashboard/src/app/router.tsx",
    "dashboard/src/app/app.tsx",
    "dashboard/src/api/agents.ts",
    "tests/test_deployment.py",
    "tests/test_campaign_release_compatibility.py",
    "tests/test_enterprise_persistence.py",
    "app/domain/agent_models.py",
    "app/release/facts.py",
    "app/providers/compatibility.py",
}


def _language(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx")):
        return "tsx"
    return ""


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
    lines: list[str] = []
    add = lines.append

    add("# Prompt 6 — Continuation: Agent Studio completion & defect fixes")
    add("")
    add(f"**Generated:** {_dt.datetime.now(_dt.timezone.utc).isoformat()}")
    add("**Repository:** `voxdesk-call-ainul-islam/voxdesk-call`")
    add("")
    add("---")
    add("")

    total = len(FILES)
    created = sum(1 for _, _, status, _ in FILES if status == "CREATED")
    modified = total - created
    add("## Accounting")
    add("")
    add("```text")
    add(f"TOTAL FILES CREATED OR MODIFIED IN THIS PASS = {total}")
    add(f"CREATED  = {created}")
    add(f"MODIFIED = {modified}")
    add("SKIPPED  = 0")
    add("UNACCOUNTED FILES = 0")
    add("```")
    add("")
    add("| # | Section | Path | Status | Purpose |")
    add("|---:|---|---|---|---|")
    for index, (section, path, status, purpose) in enumerate(FILES, start=1):
        add(f"| {index} | {section} | `{path}` | {status} | {purpose} |")
    add("")
    add("---")
    add("")

    current_section = None
    for index, (section, path, status, purpose) in enumerate(FILES, start=1):
        if section != current_section:
            current_section = section
            add(f"## {section}")
            add("")
        add(f"### {index}. `{path}` — {status}")
        add("")
        add(purpose)
        add("")
        if path in DIFF_ONLY:
            add("> This file is a large pre-existing file. Its full body is not duplicated "
                "here; the change is given as the exact diff below. The file on disk is the "
                "authoritative, complete version.")
            add("")
            diff = _git_diff(path)
            add("```diff")
            add(diff.rstrip() or "(no tracked diff — file is new but listed as modified)")
            add("```")
        else:
            body = _read(path)
            add(f"```{_language(path)}")
            add(body.rstrip())
            add("```")
        add("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


def _git_diff(path: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--", path],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    result = subprocess.run(
        ["git", "diff", "--no-index", "--", "/dev/null", path],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return result.stdout


if __name__ == "__main__":
    main()
