"""Owner 2026-11-08: the laptop's memory is never the slice's or the model's fault.

A run that never got memory, or that the memory brake cancelled, is a WAIT: it never spends an attempt in the product
build (backlog), the local drafter or the local decomposer. And after a real memory-pressure cancel the 7B waits for its
comfortable level for a while, so a busy laptop never sees a load-cancel-load loop; the lane reports a memory wait
meanwhile, which the standing Dev Mode rule acts on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_local_drafter as drafter
from project_orchestrator import iron_god_local_model as lm
from project_orchestrator import iron_god_model_routing as routing
from project_orchestrator import iron_god_task_decomposer as td
from tests.test_iron_god_local_drafter import Spawner as DraftSpawner  # noqa: F401
from tests.test_iron_god_local_drafter import request_of, world  # noqa: F401  (world is a fixture)
from tests.test_iron_god_product_lane import env  # noqa: F401  (a fixture)
from tests.test_iron_god_task_decomposer import PARENT, Spawner, clone, head_of  # noqa: F401  (clone is a fixture)

CANCELLED = "MODEL_UNAVAILABLE: ModelCancelled: MEMORY_PRESSURE: available stayed under 0.6 GiB (lowest 0.41 GiB)"
REFUSED = "MODEL_UNAVAILABLE: ModelNotAdmitted: MEMORY: 0.5 GiB available < 1.0 GiB required"


def test_every_shape_of_a_memory_wait_is_recognised_and_nothing_else_is() -> None:
    for text in (CANCELLED, REFUSED, "ENVIRONMENT_FAILURE: AGENT_FAILED: WAITING_FOR_MEMORY: cancelled on MEMORY_PRESSURE",
                 "MODEL_SLOT_BUSY: another local model run holds the single slot"):
        assert lm.is_memory_wait(text), text
    for text in ("FAIL: SLICE_TESTS_FAILED", "PLAN_REJECTED: t1: contradictory expected values", "", None,
                 "ENVIRONMENT_FAILURE: AGENT_FAILED: AIDER_EXIT_1"):
        assert not lm.is_memory_wait(text), text


def test_a_build_the_memory_brake_cancelled_does_not_spend_an_attempt(tmp_path: Path) -> None:
    lane = tmp_path / "lane"
    lane.mkdir()
    out = "ENVIRONMENT_FAILURE: AGENT_FAILED: WAITING_FOR_MEMORY: cancelled on MEMORY_PRESSURE: available stayed under 0.6 GiB"
    for n in range(3):
        backlog.record_item(lane, "ITEM-1", packet_id=f"P{n}", outcome=out)
    assert backlog.load_state(lane)["items"]["ITEM-1"]["attempts"] == 0          # three cancels, no attempt spent
    backlog.record_item(lane, "ITEM-1", packet_id="P9", outcome="ENVIRONMENT_FAILURE: AGENT_FAILED: AIDER_EXIT_1")
    assert backlog.load_state(lane)["items"]["ITEM-1"]["attempts"] == 1          # a real failure still counts


def test_a_cancelled_or_refused_decomposition_is_a_wait_and_leaves_no_spent_job(tmp_path: Path, clone: Path) -> None:
    for reason in (CANCELLED, REFUSED):
        lane = tmp_path / reason[:20].replace(":", "_").replace(" ", "_")
        td.advance(lane, [PARENT], clone, head_of(clone), spawn=Spawner({"ok": False, "reason": reason}))
        (outcome,) = td.harvest(lane)
        assert outcome["status"] == "WAITING_FOR_MEMORY", outcome
        assert td._jobs(lane) == []                                              # nothing recorded as spent


def test_a_cancelled_draft_is_a_wait_and_leaves_no_spent_job(world: dict) -> None:
    request = request_of(world)
    spawner = DraftSpawner()
    drafter.advance(world["lane"], [request], world["clone"], world["head"], spawn=spawner)
    result_file = spawner.launched[0][3]
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps({"ok": False, "reason": CANCELLED}), encoding="utf-8")
    (outcome,) = drafter.harvest(world["lane"])
    assert outcome["status"] == "WAITING_FOR_MEMORY", outcome
    assert [r for r in drafter._jobs(world["lane"]) if r.get("id") == request["id"]] == []


class Door:
    def __init__(self) -> None:
        self.unloaded: list[str] = []

    def loaded(self):
        return []

    def unload(self, name, *_a, **_k):
        self.unloaded.append(name)
        return True

    def generate(self, *_a, should_abort=None, **_k):
        for _ in range(5):                                                        # the brake trips during the run
            reason = should_abort()
            if reason:
                raise lm.ModelCancelled(reason)
        return {"response": "{}"}


def test_after_a_pressure_cancel_the_7b_waits_for_its_comfortable_level_then_starts_low_again(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    readings = iter([3.0, 3.0, 0.4, 0.3, 0.3, 0.3])
    with pytest.raises(lm.ModelCancelled, match="MEMORY_PRESSURE"):
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=0.0, client=Door(), directory=slot,
                            probes={"available": lambda: next(readings, 0.3)})
    assert lm.recent_pressure(slot)                                               # the cancel was stamped

    with pytest.raises(lm.ModelNotAdmitted, match="5.3 GiB required"):           # 3.0 GiB is not enough right after
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=0.0, client=Door(), directory=slot,
                            probes={"available": lambda: 3.0})
    assert routing.start_gib("qwen2.5-coder:7b", after_pressure=True) == pytest.approx(5.3)

    stamp = json.loads(lm.pressure_file(slot).read_text(encoding="utf-8"))
    assert not lm.recent_pressure(slot, now=stamp["at"] + lm.PRESSURE_COOLDOWN_S + 1)   # the cooldown ends
    assert routing.start_gib("qwen2.5-coder:7b") == pytest.approx(1.0)


def test_a_bare_gate_in_a_test_or_elsewhere_never_stamps_the_machine(tmp_path: Path) -> None:
    gate = lm.Gate(model_gib=4.7, reserve_gib=0.6, available=lambda: 0.1)
    reasons = [gate.abort_reason() for _ in range(3)]
    assert any(r and r.startswith("MEMORY_PRESSURE") for r in reasons)            # the brake does trip
    assert not lm.recent_pressure()                                               # but with no door there is no stamp


def test_the_lane_reports_a_memory_wait_instead_of_starting_into_a_known_squeeze(monkeypatch) -> None:
    from tests.test_iron_god_product_lane import FULL, measured

    profiles = measured(**FULL)
    tight = routing.route(2, profiles, 3.0, after_pressure=True)
    assert tight.model is None and tight.reason.startswith("WAITING_FOR_MEMORY")  # Dev Mode's cue
    assert tight.required_gib == pytest.approx(5.3)
    assert routing.route(2, profiles, 3.0).model == "qwen2.5-coder:7b"            # without a recent cancel it starts
    assert routing.max_level_now(profiles, 3.0, after_pressure=True) == 0


def test_lane_waits_after_cancel(env) -> None:  # short name: Windows path length (the lane nests branch names)
    """The lane itself passes the recent-cancel evidence to routing: at 3.0 GiB it reports a memory wait, which the
    standing Dev Mode rule acts on, instead of starting the 7B straight back into the squeeze."""
    import random

    from project_orchestrator import iron_god_product_lane as pl
    from tests.test_iron_god_product_lane import FULL, PERMIT, REPOS, FakeGitHub, RoutedRunner, direct_item, directed_env, measured

    directed_env(env, [direct_item("L2-1", REPOS[0])])
    runner = RoutedRunner()
    result = pl.advance_product_lane(
        env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=True, decide=lambda: PERMIT,
        spawn=runner.spawn, alive=lambda _w: False, stop=lambda _w: None, resources=lambda: {"admitted": True},
        lease_pool=env.pool, github=FakeGitHub(), sync=env.sync, owned_paths=lambda r: [], gates=lambda *_a: None,
        memory_root=env.memory, rng=random.Random(7), policy=env.policy, profiles=lambda: measured(**FULL),
        free_gib=lambda: 3.0, memory_pressure=lambda: True)
    assert result["status"] == "WAITING_FOR_MEMORY" and runner.spawned == 0
    assert result.get("work_waiting") is True                                    # the rule is told: Dev Mode frees memory
