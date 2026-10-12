# Forensic recovery of a NUL-filled grant lifecycle

This procedure is for an incident in which the canonical `hands-off-grant-lifecycle.json` has been overwritten with NUL bytes. Treat every newer execution as **unresolved** until separately reconciled. A supervisor that starts but dies on `GRANT_LIFECYCLE_UNREADABLE` has not recovered.

## Read-only and staging procedure

Use `python -m project_orchestrator.ledger_forensics --state-root PATH --snapshot PATH` to validate source evidence and print counts and SHA-256 digests. The command does not modify any files. Add `--preview-path PATH` to write a new **forensic preview outside live state**. Existing destinations cannot be overwritten, and the live ledger can never be named as the preview.

The preview preserves the historical snapshot record-for-record and creates a `QUARANTINED`, `LEDGER_LOSS_OUTCOME_UNVERIFIED` record for every subsequent immutable issued-grant authority document. These unknown outcomes are *not* completed, abandoned, or released. Any missing, inconsistent or malformed authority rejects the entire preview. The snapshot must be schema 1 and the current corrupt file must be exactly NUL-filled; the procedure is deliberately not a general JSON error workaround.

## Recovering to execution

**Do not replace the live ledger with the preview merely because it parses.** Reconcile all post-snapshot grants against exact-head worktrees and branches, worker/issuer liveness and start fingerprints, recovery-generation leases, GitHub PR state and CI evidence, and source records before allowing a grant or repository to be released. A locally verified commit or unpromoted branch must not be discarded by an old snapshot. Preserve original corruption bytes, hashes, the recovered preview, and all immutable issued grants separately.

Production publication requires a separate, explicit, versioned CAPA that proves the reconciliation and acquires the existing lifecycle ownership lock. Re-run repository-native tests. Do not disable the scheduler, fabricate terminal outcomes, reset dirty user workspaces, bypass CI/Product Reality gates, or reboot Windows to hide corruption.
