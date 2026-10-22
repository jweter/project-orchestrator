"""Self-development can be planned, but never bootstraps its own authority."""

from __future__ import annotations

import json

import pytest

from project_orchestrator.iron_god_shadow_self_development import (
    plan_shadow_self_development,
)
from project_orchestrator.team_runtime import build_team_plan


def candidate(**overrides):
    data = {
        "project_id": "iron-god",
        "repository": "jweter/project-orchestrator",
        "base_sha": "d" * 40,
        "branch": "fix/iron-god-shadow-repair",
        "title": "Repair resident dispatcher regression",
        "paths": [
            "src/project_orchestrator/supervisor_runtime.py",
            "tests/test_supervisor_runtime_boundary.py",
        ],
    }
    data.update(overrides)
    return data


def test_self_targeting_is_a_shadow_projection_not_a_new_dispatch():
    result = plan_shadow_self_development(candidate())
    assert result["status"] == "SHADOW_SELF_REPAIR_CANDIDATE"
    assert result["candidate"]["metadata_provenance"] == "CALLER_SUPPLIED_UNVERIFIED"
    assert result["project_id"] == "iron-god"
    assert result["execution_authorized"] is False
    assert result["dispatch_created"] is False
    assert result["lease_acquired"] is False
    assert result["controller_mode_changed"] is False
    assert result["merge_authorized"] is False
    assert "INDEPENDENT_OWNER_CONTROL" in result["next_gate"]
    assert "lease_id" not in json.dumps(result)
    assert "claim_token" not in json.dumps(result)


def test_existing_live_work_path_collision_blocks_self_repair():
    current = [
        {
            "repository": "jweter/project-orchestrator",
            "branch": "feature/claude-active",
            "changed_files": ["src/project_orchestrator"],
        }
    ]
    result = plan_shadow_self_development(candidate(), live_active_work=current)
    assert result["status"] == "SHADOW_PATH_CONFLICT"
    assert "feature/claude-active" in result["conflict"]
    assert result["execution_authorized"] is False


def test_unrelated_product_worker_does_not_create_false_overlap():
    current = [
        {
            "repository": "jweter/Project-Everward",
            "branch": "feature/live",
            "changed_files": ["src"],
        }
    ]
    result = plan_shadow_self_development(candidate(), live_active_work=current)
    assert result["status"] == "SHADOW_SELF_REPAIR_CANDIDATE"
    assert result["conflict"] is None


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"project_id": "everward"}, "SELF_DEVELOPMENT_REPOSITORY_UNVERIFIED"),
        (
            {"repository": "jweter/Project-Everward"},
            "SELF_DEVELOPMENT_REPOSITORY_UNVERIFIED",
        ),
        ({"base_sha": "d" * 39}, "SELF_DEVELOPMENT_BASE_UNVERIFIED"),
        ({"base_sha": "0" * 40 + "x"}, "SELF_DEVELOPMENT_BASE_UNVERIFIED"),
        ({"branch": "main"}, "SELF_DEVELOPMENT_BRANCH_UNVERIFIED"),
        (
            {"branch": "fix/iron-god-../owner-control"},
            "SELF_DEVELOPMENT_BRANCH_UNVERIFIED",
        ),
        ({"title": ""}, "SELF_DEVELOPMENT_OBJECTIVE_UNVERIFIED"),
        ({"paths": []}, "SELF_DEVELOPMENT_PATHS_INVALID"),
        (
            {"paths": ["src/project_orchestrator/foo.py"]},
            "SELF_DEVELOPMENT_TESTS_REQUIRED",
        ),
        ({"paths": ["tests/test_a.py"]}, "SELF_DEVELOPMENT_SOURCE_REQUIRED"),
        (
            {
                "paths": [
                    "src/project_orchestrator/foo.py",
                    "scripts/desktop-control/Take-The-Wheel.ps1",
                ]
            },
            "SELF_DEVELOPMENT_PATHS_OUTSIDE_SAFE_SCOPE",
        ),
        (
            {
                "paths": [
                    "src/project_orchestrator/../ModeController.py",
                    "tests/test_a.py",
                ]
            },
            "SELF_DEVELOPMENT_PATHS_OUTSIDE_SAFE_SCOPE",
        ),
    ],
)
def test_invalid_self_development_identity_fails_closed(updates, reason):
    with pytest.raises(ValueError, match=reason):
        plan_shadow_self_development(candidate(**updates))


def test_self_repair_does_not_enter_existing_product_dispatch_planner():
    # The existing portfolio scheduler is untouched, and this projection
    # does not grant it a fifth executable worker family.
    result = build_team_plan([candidate()])
    assert result["dispatch"] == []
    assert result["leases"] == []


def test_shadow_projection_never_touches_disk(tmp_path):
    original = list(tmp_path.iterdir())
    result = plan_shadow_self_development(candidate())
    assert result["execution_authorized"] is False
    assert list(tmp_path.iterdir()) == original
