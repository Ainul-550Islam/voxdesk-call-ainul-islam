#!/usr/bin/env bash
# Build static API documentation in docs/api/ from the current OpenAPI schema.
# Emits:
#   - contracts/openapi.json
#   - docs/api/openapi.json
#   - docs/api/index.html (self-contained static API reference + Redoc/Scalar progressive enhancement)
#   - docs/api/README.md (generated Markdown index of all API tags and operations)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/docs/api" "$ROOT/contracts"

python3 - <<'PY'
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import redirect_stdout
import html
import io
import json
from pathlib import Path

ROOT = Path.cwd()
with redirect_stdout(io.StringIO()):
    from app.main import app
    schema = app.openapi()

openapi_json = json.dumps(schema, indent=2, sort_keys=True) + "\n"
(ROOT / "contracts" / "openapi.json").write_text(openapi_json, encoding="utf-8")
(ROOT / "docs" / "api" / "openapi.json").write_text(openapi_json, encoding="utf-8")

paths = schema.get("paths", {})
method_counts: Counter[str] = Counter()
by_tag: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
total_ops = 0
for path_str in sorted(paths.keys()):
    item = paths[path_str] or {}
    for method in HTTP_METHODS:
        if method not in item:
            continue
        op = item[method] or {}
        total_ops += 1
        method_counts[method.upper()] += 1
        tags = op.get("tags") or ["default"]
        summary = str(op.get("summary") or op.get("operationId") or "").strip()
        op_id = str(op.get("operationId") or "").strip()
        for t in tags:
            by_tag[str(t)].append((method.upper(), path_str, summary, op_id))

info = schema.get("info", {})
title = str(info.get("title") or "VoxDesk API")
version = str(info.get("version") or "1.0.0")
description = str(info.get("description") or "VoxDesk multi-tenant voice AI platform OpenAPI specification.")

# 1. Generate docs/api/README.md
md_lines = [
    f"# {title} — Static API Reference (`v{version}`)",
    "",
    "Generated deterministically by `scripts/build_api_docs.sh` from `app.main:app.openapi()`.",
    "",
    "## Summary Metrics",
    "",
    f"- **OpenAPI Version**: `{schema.get('openapi', '3.1.0')}`",
    f"- **Total Unique Paths**: `{len(paths)}`",
    f"- **Total HTTP Operations**: `{total_ops}`",
    f"- **Total Tag Groups**: `{len(by_tag)}`",
    "- **Machine-Readable Schema**: [`openapi.json`](openapi.json) (mirrored at [`../../contracts/openapi.json`](../../contracts/openapi.json))",
    "- **Static HTML Reference (Redoc / Scalar + Offline Explorer)**: [`index.html`](index.html)",
    "",
    "### Operations by HTTP Method",
    "",
    "| HTTP Method | Operation Count |",
    "|---|---:|",
]
for m, count in sorted(method_counts.items()):
    md_lines.append(f"| `{m}` | {count} |")

md_lines.extend([
    "",
    "## Tag Inventory",
    "",
    "| Tag | Operations | Sample Endpoints |",
    "|---|---:|---|",
])
for tag in sorted(by_tag.keys()):
    ops = by_tag[tag]
    sample = ", ".join(f"`{m} {p}`" for m, p, _, _ in ops[:3])
    if len(ops) > 3:
        sample += f" (+{len(ops) - 3} more)"
    safe_tag = tag.replace("|", "\\|")
    md_lines.append(f"| `{safe_tag}` | {len(ops)} | {sample} |")

md_lines.append("")
(ROOT / "docs" / "api" / "README.md").write_text("\n".join(md_lines), encoding="utf-8")

