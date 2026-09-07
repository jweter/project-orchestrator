# Remote branch preflight execution

The portfolio scheduler may run in an environment that can read and mutate GitHub but cannot execute a repository working tree locally. That environment limitation must not become a recurring portfolio-wide stall when a repository already exposes a branch-triggered full Preflight workflow.

## Authorized execution path

For a repository whose governing control plane requires exact-head preflight before an ordinary development PR, use this order:

1. Prefer the canonical local `engineering/preflight.py` when an executable checkout is available.
2. Otherwise, if the repository has a governing GitHub Actions Preflight workflow triggered by pushes to the intended automation branch namespace, create the implementation branch from the verified current default-branch head and push the bounded implementation there.
3. Treat the branch as `PREFLIGHT_UNVERIFIED` until the Preflight workflow for the exact branch-head SHA completes successfully.
4. Verify that the successful workflow run is for that exact head. A successful run for an older commit does not promote the branch.
5. Only after exact-head Preflight is GREEN may the scheduler open the ordinary Ready-for-Review PR.
6. PR CI remains fresh independent verification. Never reuse branch Preflight as PR-CI evidence and never merge while required PR checks are pending, missing, failed, or uncertain.

If the branch-triggered workflow is absent, does not cover the automation branch namespace, cannot be observed at the exact head, or fails for an environment reason, classify only that branch/lane `PARKED_FOR_RETRY` and continue other independent product work. Do not reinterpret an unavailable execution path as permission to weaken or skip preflight.

## Why this exists

The control plane already defines a full branch-triggered Preflight workflow for `orchestrator/**` and `agent/**` branches. A GitHub-connected scheduler therefore has an executable verification path even when it lacks a local shell. Repeatedly reporting "no local checkout" without attempting that repository-native path violates the anti-stall and throughput-floor intent.

This document changes no product authority, merge gate, security boundary, Product Reality policy, or repository-specific verification command. Product repositories remain authoritative for whether their own branch-triggered Preflight path exists and which branch namespaces it covers.
