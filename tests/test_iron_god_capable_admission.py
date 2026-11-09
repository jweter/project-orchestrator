"""Cross-repository admission for the capable builder (issue #673, phase 2)."""
from __future__ import annotations

import itertools

from project_orchestrator import iron_god_capable_admission as adm
from project_orchestrator import iron_god_capable_builder as cap
from project_orchestrator import iron_god_product_lane as pl

NOW = 1_000_000.0
REPOS = [f"jweter/repo-{i}" for i in range(6)]


def cand(i, repo=None):
    repo = repo or REPOS[i]
    return {"name": repo.split("/")[1], "request": {"id": f"RM-{i}", "repository": repo, "route": {"builder": "CAPABLE"}}}


def row(request_id, repo, age_s, attempt=0, notes=None):
    return {"task_key": f"{request_id}#a{attempt}", "repository": repo, "started_at": NOW - age_s, "notes": notes}


def test_slots_and_next_eligible_time_follow_the_rolling_window():
    rows = [row(f"X{i}", "r", age) for i, age in enumerate([100, 200, 300, 400])]
    assert adm.remaining_slots(rows, 6, NOW) == 2
    assert adm.remaining_slots(rows, 4, NOW) == 0 and adm.remaining_slots(rows, 0, NOW) == 0
    assert adm.next_slot_at(rows, 6, NOW) is None
    assert adm.next_slot_at(rows, 4, NOW) == NOW - 400 + adm.WINDOW_S  # the oldest dispatch leaves the window first
    old = [row("O", "r", adm.WINDOW_S + 1)]
    assert adm.remaining_slots(old, 1, NOW) == 1  # exactly outside the window frees the slot


def test_window_boundary_is_exclusive():
    assert adm.remaining_slots([row("O", "r", adm.WINDOW_S - 1)], 1, NOW) == 0
    assert adm.remaining_slots([row("O", "r", adm.WINDOW_S)], 1, NOW) == 1


def test_a_burst_cannot_take_the_whole_budget_in_one_pass():
    verdict = adm.plan([cand(i) for i in range(6)], [], 4, NOW)
    assert verdict["remaining"] == 4 and verdict["pass_cap"] == 2 and len(verdict["admit"]) == 2
    assert {d["reason"] for d in verdict["deferred"]} == {"PASS_SHARE_REACHED"} and len(verdict["deferred"]) == 4
    assert adm.per_pass_cap(1) == 1 and adm.per_pass_cap(0) == 0 and adm.per_pass_cap(6) == 3


def test_nothing_is_admitted_with_no_budget_and_the_reason_names_the_next_slot():
    rows = [row(f"X{i}", REPOS[i], 3600 * (i + 1)) for i in range(4)]
    verdict = adm.plan([cand(4), cand(5)], rows, 4, NOW)
    assert verdict["admit"] == [] and verdict["remaining"] == 0
    assert {d["reason"] for d in verdict["deferred"]} == {"DAILY_BUDGET_EXHAUSTED"}
    assert verdict["next_slot_at"] == NOW - 4 * 3600 + adm.WINDOW_S


def test_the_choice_does_not_depend_on_the_order_repositories_were_visited():
    rows = [row("old", REPOS[0], 500)]
    candidates = [cand(i) for i in range(6)]
    chosen = {tuple(c["request"]["id"] for c in adm.plan(list(order), rows, 4, NOW)["admit"])
              for order in itertools.islice(itertools.permutations(candidates), 120)}
    assert len(chosen) == 1


def test_repositories_with_fewer_recent_slots_go_first_and_retries_after_no_pr_go_last():
    rows = [row("old-a", REPOS[0], 100), row("old-b", REPOS[0], 200),
            row("RM-1", REPOS[1], 300, notes=adm.NO_PR_NOTE)]
    verdict = adm.plan([cand(0), cand(1), cand(2)], rows, 20, NOW)
    order = [c["request"]["id"] for c in verdict["admit"]]
    assert order[0] == "RM-2"  # fresh work in a repository with no recent slot beats a no-PR retry and a busy repository
    full = adm.sorted_candidates([cand(0), cand(1), cand(2)], rows, NOW)
    assert [c["request"]["id"] for c in full] == ["RM-2", "RM-0", "RM-1"]


def test_one_slot_per_repository_per_pass():
    verdict = adm.plan([cand(0), cand(1, REPOS[0])], [], 6, NOW)
    assert len(verdict["admit"]) == 1
    assert [d["reason"] for d in verdict["deferred"]] == ["ONE_PER_REPOSITORY_PER_PASS"]


