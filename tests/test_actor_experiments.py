from project_orchestrator.actor_experiments import actor_experiment_metrics


def ev(state, result=None):
    item = {"timestamp_utc": "2026-09-18T01:00:00+00:00", "state": state}
    if result:
        item["result"] = result
    return item


def test_more_green_cycles_is_measured_improvement():
    baseline = [ev("EXECUTING"), ev("IDLE_AVAILABLE", "bridge_cycle_green")]
    post = baseline + [ev("EXECUTING"), ev("IDLE_AVAILABLE", "bridge_cycle_green")]
    metrics = actor_experiment_metrics(baseline, post)
    assert metrics["green_bridge_cycles"]["outcome"] == "IMPROVED"


def test_more_cooldowns_is_degradation():
    baseline = [ev("IDLE_AVAILABLE")]
    post = baseline + [ev("COOLDOWN")]
    metrics = actor_experiment_metrics(baseline, post)
    assert metrics["cooldown_count"]["outcome"] == "DEGRADED"


def test_equal_observation_is_not_invented_improvement():
    baseline = [ev("IDLE_AVAILABLE")]
    metrics = actor_experiment_metrics(baseline, list(baseline))
    assert metrics["green_bridge_cycles"]["outcome"] == "UNCHANGED"
    assert metrics["cooldown_count"]["outcome"] == "UNCHANGED"
