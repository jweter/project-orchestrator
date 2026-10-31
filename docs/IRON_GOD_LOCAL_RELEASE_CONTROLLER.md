# Iron God — Local Release Controller v1

## Why it exists

The production actor previously selected GitHub `origin/main` on every bootstrap,
while Iron God now develops locally and publishes only significant milestones.
This repeatedly replaced newer local revisions with older code and restarted
the supervisor with outdated scheduling and retry behavior.

This controller introduces a **pinned, independently verified local release
candidate**. It deliberately builds on the existing:
- `scripts/laptop_actor_bootstrap.py` local exact-SHA opt-in;
- `scripts/launch_local_actor.ps1` pinned-source loader;
- `scripts/install_local_actor_cutover.ps1` reversible task-action installer;
- `scripts/rollback_local_actor_cutover.ps1` scheduled-task rollback;
- `supervisor_runtime.prepare_actor_revision_rollover` worker-draining
  generation handoff.

It does not rewrite agent prompts, invent a new scheduler, override human mode,
change candidate-execution authorization, or silently terminate workers.

## Release contract

`SOURCE COMMIT → CLEAN TREE → NATIVE FULL PREFLIGHT → PINNED STAGED CLONE
→ VERIFIED RECEIPT → QUIESCENT CUTOVER → RUNNING REVISION PROOF`

An approved source must be a **separate clean Git checkout** at an exact
40-character commit SHA. The main development checkout may contain valuable
in-progress work and must not be reset/cleaned to satisfy release checks.

`project_orchestrator.local_release_controller` provides:

- `stage`: verify exact source HEAD and clean tree, run the repository-native
  full `engineering/preflight.py`, recheck identity, clone a separate detached
  checkout under a SHA-specific release directory, and atomically record the
  exact tested SHA, tree, preflight digest, verification time, and release path.
- `inspect`: verify the staged clone and receipt, read recent supervisor
  heartbeat/worker-pool observations, and return an **advisory** cutover status.
  Unreadable, missing, malformed, or stale state yields a blocked verdict.
- Strict fail-closed owner-control and dirty-tree checks. Already verified
  releases are idempotent; partial releases require reconciliation.

Neither operation updates the live actor checkout or Windows Task Scheduler.
The returned `ELIGIBLE_FOR_CONTROLLED_CUTOVER` verdict is **necessary but not
sufficient** to mutate the running system. An installer must independently
prove exclusive process/generation ownership, no live workers or leases, a
valid rollback path, and current owner authorization just before mutation.

## Commands on Azathoth

From the proposed release-controller worktree (replace the SHA with its
actual independently verified commit after this module is committed):

```powershell
$repo = 'C:\Users\jerem\GitHub\iron-god-local-release-controller-20261020'
$env:PYTHONPATH = "$repo\src"
$sha = (git -C $repo rev-parse HEAD).Trim()
$base = "$env:LOCALAPPDATA\ExponentialEngine"
python -m project_orchestrator.local_release_controller stage `
  --source $repo --sha $sha `
  --releases-root "$base\releases" `
  --receipts-root "$base\release-receipts" `
  --owner-marker "$base\flow-state\human-mode.json"
python -m project_orchestrator.local_release_controller inspect `
  --receipt "$base\release-receipts\$sha.json" `
  --state-root "$base\flow-state" `
  --owner-marker "$base\flow-state\human-mode.json"
```

**Important:** The actual release receipt and release checkout are local
machine records. Do not commit them or include raw runtime ledgers in GitHub.

## Controlled production cutover (separate acceptance milestone)

1. Independently review the staged release receipt and native preflight.
   Confirm the release contains the intended retry/supervisor fixes and
   has the exact Git SHA cited in the receipt.
2. Inspect current supervisor generation and both worker slots/leases.
   Let active workers complete. Refuse if any ownership or liveness is unknown.
3. Coordinate a controlled release window with watchdog/task owners,
   preserving the old actor revision/ref and existing task-action backup.
   Never run a hard reset of an active supervisor's own source checkout.
4. Only under the existing authorization and owner-control protocol, use
   `install_local_actor_cutover.ps1` with `-RepoRoot` pointing to the **staged
   release checkout** and `-ReceiptPath` pointing to the matching verified
   release receipt; source and exact Git tree must match that receipt.
   The installer refuses an omitted receipt or dirty checkout and preserves
   rollback XML/config. **Do not point it at a mutable development checkout.**
5. Reconcile the separate ContinuousFlow-Watchdog launch target so it loads
   the same pinned release, not an obsolete actor-repo. That watchdog must
   remain disabled until its replacement is tested and independently
   authorized; enabling only the LaptopActor task is not a supervisor cutover.
6. Verify the actor checkout `HEAD` equals the release SHA, the persistent
   supervisor's **live heartbeat** reports that same SHA, exactly one active
   supervisor has exclusive authority, and workers resume without duplicate
   claims or exhausted `NO_OP` redispatch.
7. On any mismatch or failed canary, stop promotion and use the existing
   reviewed rollback procedure. Do not claim successful deployment based
   solely on a staged receipt, process startup, or one passing unit test.

The release controller v1 implements steps 1's artifact preparation and
the read-only part of step 2. **It does not perform steps 3–7 unattended.**
That division is deliberate while the active worker/guardian process
ownership and the baseline upgrade races remain unresolved.

## Required production evidence

- Exact release SHA and staged source/tree identity.
- Full native preflight result at that exact SHA; no generated/shadow gates.
- Verified and preserved preceding actor revision and rollback path.
- Current owner-control authorization and worker/lease drain.
- New exclusive supervisor generation reports the exact same loaded SHA.
- A real product worker produces a diff and independently passing tests.
- Old repeated VoiceStudio `NO_OP` slice no longer monopolizes dispatch.
- No regression in owner control, source provenance, Product Reality, or CI.

Until those are shown, report `VERIFIED_STAGED`, not `DEPLOYED`.
