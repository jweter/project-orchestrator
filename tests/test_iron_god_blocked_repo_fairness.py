"""#710 portfolio proof: two Git-backed repos advance while a third is CI-blocked.

Uses the EXISTING product lane, rotating scheduler, local worker,
disposable Git clones, native slice tests and accepted handbacks. GitHub
is faked only at the PR boundary; no cloud, installed supervisor or actual
owner controls are modified.
"""

from __future__ import annotations

import json
import os

import pytest

from project_orchestrator import iron_god_product_lane as lane_module
from tests.test_iron_god_product_lane import (
    REPOS,
    FakeGitHub,
    Runner,
    git,
    make_env,
    step,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows product lane")


def test_two_repositories_make_verified_progress_while_third_waits_for_ci(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    env = make_env(tmp_path_factory.mktemp("ig-fairness"))
    lane = lane_module.ProductLane(env.state)
    blocked = REPOS[2]
    ready_repos = set(REPOS[:2])
    # The third repo is not ready *at its exact Git HEAD*. A stale verdict
    # must not be used here; retain that HEAD until the test ends.
    blocked_base = git(env.remotes[blocked], "rev-parse", "refs/heads/main")
    lane_module.record_readiness(
        lane,
        blocked,
        blocked_base,
        ready=False,
        reason="CI_PENDING_AT_THIS_GIT_HEAD",
    )

    observed: list[dict] = []
    accepted: dict[str, str] = {}
    github = FakeGitHub(("pass",))
    runner = Runner()

    def remote_head(repository: str) -> str:
        return git(env.remotes[repository], "rev-parse", "refs/heads/main")

    assert remote_head(blocked) == blocked_base
    assert lane_module.unready_repositories(
        lane._json(lane.root / "readiness.json", {})["repositories"],
        remote_head,
    ) == {blocked}

    for _ in range(22):
        outcome = step(env, runner, github, remote_head=remote_head, seed=4)
        observed.append(outcome)
        if outcome.get("status") == "ACCEPTED_LOCAL":
            repo = outcome.get("repository")
            assert repo in ready_repos, outcome
            accepted[repo] = str(outcome["packet_id"])
        if len(accepted) == len(ready_repos):
            break

    assert set(accepted) == ready_repos, {
        "accepted": accepted,
        "outcomes": observed[-10:],
    }
    assert len(set(accepted.values())) == 2
    assert runner.spawned == 2
    assert all(
        result.get("repository") != blocked
        for result in observed
        if result.get("status") in {"DISPATCHED", "ACCEPTED_LOCAL"}
    )
    # Both source worktrees were tested/accepted by the normal product lane;
    # the blocked repository has no dispatch or claimed execution packet.
    ledger = lane.ledger()
    owned = {
        row["repository"]
        for row in ledger["packets"].values()
        if row.get("repository")
        and row.get("state")
        in {
            "DISPATCHED",
            "IMPLEMENTING",
            "VERIFIED",
            "ACCEPTED_LOCAL",
        }
    }
    assert blocked not in owned
    assert set(accepted) <= owned
    assert remote_head(blocked) == blocked_base
    readiness = json.loads((lane.root / "readiness.json").read_text(encoding="utf-8"))
    assert readiness["repositories"][blocked]["reason"] == "CI_PENDING_AT_THIS_GIT_HEAD"

    # Once that exact-head CI blocker is lifted, the same round-robin lane
    # should give the previously blocked third repository a useful turn,
    # without restarting or resetting the two already accepted slices.
    lane_module.record_readiness(
        lane,
        blocked,
        blocked_base,
        ready=True,
        reason="CI_GREEN_AT_THIS_GIT_HEAD",
    )
    third_accepted = None
    for _ in range(20):
        outcome = step(env, runner, github, remote_head=remote_head, seed=4)
        if (
            outcome.get("status") == "ACCEPTED_LOCAL"
            and outcome.get("repository") == blocked
        ):
            third_accepted = outcome
            break
    assert third_accepted is not None, (
        "unblocked repository starved after other two progressed"
    )
    assert third_accepted["repository"] == blocked
    assert runner.spawned >= 3
    assert set(accepted) == ready_repos
