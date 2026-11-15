# Iron God — local microtask repair advisor (Issue #710, PR #719)

**Development code only.** This is NOT wired into the installed supervisor or product-lane dispatch graph. An owner-approved PR is not an execution grant, release pin, or Take the Wheel. Do not run this development worktree against the active lane.

## Capability

The new Python module iron_god_microtask_repair_advisor.propose_current joins the existing non-dispatchable exhausted-child candidate record (#717) to the strict local repair-outline validator (#718). Under a separately authorized owner session, it asks local Ollama for one to three narrow, independently verifiable repair steps. The JSON output is an advisory record only. It cannot create backlog items, restart spent attempts, dispatch workers, merge or publish branches, or claim product acceptance.

Experimental one-shot entry point, ONLY after controlled installation into an approved pinned release:

    python scripts/iron_god_repair_advisor.py --lane <lane-path> --clone <repo-clone> --parent <exact-parent-ID> --repository <owner/repo> --head <exact-40-character-HEAD> --failed-key <child-key>

The entrypoint uses guarded_generate and local Ollama only: the existing single-model slot, memory admission, RAM pressure safeguards and 7B primary builder. It does not have a cloud-model fallback.

## Mandatory gates

- The existing owner controller must confirm a fresh, supervisor-acknowledged grant for the accepted release SHA (IRON_GOD_ACCEPTED_SHA). Mailbox messages, CLI arguments and roadmap approval cannot create grants.
- The local clone's actual Git HEAD must equal the supplied exact SHA both before and after the local model request.
- The canonical plan must be BLOCKED. Its current deterministic checkpoint must record a matching repository, parent, SHA, and one EXHAUSTED child with the same real attempt ID, outcome, attempt count and packet evidence.
- The proposal is checked against the *original parent* paths, child-owned acceptance criteria and dependencies. It cannot take over separate downstream tasks.
- Durable advisory receipts are content-addressed by the canonical plan and checkpoint evidence. Repeated requests replay an existing result without another model call; changed evidence creates a different record. An exclusive local lock prevents duplicates.
- An orphan lock after a process crash blocks reuse until authorized manual reconciliation; it is never silently stolen. Revoked owner control, changed checkpoint or missing model capacity prevents publishing a new result.

Records live in <lane>/microtask-repair-advice/*.json. They always declare advisory_only=true, requires_new_authorization=true, dispatchable=false, attempt_budget_reset=false and product_acceptance=UNVERIFIED.

## Still required

The next slice must pass an independently reviewed proposal through the EXISTING owner authorization and approved backlog before dispatching a local worktree builder. It must preserve original exhausted attempt budgets and accepted sibling work. End-to-end three verified micro-edits, interruption/resume, multi-repository fairness, zero-cloud accounting, native Product Reality and exact-head CI/release gates are not demonstrated by this PR.
