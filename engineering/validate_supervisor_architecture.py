from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "continuous_flow_supervisor.py"

MAX_SOURCE_LINES = 4500
MAX_MAIN_LINES = 1500
MAX_HELPER_LINES = 125

REQUIRED_OWNER_IMPORTS = {
    "_authority": "project_orchestrator.activation_capability",
    "_planning": "project_orchestrator.planning_dispatch",
    "_parallel_runtime": "project_orchestrator.parallel_runtime",
    "_telemetry": "project_orchestrator.telemetry_continuation",
    "_recovery": "project_orchestrator.recovery_manager",
    "_supervisor_runtime": "project_orchestrator.supervisor_runtime",
    "_workspace_recovery": "project_orchestrator.workspace_recovery",
}

FORBIDDEN_LOCAL_IMPLEMENTATIONS = {
    "_acquire_os_file_lock",
    "_release_os_file_lock",
}


def validate(path: Path = SUPERVISOR) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    source_lines = len(source.splitlines())
    if source_lines > MAX_SOURCE_LINES:
        errors.append(
            f"supervisor source budget exceeded: {source_lines}>{MAX_SOURCE_LINES}"
        )

    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    main = functions.get("main")
    if main is None:
        errors.append("supervisor main() missing")
    else:
        main_lines = (main.end_lineno or main.lineno) - main.lineno + 1
        if main_lines > MAX_MAIN_LINES:
            errors.append(
                f"supervisor main budget exceeded: {main_lines}>{MAX_MAIN_LINES}"
            )

    for name, node in functions.items():
        if name == "main":
            continue
        size = (node.end_lineno or node.lineno) - node.lineno + 1
        if size > MAX_HELPER_LINES:
            errors.append(
                f"non-orchestration helper too large: {name}={size}>{MAX_HELPER_LINES}"
            )

    forbidden = sorted(FORBIDDEN_LOCAL_IMPLEMENTATIONS & functions.keys())
    if forbidden:
        errors.append(
            "private subsystem implementation returned to supervisor: "
            + ", ".join(forbidden)
        )

    imports: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != "project_orchestrator":
            continue
        for alias in node.names:
            imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    for alias, module in REQUIRED_OWNER_IMPORTS.items():
        if imports.get(alias) != module:
            errors.append(
                f"missing subsystem owner import: {alias} -> {module}"
            )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"SUPERVISOR ARCHITECTURE: FAIL: {error}")
        return 1
    lines = len(SUPERVISOR.read_text(encoding="utf-8").splitlines())
    print(
        "SUPERVISOR ARCHITECTURE: PASS "
        f"({lines}/{MAX_SOURCE_LINES} source lines)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
