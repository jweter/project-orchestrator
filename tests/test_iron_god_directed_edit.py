"""Directed function-level edits: only the named units may change, and a bad reply is never applied."""

from __future__ import annotations

import pytest

from project_orchestrator import iron_god_directed_edit as de

MODULE = '''"""Doc."""

from __future__ import annotations

import json
from pathlib import Path

LIMIT = 3


def keep(value: int) -> int:
    return value


@staticmethod
def change(value: int) -> int:
    return value + 1


class Box:
    size = 1

    def area(self) -> int:
        return self.size
'''


def fenced(*blocks: str) -> str:
    return "\n".join(f"```python\n{b}\n```" for b in blocks)


def test_units_cover_functions_methods_classes_assignments_and_imports():
    found = de.units(MODULE)
    assert {"imports", "LIMIT", "keep", "change", "Box", "Box.area"} <= set(found)
    assert found["change"].start == 15  # the decorator line belongs to the unit
    assert found["imports"].start == 5 and found["imports"].end == 6  # __future__ is never edited
    assert found["Box.area"].indent == "    "


def test_only_the_named_unit_changes_and_new_functions_are_inserted_after_their_anchor():
    reply = fenced("def change(value: int) -> int:\n    return value + 2",
                   "def helper() -> int:\n    return 7")
    result = de.apply_reply(MODULE, reply, ["change"], add=[{"name": "helper", "after": "keep", "spec": "seven"}])
    assert "return value + 2" in result and "return value + 1" not in result
    assert result.index("def helper") < result.index("def change")
    assert de.unit_source(result, de.units(result)["keep"]) == de.unit_source(MODULE, de.units(MODULE)["keep"])


def test_a_method_is_replaced_at_its_own_indentation():
    result = de.apply_reply(MODULE, fenced("def area(self) -> int:\n    return self.size * 2"), ["Box.area"], add=[])
    assert "    def area(self) -> int:\n        return self.size * 2" in result


@pytest.mark.parametrize(("reply", "problem"), [
    (fenced("def other(value: int) -> int:\n    return 0"), "UNIT_MISMATCH"),
    ("no code at all", "BLOCK_COUNT"),
    (fenced("def change(value: int) -> int:\n    return (", ), "UNIT_MISMATCH"),
])
def test_a_reply_that_does_not_match_the_direction_is_never_applied(reply, problem):
    with pytest.raises(de.DirectedEditError, match=problem):
        de.apply_reply(MODULE, reply, ["change"], add=[])


def test_the_prompt_shows_only_the_targets_and_the_rest_as_one_line_signatures():
    prompt = de.build_prompt(MODULE, "pkg/mod.py", "Add two instead of one.", ["change"], add=[],
                             failure="E assert 2 == 3")
    assert "return value + 1" in prompt
    assert "def keep(value: int) -> int" in prompt and "return self.size" not in prompt
    assert "E assert 2 == 3" in prompt
    with pytest.raises(de.DirectedEditError, match="UNKNOWN_UNITS"):
        de.build_prompt(MODULE, "pkg/mod.py", "x", ["missing"], add=[], failure="")
