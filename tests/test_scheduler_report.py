import pytest

from project_orchestrator.scheduler_report import build_scheduler_executive_report


RESULTS = {
    "MERGED": [],
    "FIXED": [],
    "NEW_PRS": [],
    "WAITING_ON_PREFLIGHT": [],
    "WAITING_ON_CI": [],
    "BLOCKED": [],
    "NEEDS_JEREMY": [],
}


def p(eligibility="ELIGIBLE", advancement="SUBSTANTIAL_SLICE_ADVANCED", **overrides):
    item = {
        "eligibility": eligibility,
        "advancement": advancement,
        "advancement_actor": "ORCHESTRATOR" if advancement != "NONE" else None,
        "promotion_state": "IMPLEMENTING",
        "implementation_verification": "targeted tests passed",
        "active_ownership": "orchestrator",
        "next_gate": "exact-head preflight",
        "product_reality_debt": [],
    }
    item.update(overrides)
    return item


def base_projects():
    return {
        "knowledge-engine": p(),
        "rocksmith": p(),
        "everward": p(),
    }


def build(projects=None, **kwargs):
    return build_scheduler_executive_report(
        project_evidence=projects or base_projects(),
        results=RESULTS,
        project_orchestrator={"state": "GREEN", "next_gate": "product execution"},
        product_repositories_inspected=kwargs.pop("product_repositories_inspected", 5),
        generated_at="2026-09-08T03:00:00Z",
        **kwargs,
    )


def test_all_eligible_products_advanced_is_green():
    report = build()
    assert report["executive_status"] == "GREEN"
    assert report["kpis"]["product_advancement_rate"]["value"] == 1.0


def test_partial_advancement_is_yellow_and_requires_reason():
    projects = base_projects()
    projects["rocksmith"] = p(
        advancement="NONE",
        advancement_actor=None,
        no_advancement_reason="write path unavailable",
    )
    report = build(projects)
    assert report["executive_status"] == "YELLOW"
    assert report["kpis"]["product_advancement_rate"]["value"] == pytest.approx(2 / 3)
    assert report["no_advancement_reasons"]["rocksmith"] == "write path unavailable"


def test_missing_reason_fails_closed():
    projects = base_projects()
    projects["rocksmith"] = p(advancement="NONE", advancement_actor=None)
    with pytest.raises(ValueError, match="no_advancement_reason"):
        build(projects)


def test_repeated_no_advancement_reason_is_p1_orchestration_defect():
    projects = {
        name: p(
            advancement="NONE",
            advancement_actor=None,
            no_advancement_reason="eligible product work was triaged but not serviced",
        )
        for name in ("knowledge-engine", "rocksmith", "everward")
    }
    with pytest.raises(ValueError, match="P1 orchestration defect"):
        build(
            projects,
            previous_no_advancement_reasons={
                name: "eligible product work was triaged but not serviced"
                for name in ("knowledge-engine", "rocksmith", "everward")
            },
        )


def test_changed_no_advancement_reason_may_report_once_for_new_evidence():
    projects = base_projects()
    projects["rocksmith"] = p(
        advancement="NONE",
        advancement_actor=None,
        no_advancement_reason="repository-native preflight executable unavailable in current environment",
    )
    report = build(
        projects,
        previous_no_advancement_reasons={"rocksmith": "private Product Reality pending"},
    )
    assert report["executive_status"] == "YELLOW"
    assert report["no_advancement_reasons"]["rocksmith"].startswith("repository-native preflight")


def test_orchestrator_advancement_clears_prior_no_advancement_reason():
    report = build(
        previous_no_advancement_reasons={
            "knowledge-engine": "eligible product work was triaged but not serviced"
        }
    )
    assert report["kpis"]["projects_materially_advanced"] == 3