def test_six_limit_admits_six_over_passes_and_never_a_seventh():
    rows: list[dict] = []
    started = []
    for step in range(6):
        verdict = adm.plan([cand(i) for i in range(6) if f"RM-{i}" not in started], rows, 6, NOW + step)
        for c in verdict["admit"]:
            started.append(c["request"]["id"])
            rows.append(row(c["request"]["id"], c["request"]["repository"], 0))
    assert sorted(started) == [f"RM-{i}" for i in range(6)]
    assert adm.plan([cand(6, "jweter/repo-6")], rows, 6, NOW + 7)["admit"] == []


# --- wiring in the product lane -------------------------------------------------------------------------------

def test_the_lane_admits_a_fair_share_and_logs_why_the_rest_wait(tmp_path, monkeypatch):
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(tmp_path / 'state')
    fired = []

    def fake_dispatch(request, **_kw):
        fired.append(request["id"])
        return {"dispatched": True, "reason": "DISPATCHED"}

    monkeypatch.setattr(cap, "dispatch_request", fake_dispatch)
    monkeypatch.setattr(cap, "decide", lambda *_a, **_k: {"dispatch": True, "reason": "WITHIN_BUDGET"})
    queue = []
    for i in (3, 1, 0, 2):  # arbitrary visiting order
        pl.hand_to_capable_builder(lane, REPOS[0], {"capable_requests": [cand(i)["request"]]},
                                   {"enabled": True, "daily_limit": 4}, queue=queue)
    assert fired == [] and len(queue) == 4  # collecting spends nothing
    pl.admit_capable_requests(lane, queue, {"enabled": True, "daily_limit": 4})
    assert fired == ["RM-0", "RM-1"]  # sorted, and only half of the four free slots
    held = [e for e in refill_mod.load_store(lane.root)["ledger"] if e["kind"] == "CAPABLE_NOT_DISPATCHED"]
    assert len(held) == 2 and all(e["detail"]["reason"] in {"PASS_SHARE_REACHED", "ONE_PER_REPOSITORY_PER_PASS"}
                                  for e in held)
    assert all(e["detail"]["remaining"] == 4 and e["detail"]["pass_cap"] == 2 for e in held)


def test_a_disabled_builder_still_records_each_exact_reason_and_spends_nothing(tmp_path, monkeypatch):
    lane = pl.ProductLane(tmp_path / 'state')
    fired = []
    monkeypatch.setattr(cap, "dispatch_request", lambda r, **k: fired.append(r["id"]) or {"dispatched": False,
                                                                                          "reason": "CAPABLE_BUILDER_DISABLED"})
    queue = [cand(0), cand(1)]
    pl.admit_capable_requests(lane, queue, {"enabled": False, "daily_limit": 6})
    assert fired == ["RM-0", "RM-1"]  # dispatch_request itself refuses; admission never bypasses it


def test_a_refused_top_candidate_does_not_use_up_a_pass_slot_for_an_eligible_one(tmp_path, monkeypatch):
    from project_orchestrator import iron_god_roadmap_refill as refill_mod

    lane = pl.ProductLane(tmp_path / "state")
    fired = []
    monkeypatch.setattr(cap, "dispatch_request", lambda r, **_k: fired.append(r["id"]) or {"dispatched": True})
    refused = {"RM-0": "ATTEMPTS_EXHAUSTED", "RM-1": "REQUEST_ALREADY_DELIVERED_OR_PENDING"}
    monkeypatch.setattr(cap, "decide", lambda r, **_k: {"dispatch": r["id"] not in refused,
                                                         "reason": refused.get(r["id"], "WITHIN_BUDGET")})
    pl.admit_capable_requests(lane, [cand(i) for i in range(4)], {"enabled": True, "daily_limit": 2})
    assert fired == ["RM-2"]  # cap is 1 of 2; the two top-ranked refused requests no longer take it
    reasons = {e["id"] if "id" in e else e.get("subject"): e["detail"]["reason"]
               for e in refill_mod.load_store(lane.root)["ledger"] if e["kind"] == "CAPABLE_NOT_DISPATCHED"}
    assert "ATTEMPTS_EXHAUSTED" in reasons.values() and "REQUEST_ALREADY_DELIVERED_OR_PENDING" in reasons.values()
    assert "PASS_SHARE_REACHED" in reasons.values()  # RM-3 waits for the share, not because of a refused neighbour
