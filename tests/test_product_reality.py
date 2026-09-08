import pytest

from project_orchestrator.product_reality import (
    aggregate_product_reality_debt,
    project_debt_summaries,
    validate_product_reality_debt,
)


def debt(**overrides):
    item = {
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "kind": "unreal_playtest",
        "state": "PENDING",
        "summary": "Verify manipulator target selection in Unreal.",
        "dependency_scope": "manipulator visual acceptance lane only",
        "source_ref": "Project-Everward#201",
    }
    item.update(overrides)
    return item


def test_pending_debt_is_visible_but_scope_bounded():
    result = aggregate_product_reality_debt([debt()])
    assert result["active_debt_count"] == 1
    assert result["by_project"]["everward"]["active_count"] == 1
    assert "manipulator visual acceptance lane only" in result["by_project"]["everward"]["items"][0]["dependency_scope"]


def test_verified_item_is_not_active_debt():
    result = aggregate_product_reality_debt([debt(state="VERIFIED")])
    assert result["active_debt_count"] == 0


def test_raw_payload_fields_are_rejected():
    with pytest.raises(ValueError, match="forbidden"):
        validate_product_reality_debt(debt(screenshot="private image bytes"))


def test_duplicate_sanitized_items_are_deduplicated():
    result = aggregate_product_reality_debt([debt(), debt()])
    assert result["active_debt_count"] == 1


def test_project_summaries_are_report_ready():
    summaries = project_debt_summaries([debt()])
    assert summaries["knowledge-engine"] == []
    assert summaries["rocksmith"] == []
    assert summaries["everward"][0].startswith("unreal_playtest:")


def test_unknown_human_state_is_not_invented():
    with pytest.raises(ValueError, match="unsupported Product Reality debt state"):
        validate_product_reality_debt(debt(state="PASS"))
