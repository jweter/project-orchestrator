# IRON CONTINUUM #710 — Godot scheduler review repair

This patch addresses three independent Codex findings on PR #746, retaining
the already-approved Godot capability graph as the only work authority.

1. **Owner revocation while generating**: the Godot author passes a live owner
   `should_abort` callback into the local Ollama generator. The bounded
   native builder also rechecks authorization before each prelude/model call,
   before applying model-authored files, before build/test, and after the test.
   A revoked run records `CANCELLED_BY_GUARD` and rolls back its isolated
   worktree. A cancelled Ollama request under revoked owner control is
   `BLOCKED`, never misreported as a memory wait.
2. **Local route conservation**: an eligible owner-approved, exact-head LOCAL
   GDScript request carries an immutable `test_script` SceneTree oracle,
   independent of Python `acceptance_examples`. The Python-only rerouter
   therefore leaves that request LOCAL, preventing accidental paid transport.
3. **Bounded retries**: a Godot job with a failed worker handback or independent
   verification and fewer than `MAX_ATTEMPTS` may start another admission-
   checked attempt, preserving earlier evidence. Reaching the cap emits
   `ATTEMPTS_EXHAUSTED`. A still-running or uncertain worker continues to
   demand reconciliation; no worker lease or owner permission is synthesized.
4. **Calibrated model identity**: the Product scheduler asks the existing
   measured capability/memory router for a currently admitted Level-1 model.
   It passes that exact model and the installed state root to the Godot child
   process. The child verifies the model is in the approved ladder and its
   calibration still matches this hardware/level, **before any Git worktree or
   Ollama call**. Ollama receives an explicit `requested=model`; its default
   builder cannot override the qualified selection. Missing calibration or
   insufficient memory leads to a blocked/waiting outcome, never an
   unqualified fallback or paid transport.

Targeted tests cover the generation-to-file race, post-write authority
revocation rollback, local-only task routing, independent-verifier failure,
durable retry evidence, and exhausted attempts.

Limitations: These are deterministic tests with simulated local Ollama and
Godot processes. This is **not** a claim of an installed end-to-end Everward
canary, verified native game behavior, or permission to bypass exact-head CI,
independent review, the owner gate, worker leases, or release promotion.

## Durable capability-graph progression

After the common publisher records a Godot PR as MERGED,
`roadmap_progress.completed_slices()` now recognizes its request **only**
when the persisted merged Godot receipt matches the repository-scoped
publication record with a valid merge SHA and PR URL. That explicit evidence
feeds the existing graph-only roadmap progress PR builder. Scheduler overlay
state alone never marks a capability-graph slice complete. Missing, unmerged,
cross-repository, or malformed receipts remain unverified and cannot advance
canonical product completion.
