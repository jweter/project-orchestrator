# ECC → Project Orchestrator Gap Analysis

Status: selective-adoption plan. Source repository: `affaan-m/ECC` (MIT). This repository does **not** vendor ECC or install its full skill/agent surface. ECC is treated as an external source of harness patterns; `DoWork.txt`, `project-orchestrator`, and each product repository remain authoritative.

## Executive decision

ECC is valuable to this portfolio primarily as an **agent-harness capability library**, not as a replacement control plane. The highest-value ideas are those that improve autonomous-loop correctness, harness observability, context economics, agent evaluation, AI regression protection, and extraction of repository-specific working conventions.

The portfolio already has stronger project-specific authority in several areas: exact-head preflight, fresh PR CI, Product Reality separation, deterministic P0-P6 scheduling, Knowledge Engine cross-repository compatibility, verified learning memory, throughput attribution, durable KPI telemetry, anti-stall rotation, and repository-specific licensing/provenance boundaries. ECC features that duplicate those controls should not become a parallel authority.

The adoption rule is therefore:

> **Observe with ECC-derived techniques; verify with project-orchestrator; promote only through repository authority.**

No ECC-derived capability graduates into routine use unless it closes a measured engineering gap without increasing quality, security, context, or human-intervention debt.

## First upgrade selected: autonomous-loop design audit

ECC's `loop-design-check` exposes a useful failure class that the portfolio only partially encoded: a repeated autonomous loop can be technically functional while still being structurally unsafe or wasteful because its goal is not machine-decidable, its judge is not independent, retry behavior is unbounded, acceptance criteria can drift, or the loop treats every unchanged state as a reason to force a change.

This repository now adapts those concepts into an original deterministic control:

- `engineering/autonomy-loop-contract.json` — versioned contract for DoWork Hourly;
- `project_orchestrator.loop_audit` — deterministic auditor;
- `project-orchestrator loop-audit` — CLI surface;
- canonical preflight executes the audit;
- tests exercise runaway retry, Goodhart pressure, self-judging, stale context, authority overlap, Product Reality inference, and bad regulator/no-op semantics.

This is intentionally stricter than a prose checklist and intentionally narrower than product authority. It does not decide what to build or whether a product is good. It checks whether the autonomous engineering loop is structurally allowed to trust itself.

### Deliberate adaptation from ECC

ECC's general guidance emphasizes retaining human judgment. This portfolio already has an explicit, user-authorized autonomous merge policy for objectively GREEN work. We therefore adapt the principle rather than copying a blanket human-merge rule:

- deterministic/repository-authorized merge decisions may remain automated;
- Product Reality, material ambiguity, fundamental direction, major unauthorized architecture, license changes, destructive migration, privacy/security boundary changes, paid services, credentials, release authorization, and irreversible decisions remain outside autonomous authority;
- the builder cannot rewrite its own acceptance criteria;
- KPIs are evidence, never merge authority.

## Capability-by-capability assessment

