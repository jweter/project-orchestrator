# Continuous Laptop Engineering Flow

Jeremy should not have to send `Continue` between ordinary engineering slices. The laptop control plane owns routine continuation.

## 1. Autonomous builder actor
`ExponentialEngine-AutonomousBuilder` runs from Windows Task Scheduler every five minutes. It exits immediately while the interactive session is active and only performs heavy work after five minutes of user idle time.

The builder rotates across the six control/product repositories. A repository with an open PR is treated as already owned, so the actor rotates to another repository instead of stacking conflicting work.

For an eligible repository it creates an isolated governed branch from exact `origin/main`, asks the local Ollama/Aider coding model for exactly one bounded P4/P5 slice, rejects governance/security/workflow/roadmap/architecture edits, and runs the repository's canonical `engineering/preflight.py`.

Only an exact-head preflight PASS can be committed, pushed, and opened as an `AUTO:` PR. Failed generation, protected-path changes, environment failures, and failed preflight do not become PRs.

## 2. Existing laptop actor
`ExponentialEngine-LaptopActor` remains the deterministic Windows/Ollama/native verification and queued-work executor. The new builder does not replace it; it feeds the same unattended engineering flow with bounded implementation slices.

## 3. Green PR merger
`ExponentialEngine-GreenPRMerger` is lightweight and is not idle-gated. It polls every 60 seconds.

It merges only allow-listed portfolio PRs when the PR is non-draft, authored by an allowed portfolio actor, at least two minutes old, merge state is CLEAN, every observed check is complete and SUCCESS/NEUTRAL/SKIPPED, there is no review requirement or requested change, and there is no current-head blocking P0-P3 review finding. The exact observed head SHA is supplied to GitHub at merge time.

Missing checks, pending checks, red checks, conflicts, review blockers, unknown authors, moved heads, and current blocking findings fail closed.

## 4. GitHub native auto-merge
GitHub auto-merge exists and is enabled in five product repositories, but all six default branches currently report no branch protection; project-orchestrator also has native auto-merge disabled. Without enforced required checks, native auto-merge alone does not express this portfolio's full safety contract. The merger actor therefore remains the authoritative fast-flow path.

## Flow
`builder -> exact-head preflight -> PR -> CI/review settle -> green merger -> next repository becomes eligible -> builder continues`

This is laptop-driven continuation, not a ChatGPT scheduled reminder.
