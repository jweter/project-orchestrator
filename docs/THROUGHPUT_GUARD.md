# Throughput Guard

The Throughput Guard removes recurring engineering friction between implementation and verified product capability without weakening any existing repository gate.

## Sequence

For governed work, the scheduler uses this order:

1. inspect live PRs, issues, branches, roadmaps, and repository control-plane authority;
2. run the **Branch Hygiene Guard** against non-default branches and fail closed on uncertain disposition;
3. retrieve relevant verified learning and pre-change risk evidence;
4. acquire an **ephemeral active-work lease** for the exact repository/branch/head while `IMPLEMENTING` or `REPAIRING`;
5. implement the bounded change;
6. run **FAST_GATE** using only commands explicitly authorized by the target repository;
7. run the repository's canonical full exact-head preflight;
8. open a Ready-for-Review PR only after the normal preflight gate is satisfied;
9. wait for fresh PR CI and review evidence;
10. release the active-work lease;
11. central merge authority revalidates exact head, required CI, reviews/comments, mergeability, security/licensing/provenance/privacy, and Product Reality policy;
12. merge only if GREEN;
13. update the related GitHub issue execution ledger and record rework evidence;
14. re-check branch disposition after merge so positively verified disposable branches do not accumulate.

FAST_GATE is a cheap earlier feedback stage. It never replaces canonical preflight or fresh PR CI.

## Branch Hygiene Guard

Branch hygiene is now a first-class portfolio control across the six product repositories plus `project-orchestrator`. The existing `project-orchestrator branch-lifecycle` classifier is the authoritative evidence-safe classifier for non-default branches.

Each branch is classified from live evidence into one of these operational groups:

- **ACTIVE / PR_OWNED / ACTIVE_UNMERGED** — preserve and continue the existing lane;
- **MERGED_DISPOSABLE / SUPERSEDED_OR_DISPOSABLE** — eligible for deletion only when disposition is proven and no unique unmerged work remains;
- **STALE_VALUABLE** — preserve and place into recovery/modernization review when authorized;
- **RELEASE_OR_HOTFIX_REQUIRED / PRESERVED** — explicitly protected;
- **UNCERTAIN** — fail closed; investigate before any destructive action.

Deletion is never based on branch age alone. A branch may be deleted only after live evidence proves all applicable safety conditions: it is not the default branch, no open PR or active-work lease owns it, it is not needed for release/hotfix work, merge or supersession disposition is proven, and the branch has no unique unmerged commits (`ahead_by == 0` or equivalent verified ancestry evidence). A merged PR by itself is not sufficient because commits may have been pushed to the branch after merge.

The daily bottleneck review must report branch hygiene beside the engineering effectiveness score when evidence is available, including total non-default branches, active/PR-owned counts, delete-safe count, stale-review count, recovery/integration count, protected count, branches deleted during the run, residual unresolved count, oldest unresolved branch when known, and unique-work-loss risk. Missing branch evidence is `UNKNOWN`, never interpreted as clean.

Automatic cleanup is restricted to branches positively classified delete-safe from complete live evidence. Unique, ambiguous, protected, actively owned, or PR-owned branches always fail closed.

## FAST_GATE authority

Preferred repository policy:

```json
{
  "fast_gate": {
    "enabled": true,
    "commands": [
      {
        "id": "python-quality",
        "argv": ["python", "-m", "compileall", "-q", "src", "tests"],
        "extensions": [".py"]
      }
    ]
  }
}
```

For compatibility with the existing portfolio, `preflight.checks[]` entries already marked `"fast": true` are also explicit repository authority. If neither policy form exists, FAST_GATE returns `NOT_AUTHORIZED` and runs nothing.

Example:

```text
project-orchestrator throughput fast-gate-plan \
  --repository jweter/rocksmith-cdlc-generator \
  --control-plane engineering/control-plane.json \
  --changed-path src/rocksmith_cdlc_generator/song_workspace.py
```

Use `--execute --repo-root <checkout>` only in an executable local checkout. The tool never commits, pushes, opens PRs, or merges.

## Active-work leases

Leases exist only for the current orchestration execution and are never persisted as a competing ownership database.

An active lease contains repository, branch, exact head SHA, actor, state (`IMPLEMENTING` or `REPAIRING`), acquisition time, and optional changed paths. Central merge is blocked when:

- an active lease matches the exact PR branch/head; or
- an active lease refers to the same branch at a different head, because the lane is stale and must be revalidated.

A released lease does **not** make a PR GREEN. It only proves that the active writer is finished. All normal merge gates still apply.

## Promotion failure classification

The scheduler distinguishes these classes:

- `CODE_FAILURE` — deterministic compiler/runtime code signature;
- `TEST_FAILURE` — behavioral regression or failed assertion/test;
- `QUALITY_FAILURE` — formatting, import ordering, lint-safe-fix, or type-check quality gate;
- `REVIEW_FAILURE` — changes requested or unresolved blocking review/comment evidence;
- `PLATFORM_GATE` — workflow/platform gate such as `action_required` with zero jobs;
- `PRODUCT_REALITY_PENDING` — explicit human/local/packaged/Unreal/game acceptance debt;
- `POLICY_FAILURE` — explicit security, governance, provenance, licensing, or related policy failure;
- `ENVIRONMENT_FAILURE` — job/runtime environment limitation after execution begins;
- `UNKNOWN` — insufficient deterministic evidence; always fail closed.

The classifier routes scheduler behavior. `project_orchestrator.repair` remains the lower-level deterministic repair engine for repository-authorized FORMAT/IMPORT_SORT/LINT_SAFE_FIX corrections.

## Issue execution ledger

When work is linked to an issue, meaningful state changes are posted to that issue with:

- promotion state;
- actor;
- branch;
- exact head SHA;
- verification evidence;
- failure class when applicable;
- Product Reality debt when applicable;
- exact next gate;
- timestamp and deterministic ledger-event ID.

Required update points are implementation start, preflight result, PR open, CI result, parked-for-retry, merge, and Product Reality state changes. This makes Issues the human-visible execution ledger while repository code, CI, and governing documents remain product authority.

## Rework KPIs

`project-orchestrator throughput rework-kpis <records.json>` computes only from explicit evidence:

1. **first-pass preflight rate** — one-attempt preflights / records with attempt evidence; target `>= 0.90`;
2. **merge-race count** — explicitly observed merge/external-completion races; target `0`;
3. **post-merge correctness-hotfix rate** — merged work requiring correctness hotfix / merged records with explicit hotfix evidence; target `< 0.05`;
4. **median implementation-to-merge seconds** — median elapsed time for records containing both timestamps; target is a downward trend without quality regression.

Missing evidence is `UNKNOWN`, never zero.

## Risk-based specialist review

Before merge, the scheduler adds specialist scrutiny for changes touching:

- physics or mathematical boundary behavior;
- persistence or schema migrations;
- security or privacy boundaries;
- provenance or licensing boundaries;
- cross-repository contracts.

This is additive. It never removes or weakens repository review requirements.

## Safety invariants

The Throughput Guard cannot:

- bypass or replace canonical full preflight;
- infer GREEN from missing evidence;
- infer Product Reality PASS;
- create a durable manual ownership database;
- invent executable commands not authorized by the target repository;
- merge while an active/stale lease exists;
- delete a branch with unique or uncertain unmerged work;
- delete a default, protected, release/hotfix, PR-owned, or actively leased branch without evidence-safe disposition;
- weaken security, provenance, privacy, licensing, architecture, or review gates;
- turn KPI targets into claims of measured performance.
