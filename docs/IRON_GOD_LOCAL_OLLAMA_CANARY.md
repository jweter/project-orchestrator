# Issue #710 — guarded Ollama three-edit disposable canary

This is the **next, deliberately opt-in test stage** after PR #722 proved
three deterministic source edits across an owner pause and durable
Git/SQLite reopening.

## What it adds

`iron_god_canary_codegen.propose_source()` gives Ollama two bounded attempts
per one of three standalone pure Python functions. A strict AST validator
requires a single named function with a pure bounded arithmetic expression:
no imports, calls, attributes, file/network access, loops, decorators or
global statements. There is **no cloud fallback** or auto-reschedule of failed
generations. The local model must write the source; deterministic fixture
code is never substituted after a rejected local-model proposal.

The separately triggered script
`scripts/iron_god_live_ollama_canary.py` runs the same genuine native pytest,
disposable Git commits, existing exact-head SQLite/protected-ref acceptance,
owner pause after the second change and restart/reopen verification as #722,
but sources the three implementation edits through **guarded_generate** and
the existing measured 7B builder slot and RAM pressure guard.

Use only when Claude has imported a reviewed, current-head CI-green release,
the installed/pinned accepted SHA agrees with the executing checkout, and
**owner Take the Wheel is acknowledged by an active supervisor**.
A pulled development branch or an MCP message is not authorization.

    python scripts/iron_god_live_ollama_canary.py --run-disposable-live

The CLI checks the required `IRON_GOD_ACCEPTED_SHA` against actual Git HEAD
and the existing `evaluate_execution_authority()` gate before calling the
model, after every model response, before each step's code acceptance, and at
the end. With no explicit flag it refuses without contacting Ollama.
Owner pause, model unavailability or native test failure ends this canary
without promoting any code. This is a LOCAL-ONLY canary; it cannot start or
stop the production supervisor, modify existing product repositories,
publish a branch or spend Claude tokens.

## What the JSON receipt means

A successful opt-in real Ollama run returns a structured proof of **three
model-produced source snippets**, AST hashes, three distinct disposable Git
commits, real native red/green pytest verification, exact-head acceptance and
SQLite/protected-ref reopening after simulated interruption.

It reports `engine_started=false`, `production_repository_changed=false`,
`zero_cloud_token_use_proven=false`, `product_reality_proven=false`,
`deployment_ready=false` regardless of success. Passing this staged
disposable proof does **not** show that Iron God can complete meaningful
product repository tasks, use correct PR/prefight/merge gates, sustain
throughput across two repos while another blocks, or prove global
absence of Claude cloud token use. The owner-authorized production
three-edit canary, a genuine process-kill/restart test and controlled
deployment qualification are separate future steps.

## Restart notifications

The existing enabled DoWork Hourly readiness check is responsible for
notifying both the owner and Claude Code when a **controlled local-only
canary restart** is independently safe, and later when #710 is
fully release-qualified. This is not permission to auto-start a paused
engine. Do not tell the owner that a code-review PR merge or this test's
existence alone means the engine is ready. The user remains in control.