# 2. Generate docs/api/index.html (offline-first static reference + Redoc/Scalar viewer)
tag_sections_html = []
for tag in sorted(by_tag.keys()):
    ops = by_tag[tag]
    rows_html = []
    for method, path_str, summary, op_id in ops:
        rows_html.append(
            f"<tr>"
            f"<td><span class='badge method-{method.lower()}'>{html.escape(method)}</span></td>"
            f"<td><code>{html.escape(path_str)}</code></td>"
            f"<td>{html.escape(summary)}</td>"
            f"<td class='muted'><code>{html.escape(op_id)}</code></td>"
            f"</tr>"
        )
    tag_sections_html.append(
        f"<details class='tag-group' open>"
        f"<summary><strong>{html.escape(tag)}</strong> <span class='count'>({len(ops)} operations)</span></summary>"
        f"<table><thead><tr><th>Method</th><th>Path</th><th>Summary</th><th>Operation ID</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
        f"</details>"
    )

html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)} v{html.escape(version)} — API Reference</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      padding: 1.5rem 2rem;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }}
    header h1 {{ margin: 0; font-size: 1.35rem; }}
    .stats {{ display: flex; gap: 1rem; font-size: 0.9rem; color: var(--muted); }}
    .stats strong {{ color: var(--accent); }}
    main {{ max-width: 1400px; margin: 0 auto; padding: 1.5rem 2rem; }}
    .toolbar {{
      margin-bottom: 1.25rem;
      display: flex;
      gap: 0.75rem;
      align-items: center;
      flex-wrap: wrap;
    }}
    input[type="search"] {{
      flex: 1;
      min-width: 260px;
      padding: 0.6rem 0.9rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      font-size: 0.95rem;
    }}
    .btn {{
      padding: 0.55rem 0.9rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--accent);
      text-decoration: none;
      font-size: 0.88rem;
      cursor: pointer;
    }}
    details.tag-group {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 1rem;
      overflow: hidden;
    }}
    details.tag-group summary {{
      padding: 0.85rem 1rem;
      cursor: pointer;
      user-select: none;
      border-bottom: 1px solid var(--border);
    }}
    .count {{ color: var(--muted); font-size: 0.85rem; margin-left: 0.5rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    th, td {{ padding: 0.55rem 0.85rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 600; background: rgba(15, 23, 42, 0.45); }}
    .muted {{ color: var(--muted); font-size: 0.8rem; }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      font-weight: 700;
      font-size: 0.75rem;
      letter-spacing: 0.03em;
    }}
    .method-get {{ background: #0369a1; color: #e0f2fe; }}
    .method-post {{ background: #15803d; color: #dcfce7; }}
    .method-put, .method-patch {{ background: #b45309; color: #fef3c7; }}
    .method-delete {{ background: #b91c1c; color: #fee2e2; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{html.escape(title)} <code>v{html.escape(version)}</code></h1>
      <div class="muted">{html.escape(description)}</div>
    </div>
    <div class="stats">
      <span>Paths: <strong>{len(paths)}</strong></span>
      <span>Operations: <strong>{total_ops}</strong></span>
      <span>Tags: <strong>{len(by_tag)}</strong></span>
      <a class="btn" href="openapi.json" download>Download openapi.json</a>
    </div>
  </header>
  <main>
    <div class="toolbar">
      <input type="search" id="filter" placeholder="Filter endpoints by path, method, summary, or operationId..." />
    </div>
    <div id="catalog">
      {''.join(tag_sections_html)}
    </div>
  </main>
  <script>
    const input = document.getElementById('filter');
    input.addEventListener('input', () => {{
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll('details.tag-group').forEach(group => {{
        let visible = 0;
        group.querySelectorAll('tbody tr').forEach(row => {{
          const match = !q || row.textContent.toLowerCase().includes(q);
          row.style.display = match ? '' : 'none';
          if (match) visible++;
        }});
        group.style.display = visible > 0 ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>
"""
(ROOT / "docs" / "api" / "index.html").write_text(html_doc, encoding="utf-8")
print(f"Built docs/api/index.html and docs/api/README.md ({len(paths)} paths, {total_ops} operations)")
PY
