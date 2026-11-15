"""Strict local-only codegen contract for #710's disposable Ollama canary.

This module does NOT dispatch work, write source, call models, or change owner
authority. A caller may supply Ollama output only after the existing owner and
resource gates. Python AST rejects imports, functions calls, attributes, loops
and effects before the disposable Git fixture executes the generated code.
"""

from __future__ import annotations

import ast
import hashlib
import json
from typing import Any, Callable

TASKS = (
    ("alpha", 2, "Compute 2 by adding two positive integer constants."),
    ("beta", 5, "Compute 5 by subtracting one integer constant from another."),
    ("gamma", 9, "Compute 9 by multiplying two integer constants."),
)
MAX_PROPOSALS = 2
ALLOWED_OPERATORS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)


def prompt_for(step: int, feedback: str = "") -> str:
    """Ask for one bounded meaningful edit in a throwaway repository."""
    name, value, instruction = TASKS[step]
    return (
        "LOCAL PYTHON CODEGEN CANARY; disposable offline module only. "
        f"Write exactly one pure Python function def {name}() -> int. "
        f"{instruction} The function must return {value}. "
        "The return expression MUST be a binary arithmetic operation on "
        "two small integer constants (no bare literal return). "
        "Use no imports, calls, names other than function/int annotation, "
        "attributes, side effects, commentary, or markdown. "
        'Reply strictly as JSON with one key: {"source": "def ..."}.'
        f" Validator feedback (untrusted diagnostic): {feedback[:160]}"
    )


def validate_source(step: int, source: Any) -> tuple[str | None, str | None]:
    """Static validation is not an execution grant; native red/green is separate."""
    name, _value, _instruction = TASKS[step]
    if not isinstance(source, str) or not 20 <= len(source) <= 240:
        return None, "INVALID_SOURCE_SIZE"
    if "\x00" in source or "\r" in source:
        return None, "INVALID_SOURCE_CHARS"
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return None, "INVALID_PYTHON_SYNTAX"
    if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
        return None, "EXPECTED_ONE_FUNCTION"
    fn = module.body[0]
    if (
        fn.name != name
        or fn.decorator_list
        or fn.type_params
        or fn.args.posonlyargs
        or fn.args.args
        or fn.args.kwonlyargs
        or fn.args.vararg
        or fn.args.kwarg
        or fn.args.defaults
        or fn.args.kw_defaults
        or not isinstance(fn.returns, ast.Name)
        or fn.returns.id != "int"
        or len(fn.body) != 1
        or not isinstance(fn.body[0], ast.Return)
    ):
        return None, "WRONG_FUNCTION_SIGNATURE"
    expr = fn.body[0].value
    if (
        not isinstance(expr, ast.BinOp)
        or not isinstance(expr.op, ALLOWED_OPERATORS)
        or not isinstance(expr.left, ast.Constant)
        or not isinstance(expr.right, ast.Constant)
        or type(expr.left.value) is not int
        or type(expr.right.value) is not int
        or not -100 <= expr.left.value <= 100
        or not -100 <= expr.right.value <= 100
        or (isinstance(expr.op, ast.FloorDiv) and expr.right.value == 0)
    ):
        return None, "UNSAFE_OR_TRIVIAL_RETURN_EXPRESSION"
    # Accept only these exact AST nodes. No global names, unbound code,
    # annotations with side effects, function calls, or conditional tricks.
    allowed = (
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.Return,
        ast.Name,
        ast.Load,
        ast.BinOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.FloorDiv,
    )
    if any(not isinstance(node, allowed) for node in ast.walk(module)):
        return None, "UNSAFE_AST_NODE"
    normalized = source.strip() + "\n"
    return normalized, None


def propose_source(
    step: int,
    ask_local: Callable[[str], Any],
    authorized: Callable[[], bool],
) -> dict[str, Any]:
    """Bounded local-only request; no fallback and no automatic work authorization."""
    if type(step) is not int or not 0 <= step < len(TASKS):
        return {"status": "INVALID_STEP"}
    problems: list[str] = []
    for _ in range(MAX_PROPOSALS):
        if not authorized():
            return {"status": "OWNER_AUTHORITY_DENIED"}
        try:
            raw = ask_local(prompt_for(step, problems[-1] if problems else ""))
        except json.JSONDecodeError:
            raw = {"malformed_json": True}
        except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
            return {
                "status": "LOCAL_MODEL_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "attempted_proposals": len(problems),
            }
        if not authorized():
            return {"status": "OWNER_AUTHORITY_DENIED_AFTER_MODEL"}
        value = raw.get("source") if isinstance(raw, dict) else None
        normalized, error = validate_source(step, value)
        if normalized is not None:
            return {
                "status": "LOCAL_SOURCE_VALIDATED",
                "source": normalized,
                "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "attempted_proposals": len(problems) + 1,
                "prior_rejections": problems,
            }
        problems.append(error or "MODEL_SOURCE_REJECTED")
    return {
        "status": "LOCAL_CODEGEN_REJECTED",
        "attempted_proposals": MAX_PROPOSALS,
        "validation_failures": problems,
    }