| ECC capability / family | Existing portfolio coverage | Gap | Decision | Initial scope | Evidence of value required |
|---|---|---|---|---|---|
| `loop-design-check` | Anti-stall, throughput guard, deterministic gates exist | No single structural audit for judge independence, retry bounds, Goodhart risk, dead-band semantics | **ADOPTED / ADAPTED** | project-orchestrator | Preflight prevents malformed loop contract; no repeated passive-loop failure without explicit disposition |
| `harness-audit` | We validate product/control-plane state, not the whole agent harness footprint | Agent/rule/hook/MCP/skill drift can accumulate unnoticed | **ADOPTED / ADAPTED** | project-orchestrator | Detect stale/duplicate/conflicting harness config and reduce avoidable context/tool failures |
| `context-budget` | No prior first-class measurement of injected repository rules, skills, MCP descriptors, or persistent context cost | Context can become a hidden throughput/quality bottleneck | **ADOPTED / ADAPTED** | project-orchestrator | Measured context overhead and trend; identify removable low-value context without quality regression |
| `security-scan` / AgentShield concepts | Repo security/provenance gates exist | Harness permissions, hooks, MCP servers, prompt-injection surfaces, and local agent config need a separate threat model | **PILOT** | orchestrator + local agent harness | Actionable findings with low false-positive rate; no duplication of normal source-code scanners |
| `agent-eval` | KPI system measures portfolio flow, not per-agent capability consistency | No controlled benchmark of different coding-agent configurations on our own tasks | **ADOPTED / ADAPTED** | orchestrator evaluation suite | Pass rate, deterministic gate success, intervention burden, runtime/cost where measurable |
| `skill-create` from git history | Verified learning memory stores diagnosed lessons | We did not systematically distill repeated repo conventions into task-triggered execution guidance | **ADOPTED / ADAPTED** | one repo at a time | Fewer repeated convention/test failures after a verified skill is used and measured |
| `ai-regression-testing` | Normal tests + regression memory exist | Same-model builder/reviewer correlated failure is not explicitly measured | **ADOPTED CORE / PRODUCT PILOT** | orchestrator first; products selectively | Independent or adversarial regression catches defects normal implementation loop missed |
| `continuous-learning-v2` | Strong verified learning memory + effectiveness measurement | ECC can discover more candidate patterns earlier | **DEFER, THEN BRIDGE** | project-scoped candidate layer only | Candidate instinct must be independently verified before entering authoritative memory |
| Memory Vault | GitHub + learning memory preserve durable truth; sessions can still lose transient handoff context | Cross-harness/session transfer of non-authoritative context could improve continuity | **DEFER / PILOT** | local agent handoffs | Less repeated rediscovery without stale-context errors or policy injection |
| `contract-first` | KE compatibility planner already models Core→AI→Web contracts | Generic contract discipline may catch API changes outside modeled KE paths | **SELECTIVE** | Knowledge Engine, future external APIs | Additional incompatibility caught without creating parallel schema authority |
| `autonomous-loops` | DoWork is already a regulator/engineering loop | Mostly conceptual overlap | **EXTRACT PRINCIPLES ONLY** | orchestrator | No separate runtime; use only concepts that strengthen current loop |
| `continuous-agent-loop` | DoWork + local unattended workers already cover repeated execution | Generic loop could duplicate scheduler | **REJECT AS RUNTIME** | none | N/A; do not create competing scheduler |
| verification-loop patterns | Exact-head preflight + fresh PR CI already strong | Could enrich adversarial/second-pass verification | **SELECTIVE** | high-risk changes | Additional defect detection, not extra status churn |
| TDD/testing skills | Product repos already carry repository-native tests | Generic advice may be redundant | **ON-DEMAND** | per repo | Use only when repo lacks equivalent native guidance |
| code-review agents | Specialist scrutiny already required for high-risk domains | General reviews could add breadth but can create noisy duplicate review | **SELECTIVE** | high-risk PRs | Review must identify material issues above baseline, not stylistic churn |
| security-reviewer | Existing security specialist lane | Potential second perspective on application-level boundary changes | **SELECTIVE** | security/privacy changes | Material findings only; repository policy remains authority |
| database review | No database-heavy current product except KE persistence | Generic DB review can help migrations/indexing later | **ON-DEMAND** | KE Core | Needed only when actual schema/performance change exists |
| Python review/testing | All current orchestration + much of KE/Rocksmith are Python; native tooling is already mature | Limited gap | **LOW PRIORITY** | targeted failures | Must close an observed lint/test/design failure not covered natively |
| `windows-desktop-e2e` | Rocksmith has unattended Windows/Product Reality plans | UI/build-handoff automation patterns can remove Jeremy from routine loops | **HIGH-VALUE PRODUCT PILOT** | Rocksmith only | More deterministic launcher/DLC Builder smoke coverage; never claims musical correctness |
| C++ coding/review skills | Everward has UE/C++ direction and deterministic simulation rules | Could strengthen C++ mechanical review | **PRODUCT PILOT** | Everward only | Detect correctness/lifetime/performance defects while preserving Unreal architecture |
| generic frontend skills | KE Web already has repo-specific patterns | Potentially useful for UX work but broad | **ON-DEMAND** | KE Web | Only if a concrete UX/frontend bottleneck appears |
| generic backend skills | Current products have specific architectures | Broad and potentially noisy | **ON-DEMAND** | per repo | Must match actual stack and repository policy |
| browser/E2E skills | Web Product Reality exists | Could improve deterministic web smoke testing | **SELECTIVE** | KE Web | Reduced manual regression burden without treating screenshots as product truth |
| documentation agents | Docs/status already required by policies | Could accelerate drafts but not authority | **LOW PRIORITY** | all repos | Less stale docs with no increase in prose churn |
| refactor-cleaner / dead-code skills | Branch lifecycle and roadmap priority favor product work | Risk of low-value activity | **DEFER** | none by default | Only when measured debt blocks delivery |
| build-error resolvers | Deterministic repair engine already classifies bounded repair | Significant overlap | **REJECT AS PARALLEL AUTHORITY** | none | Incorporate only missing deterministic fingerprints into existing repair engine |
| language-specific build resolvers | Same as above | Could add failure signatures | **EXTRACT SIGNATURES ONLY** | repair engine | New verified failure class improves repair success without unsafe auto-fix |
| browser/search/research helpers | Agents already have web/GitHub evidence collection paths | Redundant as engineering authority | **ON-DEMAND** | research tasks | Must preserve provenance and repository evidence boundaries |
| hooks / lifecycle automation | n8n and scheduler already perform routing | Could duplicate or create hidden execution | **HIGH CAUTION** | orchestrator only | Every hook must be versioned, observable, bounded, and non-authoritative |
| MCP server catalog/patterns | MCP can extend local agents | Connector expansion can increase attack/context surface | **SELECTIVE** | local harness | Only add a server for a measured missing capability; least privilege and audit required |
| marketplace/plugin install framework | Not needed to run current portfolio | Adds supply-chain/context complexity | **REJECT FOR NOW** | none | Revisit only if manual maintenance becomes a measured bottleneck |
| generic multi-agent orchestration | Specialist lanes already deterministic and ownership-aware | Parallel framework duplication would create competing ownership semantics | **REJECT AS CONTROL PLANE** | none | Extract isolated scheduling ideas only if KPI shows a gap |
| prompt/style/rules collections | Repo authority already contains targeted instructions | Large context cost and conflict risk | **REJECT BULK IMPORT** | none | Individual rule may be adapted only after concrete failure |
| framework-specific skills unrelated to stack | No product fit | Pure context/dependency waste | **REJECT** | none | N/A |

