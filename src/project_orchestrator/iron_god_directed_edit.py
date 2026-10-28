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


def _piece_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(target, ast.Name) and (isinstance(node, ast.AnnAssign) or len(node.targets) == 1):
            return target.id
    return None


def reply_pieces(reply: str) -> list[tuple[str, str]]:
    """Every named top-level piece of every fenced block, as (name, code), in reply order.

    Measured on 2026-10-28: the model was right but one block short when a direction combined
    an import change with a new definition -- it wrote both in one block. Pieces are therefore
    matched to the directed units by name, never by block count or position. All imports in a
    block form one ``imports`` piece; anything unnamed or unparseable rejects the whole reply.
    """
    blocks = [b for b in _FENCE.findall(reply) if b.strip()]
    if not blocks:
        raise DirectedEditError("NO_CODE_BLOCKS")
    pieces: list[tuple[str, str]] = []
    for number, block in enumerate(blocks, 1):
        code = textwrap.dedent(block)
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise DirectedEditError(f"MALFORMED_BLOCK: block {number}: {exc.msg} line {exc.lineno}") from None
        lines = code.splitlines()
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                   and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
        if imports:
            pieces.append((IMPORTS, "\n".join("\n".join(lines[n.lineno - 1:n.end_lineno]) for n in imports)))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            name = _piece_name(node)
            if name is None:
                raise DirectedEditError(f"UNNAMED_CODE: block {number} line {node.lineno}")
            start, end = _span(node)
            pieces.append((name, "\n".join(lines[start - 1:end])))
    return pieces


def apply_reply(source: str, reply: str, targets: list[str], *, add: list[dict[str, str]]) -> str:
    """The module with exactly the named units replaced and the new definitions inserted; else DirectedEditError.

    Every directed unit must appear exactly once, and nothing else may appear; the reply is
    checked completely before anything is applied.
    """
    found = units(source)
    wanted = {(IMPORTS if t == IMPORTS else t.rsplit(".", 1)[-1]): t for t in targets}
    wanted.update({a["name"]: a["name"] for a in add})
    if len(wanted) != len(targets) + len(add):
        raise DirectedEditError("AMBIGUOUS_TARGETS: two directed units share a name")
    unknown = [t for t in targets if t not in found] + [a["name"] for a in add if a["name"] in found]
    if unknown:
        raise DirectedEditError(f"DIRECTION_INVALID: unknown target or existing new name: {unknown}")
    code_for: dict[str, str] = {}
    for name, code in reply_pieces(reply):
        if name not in wanted:
            raise DirectedEditError(f"UNEXPECTED_UNIT: {name}")
        if wanted[name] in code_for:
            raise DirectedEditError(f"DUPLICATE_UNIT: {wanted[name]}")
        code_for[wanted[name]] = code
    missing = [unit for unit in wanted.values() if unit not in code_for]
    if missing:
        raise DirectedEditError(f"MISSING_UNITS: {missing}")
    lines = source.splitlines()
    for unit in sorted((found[t] for t in targets), key=lambda u: u.start, reverse=True):
        new = textwrap.indent(code_for[unit.name].strip("\n"), unit.indent).splitlines()
        lines[unit.start - 1:unit.end] = new
    result = "\n".join(lines) + "\n"
    for spec in add:
        code = code_for[spec["name"]]
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
