# Iron God — integrated development, shared MCP/SQLite, and no-fee remote access

**Owner direction:** 2026-11-22. **Status:** implementation roadmap and acceptance contract, **not** evidence of deployed production capability, an owner grant, permission to spend money, or permission to change network/security configuration.

**North star:** Iron God develops real, useful capabilities across all seven repositories without requiring the owner to prompt "continue." Its qualified local builders handle ordinary slices; the existing hourly ChatGPT DoWork task handles difficult source-code slices; a separate Claude builder may be added only after the ChatGPT route is proven and separately authorized. Azathoth, not an advisory AI message, independently verifies code, owns controlled GitHub publication/release and maintains durable evidence. All participants exchange scoped read/write coordination and learning through existing SQLite/MCP infrastructure. The remote-access layer should cost **$0 additional recurring dollars** while preserving human desktop access and rollback. BLACKICE is a candidate, not a prerequisite for code delivery or a proven substitute.

**Related authoritative plans:** [owner-approved roadmap](OWNER_APPROVED_ROADMAP_2026-10-25.md), [main roadmap](ROADMAP.md), [product-first delivery](PRODUCT_V1_DELIVERY_EXECUTION_PLAN.md), [MCP coordination](IRON_GOD_MCP_COORDINATION.md), [offload contract](IRON_GOD_CHATGPT_OFFLOAD.md), [MCP cost/compatibility evaluation](IRON_GOD_MCP_STACK_EVALUATION.md), [Iron Continuum issue #747](https://github.com/jweter/project-orchestrator/issues/747), [PR #768](https://github.com/jweter/project-orchestrator/pull/768). Product-specific native requirements and owner-control/lease authority always take precedence over advisory plans.

## Owner-approved direction ? 2026-11-24: simplify before expanding

Finish IRON CONTINUUM S2 and its existing acceptance contract before starting S3.
Do not expand S2 into a new scheduler, additional services, or speculative MCP
infrastructure. Necessary defect repair and existing authorized product work
remain in scope. S3 begins with eligibility consolidation and productive recovery.
The objective is maximum verified product development with minimum necessary
orchestration, not more modules, dashboards, PRs or supervisor cycles.

### S3.1 ? Eligibility consolidation and productive recovery

Baseline observation on Azathoth (2026-11-24 23:37 UTC): installed actor
`2ce8deac400c7ca8c970f188593c91c911629c0c` was running; Canon reported
`IDLE_NO_READY_SLICE` and Product `IDLE_NO_APPROVED_BACKLOG_ITEM`. The
eligibility projection contained 36 NEEDS_DIFFERENT_CAPABILITY, 16
ATTEMPTS_EXHAUSTED, 16 LEVEL_ABOVE_MEASURED_CAPABILITY, 15
REPEATED_FAILED_SOURCE, 3 DIRECTED_ATTEMPT_SPENT, 25 NOT_APPROVED,
11 ALREADY_COMPLETE and 3 IN_FLIGHT entries. These are dated classification
counts, not distinct root causes or a current status claim. The first five
recoverability-related categories total 86 entries. Refresh the baseline
before implementation; the observation does not establish safe deletions.

Ordered work (reuse existing modules; no second dispatcher):

1. Trace the active eligibility and recovery call graph, measure wall time,
   CPU, disk I/O, inference context and repeated decisions. Audit
   `iron_god_backlog`, `iron_god_failure_strategy`, `iron_god_task_decomposer`,
   `iron_god_slice_decomposition`, `iron_god_stall_recovery`,
   `iron_god_stalled_worker_recovery`, `iron_god_offload_eligibility` and
   `iron_god_capable_builder` plus their real callers.
2. Consolidate duplicated eligibility decisions into the existing canonical
   selection path. Keep a single authoritative decision per relevant input
   generation, revalidate volatile owner, lease, base and resource facts
   immediately before execution. Do not cache away safety boundaries.
3. Turn recoverable exclusions into bounded alternate work: capability
   mismatch -> smaller qualified slice or different strategy; exhausted
   attempts -> evidence-driven decomposition/recovery; repeated failed
   source -> investigate and change approach; spent directed attempt ->
   evaluate a genuinely different method. Preserve parent traceability,
   bounded budgets, deduplication and failure evidence. A failed approach
   must not permanently block an otherwise achievable authorized objective.
   NOT_APPROVED means determine whether standing roadmap scope already
   covers the work; if genuine authority is missing, defer it and continue
   unrelated work. Never manufacture approval or reclaim a live lease.
4. Tag canonical work items BUILD, FIX, TEST, REVIEW, DECOMPOSE, RECOVER,
   RESEARCH or SIMPLIFY. Tags are prioritization metadata, not new approval
   conditions or permanent worker processes. Use the existing scheduler and
   resource budget; rotate fairly across ready repositories and work types.
   Reuse existing action classifications where compatible; migrate
   backward-compatibly and do not introduce a competing queue.
5. Profile and simplify redundant sequential checks, state projections,
   unchanged status reports and excessive prompt context. Prefer targeted
   development tests and exact-head full verification at existing required
   integration boundaries. Reuse evidence only when all relevant inputs and
   required freshness remain valid. SQLite remains durable state; MCP
   remains advisory communication, never a prerequisite for ordinary local
   development.
6. Inventory modules as KEEP, MERGE, SIMPLIFY or DELETE. Require dependency
   analysis (including dynamic entry points), measured runtime evidence,
   regression tests and a rollback path before deletion. Do not delete
   code simply to reduce the file count.

### S3.1 acceptance and throughput budget

- One canonical eligibility decision path, existing recovery reused, bounded
  alternatives actually executed for recoverable failures, no duplicate
  logical work or unlimited retries, and blocked work cannot starve unrelated
  authorized repositories.
- Demonstrate recovery/decomposition, tagged fairness, restart/replay,
  owner revocation, lease isolation, and local-only continuation through
  focused regression and existing exact-head integration gates. No new
  permanent services or increased cloud dependency.
- Compare matched baseline and candidate runs on Azathoth with equivalent
  hardware, model availability, owner mode and work mix. Record tested
  commits/day, verified product advances, slice-selection-to-tested-commit
  latency, recovery success, eligible idle time, orchestration/communication
  time, CPU/RAM/disk and context/inference cost per successful slice.
  Separate CI waiting and genuine external blockers from engine overhead;
  report missing evidence as unknown. Proposed targets: >=85% productive
  active time, <=10% orchestration, <=5% routine communication (mutually
  exclusive categories), zero unexplained idle time when recoverable
  authorized work exists, zero duplicate logical work and zero routine
  cloud-model tokens. These are design targets, not achieved metrics.
- Require measured improvement in tested commits/day and real product
  progress without safety regressions. Unit tests, PR counts and reduced
  source lines alone do not establish throughput improvement.
- Preserve authenticated Give Me Control / Take the Wheel, roadmap scope,
  worktree and lease isolation, resource limits, native tests, exact-head
  CI/preflight, independent review, guarded publication and rollback.
  S3.1 is approved development direction, not a runtime grant or deployment
  approval. Keep S1/S2 operational acceptance distinct from source merge.

## 1. Target topology and responsibility

```text
Owner-approved repository roadmap + real unmet user journey + current GitHub head
       |
       v
Iron God product lane / roadmap-refill / canonical attempts and ownership
       |-- normal qualified local builder -> existing native verification pipeline
       |
       |-- independently proven difficult slice (no active competing work)
                |
                v
Existing SQLite coordination store -> scoped MCP proposal/read + reservation
                |
                +--> EXISTING hourly DoWork (Chat mode, unchanged schedule)
                |      source + tests on exact base; real Git patch in GitHub
                |      immutable artifact ID, patch/manifest SHA-256, evidence refs
                |
                +--> FUTURE Claude Code implementation capability
                       distinct authenticated principal; no duplicate file ownership
                |
                v
Azathoth resident importer (authenticated owner control + live worker lease)
 -> fresh exact-head/branch/path verification -> isolated Git worktree
 -> immutable artifact digest verification -> native tests and full preflight
 -> existing publisher: PR -> CI -> independent review -> guarded merge
 -> controlled release/product-specific Product Reality (when required)
 -> independently verified receipt -> existing SQLite development learning
 -> next eligible roadmap slice; durable interruption/recovery
```

This architecture **does not** create a second supervisor, new DoWork schedule, new GitHub merger, second worker-lease database, or blanket MCP command channel. A proposal or SQLite reservation is *advisory*, not an owner-control action or exclusive execution lease. Distinguish a GitHub source merge from the separately pinned resident engine installation and product native acceptance.

| Participant | May read | Permitted writes/actions after authenticating | Cannot self-authorize |
| --- | --- | --- | --- |
| Owner | Status, roadmaps, run/PR/release evidence, audits | Give Me Control / Take the Wheel, approvals and reserved operator actions | No action is inferred merely from user text stored in a mailbox |
| Iron God resident supervisor | Canonical backlog/roadmap refill, attempted work, Git and resource evidence; MCP advisory queue | Existing authorized scheduling, worker leases, local build, isolated import and publication through canonical gates | Cannot treat advisory messages, stale controls or GitHub comments as grants |
| DoWork ChatGPT task | Verified bounded offer, allowed source paths, historical failure, relevant tests, GitHub state, permitted MCP read tools | Substantive source/test patch, GitHub proposal branch, own scoped advisory claim/receipt where actually enabled | Cannot grant leases, use local OS authority, accept native results, merge/release on an advisory message |
| Future Claude Code builder | Same bounded evidence via its own principal/scope | Non-overlapping code/test artifacts under verified capacity and owner/lease gates | Cannot impersonate DoWork, override claims or incur spend without approval |
| Existing local MCP/SQLite adapter | Authenticated scope-limited status, evidence and inbox | Idempotent advisory post/ack/proposal/claim/receipt according to permission and transaction policy | No arbitrary SQL, arbitrary shell execution, owner-control mutation, CI or Product Reality writes |
| BLACKICE (conditional) | Authorized remote desktop and approved remote tool queries | Explicitly allowed audited file/UI/command capability within its separate host permissions | Cannot bypass ChatGPT client eligibility, owner veto, worker lease, GitHub gates or Norton |

## 2. Single source of truth and coordination schema

**Authoritative roots:** GitHub exact commits/PRs/CI/reviews for source and publication; repository-approved roadmap and accepted product test oracles for work scope; resident owner controller for owner mode and grants; existing canonical worker/branch/path lease provider for execution ownership; repository-native preflight/Product Reality for acceptance; existing development memory for observed attempts. The SQLite MCP store carries **references and advisory coordination**, not mirror grants or invented acceptance.

A versioned bounded `OffloadOffer` includes `offer_id`, `repository`, `slice_id`, `roadmap_ref`, `origin` (static backlog or admitted runtime roadmap), `base_sha`, `approved objective`, `allowed_paths`, `forbidden_paths`, `required tests`, `failure_ref`, `failed_packet_id`, `measured capability`, `authority_ref`, `observed_at`, `evidence digests` and `expiry`. Any value absent from the current implementation is a **required follow-up extension**, not an assertion that the deployed schema currently contains it. A producer must validate all live facts rather than trusting a caller-supplied observation.

`OffloadReservation` is a serialized immutable advisory claim keyed by exact offer, principal, base SHA and expiry/continuation. Its identity must remain replay-safe across hourly invocations; an existing claim cannot silently be stolen, duplicated or overwritten. DoWork sees at most one eligible source-code slice per run while continuing independent unblocked product development if none is eligible. A future Claude actor requires a distinct principal, conflict-free scope and explicit owner/cost decision.

`OffloadArtifact` contains a bounded Git patch (outside the mailbox), source GitHub proposal commit/ref, immutable manifest, repository and exact parent SHA, changed paths, patch/manifest SHA-256, test additions and producer identity. The mailbox carries only identifiers/digests/references and stage state. The laptop compares Git's canonical blob IDs and exact HEAD; it must not rely on an LLM statement that tests passed or that the patch is safe.

`OffloadVerificationReceipt` distinguishes `STAGED_UNVERIFIED`, `IMPORTED_UNVERIFIED`, `NATIVE_TESTS_FAILED`, `PREFLIGHT_GREEN`, `PR_OPEN`, `CI_PENDING`, `REVIEW_BLOCKED`, `MERGED`, `PRODUCT_REALITY_PENDING`, `PRODUCT_REALITY_VERIFIED`, `REVOKED` and `BLOCKED`. Each state records exact source/ref revision, result origin, verifier, timestamps, relevant log/result digest, and subsequent action. A ChatGPT/Claude result cannot issue a `PREFLIGHT_GREEN` or `MERGED` receipt; authoritative readers must independently re-fetch and corroborate evidence.

**SQLite engineering learning:** After independent results, correlate the same attempt/offer to its failure class, selector decision, model/agent capability, tests, review findings, repair fingerprints, accepted SHA, merge, and product outcome. Promote an engineering lesson only after corroborated repeatable evidence. Keep private source/secret payloads out of advisory messages and shared learning. Do not invent a new SQLite database where `mcp_coordination`, `development_memory`, Foreman or Helix already owns a record.

## 3. Scoped bidirectional MCP: "read and write for everyone" safely

Target **practical two-way collaboration** for each supported client, not unrestricted remote database or Windows writes.

1. Register one authenticated principal per actor and client surface (resident local, Claude local, hosted ChatGPT, Windows desktop, scheduled Chat mode). Do not authenticate by editable `IRON_GOD_MCP_CLIENT_LABEL`, GitHub username or message body.
2. Expose bounded status, approved work offers, dependencies, latest attempts, work ownership, artifact receipts, native failures, PR/CI/reviews, learning, and mailbox read with per-repository / recipient authorization.
3. Add independently permissioned write tools for advisory message post/ack, offload accept/decline, candidate receipt and verified outcome *request*. Require OS ACLs, authenticating principal, protected SQLite transactions, audit and idempotent replay. The existing production controller alone may validate and commit real execution/verification state.
4. Separate tool categories: read; advisory write; owner-reserved mutation; native code/import; release/merge. MCP must never collapse the latter categories into generic `run_shell` or `execute_request`.
5. Enforce schema, read/write scopes, quotas, path fences, bounded payloads, timeouts, freshness and external content sanitization. Test spoofing, malicious text, duplicates, truncated payloads, SQL corruption, permission mismatch and restart replay.
6. Confirm capabilities separately from the **actual** hosted ChatGPT session, scheduled Chat-mode task, Windows Codex host and Claude local host. A successful stdio test on the laptop does not prove a hosted personal plan supports private custom MCP writes.
7. If hosted ChatGPT cannot call the new MCP write interface, use the existing supported GitHub connector for code and immutable offer/receipt references, while an authenticated local process writes approved advisory transactions. No paid tier or unsupported tunnel bypass should be silently introduced.

## 4. Development offload lifecycle and required tests

| Stage | Producer / consumer | Exact acceptance before next stage |
| --- | --- | --- |
| 0. Select | Existing Iron God product lane | Owner-approved static **or runtime-admitted** roadmap slice; genuine capability/attempt failure; exact target source and deterministic native tests; alternate local route exhausted or excluded on evidence |
| 1. Reconcile | Resident control/ownership providers | Fresh current GitHub head/branch/path ownership, all canonical same-agent and other-agent in-flight reservations, no duplicate active PR/worker, authenticated owner mode and available resources |
| 2. Offer | Existing SQLite/MCP advisory store | One immutable bounded offer; advisory only; no automatic execution grants; expiry and audit |
| 3. Build | Existing DoWork hourly Chat-mode | Writes actual source + regression tests in an isolated worktree at approved exact SHA; no protected files, scope expansion, generated secrets or accidental policy changes |
| 4. Transfer | Existing GitHub connector -> local intake | Code at immutable GitHub proposal commit, one-source-commit exact base when required, bounded manifest and path list; SHA-256 hash and staging receipts verified by Azathoth |
| 5. Admit | Existing resident controller | Recheck owner session and lease just before mutation; stop on revocation, stale SHA, conflicting owner or invalid artifact; create a fresh isolated worktree |
| 6. Verify | Native product test runner | Native targeted tests, full repository preflight, compatibility/security tests and resource admission; report real verifier output with exact applied tree SHA |
| 7. Publish | Existing GitHub publisher | PR only after repo-native required preflight; fresh exact-head CI, independent review, all blocking threads resolved, mergeability and expected-head safe merge; avoid duplicate PR |
| 8. Accept | Resident product/release authority | Separate controlled installation/rollback and Product Reality when required; a merge does not mean the installed engine or product is updated |
| 9. Learn/recover | Existing memory and checkpoint owners | Durable independently corroborated attempt, repair, acceptance and continuation cursor; retry safe after crash/power loss, without another worker claiming the same files |

**Failure injection matrix:** missing grant; Give Me Control mid-import; untrusted agent identity; another `DISPATCHED`/`ACCEPTED_LOCAL`/`PR_BLOCKED` reservation, including an admitted runtime item; different Git base; edited roadmap spec; non-implementation failure; changing GitHub head; unsupported paths, binaries, symlinks and protected files; wrong or corrupt patch/manifest SHA; duplicate reservation, retries and lost responses; interrupted stage/partial write; test failure; review finding; GitHub outage; low RAM/free disk; Windows restart; failed release/rollback. Each must preserve existing work and explicitly deny unsafe progression.

## 5. Ordered engineering milestones (all require independent evidence)

### M0 — Current PR and runtime truth: repair before expanding
- [x] PR #762 orphan cleanup merged (GitHub); no claim that the current pinned laptop release includes it.
- [x] PR #766 guarded SQLite reservation, immutable patch and isolated importer merged (GitHub); its manual canary remains **lab-only**, not a scheduled worker.
- [x] PR #769 MCP/SQLite/offload roadmap merged (GitHub).
- [ ] PR #768: selector exact-head CI, independent review, all threads, safe merge and later separately controlled resident installation. Review its actual branch/CI before acting; its selector returns `CANDIDATE_UNVERIFIED` and **never** issues execution authority.
- [ ] Reconcile newer approved runtime-roadmap candidate support and GitHub-branch importer work against main in separate exact-head reviewed PRs. A branch Preflight or local test alone is not deployment.
- [ ] Verify live pinned release, live owner mode, existing leases, mailbox status and artifact staging capability. Never infer installed features from merged PRs.

**M0 exit:** exact-head clean merged source and documented installed-vs-merged capability inventory; no production authority changes.

### M1 — Trusted resident offer and intake
- [ ] Reuse existing roadmap refill, backlog/packet/development-memory, native capability calibration, and GitHub path/ownership; include runtime-admitted **candidate** items, not just their reservations. Verify origin, owner-approved roadmap, exact SHA, prior failure and negative oracles.
- [ ] Implement a safe local intake of GitHub proposal ref/digest: never execute a PR description, GitHub comment or mailbox text; only a pinned approved artifact.
- [ ] Bind import admission to real, *not fixture-true*, authenticated owner and independent lease callbacks; re-read immediately before applying. No generic MCP executable endpoint.
- [ ] Add durable resume cursor and explicit cancellation/revocation handling to the existing state/receipt owner.

**M1 exit:** one actual laptop-origin eligible offer can be consumed in isolated mode, safely refused on every conflict, and its receipt survives restart without duplicate admission.

### M2 — Scheduled DoWork delivers real source code
- [ ] Keep **existing task ID** `6a960254309c81919feb459eacd1b527`, hourly cadence, Chat mode, enabled state and seven-repository product mission. No duplicate scheduled task or changes to Workflow Watch.
- [x] Existing hourly DoWork instructions include guarded difficult-slice builder behavior; task ID, hourly schedule and enabled state independently verified on 2026-11-22. **Its most recent observed hourly run produced ZERO new code**, with no validated offer and an owner-control blocker; prompt text does not prove runtime intake or owner authority.
- [ ] Run a single genuine scheduled-task execution with exact registered offer, authenticated supported transport and a nontrivial source/test diff. Record scheduled run ID, correct slice ref, proposal commit and true hashes.
- [ ] Laptop independently imports, executes real native required tests/preflight, publishes through existing PR reviewer/merger, records verified outcome and resumes the next eligible product slice. Keep ordinary GitHub development fallback when no offload is offered.
- [ ] Repeat with intentional crash/duplicate-run and owner-revocation cases before enabling routine auto-offload.

**M2 exit:** demonstrable **scheduled DoWork -> real code -> immutable GitHub handoff -> Azathoth native verification -> governed PR/merge -> learning** with no fabricated actor proof, protected live files touched, or manual repetition of "continue."

### M3 — Genuine common MCP read/write and learning
- [ ] Implement and independently test per-principal scoped reads/advisory writes for resident Iron God, DoWork and Claude host when connected. Keep native controller/write authority separate.
- [ ] Resolve plan/entitlement-specific remote MCP incompatibility through the GitHub fallback rather than assuming hosted writes.
- [ ] Prove messages, receipts, cursors, restart, audit, cross-client correlation and learning provenance; no delivery-by-fiat.
- [ ] Score real code produced, native test passes, CI fixes, merges, product acceptance, rework and resource cost. A status message and advisory acknowledgment are not productive development.

**M3 exit:** proven two-way receipts for each actually supported client, independently verified learning, no unauthorized write, no duplicate supervisor or state store.

### M4 — Claude as second stronger implementer (AFTER DoWork success)
- [ ] Verify separately authorized account, credential, licensing, invocation quota and hard spend limit; do not assume a free Claude execution path.
- [ ] Allocate distinct authenticated actor identity, disjoint file/branch scope, owner/lease gates, idempotency and bounded priority relative to DoWork and local models.
- [ ] Run identical isolated canary, native acceptance, owner-veto and crash-replay tests, with deterministic routing/fallback when limits are reached.
- [ ] Do not create a second timer, user-facing approval controller or unverified write-enabled public MCP endpoint.

**M4 exit:** second-worker code reaches independent tests/PR safely, with enforced cost/no-duplication, and measured better production throughput than one-worker baseline.

### M5 — BLACKICE remote desktop integration and safe cutover
- [ ] Keep current Remote Desktop Commander connection and Norton 360. Do not alter established Tailscale HTTPS routes 443/8443.
- [x] BLACKICE [upstream PR #24](https://github.com/MoonlightClarity/BLACKICE/pull/24) is **merged** as of verification on 2026-11-22 (source milestone only; installed signed Windows release not proven).
- [ ] [Account-capability issue #25](https://github.com/MoonlightClarity/BLACKICE/issues/25) remains open with no response at verification; establish a supported real ChatGPT client and all required remote operations before any cutover. A host MCP endpoint cannot override ChatGPT plan entitlements.
- [ ] Trace installer provenance/signature and the current Windows "Access is denied" case using evidence. Never disable antivirus, bypass Windows protections or deploy a public remote-shell listener.
- [ ] Install a pinned, rollback-ready candidate in parallel only after compatible-client/security approval; bind authenticated private tailnet/local-only routes with least privilege, scoped capabilities and audited tool calls. Port 10000 remains conditional, not automatic.
- [ ] Validate full file read/write, bounded command, structured errors, terminal/process completion, transfer, Git/test workflows, security revocation, connection recovery after reboot, owner veto, resource use and actual ChatGPT/scheduled-task client compatibility.
- [ ] Quantify monthly remote tool calls. Demonstrate the required workflows fit the **Desktop Commander free allowance**, or prove BLACKICE genuinely replaces them, before canceling the paid tier. The target is about **$20/month remote-access savings**, not a claim that coding models/cloud compute have no cost.
- [ ] Document rollback and retain a recovery route through at least two witnessed restart/reconnection cycles.

**M5 exit:** functionally equivalent, secure, recoverable, zero-additional-recurring-fee remote access for the *actual* supported ChatGPT surfaces; then owner can choose to reduce/cancel paid remote access. If direct BLACKICE ChatGPT access is unsupported, use GitHub/offload for coding and retain the free/manual remote fallback without asserting full replacement.

### M6 — Demonstrate reliable product advancement
- [ ] Across the seven repositories, prove multiple real owner-approved product slices finished with native verification, safe PR/merge and actual usability evidence, not just infrastructure PRs.
- [ ] Exercise local-only/offline Ollama canary separately with no paid cloud tokens, proving multi-repo fairness, retained recovery state and qualified model/tool admission.
- [ ] Observe zero skipped owner controls, safe revocation/restart and no duplicate owners; all proposed vs verified vs installed states shown accurately.
- [ ] Report accepted development throughput, first-pass/repair rates, median handoff latency, repeated failed retries, actual native acceptance, recoveries and monthly marginal costs.

**M6 exit:** repeated unprompted end-to-end production advancements with useful end-user acceptance across products and truthful dashboard. A document, merged orchestrator PR, local fixture or synthetic test is never equivalent.

## 6. Practical release/readiness ledger to maintain

Every daily or hourly checkpoint must report, separately: `planned`, `code_written`, `focused_tests_passed`, `full_native_preflight_passed`, `exact_head_ci_passed`, `review_clear`, `merged`, `installed`, `runtime_connected`, `scheduled_canary_proven`, `product_accepted`, and `cost_cutover_proven`. Each check has exact source SHAs/test or log refs and a verified timestamp. Unknown stays UNKNOWN; an open green-independent-review PR with running CI stays **CI_PENDING**, not merged. Record outstanding blocker and single next action per workstream in [issue #747](https://github.com/jweter/project-orchestrator/issues/747).

**Hard stop:** Any ambiguous owner grant, unsigned/untrusted installer, model/cost spend, conflicting path lease, unverified exact SHA, failed mandatory test, pending review, or unqualified ChatGPT remote entitlement prevents the relevant unsafe action; it does not stop unrelated safe, independently authorized product development.