## Full skill-surface policy

ECC exposes hundreds of narrowly scoped skills. Listing every framework/library skill as a permanent project dependency would defeat the purpose of context-sensitive engineering. The complete surface is therefore covered by the following deterministic classification rule:

1. **Directly relevant harness capability** — evaluate individually in this document and pilot through project-orchestrator.
2. **Direct product-stack capability** — pilot only inside the matching product (`windows-desktop-e2e` for Rocksmith, selected C++ guidance for Everward, selected contract/AI regression guidance for Knowledge Engine).
3. **Capability already stronger in our system** — reject as a parallel authority and extract only a missing technique if evidence identifies a gap.
4. **Framework/language/domain not present in our repositories** — reject from active context; discover on demand if the stack changes.
5. **Memory/learning capability** — candidate context only until promoted through verified learning memory.
6. **Hook/plugin/MCP capability** — require threat/context-budget review before activation.

This rule means the portfolio can benefit from ECC's breadth without loading hundreds of irrelevant skills into every agent session.

## Project-specific gap map

### Knowledge Engine

High-value ECC-derived techniques:

- AI regression testing for synthesis/report behavior;
- selective contract-first review at Core→AI→Web boundaries;
- repository-history skill extraction for evidence/provenance conventions;
- harness security/context-budget checks for local AI/Ollama agents.

Must remain authoritative in Knowledge Engine itself:

- scientific provenance and traceability;
- deterministic/non-fabricated state;
- source/licensing policy;
- compatibility planning;
- human review where scientific judgment is not mechanically derivable.

No ECC generic AI workflow may convert model confidence into scientific truth.

### Rocksmith Song DLC Generator

High-value ECC-derived techniques:

- Windows desktop E2E patterns for launcher, UI, build handoff, staging, and unattended smoke checks;
- agent evaluation using deterministic fixture songs and known expected transforms;
- AI regression testing around recognition/mapping/timing code;
- repository-history skill extraction for Bass/Lead/Rhythm and provenance constraints.

Hard boundary: automation can verify transport, timing invariants, deterministic transforms, UI state, and build artifacts; it cannot fabricate human musical acceptance for tone, fingering, uncertain timing interpretation, printed score interpretation, or real-game feel.

### Everward

High-value ECC-derived techniques:

- selected modern C++ review/testing guidance;
- AI regression tests for deterministic simulation/persistence boundaries;
- agent evaluation on headless simulation tasks;
- context-budget/harness audit for Unreal/local-agent workflows.

Hard boundary: generic harness guidance cannot move mechanical truth into Unreal presentation. Simulation remains authoritative; Unreal presents it.

### Project Orchestrator

Highest leverage:

1. loop-design audit — implemented and preflight-gated;
2. harness audit — implemented in Phase B;
3. context budget — implemented in Phase B;
4. agent evaluation + AI regression harness — implemented in Phase C;
5. verified skill extraction — implemented in Phase D;
6. candidate-learning bridge / memory handoff — deferred until measured value supports Phase E.

## Conflict and compatibility analysis

| ECC-style principle | Portfolio policy | Resolution |
|---|---|---|
| Human retains judgment over high-impact decisions | DoWork may autonomously merge objectively GREEN, already-authorized changes | Keep autonomous merge; explicitly enumerate human-only authority and independent machine gates |
| Learned instincts can gain confidence from use | Durable portfolio memory requires verified root cause/fix/protection | ECC-style instinct is only a candidate; authoritative memory requires verification |
| Large catalog of skills is available | Context budget and product authority should remain narrow | Discover/select on demand; no bulk loading |
| Hooks/plugins can automate lifecycle actions | Hidden side effects conflict with explicit execution attribution | Any adopted hook must be versioned, observable, actor-attributed, bounded, and non-authoritative |
| Generic multi-agent patterns | Specialist lanes already own work deterministically | Do not create a second ownership/scheduler system |
| Generic contract guidance | KE compatibility planner encodes actual dependencies | Use contract-first as review technique only; compatibility planner remains authority |

