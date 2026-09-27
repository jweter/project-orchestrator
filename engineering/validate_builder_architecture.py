from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "autonomous_slice_builder.py"

MAX_SOURCE_LINES = 1250
MAX_BUILD_ONCE_LINES = 175
FORBIDDEN_PROMOTION_MARKERS = (
    '"PREFLIGHT_EVIDENCE_REJECTED"',
    '"BASE_RECONCILIATION_CONFLICT"',
    '"PR_IDENTITY_UNPROVEN"',
    '"PR_CREATE_FAILED"',
)


def validate(path: Path = BUILDER) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    source_lines = len(source.splitlines())
    if source_lines > MAX_SOURCE_LINES:
        errors.append(
            f"builder source budget exceeded: {source_lines}>{MAX_SOURCE_LINES}"
        )

    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    build_once = functions.get("build_once")
    if build_once is None:
        errors.append("build_once() missing")
    else:
        size = (build_once.end_lineno or build_once.lineno) - build_once.lineno + 1
        if size > MAX_BUILD_ONCE_LINES:
            errors.append(
                f"build_once orchestration budget exceeded: "
                f"{size}>{MAX_BUILD_ONCE_LINES}"
            )

    if "promote_candidate(" not in source:
        errors.append("builder must delegate committed-candidate promotion")

    for marker in FORBIDDEN_PROMOTION_MARKERS:
        if marker in source:
            errors.append(
                f"promotion implementation leaked back into builder: {marker}"
            )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"BUILDER ARCHITECTURE: FAIL: {error}")
        return 1
    source = BUILDER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    build_once = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_once"
    )
    size = (build_once.end_lineno or build_once.lineno) - build_once.lineno + 1
    print(
        "BUILDER ARCHITECTURE: PASS "
        f"(build_once={size}/{MAX_BUILD_ONCE_LINES} lines)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
