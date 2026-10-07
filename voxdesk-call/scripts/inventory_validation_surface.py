"""Inventory source/config files and conservatively classify placeholder indicators."""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"node_modules", ".venv", ".cache", ".git", "__pycache__", ".next", "dist", "target", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SOURCES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".cpp", ".h", ".sh", ".yml", ".yaml", ".toml", ".ini", ".json"}
INDICATORS = re.compile(r"\b(?:TODO|FIXME|NotImplementedError|NotImplemented|placeholder)\b|^\s*pass\s*(?:#.*)?$", re.I)


def python_classes(source):
    categories = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return categories
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Pass):
            parent = parents.get(node)
            category = "REVIEW_REQUIRED_NOOP"
            if isinstance(parent, ast.ClassDef):
                bases = [ast.unparse(base) for base in parent.bases]
                category = "EXCEPTION_MARKER_CLASS" if any(b.endswith(("Error", "Exception")) for b in bases) else "REVIEW_REQUIRED_MARKER_CLASS"
            elif isinstance(parent, ast.ExceptHandler):
                category = "REVIEW_REQUIRED_SWALLOWED_EXCEPTION"
            elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [ast.unparse(d) for d in parent.decorator_list]
                if any("abstractmethod" in d for d in decorators):
                    category = "ABSTRACT_INTERFACE"
            categories[node.lineno] = category
    return categories


def main():
    output = ROOT / ".prompt8b"
    output.mkdir(exist_ok=True)
    inventory, findings = [], []
    roots = ["app", "dashboard", "dashboard-next", "tests", "alembic", "migrations", "scripts", "workers", "services", ".github", "contracts"]
    for name in roots:
        directory = ROOT / name
        if not directory.exists():
            inventory.append({"path": name, "exists": False})
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or any(part in EXCLUDED for part in path.relative_to(ROOT).parts):
                continue
            data = path.read_bytes()
            inventory.append({"path": str(path.relative_to(ROOT)), "exists": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            if path.suffix not in SOURCES:
                continue
            source = data.decode(errors="replace")
            categories = python_classes(source) if path.suffix == ".py" else {}
            for line_number, line in enumerate(source.splitlines(), 1):
                if not INDICATORS.search(line):
                    continue
                category = categories.get(line_number, "REVIEW_REQUIRED_INDICATOR")
                if re.search(r"placeholder\s*=", line, re.I) and path.suffix in {".tsx", ".jsx"}:
                    category = "UI_INPUT_HINT"
                elif re.search(r"\bTODO\b|\bFIXME\b", line):
                    category = "REVIEW_REQUIRED_DOCUMENTED_GAP"
                findings.append({"path": str(path.relative_to(ROOT)), "line": line_number,
                                 "classification": category, "source": line.strip()})
    for path in sorted(ROOT.iterdir()):
        if path.is_file() and path.suffix in SOURCES:
            inventory.append({"path": path.name, "exists": True, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (output / "repository-inventory.json").write_text(json.dumps(inventory, indent=2))
    with (output / "placeholder-register.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "line", "classification", "source"])
        writer.writeheader()
        writer.writerows(findings)
    print(f"{len(inventory)} inventory entries; {len(findings)} indicators (review labels are not clearance).")


if __name__ == "__main__":
    main()
