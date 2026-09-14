# Flow Signal Quality

The portfolio treats CI color as raw evidence, not as a root-cause classification.

## Operational states

- `VERIFIED_GREEN`: exact evidence is green and may continue through normal promotion revalidation.
- `PROMOTION_STALE`: the branch head moved after verification. This is a safety refusal, not a product defect. Promotion remains fail-closed until exact-head evidence is restored.
- `NOT_APPLICABLE`: the workflow does not apply to the repository/ref/change. It is operationally neutral and must not be counted as a product failure.
- `PLATFORM_GATE`: GitHub, runner, permissions, startup, or similar orchestration infrastructure prevented the intended action. It is a real automation defect, but not a product-code failure.
- `ACTIONABLE_FAILURE`: code, tests, quality, policy, or another evidenced engineering gate failed and requires diagnosis/repair.
- `PENDING`: terminal evidence does not yet exist.

## Stale-head recovery

A stale-head refusal must never be weakened into a pass. The scheduler captures the live branch head and compares it to the exact head named by preflight evidence. If the live head already has exact matching full-preflight evidence, the scheduler may continue to fresh CI/review/mergeability revalidation. Otherwise it reruns full exact-head preflight before any promotion can continue.

This converts `fail safe -> stop` into `fail safe -> classify -> recover -> reverify -> continue` while preserving the same merge safety boundary.

## Reconciliation PR authority

Repository-local GitHub Actions may derive live status, commit the generated status file, and push an explicitly authorized reconciliation branch. They do not need permission to create or approve pull requests.

The portfolio orchestrator's authorized GitHub connection owns the next step:

1. verify the branch name is authorized;
2. capture its exact head SHA and bot provenance;
3. verify no open PR already owns it;
4. create the PR centrally;
5. require fresh PR CI, review, mergeability, and all normal policy gates.

This keeps least privilege and avoids recurring `GITHUB_TOKEN` PR-creation/recursion failures.

## Avoidable diagnostic churn

`avoidable_diagnostic_churn` counts diagnostic investigations where explicit evidence shows the condition was already known to be non-actionable before investigation began. Examples include a previously classified non-applicable workflow or a stale-head refusal that already has a deterministic recovery route.

The metric is evidence-only. Missing records remain `UNKNOWN`; they are never converted to zero. No target is asserted until a useful evidence baseline exists.

## Safety boundary

Signal cleanup is not greenwashing. `PROMOTION_STALE`, `NOT_APPLICABLE`, and `PENDING` are never treated as verified green. Unknown evidence fails closed. Required CI, review, security, provenance, privacy, licensing, architecture, Product Reality, and exact-head gates remain authoritative.
