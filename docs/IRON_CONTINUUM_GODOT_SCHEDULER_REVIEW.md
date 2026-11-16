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

Targeted tests cover the generation-to-file race, post-write authority
revocation rollback, local-only task routing, independent-verifier failure,
durable retry evidence, and exhausted attempts.

Limitations: These are deterministic tests with simulated local Ollama and
Godot processes. This is **not** a claim of an installed end-to-end Everward
canary, verified native game behavior, or permission to bypass exact-head CI,
independent review, the owner gate, worker leases, or release promotion.
