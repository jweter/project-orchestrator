import pytest

from project_orchestrator.planning_dispatch import select_execution_target


def test_exclusive_priority_refuses_portfolio_fallback(tmp_path):
    (tmp_path / 'iron-god-exclusive.json').write_text('{"enabled":true}', encoding='utf-8')
    activation = {'products': {'rocksmith': {'repositories': {'jweter/rocksmith-cdlc-generator': 'abc'}}}}
    with pytest.raises(RuntimeError, match='IRON_GOD_EXCLUSIVE_NO_ENGINE_TARGET'):
        select_execution_target(tmp_path, activation, ['rocksmith'], load_execution_slice_fn=lambda *args: None, conflict_check_fn=lambda *args: False)


def test_exclusive_priority_allows_iron_god_to_reach_normal_gates(tmp_path):
    (tmp_path / 'iron-god-exclusive.json').write_text('{"enabled":true}', encoding='utf-8')
    activation = {'products': {'iron-god': {'repositories': {'jweter/project-orchestrator': 'abc'}}}}
    try:
        select_execution_target(tmp_path, activation, ['iron-god'], load_execution_slice_fn=lambda *args: {'status': 'NO_WORK'}, conflict_check_fn=lambda *args: False)
    except RuntimeError as exc:
        assert 'IRON_GOD_EXCLUSIVE_NO_ENGINE_TARGET' not in str(exc)
