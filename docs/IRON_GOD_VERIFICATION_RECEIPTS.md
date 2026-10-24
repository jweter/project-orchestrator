# Iron God IG-030/031 — Exact-Head Verification Receipts

Status: **local-only implementation candidate**, not active worker integration.

A verified native receipt combines:

1. exact 40-character immutable Git commit SHA matching checkout HEAD;
2. no tracked worktree changes before or after evidence inspection;
3. explicit \`VerificationPolicy\` with repository identity and complete
   repository-specific ordered full-preflight check list;
4. \`preflight-evidence.json\` schema 3 with matching repository/HEAD, FULL
   mode, GREEN status, null active check, and every check completed with
   exit status zero, no timeout and no interruption;
5. SHA-256 digest of the exact receipt bytes for later durable ledger use;
6. central owner-control AUTHORIZED state before and after reading;
7. optional immutable candidate reference confirming the same commit;
8. explicit Product Reality decision. In the absence of an approved adapter,
   Product Reality *required* means fail closed, even if all tests pass.

This is a **validation component**, not a test runner or authority to integrate
or accept. It requires preflight evidence produced by the repository-native
runner. It is intentionally strict for the orchestrator's FULL 26-step suite;
other repositories need their own policies and evidence adapters.

\`local_git_refs.py\` protects commit objects, but Git alone does not establish
that execution requirements or product behavior were met. Unit tests here
exercise genuine disposable Git repos, forged/missing checks, wrong heads,
incomplete runs, owner veto, tracked mutations, candidate ref mismatches, and
mandatory Product Reality.

Important remaining gates:

- require signed or otherwise tamper-evident trust boundary for preflight
  evidence if adversarial local processes are in scope;
- bind production verification policies to manifest owners;
- construct integrated candidates on disposable worktrees;
- rerun full regression on the integrated commit, not the source candidate;
- atomically reconcile accepted Git ref advancement with SQLite acceptance;
- preserve original branch/dirty work even when integration conflicts;
- explicitly measure Product Reality per repository, not infer it from CI;
- protect off-device recovery before relying exclusively on local baselines;
- test failure and reboot recovery before enabling a live worker.

The old seven Windows worker tasks stay disabled through canary rollout. The
separate hourly ChatGPT roadmap execution task is for engineering and status;
it is not a substitute for the eventual event-driven local scheduler.
