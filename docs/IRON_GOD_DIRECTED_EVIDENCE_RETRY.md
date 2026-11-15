# Issue #710 — bounded evidence-driven local repair inside the existing product worker

This PR changes the **actual Iron God Level 2 directed local worker**.
It does not introduce another supervisor, planner, callback scheduler, cloud
model, durable forked job, release pin or owner-control authority.

## Motivation
The existing worker asks Ollama once to replace a specifically authorized
Python function/method, runs the owner-approved native failing test and reports
`SLICE_TESTS_FAILED` for any incorrect change. Although we have durable
failure diagnostics and advisory plans, the worker itself did not make a
changed corrective attempt using **the actual failed assertion**. That is a
real code-generation limitation and a direct path to #710's repeated useful
micro-edits.

## Exact opt-in contract
The **already owner-approved** Level 2 `direct` specification may include
`evidence_retry: true`. Without that literal opt-in, the worker remains
single-shot and the existing no-loop regression is unchanged.

When the first local source rewrite is syntactically valid but its targeted
native pytest test fails with an actual assertion (not a type-check/lint error
or missing dependency), the worker:

1. Records the first model response and native test evidence in the handback.
2. Rechecks owner authority and resource/worker cancellation.
3. Builds ONE new Ollama prompt from the *original source*, the prior answer
   and the real failing assertion (using existing
   `iron_god_directed_edit.build_prompt(..., previous=...)`).
4. Runs at most ONE additional local-model generation through the existing
   supervised callback; there is no cloud fallback or third generation.
5. Rejects an identical answer or code change as futile; on changed code,
   applies only the same already-approved named source units. The approved
   test file remains byte-identical and no other paths are allowed.
6. Re-runs native targeted tests; on success follows the **unchanged**
   format → commit → generated-change gates → exact-head canonical
   preflight → local acceptance/PR/CI/review/merge pipeline.
7. On any failure, returns the existing bounded worker failure and preserves
   the first and second diagnostic evidence; it never resets exhausted
   attempt lineage or claims product acceptance.

## Tests
- First generated implementation fails a real pytest assertion; a second
  generation receives that assertion plus previous answer, fixes the same
  function, passes and is locally accepted.
- Identical second generation fails promptly after two model requests,
  never entering a loop.
- Existing default behavior (no `evidence_retry`) still makes only one
  attempt and never auto-commits a failed implementation.

## Limits
This is **one bounded changed-strategy opportunity within a single,
already-approved slice**, not recursive autonomous multi-step development
and not an authorization to alter the runtime. Its effectiveness with live
Ollama 7B needs a real owner-authorized on-laptop trial. Controlled restart
still requires a verified installed/pinned release, CI-green PRs, acknowledged
Take the Wheel, healthy leases and RAM/Ollama conditions, and measured local-
only routing. Full #710 release proof additionally requires three real
model-authored verified edits, interrupted process restart/resume, multi-repo
fairness, zero cloud calls and native product acceptance.
