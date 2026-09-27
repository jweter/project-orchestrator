from project_orchestrator.portfolio_kpis import build_portfolio_kpi_scorecard
from project_orchestrator.scheduler_report import build_scheduler_executive_report
from project_orchestrator.timeseries import ALLOWED_METRIC_KEYS, METRIC_UNITS


def _unknown_metrics():
    return {key: {"status": "UNKNOWN"} for key in ALLOWED_METRIC_KEYS}


def _measured(key, value, ref):
    return {
        "status": "MEASURED",
        "value": value,
        "unit": METRIC_UNITS[key],
        "sample_count": 1,
        "evidence_refs": [ref],
    }


def _window(project, repository, index):
    metrics = _unknown_metrics()
    ref = f"https://github.com/example/pull/{index}"
    metrics["merge_cycle_seconds"] = _measured("merge_cycle_seconds", 3600 + index, ref)
    metrics["ci_failure_count"] = _measured("ci_failure_count", index, ref)
    return {
        "project": project,
        "repository": repository,
        "window_start": f"2026-09-{index:02d}T00:00:00Z",
        "window_end": f"2026-09-{index:02d}T01:00:00Z",
        "metrics": metrics,
    }


def test_missing_projects_and_repositories_remain_visible():
    scorecard = build_portfolio_kpi_scorecard(
        {"schema_version": 1, "windows": []},
        eligible_projects=["knowledge-engine", "rocksmith", "everward", "voicestudio"],
    )

    assert scorecard["delivery_progress"]["by_project"]["knowledge-engine"]["status"] == "INSUFFICIENT_DATA"
    assert scorecard["delivery_progress"]["by_project"]["rocksmith"]["metrics"]["merge_cycle_seconds"]["status"] == "UNKNOWN"
    assert len(scorecard["delivery_progress"]["by_repository"]) == 6
    assert scorecard["evidence_coverage"]["missing_product_projects"] == [
        "everward",
        "knowledge-engine",
        "rocksmith",
        "voicestudio",
    ]
    assert len(scorecard["evidence_coverage"]["missing_product_repositories"]) == 6
    assert {row["project_id"] for row in scorecard["bottlenecks"]["projects"]} == {
        "knowledge-engine",
        "rocksmith",
        "everward",
        "voicestudio",
    }
    assert scorecard["bottlenecks"]["selection_state"] == "INSUFFICIENT_DATA"


def test_orchestrator_only_windows_do_not_make_product_portfolio_look_measured():
    scorecard = build_portfolio_kpi_scorecard(
        {
            "schema_version": 1,
            "windows": [_window("portfolio", "jweter/project-orchestrator", 1)],
        },
        eligible_projects=["knowledge-engine", "rocksmith", "everward", "voicestudio"],
    )

    assert scorecard["delivery_progress"]["portfolio"]["status"] == "INSUFFICIENT_DATA"
    assert scorecard["evidence_coverage"]["product_windows"] == 0
    assert scorecard["evidence_coverage"]["source_store_windows"] == 1
    assert scorecard["evidence_coverage"]["orchestrator_only_windows_excluded"] == 1
    assert scorecard["evidence_coverage"]["missing_product_projects"] == [
        "everward",
        "knowledge-engine",
        "rocksmith",
        "voicestudio",
    ]


def test_measured_project_can_be_selected_as_bottleneck_only_after_evidence_floor():
    store = {
        "schema_version": 1,
        "windows": [
            _window("rocksmith", "jweter/rocksmith-cdlc-generator", 1),
            _window("rocksmith", "jweter/rocksmith-cdlc-generator", 2),
            _window("rocksmith", "jweter/rocksmith-cdlc-generator", 3),
        ],
    }
    scorecard = build_portfolio_kpi_scorecard(store, eligible_projects=["rocksmith"])

    assert scorecard["delivery_progress"]["by_project"]["rocksmith"]["window_count"] == 3
    assert scorecard["delivery_progress"]["by_repository"]["jweter/rocksmith-cdlc-generator"]["metrics"]["merge_cycle_seconds"]["status"] == "MEASURED"
    assert scorecard["bottlenecks"]["selection_state"] == "READY"
    assert scorecard["bottlenecks"]["selected_project"] == "rocksmith"


def test_rework_quality_uses_only_explicit_records_and_keeps_targets_visible():
    scorecard = build_portfolio_kpi_scorecard(
        {"schema_version": 1, "windows": []},
        rework_records=[
            {
                "preflight_attempts": 1,
                "merge_race": False,
                "merged": True,
                "post_merge_correctness_hotfix": False,
                "implementation_started_at": "2026-09-15T10:00:00Z",
                "merged_at": "2026-09-15T10:30:00Z",
            }
        ],
        eligible_projects=[],
    )

    assert scorecard["rework_quality"]["first_pass_preflight_rate"]["value"] == 1.0
    assert scorecard["rework_quality"]["first_pass_preflight_rate"]["target"] == ">=0.90"
    assert scorecard["rework_quality"]["merge_race_count"]["value"] == 0
    assert scorecard["rework_quality"]["post_merge_correctness_hotfix_rate"]["value"] == 0.0
    assert scorecard["rework_quality"]["median_implementation_to_merge_seconds"]["value"] == 1800.0


def test_scheduler_report_embeds_scorecard_and_extracts_current_run_rework_evidence():
    projects = {
        name: {
            "eligibility": "ELIGIBLE",
            "advancement": "SUBSTANTIAL_SLICE_ADVANCED",
            "advancement_actor": "ORCHESTRATOR",
            "promotion_state": "IMPLEMENTING",
            "implementation_verification": "targeted tests passed",
            "active_ownership": "orchestrator",
            "next_gate": "preflight",
            "product_reality_debt": [],
        }
        for name in ("knowledge-engine", "rocksmith", "everward", "voicestudio")
    }
    projects["knowledge-engine"].update(
        {
            "preflight_attempts": 1,
            "merge_race": False,
        }
    )

    report = build_scheduler_executive_report(
        project_evidence=projects,
        results={
            "MERGED": [],
            "FIXED": [],
            "NEW_PRS": [],
            "WAITING_ON_PREFLIGHT": [],
            "WAITING_ON_CI": [],
            "BLOCKED": [],
            "NEEDS_JEREMY": [],
        },
        project_orchestrator={"state": "GREEN", "next_gate": "product execution"},
        product_repositories_inspected=6,
        timeseries_store={"schema_version": 1, "windows": []},
        generated_at="2026-09-15T14:00:00Z",
    )

    assert report["schema_version"] == 2
    assert "portfolio_kpis" in report
    assert report["portfolio_kpis"]["rework_quality"]["first_pass_preflight_rate"]["value"] == 1.0
    assert report["portfolio_kpis"]["evidence_coverage"]["product_windows"] == 0
