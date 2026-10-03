import json
import tomllib
from pathlib import Path

import project_orchestrator.cli as cli
from engineering.validate_control_surface import validate


ROOT = Path(__file__).resolve().parents[1]


def test_control_surface_validator_passes_current_repo():
    assert validate() == []


def test_package_installs_only_canonical_cli():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {
        "project-orchestrator": "project_orchestrator.cli:main"
    }


def test_registry_classifies_every_engineering_json_exactly_once():
    registry = tomllib.loads(
        (ROOT / "engineering" / "control-surface.toml").read_text(encoding="utf-8")
    )
    categories = registry["json"]
    all_entries = [
        name
        for category in ("authority", "schema", "example", "fixture", "authoritative_state")
        for name in categories[category]
    ]
    assert len(all_entries) == len(set(all_entries))
    assert set(all_entries) == {
        path.relative_to(ROOT / "engineering").as_posix() for path in (ROOT / "engineering").rglob("*.json")
    }


def test_canonical_cli_delegates_namespaced_tool_without_reparsing(monkeypatch):
    captured = {}

    def fake_delegate(tool, argv):
        captured["tool"] = tool
        captured["argv"] = argv
        return 17

    monkeypatch.setattr(cli, "delegated_tool_main", fake_delegate)
    result = cli.main(["throughput", "action-gate", "gate.json"])
    assert result == 17
    assert captured == {
        "tool": "throughput",
        "argv": ["action-gate", "gate.json"],
    }


def test_control_plane_points_to_control_surface_registry():
    payload = json.loads(
        (ROOT / "engineering" / "control-plane.json").read_text(encoding="utf-8")
    )
    assert payload["control_surface_registry"] == "engineering/control-surface.toml"


def test_engineering_root_contains_only_authority_json():
    registry = tomllib.loads(
        (ROOT / "engineering" / "control-surface.toml").read_text(encoding="utf-8")
    )
    root_json = {path.name for path in (ROOT / "engineering").glob("*.json")}
    assert root_json == set(registry["json"]["authority"])
    assert len(root_json) == 28
