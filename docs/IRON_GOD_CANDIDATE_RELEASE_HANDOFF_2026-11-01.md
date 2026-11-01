# Iron God candidate release - handoff for the owner's controlled release procedure (2026-11-01)

Status: **HANDOFF, nothing installed.** The pinned release stays `36fb3aa628a281b95f3ad7f02c4f9ce01f934955` until the owner
separately authorizes installation. This document is what the owner needs to decide, to install with the existing
procedure, to watch the first real cycle, and to roll back. It changes no runtime behavior.

Candidate: `main` at the time of staging (at writing `f4c8916`, plus #610 if merged). The exact SHA is fixed by the stage
step; do not install a SHA that has no GREEN FULL native preflight evidence bound to it.

## 1. What the candidate adds over 36fb3aa

11 commits, all descending from the pin (`36fb3aa` is an ancestor of `main`). Runtime source changes are **additive only**:
9 files, about 2,000 lines added, none deleted; the product lane gets small hooks and the policy file gets 9 lines.

| Piece | What it does | Default |
| --- | --- | --- |
| Roadmap refill (`iron_god_roadmap_refill`) | When a repository has no dispatchable approved item, compiles the repository's *declared* capability graph at the exact head, picks the next ready slice, and writes an authoring request | on (`roadmap_refill`) |
| Builder routing | Routes each slice by its declared `capability_level` against the measured calibration profile: LOCAL, CAPABLE (stronger builder needed) or NONE (cannot be built here, e.g. Unreal/C++) | on |
| Local draft author (`iron_god_local_drafter`, `scripts/iron_god_draft_author.py`) | For LOCAL Level 1 slices that carry the owner's independent `acceptance_examples`, the worker's own planner drafts the build spec; the unchanged strict admission decides | on (`local_draft_author`) |
| Continuation overlay | A slice the lane (or a capable builder) merged unlocks its dependents at once | on |
| Graph progress (`iron_god_roadmap_progress`, `scripts/iron_god_progress_preflight.py`) | One PR per repository records merged slices as `VERIFIED_COMPLETE` in the repo's graph, after the repository's native preflight passed for that exact commit | on (`roadmap_progress`) |
| Capable-builder dispatch (`iron_god_capable_builder`) | Hands CAPABLE slices to the existing `claude_dispatch` routine within a daily budget; the routine opens an ordinary PR | **OFF** (`capable_builder: false`) and needs a routine token |
| Baseline ref rename (#610) | The cumulative accepted baseline moves to `refs/iron-god/baseline/accepted`; the legacy name is read-only | n/a |
| Gate watcher (`iron_god_gate_watch`) | Read-only evidence of whether the builder model is unloaded during mypy and the canonical preflight | tool, not automatic |

Safety properties that did not change: execution authority is checked by every new subprocess; heavy local work (draft,
progress preflight, builds) is serialized through the one shared runtime lease; no new merge path (the existing portfolio
green-PR merger merges engine PRs); no CI, review or branch-protection gate is touched.

## 2. What it will do on the first Take the Wheel (current graphs)

| Repository | Slice | Route | Result |
| --- | --- | --- | --- |
| VoiceStudio | `s2-singing-project-layout` (level 1, owner acceptance examples) | LOCAL | **drafted by the local 7B, admitted, built, PR, merged: the first real autonomous roadmap slice** |
| Knowledge Engine Core / Web / AI | docs/characterization/integration slices (level 3-4) | CAPABLE | recorded blockers (capable builder off) |
| Rocksmith | `issue-612-...` (no declared level) | CAPABLE | recorded blocker (undeclared level fails closed) |
| Everward | Unreal/C++ slice | NONE | recorded blocker (no Python package for the local lane) |

So the first cycle is one small VoiceStudio function, deliberately.

## 3. What is verified, and what is not

Verified: 352+ tests across the lane, refill, routing, drafter, author, capable-builder, progress, progress-preflight,
gate-watch and ref suites; the **whole-loop rehearsal** (`test_the_whole_roadmap_loop_runs_across_repositories...`) drives
the real lane across three repositories with every turn a restart: plan, admit, build, verify, accept, PR, CI, merge,
continuation to the dependent slice, durable progress, "what next?" at the end, blockers elsewhere. Every PR in the series
passed CI and a canonical preflight at its exact head (a local native run for #595, the GitHub Windows-runner
`Preflight` workflow for the rest). The unload mechanism was verified on
the real Ollama (control `MODEL_RESIDENT` 13/13, after the worker's release `VERIFIED_UNLOADED` 14/14, 1.5B stand-in).

**Not verified (only the first real cycle can show these):**
1. The real local 7B's plan quality. It is measured weak (about 1 in 5 turns pass end to end). The design makes failure cheap
   (mechanical validation, two attempts per slice, owner examples anchor the tests) but does not make the model better.
2. A real worker build and real GitHub timing for roadmap-derived items.
3. RAM headroom while a draft, a progress preflight and a build happen in sequence on 16 GB. They are serialized and each
   unloads the model before heavy gates, but this has not run on the real host with the new code.
4. The unload of the 7B itself before mypy/preflight in a production cycle (the 1.5B proved the mechanism).

## 4. Risks and how each is bounded

| Risk | Bound |
| --- | --- |
| The local model drafts something wrong | Worker plan validator, strict admission (scope, route, declared level, baseline, duplicates), the owner's examples injected as non-negotiable tests, the repository's own preflight and CI, two attempts then blocker |
| Resource pressure (RAM/CPU) | One shared runtime lease serializes draft, progress preflight and builds; drafting and progress run only while no worker is active; hung jobs failed by age (30 min draft, 2 h preflight) and their lease returned |
| Duplicate or conflicting PRs | One open progress PR per repository; capable dispatch suppressed while its PR is open; file reservations respected; idempotent branch names |
| A bad graph edit | Canonical-form check, exact-diff proof (only `state`/`verification` of named slices), re-validation; refused as a blocker otherwise |
| Surprise spend | Capable builder off by default; needs the routine token and has a daily budget and two attempts per request |
| The 4 preserved KE-Core worker repos | They hold the legacy baseline ref and keep working through the fallback; the lane never works in them; nothing is deleted |
| The green-PR merger deletes merged branches (`--delete-branch`) | Existing behavior of the owner's merger, unchanged by this release |

## 5. Install, using the existing controlled procedure (unchanged scripts)

Same sequence used for `36fb3aa`; each step is the owner's or an unchanged script:
1. Owner clicks **Give Me Control** (workers stop, tasks verified Disabled).
2. Post-stop safety check (read-only): no engine/worker/model processes, tasks Disabled, no unsafe publication, no lease.
3. Stage dry-run then apply with `scripts/desktop-control/Stage-VerifiedLocalRelease.ps1`: needs GREEN FULL native preflight
   evidence bound to the exact candidate SHA and a clean checkout, and the candidate must descend from the current pin.
   The native preflight is the memory-heavy step (about 14 minutes, about 5 GB headroom): run it with the engine stopped,
   under the RAM guard, so nothing else competes. (The GitHub-runner Preflight is additional evidence, not the receipt.)
4. Promote dry-run then apply with `Promote-DisabledLocalRelease.ps1` (tasks stay Disabled, nothing started).
5. Verify independently: pins read the candidate SHA, receipt exact-head and tree match, checkouts clean, tasks Disabled and
   launching from the candidate, zero engine processes, `Check-Development-ResumeReady` = `RESUME_READY`.
6. Owner clicks **Take the Wheel**.

## 6. First-cycle watch plan (I will run this and report only milestones and blockers)

- The gate watcher over the first real cycle (`python -m project_orchestrator.iron_god_gate_watch`): the unload verdict at
  mypy and the canonical preflight, plus the minimum free RAM.
- In order: `ROADMAP_REFILL` -> `LOCAL_DRAFT_STARTED` -> `LOCAL_DRAFT_DRAFTED` -> `ADMITTED` -> dispatch -> PR -> merge ->
  `PROGRESS_OPENED`; every outcome is in `roadmap-refill.json` (ledger) and working memory.
- **Stop and recommend an owner-controlled intervention** on sustained memory exhaustion, substantial paging or loss of
  responsiveness; on a draft/build failing repeatedly (the attempt cap makes this finite); or on any PR appearing in a
  repository it should not touch. I will not kill workers or change owner control myself.

## 7. Rollback

Re-promote the previous release with the same `Promote-DisabledLocalRelease.ps1` using the backup written at install
(`flow-state\pinned-release-promotion-<guid>` holds the previous pin, the actor pin, the task XMLs and the desktop
controls). The `36fb3aa` release, its actor checkout and its receipt remain on disk. New runtime state is additive
(`roadmap-refill.json`, `draft-jobs/`, `draft-results/`, `roadmap-requests/`, `roadmap-drafts/`, `capable-dispatches/`,
`canon-workspaces/products/progress/`) and is ignored by the old release; none of it needs migration or deletion. The policy flags live in
the hash-bound release, so turning a feature off means a new release or rolling back, not editing a running install.

## 8. Decisions that are the owner's

1. Install the candidate (and when).
2. Enable `capable_builder` and configure the routine token, or leave CAPABLE slices as recorded blockers.
3. The four preserved KE-Core worker repositories keep the legacy baseline ref; retiring it is an owner decision.
4. Rocksmith (#612, advanced and actively worked by another agent) and Everward (Unreal/C++): decompose into builder-sized
   Level 1-2 slices, hand to a stronger builder, or leave as blockers.
5. VoiceStudio's `docker.yml` is now build-only; publishing to `ghcr.io/jweter/voicestudio` is a separate, reviewed step.
