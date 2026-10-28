"""Directed function-level edits for Level 2 (existing-code) slices.

Measured on 2026-10-27 (Level 2 calibration, whole-file aider edits): every
failure left the target module unchanged -- the model edited or regenerated the
named test file instead, or timed out regenerating a 200+ line module. A
directed slice instead names the exact units to change (functions, methods,
classes, module-level assignments, or the import block) and the required
behaviour; the model sees only those units plus one-line signatures of the rest
of the module and the failing assertions, and returns only the new units. The
worker splices them back by syntax tree, so nothing outside the named units can
change and no test file is ever in the model's context.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass

IMPORTS = "imports"
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


class DirectedEditError(ValueError):
    """The reply cannot be applied exactly as directed (never partially applied)."""


@dataclass(frozen=True)
class Unit:
    name: str
    start: int  # 1-based first line (decorators included)
    end: int  # 1-based last line
    indent: str


def _span(node: ast.AST) -> tuple[int, int]:
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno, *(d.lineno for d in decorators)])
    return start, node.end_lineno or node.lineno


def units(source: str) -> dict[str, Unit]:
    """Every editable unit in a module, by name."""
    tree = ast.parse(source)
    lines = source.splitlines()
    found: dict[str, Unit] = {}

    def indent_of(line_no: int) -> str:
        line = lines[line_no - 1]
        return line[: len(line) - len(line.lstrip())]

    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
               and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    if imports:
        found[IMPORTS] = Unit(IMPORTS, imports[0].lineno, max(n.end_lineno or n.lineno for n in imports), "")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start, end = _span(node)
            found[node.name] = Unit(node.name, start, end, indent_of(node.lineno))
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        s, e = _span(sub)
                        found[f"{node.name}.{sub.name}"] = Unit(f"{node.name}.{sub.name}", s, e, indent_of(sub.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = Unit(target.id, node.lineno, node.end_lineno or node.lineno, "")
    return found


def unit_source(source: str, unit: Unit) -> str:
    return "\n".join(source.splitlines()[unit.start - 1:unit.end])


def outline(source: str, exclude: set[str]) -> str:
    """One line per other top-level name: what the model may call without seeing its code."""
    tree = ast.parse(source)
    rows = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in exclude:
            rows.append(f"def {node.name}({ast.unparse(node.args)})"
                        + (f" -> {ast.unparse(node.returns)}" if node.returns else ""))
        elif isinstance(node, ast.ClassDef) and node.name not in exclude:
            rows.append(f"class {node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            rows += [f"{t.id} = ..." for t in targets if isinstance(t, ast.Name) and t.id not in exclude]
    return "\n".join(rows)


def build_prompt(source: str, module: str, direction: str, targets: list[str], *, add: list[dict[str, str]],
                 failure: str, previous: str = "") -> str:
    found = units(source)
    missing = [t for t in targets if t not in found]
    if missing:
        raise DirectedEditError(f"UNKNOWN_UNITS: {missing}")
    parts = "\n\n".join(f"### {t}\n```python\n{textwrap.dedent(unit_source(source, found[t]))}\n```" for t in targets)
    additions = "".join(f"\n- NEW top-level definition {a['name']}: {a['spec']}" for a in add)
    prompt = (
        f"You are changing existing Python code in {module}.\n"
        f"Task: {direction}\n\n"
        "Change only the parts below. Current code of each part:\n\n" + parts + "\n"
        + (f"\nAlso add:{additions}\n" if add else "")
        + "\nOther names already defined in this module (you may use them):\n" + outline(source, set(targets)) + "\n"
        + "\nFailing tests (they are correct; make them pass):\n" + failure.strip() + "\n"
        + (f"\nYour previous answer did not pass. It was:\n{previous.strip()}\n" if previous else "")
        + "\nReply with one ```python block per part, in the order listed"
        + (", then one block per new definition" if add else "")
        + ". Each block holds the complete new code of that part only. Keep every name and signature unless the "
        "task says otherwise. No explanations."
    )
    return prompt


def _block_name(code: str) -> str | None:
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None
    if tree.body and all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body):
        return IMPORTS
    if len(tree.body) != 1:
        return None
    node = tree.body[0]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        return target.id if isinstance(target, ast.Name) else None
    return None


def apply_reply(source: str, reply: str, targets: list[str], *, add: list[dict[str, str]]) -> str:
    """The module with exactly the named units replaced and the new functions inserted; else DirectedEditError."""
    found = units(source)
    blocks = [b for b in _FENCE.findall(reply) if b.strip()]
    expected = len(targets) + len(add)
    if len(blocks) != expected:
        raise DirectedEditError(f"BLOCK_COUNT: expected {expected}, got {len(blocks)}")
    replacements: list[tuple[Unit, str]] = []
    for target, code in zip(targets, blocks[:len(targets)]):
        name = _block_name(code)
        if name != target.rsplit(".", 1)[-1] and not (target == IMPORTS and name == IMPORTS):
            raise DirectedEditError(f"UNIT_MISMATCH: expected {target}, got {name}")
        replacements.append((found[target], code))
    lines = source.splitlines()
    for unit, code in sorted(replacements, key=lambda r: r[0].start, reverse=True):
        new = textwrap.indent(textwrap.dedent(code).strip("\n"), unit.indent).splitlines()
        lines[unit.start - 1:unit.end] = new
    result = "\n".join(lines) + "\n"
    for spec, code in zip(add, blocks[len(targets):]):
        if _block_name(code) != spec["name"]:
            raise DirectedEditError(f"NEW_DEFINITION_MISMATCH: expected {spec['name']}")
        anchor = units(result).get(spec["after"])
        if anchor is None:
            raise DirectedEditError(f"UNKNOWN_ANCHOR: {spec['after']}")
        rows = result.splitlines()
        rows[anchor.end:anchor.end] = ["", "", *textwrap.dedent(code).strip("\n").splitlines()]
        result = "\n".join(rows) + "\n"
    try:
        ast.parse(result)
    except SyntaxError as exc:
        raise DirectedEditError(f"RESULT_UNPARSEABLE: {exc.msg} line {exc.lineno}") from None
    before, after = units(source), units(result)
    touched = set(targets) | {a["name"] for a in add}
    for name, unit in before.items():
        owner = name.split(".", 1)[0]
        # a targeted method changes its class; a targeted class changes its methods
        if name in touched or owner in touched or any(t.startswith(f"{name}.") for t in touched):
            continue
        if name not in after or unit_source(source, unit) != unit_source(result, after[name]):
            raise DirectedEditError(f"UNTARGETED_UNIT_CHANGED: {name}")
    return result