def test_no_safe_work_is_not_counted_in_denominator():
    projects = base_projects()
    projects["rocksmith"] = p(
        eligibility="NO_SAFE_ELIGIBLE_WORK",
        advancement="NONE",
        advancement_actor=None,
        promotion_state="NO_SAFE_ELIGIBLE_WORK",
        reason="all roadmap candidates are dependency blocked",
    )
    report = build(projects)
    rate = report["kpis"]["product_advancement_rate"]
    assert rate["denominator"] == 2
    assert rate["value"] == 1.0


def test_orchestrator_only_progress_cannot_fake_product_throughput():
    projects = {
        name: p(
            advancement="NONE",
            advancement_actor=None,
            no_advancement_reason="not serviced",
        )
        for name in ("knowledge-engine", "rocksmith", "everward")
    }
    report = build(projects)
    assert report["executive_status"] == "RED"
    assert report["kpis"]["throughput_floor_met"] is False


def test_jeremy_merge_does_not_count_as_current_run_advancement():
    projects = {
        name: p(
            advancement="NONE",
            advancement_actor=None,
            no_advancement_reason="not serviced",
        )
        for name in ("knowledge-engine", "rocksmith", "everward")
    }
    report = build(
        projects,
        external_state_changes={
            "everward": [
                {
                    "advancement": "MERGED_GREEN_PR",
                    "actor": "JEREMY",
                    "note": "Jeremy merged PR #213 before this run could claim it",
                }
            ]
        },
    )
    assert report["executive_status"] == "RED"
    assert report["kpis"]["projects_materially_advanced"] == 0
    assert report["kpis"]["throughput_floor_met"] is False
    assert report["external_state_changes"]["everward"][0]["actor"] == "JEREMY"


def test_external_change_and_orchestrator_progress_can_coexist():
    report = build(
        external_state_changes={
            "everward": [
                {
                    "advancement": "MERGED_GREEN_PR",
                    "actor": "JEREMY",
                    "note": "Jeremy merged the previous item before re-triage",
                }
            ]
        }
    )
    assert report["kpis"]["projects_materially_advanced"] == 3
    assert report["kpis"]["throughput_floor_met"] is True
    assert report["external_state_changes"]["everward"][0]["actor"] == "JEREMY"


def test_external_change_remains_visible_when_project_becomes_ineligible():
    projects = base_projects()
    projects["everward"] = p(
        eligibility="NO_SAFE_ELIGIBLE_WORK",
        advancement="NONE",
        advancement_actor=None,
        promotion_state="NO_SAFE_ELIGIBLE_WORK",
        reason="all remaining candidates are dependency blocked",
    )
    report = build(
        projects,
        external_state_changes={
            "everward": [
                {
                    "advancement": "MERGED_GREEN_PR",
                    "actor": "JEREMY",
                    "note": "Jeremy merged the last eligible item before this run",
                }
            ]
        },
    )
    assert report["external_state_changes"]["everward"][0]["advancement"] == "MERGED_GREEN_PR"
    assert report["kpis"]["product_advancement_rate"]["denominator"] == 2


def test_external_ci_completion_does_not_count_as_run_advancement():
    projects = base_projects()
    projects["rocksmith"] = p(
        advancement="NONE",
        advancement_actor=None,
        no_advancement_reason="CI completed externally; no orchestrator product action followed",
    )
    report = build(
        projects,
        external_state_changes={
            "rocksmith": [
                {
                    "advancement": "PREFLIGHT_GREEN",
                    "actor": "EXTERNAL_AUTOMATION",
                    "note": "external CI completed",
                }
            ]
        },
    )
    assert report["kpis"]["projects_materially_advanced"] == 2
    assert report["kpis"]["product_advancement_rate"]["value"] == pytest.approx(2 / 3)


def test_non_orchestrator_advancement_actor_fails_closed():
    projects = base_projects()
    projects["everward"] = p(advancement_actor="JEREMY")
    with pytest.raises(ValueError, match="record outside actions in external_state_changes"):
        build(projects)


def test_unknown_advancement_actor_fails_closed():
    projects = base_projects()
    projects["everward"] = p(advancement_actor="UNKNOWN")
    with pytest.raises(ValueError, match="advancement_actor must be known"):
        build(projects)


