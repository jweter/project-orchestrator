# GitHub-side scope gate

The portfolio merger (`iron_god_portfolio_merger.py`) decides two different things. Most of it duplicates what GitHub already
does natively (required checks, conversation resolution, up-to-date branch, merging when green). Exactly one part is something
GitHub cannot do: **is this change inside the slice it claims to deliver?** The scope gate is that part on its own, as a
GitHub Actions check, so merging can be left to GitHub and the daemon, the scheduled task and the owner-control handshake for
merging are no longer needed.

## What it judges

`scripts/roadmap_scope_gate.py` calls `iron_god_portfolio_merger.authorize` (the same function the merger uses) and nothing else:

* the changed files, both sides of a rename, are inside the slice's `allowed_paths` and outside its `forbidden_paths`, with the
  slice taken from the capability graph **on `main`**, never from the pull request;
* planning and progress edits of the graph are additive and keep every authority and evidence rule; completion evidence names
  pull requests GitHub reports `MERGED`;
* protected paths, dependency manifests, secrets, deleted tests, unreadable or oversized diffs are never in scope.

A pull request that is not roadmap development is not applicable and passes. CI state, review conversations and timing are not its
business: required checks and the repository ruleset enforce those.

## Why it cannot be talked out of its job

* `pull_request_target`: the workflow comes from the default branch, so a pull request cannot edit the gate that judges it.
* Nothing from the pull request is checked out or run. It is read through the API only.
* The policy is checked out at a **pinned commit** of the public orchestrator repository, so a policy change is a reviewed bump of one SHA.
* No third-party dependencies (the policy is standard-library Python), read-only token permissions.

## Rollout (each step is reversible and changes no existing protection until the last)

1. **Report-only** (default): add `docs/templates/roadmap-scope-gate.yml` to a repository with the pinned SHA. The check always
   succeeds; a would-be refusal appears as an annotation and in the job summary. Watch real pull requests for a while.
2. **Enforce**: add `--enforce` to the workflow's command. A refusal or an escalation now fails the check.
3. **Make it required**: add `Roadmap Scope Gate` to the repository ruleset's required checks. This only strengthens protection.
4. **Merge by GitHub**: enable auto-merge for the repository and let the engine request auto-merge (squash) on the roadmap pull
   requests it opens. Whatever the ruleset requires (preflight, CI, resolved conversations, this gate) must pass first, and the
   exact head is merged. Changing repository settings (steps 3 and 4) is the owner's decision.

An `ESCALATE` verdict (a change that widens or leaves its scope, touches protected paths, dependencies or secrets) fails the
enforced check, so such a pull request cannot auto-merge; it waits for the owner, exactly as with the merger.

## What this retires

When steps 1 to 4 are live, the merge decision no longer needs `Azathoth-Independent-PR-Controller`,
`independent_pr_controller.py`, `green_pr_watch_loop.py` or the portfolio merger daemon. The merger module stays as the policy
library the gate imports, and as a read-only `--dry-run` diagnostic.
