# ChatGPT-to-Iron God difficult-slice offload — guarded pilot

**Status:** source implementation and isolated local Git canary only. **NOT ENABLED in the hourly task, not installed in the resident engine, not a merge or owner-control authority.** Follow [Iron Continuum #747](https://github.com/jweter/project-orchestrator/issues/747), existing [microtask #710](https://github.com/jweter/project-orchestrator/issues/710), and [MCP contract](IRON_GOD_MCP_COORDINATION.md).

## Why and what is reused

An authorized local worker may exhaust calibrated capability on a substantive slice. Instead of repeating the same failed generation, the existing hourly **DoWork** could take one *unowned, owner-approved* slice, produce a reviewable patch and tests using its available reasoning and tools, and return candidate bytes for **independent** validation on Azathoth. This reduces liveness dependence on paid Claude tokens without moving owner authorization or publication power into the MCP mailbox.

- **SQLite:** Additive offload proposal/reservation/artifact tables in the existing local advisory `coordination.sqlite` via `mcp_coordination`. No second worker-leasing DB, supervisor or task scheduler.
- **MCP:** The existing local stdio `iron_god_mcp_server` offers read-only `get_offload_proposals(limit)`. It exposes bounded IDs/repository/base SHA/status only. This tool cannot create or execute a claim.
- **Engine authority:** `human_ownership`, existing owner controller, worktree/worker lease and release identity, approved roadmap, product native preflight, GitHub CI/reviews and verified release policy remain authoritative. A GitHub issue, SQLite row or MCP message never grants these.
- **GitHub:** Existing PR creation/repair/merge processes apply only after exact-head evidence. Offload doesn't create a competing publisher.

## Protocol (source in `iron_god_offload.py`)

1. **Offer (advisory):** A caller supplies one registered repository, fixed `slice_id`, exact 40-hex baseline SHA, no more than 16 explicitly allowed source/test file paths, an existing approved authority *reference*, and the measured capability-failure *reference*. This layer validates shape, **not** authenticity or current eligibility; the resident dispatch must independently prove them. Immutable identity is SHA-256(repo, slice, base).
2. **Reserve:** `claim_slice` uses SQLite `BEGIN IMMEDIATE` and one unique row to serialize ChatGPT reservation. Identical replays succeed; a different claimant fails closed. This **is not a worker lease**; it cannot preempt existing active work.
3. **Produce:** ChatGPT writes a bounded, UTF-8 Git unified patch (<= 256 KiB, <= 16 exact file paths). The producer prohibits binary patches, renames, deletions, Git metadata, policy/protected paths, and symlink/submodule modes. `seal_candidate` creates exclusive immutable `<offer-id>-<patch-sha>.patch/.json` files and SHA-256 manifest evidence in a configured private staging folder. Replays are byte-identical; collisions refuse. Source code is **not** sent in a 3,000-character advisory mailbox message.
4. **Register:** `register_candidate` independently hashes the staged patch and manifest, verifies the offer/claim and writes one immutable advisory candidate receipt in the same SQLite DB.
5. **Consume:** `prepare_isolated_import` requires exact registered SQLite offer, claim, candidate digest, configured stage root and **independent callbacks** for currently authenticated owner authority and worker ownership. It does not expose itself through MCP. It checks those gates repeatedly, including immediately before applying, checks Git has the exact base commit, creates a NEW detached worktree, verifies `git apply --check --index`, applies and cross-checks changed paths.
6. **Verify:** The result is *only* `PREFLIGHT_UNVERIFIED`, never `PASS`. The resident product's native targeted tests, canonical full preflight and exact-head CI/review are still mandatory, using the **existing** publication lane. Tests and verification receipts must not be asserted from ChatGPT-generated prose.
7. **Publish/learn:** Once native checks pass, the normal governed PR/CI/review/merge and native Product Reality pipeline applies. Only independently corroborated outcomes can be promoted to `development-memory.sqlite3` learning rules. This pilot deliberately does not add a self-reported accepted outcome.

## Local CLI for existing hourly task (not activated)

The existing local Python CLI wraps the same advisory SQLite store; no new
service, scheduler, network listener or worker is created:

```powershell
# Values are supplied by the protected installed environment, never by a message.
$env:IRON_GOD_MCP_COORDINATION_DB = "<absolute private coordination.sqlite>"
python -m project_orchestrator.iron_god_offload_cli list
python -m project_orchestrator.iron_god_offload_cli show --id <64-hex-offer-id>

# These remain disabled unless the local advisory write switch is explicitly
# enabled by existing owner-controlled installation policy:
# python -m project_orchestrator.iron_god_offload_cli claim --id <offer-id>
# python -m project_orchestrator.iron_god_offload_cli seal --id <offer-id> < candidate.patch
```

The `seal` command requires both `IRON_GOD_MCP_ALLOW_ADVISORY_WRITES=1`
and a protected absolute `IRON_GOD_OFFLOAD_STAGE_ROOT`. It reads at most
256 KiB plus one byte from standard input, creates immutable digests and
registers an unverified candidate. The output carries hashes and bounded IDs
only, not patch data. It does not import or run code. The separate isolated
import API is **not exposed in CLI or MCP** and still requires independent
current owner/worker-lease checks from the resident controller.

A two-process Windows test now proves CLI claim and seal in separate
Python sessions, a durable SQLite receipt, and independent import into a
disposable Git worktree. Its test-only true callbacks **do not verify** live
Take the Wheel authorization. Nothing in this test enables an hourly offload.

## Safety and operating limits

- **No routine rollout now:** The live hourly DoWork schedule, Chat mode, owner controls, runtime grants, Claude billing and pinned release remain unchanged. The current laptop mailbox is read-only for ChatGPT, so it cannot silently write offload proposals/reservations.
- **No automatic PR:** This component stops before commit/push/PR because it does not authenticate GitHub review/CI or native acceptance. Use existing product-lane facilities after independent local validation.
- **No duplicate work:** Before any reservation/real import, the caller must reconcile current GitHub branch/PR/file claims, exact owner session, active worker leases, source head, and actual failure evidence. Since the local advisory label is not an authenticated security principal, an offload reservation alone cannot establish exclusive execution ownership.
- **No reset/cleanup:** An import that fails after creating an isolated worktree preserves it for controlled recovery; neither code nor test ever resets the user's current checkout, cleans active worktrees, changes system directories or deletes evidence.
- **No unconditional authority callbacks in production:** The canary deliberately injects boolean callbacks to test allowed/denied/revoked paths. The real product lane must wire callbacks to its authenticated controller and worker lease; returning `True` unconditionally would violate this design.

## Evidence and next gates

Focused deterministic tests in `tests/test_iron_god_offload.py` cover SQLite concurrent replay, immutable candidate registration, wrong base, altered bytes, path fencing, owner veto, revocation between `git apply --check` and `git apply`, and isolated Git worktree import. The canary checks an independently compiled Python edit; that is **not** any product's full canonical native acceptance.

To enable production offload:
1. Review this PR, pass full exact-head preflight/CI/review, merge, and complete the separate owner-controlled installed-release update.
2. In a separate guarded PR, wire **real** eligible stalled slices from the existing canonical backlog/failure history into the advisory offer producer. The mailbox does not originate tasks. Add an authenticated claimant adapter and exact-owner/lease callbacks; prove no duplicate work or unauthorized owner transition.
3. Build a protected staging transport reachable in the **scheduled ChatGPT execution environment** and the laptop, with bounded content and independent digest checks. Do not assume the scheduled job has the same connector availability as the current interactive conversation.
4. Run a controlled end-to-end canary: owner-approved slice -> ChatGPT actual code + tests -> immutable artifact -> Azathoth import -> real native preflight -> existing GitHub PR -> exact-head CI/review -> authorized merge -> Product Reality -> evidence-based learning. Test power loss/replay halfway through without duplicate PRs.
5. **Only after this real canary succeeds**, modify the prompt of the *existing* DoWork task to use this path; preserve its task ID, hourly cadence, enabled status and Chat mode. Do not create another automation.

**Definition of current PR:** a usable guarded transport/import primitive with focused isolated tests, *not* completed S1–S6 Iron Continuum or working unattended cloud-to-laptop offload.
