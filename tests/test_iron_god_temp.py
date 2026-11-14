"""One temp root for everything the engine runs, so a single security-software exclusion covers it."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_temp


@pytest.fixture
def temp(monkeypatch: pytest.MonkeyPatch):
    """The real module behaviour: conftest replaces ``configure`` for every other test."""
    monkeypatch.undo()
    module = importlib.reload(iron_god_temp)
    monkeypatch.delenv(module.ENV, raising=False)
    yield module
    monkeypatch.delenv(module.ENV, raising=False)


def test_without_a_configured_root_every_temp_directory_stays_where_it_was(temp) -> None:
    beside = Path("C:/work/worktrees/P-1.quick.tmp")
    assert temp.temp_dir(beside) == beside
    assert temp.configure(None) is None and temp.configure("") is None and temp.temp_dir(beside) == beside


def test_a_configured_root_holds_every_temp_directory_under_its_own_name(temp, tmp_path: Path) -> None:
    assert temp.configure(str(tmp_path)) == str(tmp_path)
    names = ["P-1.tmp", "P-1.quick.tmp", "P-1.mypy.tmp", "P-1.l2.tmp", "CANON-7.tmp"]
    placed = [temp.temp_dir(Path("C:/somewhere/else") / name) for name in names]
    assert placed == [tmp_path / name for name in names]           # one folder, and no two runs share a directory


def test_environment_variables_in_the_policy_value_are_expanded(temp, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert temp.configure("%LOCALAPPDATA%\\ExponentialEngine\\temp") == str(tmp_path) + "\\ExponentialEngine\\temp"


def test_a_variable_that_does_not_resolve_keeps_the_old_placement(temp, monkeypatch) -> None:
    monkeypatch.delenv("NO_SUCH_VARIABLE_HERE", raising=False)
    assert temp.configure("%NO_SUCH_VARIABLE_HERE%\\temp") is None
    assert temp.temp_dir(Path("C:/w/P.tmp")) == Path("C:/w/P.tmp")   # never a folder literally named %...%


def test_unsetting_the_policy_clears_a_root_set_earlier(temp, tmp_path: Path) -> None:
    temp.configure(str(tmp_path))
    assert temp.configure(None) is None and temp.temp_dir(Path("C:/w/P.tmp")) == Path("C:/w/P.tmp")


def test_the_shipped_policy_names_the_owners_excluded_test_folder() -> None:
    policy = json.loads((Path(__file__).resolve().parents[1] / "engineering" / "iron-god-commissioning.json")
                        .read_text(encoding="utf-8"))
    # the folder the owner created and excluded in Norton 360 (issue #675, 2026-11-14)
    assert policy["portfolio_rotation"]["engine_temp_root"] == "C:\\IronGod\\TestTemp"


def test_no_other_test_can_point_temp_directories_at_the_live_engine_folder(monkeypatch) -> None:
    import os

    assert iron_god_temp.ENV not in os.environ
    iron_god_temp.configure("C:/would/be/the/live/folder")           # what a lane test running the shipped policy does
    assert iron_god_temp.ENV not in os.environ
