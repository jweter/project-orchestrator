"""Owner-held product trial: one real product turn that never publishes.

Run it from the installed release while you hold control (after Give Me
Control). It picks a portfolio repository (random, or ``--repository``),
fetches GitHub's main into the product lane's clone, and runs the exact
production product worker -- self-planned slice, scope envelope, change gates
and the repository's canonical preflight -- in a throwaway worktree. It never
pushes, opens a pull request or merges, and writes nothing to the product
ledger, rotation or working memory. It refuses while a Take the Wheel grant is
live or the local model is leased, and stops if Take the Wheel is clicked.

    python scripts/iron_god_product_trial.py [--repository jweter/NAME]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator import iron_god_owner_control as owner_controller  # noqa: E402
from project_orchestrator import iron_god_product_lane as pl  # noqa: E402
from project_orchestrator import iron_god_working_memory as working_memory  # noqa: E402
from project_orchestrator.iron_god_calibration import remove_tree  # noqa: E402
from project_orchestrator.iron_god_canon_lane import WORKER_LEASE_RUNTIME, default_lease_pool, load_policy  # noqa: E402
from project_orchestrator.state_io import atomic_json  # noqa: E402

TRIAL_LEASE_ID = "iron-god-product-trial"


def load_worker():
    spec = importlib.util.spec_from_file_location("iron_god_product_worker", ROOT / "scripts" / "iron_god_product_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-held Iron God product trial (never publishes)")
    parser.add_argument("--repository", help="portfolio repository, e.g. jweter/VoiceStudio (default: random)")
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state")
    parser.add_argument("--preflight-only", action="store_true",
                        help="readiness check: the repository's own preflight on unchanged main (no model)")
    parser.add_argument("--planner-model", help="Ollama model for the planning call (default: the worker model)")
    parser.add_argument("--remember", action="store_true",
                        help="record what the trial attempted in the shared working memory (default: leave it untouched)")
    args = parser.parse_args(argv)
    root = owner_controller.control_root(args.state_root)

    def held() -> bool:
        return owner_controller.owner_status(root) == owner_controller.OWNER_CONTROL_ACTIVE

    def refuse(reason: str) -> int:
        print(json.dumps({"status": "TRIAL_REFUSED", "reason": reason}))
        return 3

    if not held():
        return refuse("OWNER_MUST_HOLD_CONTROL")
    config = pl.rotation_config(load_policy(ROOT))
    if config is None:
        return refuse("PORTFOLIO_ROTATION_DISABLED")
    repos = pl.product_repositories(ROOT, config)
    lane_root = pl.ProductLane(args.state_root)
    readiness = lane_root._json(lane_root.root / "readiness.json", {"repositories": {}}).get("repositories", {})
    ready = [r for r in repos if readiness.get(r, {}).get("ready") is not False]  # as the rotation does
    repo = args.repository or random.SystemRandom().choice(ready or repos)
    if repo not in repos:
        return refuse(f"NOT_A_ROTATION_REPOSITORY: {repo}")
    pool = default_lease_pool()
    try:
        lease = pool.acquire(WORKER_LEASE_RUNTIME, TRIAL_LEASE_ID, owner_pid=os.getpid())
    except Exception as exc:
        return refuse(f"MODEL_LEASE_BUSY: {type(exc).__name__}")
    lane = pl.ProductLane(args.state_root)
    trial_id = "TRIAL-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    worktree = lane.workspaces / "trials" / trial_id
    report: dict = {"trial_id": trial_id, "repository": repo, "started_at_utc": datetime.now(UTC).isoformat()}
    clone = None
    try:
        clone, base = pl.sync_clone(lane, repo)
        roots = pl.scope_envelope(clone, base)
        avoid = pl.open_pr_paths(repo)
        branch = f"iron-god/trial/{trial_id.lower()}"
        added = pl._git(clone, "worktree", "add", "-b", branch, str(worktree), base)
        if added.returncode != 0:
            return refuse("TRIAL_WORKTREE_FAILED: " + added.stderr[-200:])
        packet = pl.build_product_packet(repo, packet_id=trial_id, base=base, worktree=worktree, branch=branch,
                                         roots=roots, avoid=avoid, memory_text=working_memory.excerpt(
                                             args.state_root, repo), project=pl.memory_project(ROOT, repo),
                                         attempted=working_memory.attempted_functions(args.state_root, repo))
        worker = load_worker()
        started = time.monotonic()

        def check() -> None:
            if not held():
                raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")

        if args.preflight_only and not pl.python_packages(clone, base, roots):
            pl.record_readiness(lane, repo, base, ready=False, reason="NO_PYTHON_PACKAGE")
            print(json.dumps({"repository": repo, "ready": False, "reason": "NO_PYTHON_PACKAGE"}, indent=2))
            return 0
        if args.preflight_only:
            result = worker.production_preflight(worktree, check, int(packet["preflight_timeout_s"]))
            ready = {"repository": repo, "base_sha": base, "ready": result["ok"], "checked_at_utc": report["started_at_utc"],
                     "environment": result.get("environment"), "wall_s": round(time.monotonic() - started, 1),
                     "tail": result["output_tail"][-1500:]}
            readiness = lane._json(lane.root / "readiness.json", {"schema_version": 1, "repositories": {}})
            readiness["repositories"][repo] = ready
            atomic_json(lane.root / "readiness.json", readiness)
            print(json.dumps({k: v for k, v in ready.items() if k != "tail"}, indent=2))
            return 0
        if args.planner_model:
            packet["planner_model"] = args.planner_model
        handback = worker.execute_product_packet(packet, authority=held)
        report.update(base_sha=base, scope=roots, avoid_path_count=len(avoid), wall_s=round(time.monotonic() - started, 1),
                      status=handback["status"], summary=handback["summary"], note=handback.get("note", ""),
                      plan=handback.get("plan"), changed_paths=handback["changed_paths"],
                      discarded_paths=handback.get("discarded_paths", []),
                      recovered_from=handback.get("recovered_from"),
                      plan_rejections=handback.get("plan_rejections", []),
                      transcript_tail=handback.get("transcript_tail", ""),
                      preflight=[{k: v for k, v in r.items() if k != "output_tail"} | {"tail": r["output_tail"][-1500:]}
                                 for r in handback["verification"]],
                      diff=pl._git(worktree, "diff", base, "HEAD").stdout[:20000])
    finally:
        pool.release(TRIAL_LEASE_ID, lease.lease_id)
        if clone is not None:
            pl._git(clone, "worktree", "remove", "--force", str(worktree))
            pl._git(clone, "branch", "-D", f"iron-god/trial/{trial_id.lower()}")
        remove_tree(worktree)
    plan = report.get("plan") or {}
    if args.remember and plan.get("function_name"):
        working_memory.record(args.state_root, repo, "TRIAL_" + str(report.get("status")), packet_id=trial_id,
                              outcome=f"TRIAL_{report.get('status')}",
                              attempt=f"{plan['function_name']} in {plan['module_path']}")
    path = lane.root / "trials" / f"{trial_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"diff", "transcript_tail"}}
                     | {"report": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
