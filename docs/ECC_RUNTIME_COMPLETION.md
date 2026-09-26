# ECC-Derived Runtime Completion

Status: implementation completion slice for the selectively adopted ECC concepts. This document supersedes the deferred implementation status in `ECC_GAP_ANALYSIS.md` for harness security, candidate learning, and Phase E transient handoffs. The earlier gap analysis remains the historical selection rationale.

## Completed core adaptations

Project Orchestrator now contains original, repository-native implementations of the ECC-derived capabilities we selected as useful:

1. autonomous-loop structural audit;
2. harness inventory and context-budget observability;
3. agent evaluation and deterministic AI-regression fixtures;
4. disposable exact-baseline agent-evaluation matrix runner;
5. verified repository-history skill extraction and explicit task/path selection;
6. read-only harness-security audit;
7. non-authoritative candidate-learning bridge into the existing VERIFIED learning-memory contract;
8. local-first create-only cross-harness transient memory.

ECC is still not a runtime dependency and is not bulk-installed. Repository policy, GitHub state, exact-head preflight, fresh PR CI, Product Reality, and verified learning memory remain authoritative.

## Harness security

`project_orchestrator.harness_security` and `engineering/harness-security.json` add a deterministic read-only audit for harness-specific attack surface:

- known credential/private-key shapes in harness configuration;
- literal values under secret-like MCP/config keys;
- general-purpose shell MCP launchers;
- unpinned `npx` MCP packages;
- broad filesystem-root arguments;
- remote MCP transports requiring explicit trust review;
- broad workflow write permissions;
- policy-declared untrusted-content handlers that lack required defensive markers.

The audit never auto-fixes configuration. Missing local repository roots are `UNVERIFIED`, never silently GREEN. Normal source-code security scanners remain separate and authoritative for their own scope.

## Candidate-learning bridge

`project_orchestrator.learning_candidates` creates a deliberately weaker state than verified learning memory:

`observation -> CANDIDATE / NON_AUTHORITATIVE -> independent verification -> promotion packet -> explicit VERIFIED-memory promotion`

Candidate confidence is never authority. A candidate cannot mark itself VERIFIED, cannot infer Product Reality, cannot use its source harness as its own independent verifier, and cannot mutate `engineering/state/learning-memory.json` automatically. The promotion packet is validated through the existing `validate_lesson` contract so there is still exactly one authoritative durable-learning schema.

## Cross-harness transient memory

`project_orchestrator.transient_memory` provides bounded local session handoff without creating a second policy store:

- human-readable JSON;
- local-first `.orchestrator-memory/` storage, git-ignored;
- create-only writes;
- `UNREVIEWED` trust and `CONTEXT_ONLY` authority;
- explicit source and target harnesses;
- bounded title/body/document/result sizes;
- literal secret rejection;
- control-character rejection;
- symlink/protection-file checks;
- deterministic lexical retrieval only;
- direct reads fail closed if the vault scan is incomplete or malformed;
- search may return partial results only with explicit diagnostics;
- no automatic promotion to learning memory, repository skills, policy, or Product Reality.

Target-harness routing is a relevance filter, not an authorization boundary. Live repository state outranks every recalled handoff.

## Preflight integration

Canonical preflight now validates all three completion contracts before the normal compile/test/CLI gates:

- `harness_security`;
- `learning_candidates`;
- `transient_memory` self-test.

Regression tests cover unsafe MCP launch/config shapes, candidate self-promotion attempts, secret-bearing observations, independent-verifier requirements, create-only handoffs, targeting, malformed vault state, and attempts to rewrite transient context as trusted authority.

## What remains after implementation

The remaining ECC work is validation and product application, not another generic control-plane framework:

- run harness/context/security audits against the actual six-repository local workspace;
- run the same pinned agent-evaluation matrix with at least two real local agent configurations;
- measure repeated repository-skill effectiveness rather than inferring it from one successful use;
- apply selected product-specific techniques where they solve demonstrated gaps: Rocksmith Windows desktop E2E/timing regression, Everward deterministic simulation/C++ testing, and Knowledge Engine contract/AI-regression work where current native controls leave a concrete gap.

Those activities may create product work, but they do not justify another scheduler, KPI store, global skill catalog, hidden hook framework, or ECC vendor dependency.

## Completion rule

The ECC-derived core integration is implementation-complete when this slice passes exact-head canonical preflight and fresh PR CI. It becomes operationally validated only after the local six-repository and multi-agent test campaign produces evidence. Any new gap discovered by that campaign returns to the normal repository roadmap and issue queue rather than automatically expanding this integration.
