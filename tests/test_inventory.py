import pytest

from project_orchestrator.inventory import load_inventory, validate_inventory


def test_canonical_inventory_contains_four_projects_and_six_repositories() -> None:
    inventory = load_inventory("engineering/portfolio.json")
    assert [project["id"] for project in inventory["projects"]] == ["knowledge-engine", "rocksmith", "everward", "voicestudio"]
    assert sum(len(project["repositories"]) for project in inventory["projects"]) == 6


def test_inventory_rejects_missing_repository() -> None:
    payload = {
        "schema_version": 1,
        "projects": [{"id": "knowledge-engine", "repositories": [{"name": "jweter/knowledge-engine-core", "role": "core"}]}],
    }
    with pytest.raises(ValueError, match="portfolio repository mismatch"):
        validate_inventory(payload)
