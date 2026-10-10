# Iron God IG-032/033: Isolated integration staging and conflict detection

Status: candidate implementation requiring exact-head FULL preflight. Not deployed.
Source: authoritative project Roadmap.txt, Phase 3, IG-032 through IG-036.

## Scope implemented in local_integration.py

LocalIntegrationController.stage takes an immutable candidate reference,
candidate's own starting baseline, and the current expected local accepted
baseline SHA. It refuses an unknown candidate, stale accepted baseline,
missing common ancestry, already incorporated candidate, unsafe integration
ID, occupied workspace, or owner TAKE THE WHEEL veto.

Git's merge-tree --write-tree detects content conflicts before an
integration worktree is created. Only a conflict-free candidate receives a
detached, isolated worktree under a dedicated integration root. A normal Git
merge creates a combined candidate commit with both parents, using the
repository's normal hooks. After the merge, the controller checks for tracked
changes and expected accepted ref, and anchors the new commit under a protected
refs/iron-god/recovery/integrated-<id> ref.

Any interrupt or failed step retains its worktree; the controller never
force-resets, cleans, removes, overwrites, or deletes the source worktrees.
No GitHub access is required. No automated cleanup is attempted.

## Required downstream gates (not yet implemented)

This staging function is deliberately not milestone acceptance. The
orchestrator must run the relevant repository's full formatting, linting,
static/type checks, tests, integration regression and Product Reality against
the integrated commit. It must use an exact-head verification receipt,
preserve user ownership and protect the integrated commit.

IG-034 must advance the protected local accepted baseline only after all
mandatory evidence is checked and the SQLite acceptance journal can be
reconciled across process crashes. Failed verification routes to IG-035 repair,
never a bypass merge. IG-036 requires full combined regression evidence.

A complete Phase 3 exit demo must integrate two independent candidates and
advance the accepted baseline without GitHub; unit tests simulating ref
advancement alone do not establish production readiness.

## Safety and maintenance

The original user workspace and all candidate refs remain untouched. When
integration conflicts, no output worktree is created. When a late owner veto,
hook failure or interruption happens after the new worktree exists, retain it
for forensic inspection and explicit reconciliation (IG-026 and IG-056).

Do not re-enable the seven disabled legacy worker tasks. Do not push routine
slices to GitHub. Preserve 25+ GiB free disk headroom before heavy work.
