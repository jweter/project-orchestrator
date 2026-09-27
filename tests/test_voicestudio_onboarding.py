from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator import exponential_engine
from project_orchestrator.inventory import load_inventory
from project_orchestrator.n8n_policy import PORTFOLIO_REPOSITORIES
from project_orchestrator.portfolio_laptop_probe import PORTFOLIO
from project_orchestrator.evolution_bridge import PROJECT_REPOSITORIES

import scripts.autonomous_slice_builder as builder
import scripts.green_pr_merger as merger
import scripts.laptop_actor_runner as laptop_runner
import scripts.laptop_actor_github_bridge as laptop_bridge


REPOSITORY = "jweter/VoiceStudio"
PROJECT = "voicestudio"


def test_voicestudio_is_canonical_fourth_product():
    inventory = load_inventory("engineering/portfolio.json")
    projects = {item["id"]: item for item in inventory["projects"]}

    assert tuple(projects) == (
        "knowledge-engine",
        "rocksmith",
        "everward",
        "voicestudio",
    )
    voice = projects[PROJECT]
    assert voice["repositories"] == [{"name": REPOSITORY, "role": "standalone"}]
    assert voice["authority_docs"] == ["docs/projects/singing-mode.md"]
    assert voice["initial_milestone"] == "S0"
    assert "singing voice conversion" in voice["product_goal"].lower()


def test_voicestudio_is_admitted_by_runtime_surfaces():
    assert PROJECT in exponential_engine.PROJECTS
    assert PROJECT_REPOSITORIES[PROJECT] == {REPOSITORY}
    assert REPOSITORY in PORTFOLIO_REPOSITORIES
    assert ("voicestudio", REPOSITORY) in PORTFOLIO
    assert REPOSITORY in laptop_runner.ALLOWED_REPOSITORIES
    assert REPOSITORY in laptop_bridge.ALLOWED_REPOSITORIES
    assert REPOSITORY in merger.PORTFOLIO_REPOS


def test_voicestudio_builder_prompt_uses_active_singing_authority(monkeypatch):
    monkeypatch.setattr(builder, "open_pr_paths", lambda _repo: [])
    text = builder.prompt_for(REPOSITORY)

    assert "docs/projects/singing-mode.md" in text
    assert "first unchecked S0-S8 milestone" in text
    assert "starting with S0" in text
    assert "singing voice conversion" in text
    assert "superseded OmniVoice singing ADR" in text


def test_exponential_engine_capacity_starts_at_four():
    config = json.loads(Path("engineering/exponential-engine.json").read_text(encoding="utf-8"))
    assert config["dispatch"]["independent_product_lanes"] == [
        "knowledge-engine",
        "rocksmith",
        "everward",
        "voicestudio",
    ]
    assert config["capacity_controller"]["initial_max_concurrent_product_lanes"] == 4
