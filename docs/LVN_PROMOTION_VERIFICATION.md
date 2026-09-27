# Local Verification Node promotion verification

The **Local Verification Node (LVN)** is the Windows machine actor that independently verifies exact candidate heads. `Portfolio Idle Worker` is one implementation of that actor. The LVN never promotes or merges code; it produces bounded evidence for the Evolution Engine / Autonomous Engineering Scientist.

## Why failed-PR reproduction was insufficient

A PR can be cloud-green and still require environment-specific evidence before promotion. The first production example was Knowledge Engine Core PR #497: its Scientist experiment contract required exact-head Windows verification, but the installed selector only looked for failed PRs. The worker itself was healthy; the selection policy was too narrow.

This upgrades the model from **failed-PR reproduction** to a **promotion-candidate verification queue**.

## Selection order

1. **Explicit promotion gates**: open, non-draft, same-repository PRs carrying `<!-- lvn:required -->` or an approved LVN-required label, with authoritative CI GREEN and no exact-head LVN evidence yet.
2. **Failed-PR reproduction**: open, non-draft, same-repository PRs whose authoritative CI is FAILED.
3. **No speculative green work**: green PRs without an explicit local-verification contract are not selected.

P0-P6 priority remains authoritative before lane preference. One LVN is modeled as the exclusive `LVN_WINDOWS` resource; additional eligible work is deferred, not lost.

## Multidimensional state model

LVN dispatch is not a boolean. The selector reasons across multiple dimensions:

- **Code identity**: repository, PR, branch, full 40-character candidate SHA.
- **Cloud verification state**: GREEN / FAILED / PENDING.
- **Promotion contract**: explicit LVN requirement versus ordinary green PR.
- **Evidence freshness**: verification is deduplicated by `repository#PR@SHA`; a head change invalidates old evidence automatically.
- **Trust boundary**: cross-repository/fork heads fail closed.
- **Lifecycle**: closed PRs and drafts are ineligible; pending CI waits rather than consuming local resources.
- **Environment availability**: environment failures do not become product defects; callers may put that exact key into retry backoff.
- **Resource contention**: bounded dispatch prevents two candidate runs from fighting over the same Windows node.
- **Promotion authority**: LVN result is evidence only; GitHub CI/review, provenance/security, Product Reality, and reserved human decisions remain authoritative.

## Request envelope

`project-orchestrator scientist lvn-select INPUT.json` produces deterministic queue decisions.

`project-orchestrator scientist lvn-request INPUT.json` converts one selected item into a deterministic read-only request containing:

- request id;
- repository / PR / branch / exact SHA;
- selection reason;
- requested checks;
- environment class;
- permitted result states;
- explicit `promotion_authority: NONE`.

The request can be consumed by a repository-owned unattended worker such as the Knowledge Engine worker being developed under Core #493.

## State transitions

```text
PR changes
  -> authoritative CI pending
      -> WAIT_CI
  -> CI failed
      -> FAILED_PR_REPRODUCTION
  -> CI green + no explicit LVN contract
      -> INELIGIBLE (do not speculate)
  -> CI green + explicit LVN contract
      -> PROMOTION_GATE
          -> exact head already verified -> SATISFIED
          -> retry backoff -> DEFERRED
          -> LVN capacity available -> DISPATCH_READY
              -> PASS -> independent evidence may satisfy the experiment gate
              -> FAIL -> exact failure enters Scientist lineage
              -> ENVIRONMENT_FAILURE -> backoff/retry, not product failure
              -> head changes -> old evidence becomes stale automatically
```

## Safety and anti-loop rules

- Never infer an LVN requirement from prose such as "Windows" or "local"; require the machine-readable marker/label.
- Never trust a partial SHA.
- Never verify a fork head under owner-machine credentials.
- Never reuse evidence after a candidate head changes.
- Never treat an environment failure as proof of a code failure.
- Never allow LVN evidence to merge code by itself.
- Never continuously retry the same environment failure; use bounded backoff.
- Preserve failed-PR reproduction as a fallback diagnostic lane rather than replacing it.

This design lets the control plane become more autonomous without becoming less controlled.