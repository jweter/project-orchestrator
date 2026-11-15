"""Claude is an optional strategic accelerator, never the default slice worker."""
from project_orchestrator.iron_god_strategic_upgrades import strategic_upgrade_candidates


def upgrade(ident: str, **changes):
    row = {"id": ident, "work_class": "ENGINE_UPGRADE", "owner_approved": True,
           "route": {"builder": "CAPABLE"}, "impact": 80, "confidence": 80,
           "implementation_cost": 20, "affected_repositories": 7,
           "estimated_cloud_tokens": 1000}
    row.update(changes)
    return row


def choose(rows, **kwargs):
    return strategic_upgrade_candidates(rows, cloud_authorized=True,
                                        remaining_tokens=2000, window_open=True, **kwargs)


def test_systemic_upgrade_precedes_ordinary_slice():
    ordinary = upgrade("slice", work_class="PRODUCT_SLICE", impact=100)
    systemic = upgrade("systemic")
    narrow = upgrade("narrow", affected_repositories=1)
    assert [r["id"] for r in choose([ordinary, narrow, systemic])] == ["systemic", "narrow"]


def test_no_window_no_authorization_or_no_budget_means_no_claude():
    row = upgrade("systemic")
    assert strategic_upgrade_candidates([row], cloud_authorized=False,
                                         remaining_tokens=2000, window_open=True) == []
    assert strategic_upgrade_candidates([row], cloud_authorized=True,
                                         remaining_tokens=2000, window_open=False) == []
    assert strategic_upgrade_candidates([row], cloud_authorized=True,
                                         remaining_tokens=0, window_open=True) == []


def test_unapproved_unaffordable_and_malformed_work_fails_closed():
    rows = [upgrade("not-approved", owner_approved=False),
            upgrade("too-expensive", estimated_cloud_tokens=3000),
            upgrade("invalid", confidence="100"),
            upgrade("wrong-route", route={"builder": "LOCAL"}),
            upgrade("valid")]
    assert [r["id"] for r in choose(rows)] == ["valid"]


def test_deterministic_ranking_and_duplicate_suppression():
    rows = [upgrade("z"), upgrade("a"), upgrade("a", impact=100)]
    assert [r["id"] for r in choose(rows)] == ["a", "z"]
