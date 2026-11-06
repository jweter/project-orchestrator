"""GitHub-side scope gate for roadmap pull requests.

Run from a ``pull_request_target`` workflow (so the workflow definition comes from ``main`` and a pull request cannot
weaken the gate that judges it). It never checks out the pull request: it reads the pull request, the capability graph
on ``main`` and, for the graph file, the head version, through the GitHub API only, then applies the same authorization
policy the portfolio merger uses (``iron_god_portfolio_merger.authorize``): the changed files must be inside the slice's
``allowed_paths`` and outside its ``forbidden_paths``; graph edits must be additive; completion evidence must name
pull requests GitHub reports MERGED; protected paths, dependency manifests, secrets and unreadable diffs go to the owner.

CI state, review conversations and timing are deliberately not judged here: GitHub's own required checks and
conversation-resolution rule do that. Report-only unless ``--enforce``; a pull request that is not roadmap development
is not applicable and passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for _path in (str(_root), str(_root / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_portfolio_merger as pm  # noqa: E402

PASSING = {pm.AUTHORIZED, pm.IGNORE}


def pull_number(explicit: int | None) -> int:
    if explicit:
        return explicit
    event = os.environ.get("GITHUB_EVENT_PATH")
    if event and Path(event).is_file():
        payload = json.loads(Path(event).read_text(encoding="utf-8"))
        number = (payload.get("pull_request") or {}).get("number") or payload.get("number")
        if isinstance(number, int):
            return number
    raise SystemExit("pull request number unknown: pass --pr or run from a pull_request event")


def judge(port: pm.Port, repository: str, number: int) -> tuple[pm.Verdict, dict]:
    summary = port.pull_summary(repository, number)
    facts = pm.gather(port, repository, summary, scope_only=True)
    return pm.scope_verdict(facts), facts


def render(verdict: pm.Verdict, facts: dict, enforce: bool) -> str:
    lines = [f"Roadmap scope gate: {verdict.action} ({'enforcing' if enforce else 'report-only'})"]
    lines += [f"- {reason}" for reason in verdict.reasons]
    grant = verdict.authorization or {}
    if grant.get("slice"):
        lines.append(f"- authorizing slice: {grant['slice']} (graph on main {str(grant.get('graph_main_sha'))[:10]})")
    lines.append(f"- changed files: {len(facts.get('files') or [])}")
    if verdict.action == pm.ESCALATE:
        lines.append("- needs the owner: this change widens, leaves or is not covered by its authorized scope")
    return "\n".join(lines)


def main(argv: list[str] | None = None, port: pm.Port | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    if not args.repo:
        raise SystemExit("repository unknown: pass --repo or set GITHUB_REPOSITORY")
    port = port or pm.GhPort()
    verdict, facts = judge(port, args.repo, pull_number(args.pr))
    text = render(verdict, facts, args.enforce)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if verdict.action in PASSING:
        return 0
    if args.enforce:
        print(f"::error::{verdict.action}: {', '.join(verdict.reasons)[:400]}")
        return 1
    print(f"::warning::{verdict.action} (report-only): {', '.join(verdict.reasons)[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
