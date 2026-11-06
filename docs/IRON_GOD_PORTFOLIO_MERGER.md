# Iron God autonomous portfolio merger

The owner wrote the roadmaps and the standing development authorization. Approving every pull request that delivers them
adds nothing; what must never be skipped is *verification*. `iron_god_portfolio_merger.py` makes that verification an
explicit, deterministic, auditable policy that runs without Claude, ChatGPT or a human approving each pull request.

## What it does each pass

1. **Discover** open pull requests in the authorized repositories (`PORTFOLIO_REPOS`, pinned by a test to
   `scripts/green_pr_merger.py`).
2. **Recognise roadmap development**: title `Iron God [RM-...]`, opened under the owner's account. A dependency bot's pull
   request is *escalated*, never merged. Anything else is ignored.
3. **Authorize from the graph on `main`**, never from the pull request: the identity (with `-rN`, `-tN`, `-cap` suffixes
   removed) must resolve to a slice of `engineering/capability-graph.json`, and every changed file must be inside that
   slice's `allowed_paths` and outside its `forbidden_paths`. A *planning* pull request may change only the graph.
4. **Read the diff**, not only the file list.
5. **Verify** exact-head `Canonical preflight`, every other check, `CLEAN` merge state, no unresolved review conversation,
   and the five-minute quiet window (restarts at promotion and at any new review activity on the head).
6. **Promote** a verified draft (`gh pr ready`); the window restarts, so the merge happens on a later pass.
7. **Re-check everything on a fresh read** immediately before merging, then `gh pr merge --squash --match-head-commit
   <verified head>`. No `--admin`, no `--auto`, no `--delete-branch`: GitHub's own branch protection still decides.
8. **Record** every merge, promotion, refusal and escalation.

## Decisions

| Decision | Meaning | Who acts |
|---|---|---|
| `MERGE` / `PROMOTE` | every gate passed | the merger |
| `WAIT` | a gate is still pending (checks, merge state, quiet window) | the merger, next pass |
| `REFUSE` | a gate failed deterministically (check failed, preflight missing, unresolved review, conflict, graph edit rewrote authority) | the engine's repair loop |
| `ESCALATE` | needs the owner: out-of-scope paths, protected or security-sensitive paths, dependency changes, secret patterns, deleted tests, unreadable or oversized diffs, unknown slice, non-owner author, dependency-bot PR, advisor concern | the owner |
| `IGNORE` | not roadmap development | nobody |

## Planning pull requests are checked hardest

The graph is the source of future authorization, so a planning (or delivery-recording) edit must be purely additive:
no capability, slice or evidence removed or rewritten; `allowed_paths`, `forbidden_paths`, `capability_level`, objective,
acceptance and dependencies of existing slices unchanged; states only forward; a new slice names a scope that cannot match a
protected path or the whole repository, and inherits every fence the graph already declares.

## Evidence

`<state-root>/portfolio-merger/decisions.jsonl` is append-only and fsynced: repository, PR, exact head, action, reasons,
the authorizing slice, the graph commit on `main` that authorized it, the changed files, a SHA-256 of the diff, the check
summary and, for a merge, the merge commit. Refusals and escalations are written once per (head, reasons); merges and
promotions always. `status.json` is the latest pass; `last-decision.json` is the dedupe index.

## Local model

`cycle(..., advisor=fn)` accepts an optional advisor (for example a local model that reads the diff). It runs only on a pull
request that already passed every deterministic gate and can only *downgrade* it to `ESCALATE`. It cannot authorize, widen
or override a refusal, and an advisor that fails neither blocks nor authorizes. It is off by default so it never competes
with the local builder for the single CPU model.

## Running it

* Read-only, any time: `python scripts/iron_god_portfolio_merger.py --dry-run`.
* Live: `scripts/independent_pr_controller.py` (and the always-on `green_pr_watch_loop.py`) now call this merger instead of
  the earlier, looser `green_pr_merger.cycle`. They act only while owner control is `AUTHORIZED` and re-check it before every
  `gh` command. `Azathoth-Independent-PR-Controller` is *disabled* on Azathoth today, which is why green roadmap pull
  requests were waiting; enabling a new release's controller goes through the owner-control procedure (Give Me Control, Stage
  and Promote, Take the Wheel).

## Known limits

* Authorization is structural (paths, graph, gates); whether a diff is *good* is still decided by the repository's own tests,
  preflight and review. A merge is never judged by a model alone.
* A repository with no `Canonical preflight` check (VoiceStudio today) is refused until it has exact-head preflight evidence.
* A review bot that posts an issue comment instead of review threads (for example a usage-limit notice) is not detected; the
  quiet window and unresolved-thread rule are the safeguards.
* Dependency-only and dependency-touching changes always go to the owner.

## The engine's own roadmap-progress records

After the engine merges a slice it opens `Iron God roadmap progress: record N merged slice(s)` (see
`iron_god_roadmap_progress.py`), a mechanical edit of the capability graph that marks the slice `VERIFIED_COMPLETE` and cites
the merged pull request, so completed work is not planned again. Those pull requests carry no `[RM-...]` identity, so they
are authorized by their own, stricter rule instead of being ignored:

* only `engineering/capability-graph.json` may change, opened under the owner's account;
* the edit must satisfy every graph rule above (no authority, evidence or dependency rewritten) **and** the new graph must equal
  the old one with only slice `state` and `verification` updated: no capability or slice added, no capability state or
  evidence changed, no other field touched (capabilities are completed with their own evidence, not by a progress record);
* it must complete at least one slice, and each newly completed slice must cite at least one pull request URL in the evidence
  it appended;
* every cited pull request must belong to the authorized portfolio and GitHub must report it `MERGED` right now. Completion
  evidence is therefore confirmed, never taken on trust.
