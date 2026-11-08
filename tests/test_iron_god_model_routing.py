from __future__ import annotations

import pytest

from project_orchestrator import iron_god_calibration as cal
from project_orchestrator import iron_god_model_routing as routing

HW = {"logical_cpus": 20, "processor": "test", "ram_gib": 16}


def profile(model: str, level: int, **over) -> dict:
    return {"schema_version": 1, "ladder_version": cal.LADDER_VERSION, "model": f"ollama/{model}",
            "max_passing_level": level, "hardware": HW, **over}


# What the engine measured on Azathoth on 2026-11-07 (see C:\ev\calib): 7B level 3, 4B level 2, 3B level 1.
MEASURED = {f"ollama/{m}": profile(m, level) for m, level in
            (("qwen2.5-coder:7b", 3), ("qwen3.5:4b", 2), ("qwen2.5-coder:3b", 1), ("qwen3.5:2b", 1))}


def pick(level, free, profiles=MEASURED):
    return routing.route(level, profiles, free, hardware=HW)


def test_the_thresholds_are_the_measured_ones() -> None:
    assert routing.required_gib("qwen2.5-coder:7b") == pytest.approx(5.3)   # measured 4.7 GiB load cost + 0.6 GiB cancel floor (2026-11-08)
    assert routing.required_gib("qwen3.5:4b") == pytest.approx(6.1)
    assert routing.required_gib("qwen2.5-coder:3b") == pytest.approx(3.9)


@pytest.mark.parametrize("level,free,expected", [
    (3, 8.0, "qwen2.5-coder:7b"), (2, 8.0, "qwen2.5-coder:7b"), (1, 8.0, "qwen2.5-coder:7b"),   # plenty of memory: the primary builder
    (2, 5.5, "qwen2.5-coder:7b"), (3, 5.4, "qwen2.5-coder:7b"),                                   # the measured threshold admits the 7B at 5.3
    (3, 2.0, "qwen2.5-coder:7b"), (1, 1.5, "qwen2.5-coder:7b"),                                   # tight memory: still the 7B, never a downgrade
])
def test_the_primary_builder_is_preferred_and_smaller_models_take_only_work_they_have_demonstrated(level, free, expected) -> None:
    decision = pick(level, free)
    assert decision.model == expected, decision.reason
    assert decision.level == level and expected in decision.admitted


@pytest.mark.parametrize("level,free", [(3, 0.5), (2, 0.9), (1, 0.5)])
def test_a_task_is_never_downgraded_below_the_level_a_model_has_demonstrated(level, free) -> None:
    decision = pick(level, free)
    assert decision.model is None                                    # wait, do not hand it to a model that has not shown it can do it
    assert decision.reason.startswith("WAITING_FOR_MEMORY") and "GiB available" in decision.reason
    for model in decision.qualified:                                 # every model considered covers the level
        assert int(MEASURED[f"ollama/{model}"]["max_passing_level"]) >= level


def test_with_only_the_3b_profile_hard_tasks_wait_for_calibration_not_for_memory() -> None:
    only3 = {"ollama/qwen2.5-coder:3b": profile("qwen2.5-coder:3b", 1)}
    decision = pick(2, 20.0, only3)
    assert decision.model is None and decision.reason.startswith("NO_MODEL_QUALIFIES_FOR_LEVEL_2")
    assert "SLICE_EXCEEDS_CALIBRATED_CAPABILITY" in decision.reason


def test_a_profile_for_another_machine_or_ladder_is_not_evidence() -> None:
    wrong_hw = {"ollama/qwen2.5-coder:7b": profile("qwen2.5-coder:7b", 3, hardware={**HW, "ram_gib": 64})}
    assert pick(1, 20.0, wrong_hw).reason.startswith("NO_MODEL_QUALIFIES_FOR_LEVEL_1")
    old_ladder = {"ollama/qwen2.5-coder:7b": profile("qwen2.5-coder:7b", 3, ladder_version=cal.LADDER_VERSION + 1)}
    assert pick(1, 20.0, old_ladder).model is None
    assert pick(1, 20.0, {}).model is None


def test_unknown_memory_and_undeclared_level_never_route() -> None:
    assert routing.route(1, MEASURED, None, hardware=HW).reason == "AVAILABLE_MEMORY_UNKNOWN"
    for bad in (None, 0, "2", 2.5):
        assert routing.route(bad, MEASURED, 20.0, hardware=HW).reason == "SLICE_CAPABILITY_LEVEL_UNDECLARED"


def test_a_model_outside_the_ladder_is_never_chosen() -> None:
    odd = {**MEASURED, "ollama/mystery:13b": profile("mystery:13b", 5)}
    assert pick(5, 40.0, odd).model is None


def test_tight_memory_does_not_push_work_to_a_smaller_model() -> None:
    """Owner decision 2026-11-08: the 7B is started whenever it can load at all, so there is no constrained fallback."""
    for free in (1.0, 2.0, 3.0, 4.5, 5.2):
        decision = pick(1, free)
        assert decision.model == "qwen2.5-coder:7b" and decision.reason == "PRIMARY_BUILDER"


def test_the_highest_level_available_now_follows_memory() -> None:
    levels = {free: routing.max_level_now(MEASURED, free, hardware=HW) for free in (0.5, 1.5, 3.0, 5.4)}
    assert levels == {0.5: 0, 1.5: 3, 3.0: 3, 5.4: 3}
    assert routing.max_level_now(MEASURED, None, hardware=HW) == 0


def test_best_profile_answers_can_any_local_model_ever_do_this() -> None:
    assert routing.best_profile(MEASURED, hardware=HW)["model"] == "ollama/qwen2.5-coder:7b"
    assert routing.best_profile({"ollama/qwen2.5-coder:3b": profile("qwen2.5-coder:3b", 1)}, hardware=HW)["max_passing_level"] == 1
    assert routing.best_profile({}, hardware=HW) is None


def test_per_model_profiles_are_stored_and_read_back_beside_the_legacy_one(tmp_path) -> None:
    for model, level in (("ollama/qwen2.5-coder:7b", 3), ("ollama/qwen3.5:4b", 2)):
        path = cal.profile_path(tmp_path, model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps(profile(model.removeprefix("ollama/"), level)), encoding="utf-8")
    cal.profile_path(tmp_path).write_text(__import__("json").dumps(profile("qwen2.5-coder:3b", 1)), encoding="utf-8")
    loaded = cal.load_profiles(tmp_path)
    assert {m: p["max_passing_level"] for m, p in loaded.items()} == {
        "ollama/qwen2.5-coder:7b": 3, "ollama/qwen3.5:4b": 2, "ollama/qwen2.5-coder:3b": 1}
    assert cal.profile_path(tmp_path, "ollama/qwen3.5:4b").name == "qwen3.5_4b.json"