def test_repository_inspection_floor_is_visible():
    report = build(product_repositories_inspected=4)
    assert report["executive_status"] == "RED"
    assert report["kpis"]["product_repositories_inspected"]["status"] == "RED"


def test_learning_effectiveness_and_jeremy_help_are_first_class():
    report = build(
        learning_evolution={"prior_lessons_reused": 2, "preventable_repeat_failures": 0},
        learning_effectiveness={"status": "MEASURED", "value": 0.75},
        jeremy_can_help=["Merge surfaced GREEN PRs promptly."],
    )
    assert report["learning_evolution"]["prior_lessons_reused"] == 2
    assert report["learning_evolution"]["learning_effectiveness"]["value"] == 0.75
    assert report["jeremy_can_help"] == ["Merge surfaced GREEN PRs promptly."]


def test_unknown_learning_metrics_stay_unknown():
    report = build()
    assert report["learning_evolution"]["root_causes_established"] == "UNKNOWN"


def test_cli_scheduler_report_passes_previous_no_advancement_reasons(tmp_path, monkeypatch, capsys):
    import json
    from project_orchestrator import cli
    projects = {name: p(advancement="NONE", advancement_actor=None, no_advancement_reason="same stall") for name in ("knowledge-engine", "rocksmith", "everward")}
    evidence = {"projects": projects, "results": RESULTS, "project_orchestrator": {"state": "GREEN", "next_gate": "product execution"}, "product_repositories_inspected": 5, "previous_no_advancement_reasons": {name: "same stall" for name in projects}}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(cli, "load_memory", lambda *_: {})
    monkeypatch.setattr(cli, "load_learning_effectiveness_store", lambda *_: {})
    monkeypatch.setattr(cli, "build_report", lambda *_: {"learning_effectiveness_rate": None})
    with pytest.raises(ValueError, match="P1 orchestration defect"):
        cli.scheduler_run_report(path)


def test_eligible_product_lane_utilization_is_three_of_three_when_all_active():
    report = build()
    utilization = report["kpis"]["eligible_product_lane_utilization"]

    assert utilization["status"] == "MEASURED"
    assert utilization["active_numerator"] == 3
    assert utilization["eligible_denominator"] == 3
    assert utilization["value"] == 1.0
    assert utilization["active_products"] == ["everward", "knowledge-engine", "rocksmith"]
    assert utilization["explained_waits"] == {}
    assert utilization["unexplained_idle"] == {}


def test_lane_utilization_separates_ci_wait_from_unexplained_idle():
    projects = base_projects()
    projects["rocksmith"] = p(
        advancement="NONE",
        advancement_actor=None,
        lane_state="CI_PENDING",
        no_advancement_reason="fresh exact-head CI is still pending",
    )
    report = build(projects)
    utilization = report["kpis"]["eligible_product_lane_utilization"]

    assert utilization["active_numerator"] == 2
    assert utilization["eligible_denominator"] == 3
    assert utilization["value"] == pytest.approx(2 / 3)
    assert utilization["explained_waits"] == {"rocksmith": "CI_PENDING"}
    assert utilization["unexplained_idle"] == {}


def test_missing_nonadvanced_lane_state_stays_unknown():
    projects = base_projects()
    projects["everward"] = p(
        advancement="NONE",
        advancement_actor=None,
        no_advancement_reason="eligible lane was not serviced",
    )
    report = build(projects)
    utilization = report["kpis"]["eligible_product_lane_utilization"]

    assert utilization["unexplained_idle"] == {"everward": "UNKNOWN"}
    assert "everward" not in utilization["explained_waits"]


def test_advancement_cannot_claim_nonactive_lane_state():
    projects = base_projects()
    projects["knowledge-engine"] = p(lane_state="CI_PENDING")
    with pytest.raises(ValueError, match="lane_state must be ACTIVE"):
        build(projects)
