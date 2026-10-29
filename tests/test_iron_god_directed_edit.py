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
    (fenced("def other(value: int) -> int:\n    return 0"), "UNEXPECTED_UNIT: other"),
    ("no code at all", "NO_CODE_BLOCKS"),
    (fenced("def change(value: int) -> int:\n    return (", ), "MALFORMED_BLOCK"),
    (fenced("def change(value: int) -> int:\n    return 2", "def change(value: int) -> int:\n    return 3"),
     "DUPLICATE_UNIT: change"),
    (fenced("print('hi')\ndef change(value: int) -> int:\n    return 2"), "UNNAMED_CODE"),
    (fenced("def change(value: int) -> int:\n    return 2", "def keep(value: int) -> int:\n    return 0"),
     "UNEXPECTED_UNIT: keep"),  # an unauthorized unit is never written, even alongside a correct one
])
def test_a_reply_that_does_not_match_the_direction_is_never_applied(reply, problem):
    with pytest.raises(de.DirectedEditError, match=problem):
        de.apply_reply(MODULE, reply, ["change"], add=[])


def test_an_incomplete_reply_names_every_missing_unit():
    reply = fenced("def change(value: int) -> int:\n    return value + 2")
    with pytest.raises(de.DirectedEditError, match=r"MISSING_UNITS: \['imports', 'helper'\]"):
        de.apply_reply(MODULE, reply, ["imports", "change"], add=[{"name": "helper", "after": "keep", "spec": "x"}])


def test_reordered_blocks_and_imports_written_with_a_new_definition_are_matched_by_name():
    # measured 2026-10-28: the model wrote the new import and the new helper in one block (BLOCK_COUNT before)
    reply = fenced("import re\n\ndef helper(text: str) -> str:\n    return re.sub('x', '', text)",
                   "def change(value: int) -> int:\n    return value + 2")
    result = de.apply_reply(MODULE, reply, ["imports", "change"],
                            add=[{"name": "helper", "after": "keep", "spec": "strip x"}])
    assert "import re\n" in result and "import json" not in result  # the imports unit was replaced as written
    assert result.index("def keep") < result.index("def helper") < result.index("def change")
    assert "return value + 2" in result


def test_a_direction_naming_an_unknown_unit_or_an_existing_new_name_is_rejected():
    with pytest.raises(de.DirectedEditError, match="DIRECTION_INVALID"):
        de.apply_reply(MODULE, fenced("def keep(value: int) -> int:\n    return 1"), [],
                       add=[{"name": "keep", "after": "LIMIT", "spec": "x"}])


def test_the_prompt_shows_only_the_targets_and_the_rest_as_one_line_signatures():
    prompt = de.build_prompt(MODULE, "pkg/mod.py", "Add two instead of one.", ["change"], add=[],
                             failure="E assert 2 == 3")
    assert "return value + 1" in prompt
    assert "def keep(value: int) -> int" in prompt and "return self.size" not in prompt
    assert "E assert 2 == 3" in prompt
    with pytest.raises(de.DirectedEditError, match="UNKNOWN_UNITS"):
        de.build_prompt(MODULE, "pkg/mod.py", "x", ["missing"], add=[], failure="")


def test_the_prompt_states_what_each_block_must_hold_and_the_repository_rules():
    # pilot 2026-10-29: a lone dict entry instead of the whole assignment; a helper hoisted to module level
    prompt = de.build_prompt(MODULE, "pkg/mod.py", "Raise the limit.", ["imports", "LIMIT", "change", "Box.area"],
                             add=[], failure="", rules=["mypy runs in strict mode: annotate everything."])
    rules = prompt.split("\nRules:", 1)[1].split("\nReply with", 1)[0]
    assert "The imports block holds every import the module needs" in rules
    assert "The LIMIT block is the whole assignment statement" in rules and "including the entries that" in rules
    assert "The change block is the whole definition of change; any helper" in rules
    assert "The Box.area block is the whole definition" in rules
    assert "Define no other top-level name." in rules
    assert "- mypy runs in strict mode: annotate everything." in rules
    added = de.build_prompt(MODULE, "pkg/mod.py", "Use a helper.", ["change"], failure="",
                            add=[{"name": "_helper", "after": "keep", "spec": "returns 2"}])
    assert "Define no other top-level name besides _helper." in added
