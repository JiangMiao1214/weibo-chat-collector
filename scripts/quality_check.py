"""Dependency, file-size, local cyclomatic-complexity and duplicate-body ratchet."""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts/quality-baseline.json"


def find_cycles(graph):
    visited, active, errors = set(), [], []

    def visit(node):
        if node in active:
            errors.append("dependency cycle: " + " -> ".join(active[active.index(node):] + [node]))
            return
        if node in visited:
            return
        active.append(node)
        for target in graph.get(node, []):
            visit(target)
        active.pop()
        visited.add(node)

    for node in graph:
        visit(node)
    return errors


def local_complexity(node):
    count = 1
    branches = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.comprehension, ast.match_case)

    def visit(child):
        nonlocal count
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(child, branches):
            count += 1
        if isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        for nested in ast.iter_child_nodes(child):
            visit(nested)

    for child in node.body:
        visit(child)
    return count


def python_measurements():
    metrics, graph, violations = {}, {}, []
    bodies = defaultdict(list)
    paths = sorted((ROOT / "backend/app").rglob("*.py"))
    modules = {".".join(path.relative_to(ROOT / "backend").with_suffix("").parts).removesuffix(".__init__"): path.relative_to(ROOT).as_posix() for path in paths}
    for path in paths:
        file = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        metrics[f"lines:{file}"] = len(source.rstrip().splitlines())
        tree = ast.parse(source)
        graph[file] = []
        package = ".".join(path.relative_to(ROOT / "backend").parent.parts)

        def visit(node, scope="module"):
            if isinstance(node, ast.ImportFrom):
                prefix = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
                module = ".".join(prefix + ([node.module] if node.module else []))
                targets = [module] + [f"{module}.{alias.name}" for alias in node.names]
                graph[file].extend(modules[target] for target in targets if target in modules)
                if file.endswith(("/message_filter.py", "/collectors/base.py")) and any(part in module.split(".") for part in ("api", "database", "httpx", "requests", "sqlite3", "urllib")):
                    violations.append(f"pure domain imports IO: {file} -> {module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        graph[file].append(modules[alias.name])
                    if file.endswith(("/message_filter.py", "/collectors/base.py")) and alias.name.split(".")[0] in {"sqlite3", "requests", "httpx", "urllib"}:
                        violations.append(f"pure domain imports IO: {file} -> {alias.name}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope += "/" + node.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                metrics[f"complexity:{file}:{scope}"] = local_complexity(node)
                body = "\n".join(ast.dump(child, include_attributes=False) for child in node.body)
                if len(body) >= 500:
                    bodies[body].append(f"{file}:{scope}")
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(tree)
    duplicates = sorted(" | ".join(sorted(locations)) for locations in bodies.values() if len(locations) > 1)
    return dict(metrics=metrics, graph=graph, duplicates=duplicates, violations=violations)


def compare(snapshot, baseline):
    errors = list(snapshot["violations"]) + find_cycles(snapshot["graph"])
    for key, value in snapshot["metrics"].items():
        default = 400 if key.startswith("lines:") else 20
        maximum = baseline["metrics"].get(key, default)
        if value > maximum:
            errors.append(f"{key}: {value} > {maximum}")
    old_duplicates = set(baseline["duplicates"])
    errors.extend(f"new duplicate function bodies: {item}" for item in snapshot["duplicates"] if item not in old_duplicates)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true", help="print measurements; never update baseline automatically")
    args = parser.parse_args()
    snapshot = python_measurements()
    run = subprocess.run(["node", str(ROOT / "scripts/frontend_quality.mjs")], cwd=ROOT, capture_output=True, text=True, timeout=30)
    if run.returncode:
        print(run.stderr, file=sys.stderr)
        return 2
    frontend = json.loads(run.stdout)
    for key in ("metrics", "graph"):
        snapshot[key].update(frontend[key])
    for key in ("duplicates", "violations"):
        snapshot[key].extend(frontend[key])
    if args.snapshot:
        # Only legacy exceptions need storage. New small functions use defaults.
        print(json.dumps({"metrics": {k: v for k, v in snapshot["metrics"].items() if v > (400 if k.startswith("lines:") else 20)}, "duplicates": snapshot["duplicates"]}, ensure_ascii=False, indent=2))
        return 0
    errors = compare(snapshot, json.loads(BASELINE.read_text(encoding="utf-8")))
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Quality ratchet: {'FAIL' if errors else 'PASS'} ({len(snapshot['graph'])} modules, {len(snapshot['metrics'])} measurements)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