## Adoption roadmap

### Phase A — structural loop safety

**Status: implemented and verified through exact-head preflight + PR CI in PR #77.**

- autonomy loop contract;
- deterministic audit;
- exact-head preflight gate;
- regression tests;
- no new dependency.

Success criterion: malformed/unsafe DoWork loop design fails preflight before it can become the portfolio control contract.

### Phase B — harness observability

**Status: implemented and verified through exact-head preflight + fresh PR CI in PR #81. Operational six-repository audit evidence is still required before claiming measured portfolio benefit.**

The read-only harness inventory/audit and context-budget report now measure:

- static repository instruction/rule bytes and approximate tokens;
- task-triggered skill/agent/tool-descriptor context when present;
- repository workflow hooks and MCP descriptors;
- exact duplicate context surfaces;
- missing required surfaces for mounted repositories;
- explicit authority overlap;
- literal secret-like MCP values;
- unmounted repositories as `UNVERIFIED` rather than inferred state.

The first version intentionally does not use a language model to decide whether two prose instructions semantically conflict. It uses deterministic path, hash, budget, authority, and configuration evidence only.

Do **not** auto-delete configuration in the first version.

Success criterion: identify at least one real source of context, security, or execution friction and verify remediation without product regression. If no material issue appears, stop expanding this lane.

### Phase C — agent quality measurement

**Status: evaluation harness verified in PR #83; disposable exact-baseline matrix runner verified in PR #85. Comparative agent-quality claims remain blocked until at least two real local agent/configurations execute the same pinned tasks.**

The implemented harness tracks:

- first-pass preflight/CI success;
- repair iterations;
- human intervention;
- elapsed execution time when available;
- repeated failure classes;
- deterministic acceptance only where an objective oracle exists.

Results use the existing KPI telemetry; no second KPI store exists. Product Reality remains outside automated scoring.

### Phase D — AI regression and repository-specific skills

**Status: verified-history skill extraction and explicit task/path selection implemented and verified in PR #87. The first project-orchestrator pilot has a verified evidence contract but effectiveness is deliberately `UNMEASURED` until used on a real matching change.**

A repository skill must identify:

- verified source lessons;
- exact history commit refs;
- exact repository scope;
- applicable paths/task triggers;
- prevention rule;
- deterministic verification;
- residual risk.

Selection is explicit only; fuzzy similarity and cross-repository transfer are prohibited. Product-repository skills are not created until their own verified lessons/history support them. Re-measure whether each adopted skill reduces repeat failures, catches defects earlier, shortens repair, or enables verified successful reuse.

### Phase E — cross-harness transient memory

Only after the earlier phases show measured value, pilot Memory-Vault-like session handoff for **untrusted transient context**. Durable truth stays in GitHub/control-plane/verified learning memory. Every handoff must declare freshness and source; stale context cannot override live repository state.

## KPI linkage

ECC adoption is successful only when the durable KPI/event system provides evidence of improvement. Relevant measures include:

- first-pass preflight rate;
- first-pass CI rate;
- repair latency;
- promotion latency;
- implementation→merge time;
- repeated CI/failure class count;
- human intervention rate/count;
- branch hygiene debt;
- issue advancement rate;
- hours with product movement;
- context overhead now instrumented by Phase B.

No single metric can authorize expansion. A capability that improves speed while increasing correctness hotfixes, Product Reality debt, security risk, or human cleanup is not a successful adoption.

## Licensing and provenance

ECC is MIT licensed. This integration currently vendors **no ECC source code** and takes no runtime dependency on ECC. The loop audit, harness/context audit, evaluation harness, and repository-skill extraction are original implementations derived from general harness/loop-safety/agent-evaluation/skill-extraction concepts described by ECC. If a future phase copies or substantially modifies ECC code, retain the required MIT copyright/license notice for that copied portion and document its provenance explicitly.

## Next exact gates

1. run the harness/context audit against the full local six-repository workspace and record actual findings;
2. configure and execute the pinned Phase C matrix against at least two real local agent/configurations before making comparative claims;
3. use the first Phase D repository skill on a real matching engineering change and record verified effectiveness evidence;
4. mine verified product-repository history and add product skills one at a time only where the evidence contract is satisfied;
5. begin Phase E transient-memory work only if the earlier phases demonstrate measured value rather than additional context/governance overhead.
